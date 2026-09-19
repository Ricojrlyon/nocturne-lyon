"""Scraper des matchs À DOMICILE du LDLC ASVEL (ldlcasvel.com/calendrier).

Le calendrier tient toute la saison sur une page, matchs à domicile et
matchs à l'extérieur mêlés. Un match tient sur une ligne, toujours de la
même forme :

    sam. 19 Sep. 2026  16:00  Roland-Garros  Roanne VS LDLC ASVEL
    jeu. 24 Sep. 2026  20:45  LDLC Arena     LDLC ASVEL VS Maccabi Tel-Aviv

À DOMICILE OU NON, C'EST LA LIGNE QUI LE DIT, et sans ambiguïté : le club
qui reçoit est écrit À GAUCHE du « VS », précédé du nom de sa salle. On ne
garde donc que les lignes dont la gauche finit par « LDLC ASVEL », et le
préfixe qui reste EST la salle. Mesuré sur la saison 2026-2027 : 68 lignes,
34 à domicile, 34 à l'extérieur, et les 34 lignes extérieures portent
toutes exactement « LDLC ASVEL » à droite — aucun cas limite.

DEUX SALLES, PAS UNE. L'ASVEL reçoit à l'Astroballe (Villeurbanne, 20
matchs) et à la LDLC Arena (Décines, 14). Le `venue` porte donc la SALLE
et non le club : c'est le modèle du site — une carte dit où l'on va —, et
c'est ce qui donne le bon arrondissement. La LDLC Arena y est déjà connue,
elle accueille aussi des concerts que les agrégateurs remontent.

LA PAGE EST UN ELEMENTOR, et ses classes portent des identifiants de build
qui changent à chaque édition. On ne sélectionne donc RIEN par classe : on
cherche le plus petit élément dont le texte a la forme d'une ligne de
match. C'est la donnée qui sert de repère, pas le balisage.

Une API WordPress existe — /wp-json/wp/v2/match, avec même une taxonomie
« match-a-domicile » — mais elle n'expose ni la date du match, ni l'heure,
ni la salle : ses champs JetEngine restent côté serveur. Elle ne remplace
donc pas la lecture de la page.
"""
from typing import List, Optional
from datetime import date as Date, timedelta
import re
import sys
import unicodedata

import requests
from bs4 import BeautifulSoup

from .base import Event, iso, get as base_get

CLUB = "LDLC ASVEL"
HOST = "https://ldlcasvel.com"
URL = HOST + "/calendrier/"
HORIZON_DAYS = 180

# Le site est derrière Cloudflare, et Cloudflare a rendu 403 au runner
# GitHub le 2026-09-19 alors que la même requête passe depuis une adresse
# résidentielle. Les en-têtes sont donc celles COMPLÈTES d'un navigateur :
# une requête qui annonce un Chrome sans envoyer ni Accept, ni Sec-Fetch-*,
# ni Upgrade-Insecure-Requests se reconnaît au premier coup d'œil.
#
# Cela peut ne pas suffire : si le filtrage porte sur l'empreinte TLS ou
# sur le numéro d'AS, aucune en-tête n'y changera rien. On le saura au
# prochain run, c'est la seule façon de le savoir — la requête passe
# d'ici, 403 ou non se décide à l'autre bout.
#
# Pas de « br » dans Accept-Encoding : brotli n'est pas installé (voir
# requirements.txt), et l'annoncer ferait rendre un corps que requests ne
# saurait pas décoder.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-CH-UA": '"Chromium";v="124", "Google Chrome";v="124", '
                 '"Not-A.Brand";v="99"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
}

# Les salles où l'ASVEL reçoit, avec l'orthographe que le site leur donne.
# La liste est FERMÉE, et c'est voulu : une finale délocalisée à Paris
# serait un match « à domicile » que nous n'avons aucune raison de publier
# dans un agenda lyonnais. Une salle inconnue est écartée avec un mot sur
# stderr plutôt que publiée en silence.
SALLES = {
    "astroballe": "Astroballe",
    "ldlc arena": "LDLC Arena",
}

SLUGS = {
    "Astroballe": "astroballe",
    "LDLC Arena": "ldlc-arena",
}

# Le mois est abrégé sur trois ou quatre lettres — « Sep. », « Déc. »,
# « Juil. » — là où base.FR_MONTHS attend « sept », « dec », « juil ». On
# garde donc notre propre table, interrogée sur le préfixe : « mars »
# retombe sur « mar », et « juin » et « juil » restent distincts parce
# qu'on essaie quatre lettres avant trois.
MOIS = {
    "jan": 1, "fev": 2, "mar": 3, "avr": 4, "mai": 5, "juin": 6,
    "juil": 7, "aou": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# « sam. 19 Sep. 2026 16:00 <gauche> VS <droite> »
LIGNE = re.compile(
    r"^(?:lun|mar|mer|jeu|ven|sam|dim)\.\s+"
    r"(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\.?\s+(\d{4})\s+"
    r"(\d{1,2}):(\d{2})\s+"
    r"(.*?)\s+VS\s+(.*?)$",
    re.IGNORECASE,
)

# Ce que le site accroche derrière le nom de l'adversaire, selon que les
# places sont en vente ou non.
BILLETTERIE = re.compile(r"\s*(?:Billetterie|Bient[oô]t en vente)\s*$",
                         re.IGNORECASE)

# La compétition ne s'écrit nulle part en toutes lettres : elle se lit sur
# le logo posé en tête de ligne. Le nom du fichier suffit.
COMPETITIONS = (
    ("euroleague", "EuroLeague"),
    ("betclic", "Betclic ELITE"),
    ("eurocup", "EuroCup"),
    ("coupe-de-france", "Coupe de France"),
    ("leaders-cup", "Leaders Cup"),
)


def _norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower())
                   if unicodedata.category(c) != "Mn").strip()


def _mois(mot: str) -> Optional[int]:
    m = _norm(mot).rstrip(".")
    for n in (len(m), 4, 3):
        if m[:n] in MOIS:
            return MOIS[m[:n]]
    return None


def _competition(bloc) -> Optional[str]:
    """Nom de la compétition, lu sur le logo posé en tête de ligne."""
    for img in bloc.find_all("img"):
        src = _norm(img.get("src") or img.get("data-src") or "")
        for marqueur, nom in COMPETITIONS:
            if marqueur in src:
                return nom
    return None


def _billetterie(bloc) -> Optional[str]:
    for a in bloc.find_all("a", href=True):
        href = a["href"]
        if href.startswith("http") and "ldlcasvel.com" not in href:
            return href
    return None


def _lignes(soup: BeautifulSoup) -> List[tuple]:
    """(bloc, texte) de chaque ligne de match, sans doublon.

    Le plus PETIT élément dont le texte a la forme voulue : la page
    imbrique les conteneurs, et un ancêtre colle aussi bien que son
    enfant. Sans ce tri on ramasserait la même ligne à cinq profondeurs.
    """
    out, vus = [], set()
    for el in soup.find_all(["div", "section", "article", "li"]):
        t = el.get_text(" ", strip=True)
        if len(t) > 220 or " VS " not in t.upper():
            continue
        if not LIGNE.match(t):
            continue
        if any(LIGNE.match(d.get_text(" ", strip=True))
               for d in el.find_all(["div", "section", "article", "li"])):
            continue
        if t in vus:
            continue
        vus.add(t)
        out.append((el, t))
    return out


def fetch() -> List[Event]:
    try:
        r = base_get(URL, timeout=30, headers=HEADERS, etiquette="[ASVEL]")
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"[ASVEL] {URL}: {exc}", file=sys.stderr)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    lignes = _lignes(soup)
    if not lignes:
        print("[ASVEL] aucune ligne de match reconnue sur %s — la page a "
              "probablement changé de forme" % URL, file=sys.stderr)
        return []

    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    events: List[Event] = []
    exterieur = 0
    inconnues: List[str] = []

    for bloc, texte in lignes:
        m = LIGNE.match(texte)
        jour, mot_mois, annee, hh, mm, gauche, droite = m.groups()

        # Le club qui REÇOIT est à gauche. Tout le reste est un déplacement.
        if _norm(gauche)[-len(CLUB):] != _norm(CLUB):
            exterieur += 1
            continue

        salle_brute = gauche[:-len(CLUB)].strip()
        salle = SALLES.get(_norm(salle_brute))
        if not salle:
            inconnues.append(salle_brute or "(sans salle)")
            continue

        mois = _mois(mot_mois)
        if not mois:
            continue
        try:
            d = Date(int(annee), mois, int(jour))
        except ValueError:
            continue
        if not (today <= d <= horizon):
            continue

        adversaire = BILLETTERIE.sub("", droite).strip()
        if not adversaire:
            continue

        events.append(Event(
            venue=salle,
            venue_slug=SLUGS[salle],
            title="%s - %s" % (CLUB, adversaire),
            subtitle=_competition(bloc),
            category="basket",
            date_start=iso(d),
            date_end=None,
            time="%02d:%02d" % (int(hh), int(mm)),
            url=_billetterie(bloc) or URL,
            image=None,
        ))

    if inconnues:
        print("[ASVEL] salle inconnue, match(s) écarté(s) : %s"
              % ", ".join(sorted(set(inconnues))), file=sys.stderr)
    print("[ASVEL] %d ligne(s) lue(s), %d à l'extérieur, %d à domicile "
          "retenu(s) sous %d jours" % (len(lignes), exterieur, len(events),
                                       HORIZON_DAYS), file=sys.stderr)

    events.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time, "·", e.venue, "·", e.title,
              "·", e.subtitle or "-", "·", e.url[:60])
