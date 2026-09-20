"""Les matchs À DOMICILE du LOU, hommes et femmes, au Matmut Stadium.

DEUX ÉQUIPES, UN STADE, ET DEUX SOURCES QUI N'ONT RIEN À VOIR. Le LOU est
le seul club lyonnais au sommet absolu de sa discipline des deux côtés :
Top 14 pour les hommes, Élite 1 pour les femmes. Ils reçoivent au même
endroit, 353 avenue Jean Jaurès, Lyon 7e.

LES HOMMES PAR LA BILLETTERIE. Le site du club ne date pas ses matchs —
sa page « matchs » donne la journée, l'heure et le stade, jamais le jour.
La billetterie, elle, doit bien dire quand on vient : elle liste les
treize réceptions de la saison, Top 14 ET coupe d'Europe, chacune avec
son lien d'achat.
  Mais elle ne les date qu'une fois l'horaire FIXÉ par la Ligue. Les
  autres s'affichent « Week-end des 28/29 Novembre », et on ne publie pas
  ça : une carte sans jour n'est pas une carte. Au 20 septembre 2026,
  trois réceptions sur treize ont leur jour et leur heure ; le fil
  ramassera les autres au fil des semaines, il se relit tous les matins.
  La billetterie ne dit pas non plus la COMPÉTITION, et on préfère ne
  rien écrire plutôt que de deviner : les cartes masculines n'ont pas de
  sous-titre.

LES FEMMES PAR LA FÉDÉRATION. L'Élite 1 est calée d'un bloc dès l'été :
les neuf réceptions de la saison ont leur date et leur heure. On passe
par l'URL STABLE de la FFR — competitions.ffr.fr, qui redirige vers la
saison en cours — pour en tirer l'identifiant de phase, puis on lit le
calendrier complet de la poule. Rien à mettre à jour d'une saison sur
l'autre.

LE STADE DES FÉMININES EST DÉCLARÉ, PAS LU. La fiche FFR porte bien un
terrain quand l'adversaire reçoit (« STADE MARCEL DEFLANDRE » à La
Rochelle), mais elle n'en porte AUCUN pour les réceptions du LOU. On
écrit donc le Matmut Stadium, que le club annonce comme son terrain
depuis 2021. Si une rencontre était délocalisée, on l'annoncerait à tort
et rien ne le signalerait : c'est la faiblesse connue de ce scraper, la
même que pour la Betclic à l'Astroballe.
"""
from typing import List, Optional
from datetime import date as Date, datetime, timedelta
import json
import os
import re
import sys
import unicodedata

import requests
from bs4 import BeautifulSoup

from .base import Event, iso, get as base_get

HORIZON_DAYS = 180

# Le seul stade où le LOU reçoit, hommes comme femmes.
STADE = "Matmut Stadium de Gerland"
STADE_SLUG = "matmut-stadium-gerland"

# Ce que la billetterie écrit dans sa case « lieu ». Liste FERMÉE : un
# match ailleurs est écarté avec un mot plutôt que publié sous un nom
# qu'on n'a pas vérifié.
LIEUX_BILLETTERIE = {
    "matmut stadium": STADE,
    "matmut stadium de gerland": STADE,
}

BILLETTERIE = "https://billetterie.lourugby.fr/fr"
BILLETTERIE_HOTE = "https://billetterie.lourugby.fr"

# L'URL stable de la FFR : elle redirige vers la saison en cours, et la
# page d'arrivée porte l'identifiant de phase dont on a besoin.
FFR_STABLE = ("https://competitions.ffr.fr/competitions/elite-1-feminine"
              "/calendrier.html")
FFR_CALENDRIER = ("https://monclubhouse.ffr.fr/nationales/elite-1-feminine"
                  "/qualification-%s/calendrier-resultats")
FFR_HOTE = "https://monclubhouse.ffr.fr"
# Le nom du LOU chez la fédération.
FFR_CLUB = "Lyon Ol U"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

MOIS = {"janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
        "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
        "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
        "decembre": 12}

# « Samedi 10 octobre 2026 - 16:35 ». Le jour de la semaine est facultatif,
# l'heure ne l'est pas : sans elle on ne publie pas.
DATE_BILLET = re.compile(
    r"^(?:[A-Za-zÀ-ÿ]+\s+)?(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})\s*[-–]\s*"
    r"(\d{1,2})[h:](\d{2})$")

# « LOU RUGBY - STADE ROCHELAIS », parfois avec deux espaces.
TITRE_BILLET = re.compile(r"^LOU\s+RUGBY\s*[-–]\s*(.+)$", re.I)

# Les sigles des clubs de rugby, qu'un simple title() abîmerait.
SIGLES = frozenset("RC USA HR UBB ASM CA SU FC LOU AC US CO SF MHR RCT "
                   "SUA UBB".split())
PARTICULES = frozenset("de du des la le les et en sur aux au d l".split())
_LETTRES = re.compile(r"[A-Za-zÀ-ÿ]+")

# Le texte « flight » d'une page Next.js, où la FFR range ses données.
PUSH = re.compile(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\]\)')


def _norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower())
                   if unicodedata.category(c) != "Mn").strip()


def _joli(nom: str) -> str:
    """« STADE ROCHELAIS » → « Stade Rochelais », « RC TOULON » → « RC Toulon »."""
    mots = re.split(r"(\s+)", (nom or "").strip())
    out = []
    for i, mot in enumerate(mots):
        if not mot.strip():
            out.append(" ")
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
    return re.sub(r"\s{2,}", " ", "".join(out)).strip()


def _alerte(message: str) -> None:
    """Un avertissement qui se voit jusque dans l'en-tête du run."""
    print("[RUGBY] " + message, file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("::warning title=RUGBY::%s" % message.replace("\n", "%0A"))


# ── les hommes, par la billetterie ────────────────────────────────────
def _hommes(today: Date, horizon: Date) -> List[Event]:
    r = base_get(BILLETTERIE, headers=HEADERS, timeout=30, etiquette="[RUGBY]")
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    cartes, vus = [], set()
    for li in soup.find_all("li", class_="cards-grid__item"):
        titre = li.find("h3", class_="title")
        if not titre:
            continue
        brut = titre.get_text(" ", strip=True)
        if brut in vus:          # la grille sort deux fois, desktop et mobile
            continue
        vus.add(brut)
        cartes.append((brut, li))

    if not cartes:
        _alerte("aucune carte sur la billetterie du LOU — la page a changé "
                "de forme, il faut rouvrir %s" % BILLETTERIE)
        return []

    events, sans_date = [], 0
    for brut, li in cartes:
        m = TITRE_BILLET.match(brut)
        if not m:                # l'abonnement de saison, les offres…
            continue
        adversaire = _joli(m.group(1))

        champ_date = li.find("span", class_="date")
        texte_date = champ_date.get_text(" ", strip=True) if champ_date else ""
        d = _date_billetterie(texte_date)
        if not d:
            # « Week-end des 28/29 Novembre » : la Ligue n'a pas fixé le
            # coup d'envoi, on ne publie pas un jour qu'on ignore.
            sans_date += 1
            continue
        if not (today <= d.date() <= horizon):
            continue

        champ_lieu = li.find("span", class_="venue")
        lieu = LIEUX_BILLETTERIE.get(
            _norm(champ_lieu.get_text(" ", strip=True)) if champ_lieu else "")
        if not lieu:
            print("[RUGBY] lieu inconnu (%s), match écarté : %s"
                  % (champ_lieu.get_text(" ", strip=True) if champ_lieu
                     else "vide", brut), file=sys.stderr)
            continue

        lien = li.find("a", href=True)
        url = lien["href"] if lien else None
        if url and url.startswith("/"):
            url = BILLETTERIE_HOTE + url

        events.append(Event(
            venue=lieu,
            venue_slug=STADE_SLUG,
            title="LOU Rugby - %s" % adversaire,
            subtitle=None,
            category="rugby",
            date_start=iso(d.date()),
            date_end=None,
            time=d.strftime("%H:%M"),
            url=url,
            image=None,
        ))

    print("[RUGBY] hommes : %d réception(s) en vente, %d sans jour fixé, "
          "%d retenue(s)" % (len(cartes) - 1, sans_date, len(events)),
          file=sys.stderr)
    return events


def _date_billetterie(texte: str) -> Optional[datetime]:
    m = DATE_BILLET.match((texte or "").strip())
    if not m:
        return None
    mois = MOIS.get(_norm(m.group(2)))
    if not mois:
        return None
    try:
        return datetime(int(m.group(3)), mois, int(m.group(1)),
                        int(m.group(4)), int(m.group(5)))
    except ValueError:
        return None


# ── les femmes, par la fédération ─────────────────────────────────────
def _flight(page: str) -> str:
    """Le texte que Next.js pousse morceau par morceau, recollé."""
    return "".join(json.loads('"%s"' % b) for b in PUSH.findall(page))


def _objets(texte: str, cle: str) -> List[dict]:
    """Les objets JSON du texte qui portent `cle`.

    On remonte du nom de la clé vers l'accolade qui ouvre son objet, et
    on décode. C'est plus sûr qu'une expression régulière sur des objets
    imbriqués, et ça tolère que la fédération ajoute des champs.
    """
    dec = json.JSONDecoder()
    out = []
    for m in re.finditer(re.escape('"%s"' % cle), texte):
        for i in range(m.start(), max(0, m.start() - 6000), -1):
            if texte[i] != "{":
                continue
            try:
                o, _ = dec.raw_decode(texte, i)
            except ValueError:
                continue
            if isinstance(o, dict) and cle in o:
                out.append(o)
            break
    return out


def _femmes(today: Date, horizon: Date) -> List[Event]:
    try:
        r = base_get(FFR_STABLE, headers=HEADERS, timeout=40,
                     etiquette="[RUGBY]")
        r.raise_for_status()
        phase = re.search(r"qualification-(\d+)", r.text)
        if not phase:
            _alerte("la FFR ne dit plus quelle est la phase en cours sur %s : "
                    "aucune rencontre féminine au fil" % FFR_STABLE)
            return []

        r = base_get(FFR_CALENDRIER % phase.group(1), headers=HEADERS,
                     timeout=60, etiquette="[RUGBY]")
        r.raise_for_status()
    except (requests.RequestException, ValueError) as exc:
        print("[RUGBY] FFR : %s" % exc, file=sys.stderr)
        return []

    matchs, vus = [], set()
    for o in _objets(_flight(r.text), "dateEffective"):
        if o.get("id") in vus:
            continue
        vus.add(o.get("id"))
        matchs.append(o)

    domicile = [m for m in matchs
                if ((m.get("competitionEquipeLocaleId") or {}).get("nomEdito")
                    == FFR_CLUB)]
    if not domicile:
        _alerte("aucune réception du %s dans le calendrier d'Élite 1 "
                "(%d rencontres lues) : la poule a changé, ou le nom du "
                "club" % (FFR_CLUB, len(matchs)))
        return []

    events = []
    for m in domicile:
        brut = (m.get("dateEffective") or "").replace("Z", "")
        try:
            # L'heure est déjà celle du coup d'envoi ; le Z de la
            # fédération ne désigne pas UTC, la page affiche 15:00 pour
            # « 15:00:00.000Z ».
            d = datetime.fromisoformat(brut[:19])
        except ValueError:
            continue
        if not (today <= d.date() <= horizon):
            continue
        adversaire = ((m.get("competitionEquipeVisiteuseId") or {})
                      .get("nomEdito") or "").strip()
        if not adversaire:
            continue
        url = m.get("url_monclubhouse") or ""
        events.append(Event(
            venue=STADE,
            venue_slug=STADE_SLUG,
            title="LOU Rugby féminines - %s" % adversaire,
            subtitle="Élite 1 féminine",
            category="rugby",
            date_start=iso(d.date()),
            date_end=None,
            time=d.strftime("%H:%M"),
            url=(FFR_HOTE + url) if url.startswith("/") else (url or None),
            image=None,
        ))

    print("[RUGBY] femmes : %d réception(s) à la saison, %d retenue(s) sous "
          "%d jours" % (len(domicile), len(events), HORIZON_DAYS),
          file=sys.stderr)
    return events


def fetch() -> List[Event]:
    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)

    events: List[Event] = []
    for nom, source in (("hommes", _hommes), ("femmes", _femmes)):
        try:
            events.extend(source(today, horizon))
        except (requests.RequestException, ValueError) as exc:
            print("[RUGBY] %s : %s" % (nom, exc), file=sys.stderr)

    events.sort(key=lambda e: (e.date_start, e.time or ""))
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time, "·", e.venue, "·", e.title,
              "·", e.subtitle or "-")
