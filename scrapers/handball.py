"""Les matchs À DOMICILE du handball lyonnais de niveau national.

DEUX ÉQUIPES, DEUX SALLES. Personne à Lyon ne joue en StarLigue ni en
ProLigue ; le plus haut de l'agglomération est la Nationale 1, et le
meilleur club de la ville est deux étages plus bas :

  Villeurbanne HA        Nationale 1 masculine   Salle des Gratte-Ciel
  Handball Club de Lyon  Nationale 3 féminine    Gymnase Matthias Favier (9e)

LA SOURCE EST UN ICS. La fédération publie, pour chaque équipe engagée
dans une compétition, un calendrier iCalendar :

  https://competition-calendar.ffhandball.fr/c-<compétition>/s-<club>.ics

Sept kilo-octets qui donnent tout ce qu'une carte demande — l'heure à
l'heure de Paris, les deux équipes, la salle AVEC son adresse, et l'URL
de la fiche du match. C'est le même fichier que propose le bouton
« Ajouter les matchs à votre calendrier » sur le site fédéral.

CE QUE L'ICS NE DONNE PAS : la saison entière. Le handball programme au
fur et à mesure — sur les 22 journées de Nationale 1, neuf rencontres
seulement ont une date au 20 septembre 2026, les autres n'ont ni horaire
ni salle dans le système fédéral. Le fil publie donc ce qui est calé et
ramassera le reste au fil des jours, ce qui tombe bien : il se relit
tous les matins.

LES IDENTIFIANTS SE PÉRIMENT. Le numéro de club (2732, 2746) ne bouge
jamais ; celui de la COMPÉTITION change à chaque saison, et changerait
aussi le jour d'une montée. Pour les relire, une fois par an :

  1. ouvrir la page de la compétition sur ffhandball.fr
  2. cliquer « Ajouter les matchs à votre calendrier »
  3. choisir « iCal (.ics) » puis l'équipe : le bouton porte l'URL,
     dont on recopie les deux nombres.

En attendant, le scraper surveille : si le dernier match d'un calendrier
date de plus de trois mois, il réclame la relecture en tête du run.
"""
from typing import List, Optional
from datetime import date as Date, datetime, timedelta
import os
import re
import sys
import unicodedata

import requests

from .base import Event, iso, get as base_get

HORIZON_DAYS = 180

ICS = "https://competition-calendar.ffhandball.fr/c-%s/s-%s.ics"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# (compétition, club, nom fédéral de l'équipe qui reçoit, nom affiché).
# Le nom fédéral sert à reconnaître le domicile dans « A vs B » ; il est
# écrit exactement comme la fédération l'écrit.
EQUIPES = (
    ("32469", "2732", "VILLEURBANNE HANDBALL ASSOCIATION",
     "Villeurbanne Handball"),
    ("30494", "2746", "HANDBALL CLUB DE LYON",
     "Handball Club de Lyon féminines"),
)

# Au-delà, un calendrier dont le dernier match est si vieux ne parle plus
# de la saison en cours : ses identifiants sont périmés.
PERIME_JOURS = 90

# Les salles où ces deux équipes reçoivent. Liste FERMÉE : un match
# délocalisé ailleurs est écarté avec un mot sur stderr plutôt que publié
# sous un nom qu'on n'a pas vérifié. Clés sans accents ni casse.
SALLES = {
    "salle des gratte ciel": "Salle des Gratte-Ciel",
    "matthias favier": "Gymnase Matthias Favier",
}
SLUGS = {
    "Salle des Gratte-Ciel": "salle-des-gratte-ciel",
    "Gymnase Matthias Favier": "gymnase-matthias-favier",
}

# La fédération écrit ses divisions en capitales et sans accents. On les
# nomme proprement ; une division absente de la table passe quand même,
# en minuscules, avec un mot sur stderr.
DIVISIONS = (
    ("nationale 1 masculine", "Nationale 1 masculine"),
    ("nationale 2 masculine", "Nationale 2 masculine"),
    ("nationale 3 masculine", "Nationale 3 masculine"),
    ("nationale 1 feminine", "Nationale 1 féminine"),
    ("nationale 2 feminine", "Nationale 2 féminine"),
    ("nationale 3 feminine", "Nationale 3 féminine"),
    ("proligue", "ProLigue"),
    ("starligue", "Liqui Moly StarLigue"),
)

# Les sigles des clubs de hand, qu'un title() abîmerait.
SIGLES = frozenset("HB HBC HBCL AS ASUL US USAM CA CS AC SC JS ES EC CSAV "
                   "OGC ALC CJF CM MJC ASPTT US SMB JDA AL P16F N3".split())
PARTICULES = frozenset("de du des la le les et en sur aux au d l".split())
_LETTRES = re.compile(r"[A-Za-zÀ-ÿ]+")

# « A vs B », tel que l'ICS résume un match.
DUEL = re.compile(r"^(.+?)\s+vs\s+(.+)$", re.I)


def _norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower())
                   if unicodedata.category(c) != "Mn").strip()


def _joli(nom: str) -> str:
    """« CALUIRE RILLIEUX HANDBALL » → « Caluire Rillieux Handball »."""
    mots = re.split(r"(\s+)", (nom or "").strip())
    out = []
    for i, mot in enumerate(mots):
        if not mot.strip():
            out.append(mot)
            continue
        nu = mot.strip(".").upper()
        if nu in SIGLES or "." in mot:
            out.append(mot.upper())
        elif mot.lower() in PARTICULES and i > 0:
            out.append(mot.lower())
        elif mot.isupper():
            out.append(_LETTRES.sub(lambda m: m.group(0).capitalize(), mot))
        else:
            out.append(mot)
    return "".join(out)


def _alerte(message: str) -> None:
    """Un avertissement qui se voit jusque dans l'en-tête du run."""
    print("[HAND] " + message, file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("::warning title=HANDBALL::%s" % message.replace("\n", "%0A"))


def _deplie(texte: str) -> List[str]:
    """Les lignes d'un ICS, repliées comme le veut la norme.

    iCalendar coupe toute ligne de plus de 75 octets et fait commencer la
    suite par une espace. Sans ce recollage, une URL de fiche de match ou
    une adresse de salle arrive en morceaux.
    """
    return (texte.replace("\r\n", "\n").replace("\r", "\n")
            .replace("\n ", "").replace("\n\t", "").splitlines())


def _dechappe(v: str) -> str:
    return (v.replace("\\,", ",").replace("\\;", ";")
            .replace("\\n", " ").replace("\\N", " ").replace("\\\\", "\\"))


def _evenements(texte: str) -> List[dict]:
    """Les VEVENT d'un ICS, en dictionnaires {propriété: valeur}."""
    out, courant = [], None
    for ligne in _deplie(texte):
        if ligne.startswith("BEGIN:VEVENT"):
            courant = {}
        elif ligne.startswith("END:VEVENT"):
            if courant:
                out.append(courant)
            courant = None
        elif courant is not None and ":" in ligne:
            cle, valeur = ligne.split(":", 1)
            courant[cle.split(";")[0].upper()] = _dechappe(valeur.strip())
    return out


def _debut(e: dict) -> Optional[datetime]:
    """DTSTART, en heure locale. La fédération écrit l'heure de Paris."""
    v = e.get("DTSTART") or ""
    try:
        if v.endswith("Z"):        # jamais vu, mais la norme l'autorise
            from zoneinfo import ZoneInfo
            return (datetime.strptime(v, "%Y%m%dT%H%M%SZ")
                    .replace(tzinfo=ZoneInfo("UTC"))
                    .astimezone(ZoneInfo("Europe/Paris")))
        return datetime.strptime(v, "%Y%m%dT%H%M%S")
    except ValueError:
        return None


def _division(e: dict) -> Optional[str]:
    """Le nom de la compétition, lu dans « <COMPÉTITION> - Journée 3 »."""
    brut = (e.get("DESCRIPTION") or "").split(" - Journée")[0].strip()
    if not brut:
        return None
    n = _norm(brut)
    for marqueur, nom in DIVISIONS:
        if n.startswith(marqueur):
            # « NATIONALE 3 FEMININE AURA » garde son AURA.
            reste = brut[len(marqueur):].strip()
            reste = re.sub(r"\b20\d\d-20\d\d\b", "", reste).strip()
            return (nom + " " + reste).strip() if reste else nom
    return brut.capitalize()


def _calendrier(competition: str, club: str) -> List[dict]:
    r = base_get(ICS % (competition, club), headers=HEADERS, timeout=30,
                 etiquette="[HAND]")
    r.raise_for_status()
    return _evenements(r.content.decode("utf-8", errors="replace"))


def fetch() -> List[Event]:
    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    events: List[Event] = []
    inconnues: List[str] = []
    programmes = 0

    for competition, club, nom_federal, affiche in EQUIPES:
        try:
            evs = _calendrier(competition, club)
        except (requests.RequestException, ValueError) as exc:
            print("[HAND] %s : %s" % (affiche, exc), file=sys.stderr)
            continue

        if not evs:
            _alerte("le calendrier de %s est vide : identifiants de "
                    "compétition périmés ? (c-%s/s-%s)"
                    % (affiche, competition, club))
            continue

        debuts = [d for d in (_debut(e) for e in evs) if d]
        if debuts and max(debuts).date() < today - timedelta(days=PERIME_JOURS):
            _alerte("le dernier match du calendrier de %s date du %s : la "
                    "saison a tourné, il faut relire l'identifiant de "
                    "compétition (voir scrapers/handball.py)"
                    % (affiche, max(debuts).date().isoformat()))
            continue

        for e in evs:
            duel = DUEL.match(e.get("SUMMARY") or "")
            if not duel:
                continue
            recoit, visiteur = duel.group(1).strip(), duel.group(2).strip()
            if _norm(recoit) != _norm(nom_federal):
                continue
            programmes += 1

            d = _debut(e)
            if not d or not (today <= d.date() <= horizon):
                continue

            # « MATTHIAS FAVIER, 2 PLACE SCHONBERG 69009, LYON 9e » : le
            # nom de la salle tient avant la première virgule.
            brut = (e.get("LOCATION") or "").split(",")[0].strip()
            salle = SALLES.get(_norm(brut))
            if not salle:
                inconnues.append(brut or "(vide)")
                continue

            division = _division(e)
            if not division:
                continue

            events.append(Event(
                venue=salle,
                venue_slug=SLUGS[salle],
                title="%s - %s" % (affiche, _joli(visiteur)),
                subtitle=division,
                category="handball",
                date_start=iso(d.date()),
                date_end=None,
                time=d.strftime("%H:%M"),
                url=e.get("URL") or None,
                image=None,
            ))

    if inconnues:
        print("[HAND] salle inconnue, match(s) écarté(s) : %s"
              % ", ".join(sorted(set(inconnues))), file=sys.stderr)
    events.sort(key=lambda x: (x.date_start, x.time))
    print("[HAND] %d match(s) à domicile programmé(s), %d retenu(s) sous "
          "%d jours" % (programmes, len(events), HORIZON_DAYS),
          file=sys.stderr)
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time, "·", e.venue, "·", e.title,
              "·", e.subtitle)
