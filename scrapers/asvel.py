"""Les matchs À DOMICILE du LDLC ASVEL : 19 en direct, 15 relevés.

DEUX SOURCES, PARCE QU'UNE SEULE NE PASSE PAS.

  EuroLeague   api-live.euroleague.net, en direct, 19 matchs à domicile
  Betclic      asvel_betclic.json, relevé à la main, 15 matchs

Les deux ensembles sont disjoints par construction — une rencontre
appartient à une compétition et à une seule — et leur somme fait les 34
matchs à domicile que le site du club annonce.

POURQUOI LA BETCLIC EST FIGÉE. Les trois chemins qui la portent en ligne
refusent le runner GitHub par un 403 : ldlcasvel.com, le site du club ;
lnb.fr, celui de la ligue ; et api-prod.lnb.fr, son API, pourtant ouverte
à qui la joint depuis une adresse résidentielle. C'est un blocage
d'adresses de centre de données, et les en-têtes complètes d'un navigateur
n'y changent rien : le filtrage porte sur l'empreinte TLS ou sur le numéro
d'AS. Imiter un navigateur jusqu'à cette couche aurait été contourner une
protection délibérée, pas du scraping.
  Alors on lit le calendrier du club UNE FOIS, à la main, depuis Lyon, et
  on écrit ce qu'on a lu dans asvel_betclic.json. Le fil le publie ensuite
  tous les jours sans rien demander à personne. Le relevé se refait avec
  « python -m scrapers.asvel_releve » — voir ce module pour le détail.

LA SALLE, MAINTENANT DONNÉE. L'ASVEL reçoit à l'Astroballe (Villeurbanne)
et à la LDLC Arena (Décines). L'EuroLeague la donne par match. La LNB, qui
a bien un champ venue_name, le laisse VIDE sur les 242 rencontres de la
saison : tant qu'elle servait de source, les matchs de Betclic étaient
placés à l'Astroballe par soustraction — 20 matchs annoncés à l'Astroballe
et 14 à la LDLC Arena, dont l'EuroLeague revendiquait 5 et 14, restaient
15 et 0. Le relevé lit la salle sur la page du club, match par match, et
confirme la soustraction. La faiblesse est levée : un match délocalisé à
la LDLC Arena se verra au prochain relevé.

L'HEURE. L'EuroLeague donne localDate, déjà à l'heure de Paris. Le relevé
porte l'heure telle que le club l'affiche, elle aussi locale. Reste _lnb(),
que le relevé appelle pour se comparer : la LNB donne match_time_utc, qu'il
FAUT convertir — 15:30 UTC le 25 octobre 2026 vaut 16:30 à Lyon, et 17:00
UTC le 11 octobre vaut 19:00, le changement d'heure tombant entre les deux.
zoneinfo s'en charge.

LE LIEN DE LA CARTE. Les cartes ouvrent le calendrier du club, qui refuse
le runner mais répond très bien au navigateur du lecteur. Quand le relevé
a trouvé une billetterie pour un match — les ventes n'ouvrent qu'à
l'approche de la rencontre —, la carte mène directement à elle.

LA PÉREMPTION, SURVEILLÉE. Un calendrier figé vieillit : des dates bougent
en cours de saison, et au 1er septembre suivant le fichier entier est
caduc. Deux garde-fous, tous deux visibles en tête du run GitHub :
saison dépassée, on ne publie AUCUN match de Betclic plutôt que ceux de
l'an dernier ; relevé vieux de plus de 120 jours, on publie mais on
réclame une relecture.
"""
from typing import List, Optional
from datetime import date as Date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os
import sys

import requests

from .base import Event, iso, get as base_get

CLUB = "LDLC ASVEL"
HORIZON_DAYS = 180
PARIS = ZoneInfo("Europe/Paris")

# Le lien des cartes. Le site du club nous refuse, mais il répond très bien
# au navigateur du lecteur — c'est la page qu'il veut voir.
LIEN = "https://ldlcasvel.com/calendrier/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# Les salles où l'ASVEL reçoit, et l'orthographe qu'on leur donne.
# L'EuroLeague les écrit en capitales. La liste est FERMÉE : une finale
# délocalisée à Paris serait un match « à domicile » qu'un agenda lyonnais
# n'a pas à publier, et une salle inconnue est écartée avec un mot sur
# stderr plutôt que publiée en silence.
SALLES = {
    "astroballe": "Astroballe",
    "ldlc arena": "LDLC Arena",
}
SLUGS = {"Astroballe": "astroballe", "LDLC Arena": "ldlc-arena"}

# Faute de salle par match, la LNB place la Betclic à l'Astroballe — voir
# le chapeau du module pour la soustraction qui l'établit. Ne sert plus
# qu'à _lnb(), que seul le relevé appelle : le fichier figé, lui, porte la
# salle de chaque match.
SALLE_BETCLIC = "Astroballe"

# Le calendrier de Betclic, relevé à la main : voir scrapers/asvel_releve.py.
BETCLIC_FIGE = Path(__file__).parent.parent / "asvel_betclic.json"

# Au-delà, on réclame une relecture. Une saison court de septembre à mai :
# quatre mois laissent le temps aux reprogrammations de s'accumuler sans
# qu'on aboie tous les quinze jours.
RELEVE_PERIME_JOURS = 120

EUROLEAGUE = ("https://api-live.euroleague.net/v2/competitions/E"
              "/seasons/E%d/games?teamCode=ASV")
# lnb.fr, le site, rend un 403 nginx au runner GitHub — un blocage
# d'adresses de centre de données, comme celui du club. Sa page de
# calendrier demande d'abord un jeton à lnb.fr/api/token, et la première
# version passait par là : elle échouait donc au premier appel.
# Or api-prod.lnb.fr, qui est un AUTRE hôte, répond sans jeton. On ne
# demande plus rien à lnb.fr.
LNB_COMPET = ("https://api-prod.lnb.fr/competition/getDivisionCompetitionByYear"
              "?year=%d&division_external_id=1")
LNB_CALENDRIER = "https://api-prod.lnb.fr/match/v3/getCalendar"

# Le nom de l'ASVEL chez la LNB, qui nomme les clubs par leur VILLE.
LNB_CLUB = "Lyon-Villeurbanne"

# Le mois où la saison bascule, comme pour l'Opéra : une saison de basket
# court de septembre à juin, et « E2026 » comme « year=2026 » désignent
# tous deux la saison 2026-2027.
MOIS_BASCULE = 8


def _saison(aujourdhui: Optional[Date] = None) -> int:
    j = aujourdhui or Date.today()
    return j.year if j.month >= MOIS_BASCULE else j.year - 1


def _salle(nom: str) -> Optional[str]:
    return SALLES.get((nom or "").strip().lower())


def _euroleague(saison: int, inconnues: list) -> List[dict]:
    """Matchs à domicile de l'EuroLeague : date, heure et SALLE."""
    try:
        r = base_get(EUROLEAGUE % saison, headers=HEADERS, timeout=30,
                     etiquette="[ASVEL]")
        r.raise_for_status()
        parties = r.json().get("data") or []
    except (requests.RequestException, ValueError) as exc:
        print("[ASVEL] EuroLeague : %s" % exc, file=sys.stderr)
        return []

    out = []
    for p in parties:
        if ((p.get("local") or {}).get("club") or {}).get("code") != "ASV":
            continue
        debut = str(p.get("localDate") or "")
        if len(debut) < 16:
            continue
        brut = (p.get("venue") or {}).get("name") or ""
        salle = _salle(brut)
        if not salle:
            inconnues.append(brut or "(vide)")
            continue
        out.append({
            "jour": debut[:10],
            "heure": debut[11:16],
            "salle": salle,
            "adversaire": (((p.get("road") or {}).get("club") or {})
                           .get("name") or "").strip(),
            "competition": "EuroLeague",
        })
    return out


def _alerte(message: str) -> None:
    """Un avertissement qui se voit jusque dans l'en-tête du run."""
    print("[ASVEL] " + message, file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("::warning title=ASVEL::%s" % message.replace("\n", "%0A"))


def _betclic(saison: int) -> List[dict]:
    """Les matchs de Betclic ÉLITE, tels qu'on les a relevés à la main."""
    try:
        d = json.loads(BETCLIC_FIGE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _alerte("relevé Betclic illisible (%s) : aucun match de Betclic au "
                "fil, il faut relancer python -m scrapers.asvel_releve"
                % exc)
        return []

    if d.get("saison") != saison:
        _alerte("le relevé Betclic porte la saison %s-%s, on est en %d-%d : "
                "aucun match de Betclic au fil tant qu'il n'est pas refait "
                "(python -m scrapers.asvel_releve, depuis une connexion "
                "française)"
                % (d.get("saison"), (d.get("saison") or 0) + 1, saison,
                   saison + 1))
        return []

    age = None
    try:
        age = (Date.today() - Date.fromisoformat(d["releve_le"])).days
    except (KeyError, TypeError, ValueError):
        pass
    if age is not None and age > RELEVE_PERIME_JOURS:
        _alerte("le relevé Betclic date de %d jours (%s) : des dates ont pu "
                "bouger depuis, une relecture serait bonne"
                % (age, d.get("releve_le")))

    out = []
    for m in d.get("matchs") or []:
        salle = m.get("salle")
        if salle not in SLUGS:
            _alerte("relevé Betclic : salle inconnue %r le %s, match écarté"
                    % (salle, m.get("jour")))
            continue
        out.append({
            "jour": m["jour"],
            "heure": m["heure"],
            "salle": salle,
            "adversaire": (m.get("adversaire") or "").strip(),
            "competition": "Betclic ÉLITE",
            "billet": m.get("billet"),
        })
    return out


def _json_ou_bruit(r, etape: str) -> dict:
    """Le JSON d'une réponse, ou une erreur qui DIT ce qu'on a reçu.

    Sans ça, une page d'erreur HTML servie à la place du JSON ne laisse
    qu'un « Expecting value: line 1 column 1 » qui n'apprend rien. Or
    c'est exactement ce que le runner a obtenu le 2026-09-19 quand la
    même requête passait depuis une adresse résidentielle : sans le corps
    de la réponse, impossible de savoir qui refuse, ni pourquoi.
    """
    try:
        return r.json()
    except ValueError:
        corps = (r.text or "")[:220].replace("\n", " ")
        raise ValueError(
            "%s : réponse non-JSON, statut %d, type %r, corps %r"
            % (etape, r.status_code, r.headers.get("content-type"), corps))


def _lnb(saison: int) -> List[dict]:
    """Matchs à domicile de Betclic ÉLITE vus par la LIGUE : date et heure.

    Le fil ne passe plus par ici — api-prod.lnb.fr rend 403 au runner. Seul
    scrapers/asvel_releve.py appelle cette fonction, pour comparer ce que
    dit la ligue à ce qu'affiche le club et consigner les écarts.
    """
    entetes = dict(HEADERS)
    entetes["Content-Type"] = "application/json"
    entetes["Origin"] = "https://lnb.fr"
    entetes["Referer"] = "https://lnb.fr/"
    try:
        # L'identifiant de compétition change à chaque saison : on le
        # DEMANDE au lieu de l'écrire en dur, faute de quoi le scraper
        # rendrait zéro le jour où la saison tourne.
        compet = _json_ou_bruit(
            base_get(LNB_COMPET % saison, headers=entetes, timeout=25,
                     etiquette="[ASVEL]"), "compétition").get("data") or []
        if not compet:
            print("[ASVEL] LNB : aucune compétition pour la saison %d"
                  % saison, file=sys.stderr)
            return []
        cid = compet[0]["external_id"]

        r = requests.post(LNB_CALENDRIER, headers=entetes, timeout=30, json={
            "competition_external_id": cid,
            "division_external_id": 1,
            "year": saison,
            # Sans limite explicite l'API ne rend qu'une fenêtre de neuf
            # journées autour d'aujourd'hui, soit 22 matchs sur 242.
            "limit": 500,
        })
        r.raise_for_status()
        journees = _json_ou_bruit(r, "calendrier").get("data") or []
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        print("[ASVEL] LNB : %s" % exc, file=sys.stderr)
        return []

    out = []
    for journee in journees:
        for m in journee.get("data") or []:
            equipes = m.get("teams") or []
            # La première équipe est celle qui reçoit.
            if len(equipes) < 2 or equipes[0].get("team_name") != LNB_CLUB:
                continue
            brut = str(m.get("match_time_utc") or "")
            if not brut:
                continue
            try:
                t = datetime.fromisoformat(
                    brut.replace("Z", "+00:00")).astimezone(PARIS)
            except ValueError:
                continue
            out.append({
                "jour": t.strftime("%Y-%m-%d"),
                "heure": t.strftime("%H:%M"),
                "salle": SALLE_BETCLIC,
                "adversaire": (equipes[1].get("team_name") or "").strip(),
                "competition": "Betclic ÉLITE",
            })
    return out


def fetch() -> List[Event]:
    saison = _saison()
    inconnues: List[str] = []
    euro = _euroleague(saison, inconnues)
    betclic = _betclic(saison)

    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    today_iso, horizon_iso = iso(today), iso(horizon)

    events: List[Event] = []
    vus = set()
    for m in sorted(euro + betclic, key=lambda x: (x["jour"], x["heure"])):
        if not (today_iso <= m["jour"] <= horizon_iso):
            continue
        if not m["adversaire"]:
            continue
        # Deux compétitions ne peuvent pas programmer la même rencontre,
        # mais le garde-fou ne coûte rien.
        cle = (m["jour"], m["heure"], m["adversaire"])
        if cle in vus:
            continue
        vus.add(cle)
        events.append(Event(
            venue=m["salle"],
            venue_slug=SLUGS[m["salle"]],
            title="%s - %s" % (CLUB, m["adversaire"]),
            subtitle=m["competition"],
            category="basket",
            date_start=m["jour"],
            date_end=None,
            time=m["heure"],
            url=m.get("billet") or LIEN,
            image=None,
        ))

    if inconnues:
        print("[ASVEL] salle inconnue, match(s) écarté(s) : %s"
              % ", ".join(sorted(set(inconnues))), file=sys.stderr)
    print("[ASVEL] saison %d-%d : %d match(s) EuroLeague (en direct) + %d "
          "Betclic (relevés), %d retenu(s) sous %d jours"
          % (saison, saison + 1, len(euro), len(betclic), len(events),
             HORIZON_DAYS), file=sys.stderr)
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time, "·", e.venue, "·", e.title,
              "·", e.subtitle)
