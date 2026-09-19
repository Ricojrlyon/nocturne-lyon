"""Scraper des matchs À DOMICILE du LDLC ASVEL, par les deux LIGUES.

POURQUOI PAS LE SITE DU CLUB. La première version lisait
ldlcasvel.com/calendrier, qui porte tout : les 68 matchs de la saison, à
domicile et à l'extérieur, avec salle et horaire. Elle marchait en local
et rendait 403 depuis le runner GitHub — le site est derrière Cloudflare,
qui refuse les adresses de centre de données. Les en-têtes complètes d'un
navigateur n'y ont rien changé : le filtrage porte sur l'empreinte TLS ou
sur le numéro d'AS. Imiter un navigateur jusqu'à cette couche aurait été
contourner une protection délibérée, pas du scraping.

On passe donc par les deux compétitions, qui publient chacune leurs
matchs sans rien demander :

  EuroLeague   api-live.euroleague.net, en clair, 19 matchs à domicile
  Betclic      api-prod.lnb.fr, jeton anonyme public, 15 matchs

Les deux ensembles sont disjoints par construction — une rencontre
appartient à une compétition et à une seule — et leur somme fait les 34
matchs à domicile que le site du club annonce.

LA SALLE. L'ASVEL reçoit à l'Astroballe (Villeurbanne) et à la LDLC Arena
(Décines). L'EuroLeague la donne par match. La LNB, elle, a bien un champ
venue_name mais il est VIDE sur les 242 rencontres de la saison : les
matchs de Betclic sont donc placés à l'Astroballe. Ce n'est pas une
supposition en l'air, c'est une soustraction : le club annonce 20 matchs à
l'Astroballe et 14 à la LDLC Arena ; l'EuroLeague en revendique 5 et 14 ;
il reste 15 et 0 pour la Betclic. C'est aussi l'usage — la LDLC Arena sert
aux grands soirs européens.
  Le jour où un match de Betclic sera délocalisé à la LDLC Arena, on
  l'annoncera à tort et RIEN ne le signalera : la LNB ne dit pas la salle.
  C'est la faiblesse connue de ce scraper.

L'HEURE. L'EuroLeague donne localDate, déjà à l'heure de Paris. La LNB
donne match_time_utc, qu'il FAUT convertir : 15:30 UTC le 25 octobre 2026
vaut 16:30 à Lyon, et 17:00 UTC le 11 octobre vaut 19:00 — le changement
d'heure tombe entre les deux. zoneinfo s'en charge.

UN DÉSACCORD DE SOURCES, ASSUMÉ. Comparées au calendrier du club le
2026-09-19, les 19 dates d'EuroLeague concordent au jour et à la minute.
Sur les 15 dates de Betclic, ONZE concordent et QUATRE non — la LNB les
place le samedi à 20h00, le club le dimanche :

  Bourg-en-Bresse   LNB sam. 21 nov. 20:00   club dim. 22 nov. 19:00
  Paris             LNB sam. 12 déc. 20:00   club dim. 13 déc. 19:00
  Strasbourg        LNB sam. 23 jan. 20:00   club dim. 24 jan. 16:30
  Chalon/Saône      LNB sam.  6 fév. 20:00   club dim.  7 fév. 19:00

On suit la LNB. C'est elle qui fixe le calendrier de sa compétition, elle
est cohérente avec elle-même — ses horaires de diffusion télévisée
tombent cinq minutes avant chacun de ces coups d'envoi —, elle porte bien
les horaires NON standards quand ils sont connus (19:00 le 11 octobre,
16:30 le 25), et surtout elle est la seule des deux que le runner puisse
joindre. Mais le doute est réel, et si ces quatre dates se révélaient
fausses, c'est le calendrier du club qui aurait raison.
"""
from typing import List, Optional
from datetime import date as Date, datetime, timedelta
from zoneinfo import ZoneInfo
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

# Faute de salle par match, la Betclic se joue à l'Astroballe — voir le
# chapeau du module pour la soustraction qui l'établit.
SALLE_BETCLIC = "Astroballe"

EUROLEAGUE = ("https://api-live.euroleague.net/v2/competitions/E"
              "/seasons/E%d/games?teamCode=ASV")
LNB_JETON = "https://lnb.fr/api/token"
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


def _lnb(saison: int) -> List[dict]:
    """Matchs à domicile de Betclic ÉLITE : date et heure, pas de salle.

    Le jeton est anonyme et public — la page du calendrier le demande pour
    chaque visiteur. Il vaut un quart d'heure, largement de quoi tenir un
    run.
    """
    try:
        jeton = base_get(LNB_JETON, headers=HEADERS, timeout=25,
                         etiquette="[ASVEL]").json()["token"]
        entetes = dict(HEADERS)
        entetes["Authorization"] = "Bearer " + jeton
        entetes["Content-Type"] = "application/json"
        entetes["Origin"] = "https://lnb.fr"
        entetes["Referer"] = "https://lnb.fr/"

        # L'identifiant de compétition change à chaque saison : on le
        # DEMANDE au lieu de l'écrire en dur, faute de quoi le scraper
        # rendrait zéro le jour où la saison tourne.
        compet = base_get(LNB_COMPET % saison, headers=entetes, timeout=25,
                          etiquette="[ASVEL]").json().get("data") or []
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
        journees = r.json().get("data") or []
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
    lnb = _lnb(saison)

    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    today_iso, horizon_iso = iso(today), iso(horizon)

    events: List[Event] = []
    vus = set()
    for m in sorted(euro + lnb, key=lambda x: (x["jour"], x["heure"])):
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
            url=LIEN,
            image=None,
        ))

    if inconnues:
        print("[ASVEL] salle inconnue, match(s) écarté(s) : %s"
              % ", ".join(sorted(set(inconnues))), file=sys.stderr)
    print("[ASVEL] saison %d-%d : %d match(s) EuroLeague + %d Betclic à "
          "domicile, %d retenu(s) sous %d jours"
          % (saison, saison + 1, len(euro), len(lnb), len(events),
             HORIZON_DAYS), file=sys.stderr)
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time, "·", e.venue, "·", e.title,
              "·", e.subtitle)
