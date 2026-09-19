"""Scraper for the Comédie Odéon (2e, 6 rue Grôlée).

WordPress dont le type « spectacle » n'est PAS exposé à l'API REST : la
lecture se fait en HTML, sur /spectacle/ — une seule requête, qui porte
tout ce qu'il faut.

  article[data-category]   une carte par spectacle : titre, affiche,
                           lien, résumé de dates et genre
  .tab-pane[id]            un onglet de calendrier par mois, l'id étant
                           le timestamp Unix du 1er
  a.TMdate                 une cellule par jour, son data-content nommant
                           les spectacles qui s'y jouent

C'EST LE CALENDRIER QUI FAIT FOI pour les dates, et c'est le choix
central de ce scraper. La fiche d'un spectacle décrit son rythme en
français — « Du mercredi au samedi à 20h », « Relâches : 15/10 + 16/10 »
— et régénérer des dates depuis une telle règle serait fragile. Le
calendrier, lui, contient les jours que le théâtre a lui-même calculés.

Sans lui on n'aurait que la plage : le Petit Bulletin publiait « La
Machine de Turing » du 2 septembre au 30 octobre, soit une carte par
jour pendant 53 jours, relâches comprises. C'est précisément la fausse
continuité que ce scraper corrige.

L'HEURE vient du résumé de la carte — « à 21h », « Les sam. 17h » — qui
la donne pour 17 des 19 spectacles. Pour les autres seulement, on ouvre
la fiche et l'on y lit l'horaire par défaut, ainsi que les exceptions
datées de la forme « Le 09/10 à 19h », qui sans cela publieraient une
heure fausse ce jour-là.

Le calendrier ne couvre que six mois glissants, soit un peu moins que
l'horizon de 180 jours. Les derniers jours de l'horizon manquent donc
parfois ; le Petit Bulletin les couvre encore.
"""
from __future__ import annotations

import calendar as _cal
import datetime as _dt
import html as _html
import re
import sys
from datetime import date as Date, timedelta
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from . import detail_cache
from .base import Event, get as base_get

VENUE = "Comédie Odéon"
SLUG = "comedie-odeon"
BASE = "https://www.comedieodeon.com"
LISTING = BASE + "/spectacle/"

HORIZON_DAYS = 180

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
                  "+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# Genres du site → étiquettes que TYPE_BUCKETS (index.html) reconnaît.
# La traduction est nécessaire, pas décorative : « comédie dramatique »
# et « seul en scène » ne correspondent à aucun motif et tomberaient dans
# « autre », donc dans la famille « autres » — alors que ce sont de la
# scène. Chaque étiquette ci-dessous a été vérifiée contre le bucket réel.
GENRES = {
    "one-man-show": "one-man-show",         # → humour
    "seul-en-scene": "one-man-show",        # → humour
    "talk-show-queer": "humour",
    "comedie": "théâtre",
    "comedie-dramatique": "théâtre",
    "spectacle-musical": "spectacle musical",   # → théâtre
    "concert": "concert",                   # → musique
    "conference-spectacle": "conférence",
}
# Jetons de data-category qui ne sont pas des genres : état d'affiche,
# saison, numéros de mois, tranche de public.
NON_GENRES = ("coursvenir", "weekcurrent", "jeune-public", "avignon",
              "creation", "ovni")

_HEURE = re.compile(r"\b(\d{1,2})\s*h\s*(\d{2})?\b")
_EXCEPTION = re.compile(r"\ble\s+(\d{1,2})/(\d{1,2})\s*[àa]\s*(\d{1,2})\s*h\s*(\d{2})?",
                        re.I)
_TITRE_POP = re.compile(
    r'class="titlePop">(.*?)</span>.*?href="([^"]*/spectacle/[^"]+)"', re.S)


def _heure(txt: str) -> Optional[str]:
    m = _HEURE.search(txt or "")
    return f"{int(m.group(1)):02d}:{m.group(2) or '00'}" if m else None


def _lire_fiche(url: str) -> Optional[dict]:
    """Horaire par défaut d'un spectacle et ses exceptions datées."""
    try:
        r = base_get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"[Comédie Odéon] {url}: {exc}", file=sys.stderr)
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    bloc = soup.select_one(".date_spect")
    txt = bloc.get_text(" ", strip=True) if bloc else ""
    # Les exceptions sont retirées AVANT de chercher l'horaire par défaut,
    # sinon « Le 09/10 à 19h » pourrait le fournir.
    exceptions = {f"{int(m.group(2)):02d}-{int(m.group(1)):02d}":
                  f"{int(m.group(3)):02d}:{m.group(4) or '00'}"
                  for m in _EXCEPTION.finditer(txt)}
    return {"defaut": _heure(_EXCEPTION.sub(" ", txt)), "exceptions": exceptions}


def _calendrier(soup: BeautifulSoup) -> List[Tuple[str, str]]:
    """(date ISO, lien du spectacle) pour chaque jour joué.

    Les onglets portent des cellules de DÉBORDEMENT en fin de mois — la
    grille se termine sur les premiers jours du mois suivant. Le
    quantième redescend alors, ce qui signale la bascule.
    """
    out: List[Tuple[str, str]] = []
    for pane in soup.select(".tab-pane[id]"):
        cellules = pane.select("a.TMdate")
        if not cellules:
            continue
        try:
            premier = _dt.datetime.fromtimestamp(
                int(pane["id"]), _dt.timezone.utc).date().replace(day=1)
        except (ValueError, KeyError, OverflowError, OSError):
            continue
        an, mois, precedent = premier.year, premier.month, 0
        for c in cellules:
            txt = c.get_text(strip=True)
            if not txt.isdigit():
                continue
            jj = int(txt)
            if jj < precedent:               # débordement sur le mois suivant
                mois += 1
                if mois > 12:
                    mois, an = 1, an + 1
            precedent = jj
            if jj > _cal.monthrange(an, mois)[1]:
                continue
            jour = Date(an, mois, jj).isoformat()
            for _titre, lien in _TITRE_POP.findall(
                    _html.unescape(c.get("data-content") or "")):
                out.append((jour, lien))
    return out


def fetch() -> List[Event]:
    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)

    session = requests.Session()
    r = session.get(LISTING, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    spectacles: Dict[str, dict] = {}
    for art in soup.select("article[data-category]"):
        a = art.select_one('a[href*="/spectacle/"]')
        h2 = art.select_one("h2")
        if not (a and a.get("href") and h2):
            continue
        cap = art.select_one(".dateCaption")
        resume = cap.get_text(" ", strip=True).replace("Voir plus", "") if cap else ""
        img = art.select_one("img.wp-post-image")
        genre = next((GENRES[j] for j in (art.get("data-category") or "").split()
                      if j.lower() in GENRES), None)
        spectacles[a["href"]] = {
            "titre": _html.unescape(h2.get_text(" ", strip=True)),
            "image": img.get("src") if img else None,
            "heure": _heure(resume),
            "genre": genre,
        }

    jours = _calendrier(soup)
    if not spectacles or not jours:
        # Page lisible mais vide : la structure a changé. On le signale
        # plutôt que de rendre une liste vide qu'aggregate.py ne
        # distinguerait pas d'une panne.
        print(f"[Comédie Odéon] {len(spectacles)} spectacle(s) et "
              f"{len(jours)} date(s) lus sur {LISTING} — structure modifiée ?",
              file=sys.stderr)
        return []

    # Une fiche n'est ouverte que pour les spectacles dont le résumé ne
    # donne pas d'heure : deux sur dix-neuf à l'écriture.
    horaires: Dict[str, dict] = {}
    for lien, sp in spectacles.items():
        if sp["heure"] is None:
            horaires[lien] = detail_cache.get_details(
                lien, _lire_fiche, fields=("defaut", "exceptions")) or {}

    events: List[Event] = []
    inconnus = set()
    for jour, lien in jours:
        sp = spectacles.get(lien)
        if sp is None:
            inconnus.add(lien)
            continue
        if not (today.isoformat() <= jour <= horizon.isoformat()):
            continue
        heure = sp["heure"]
        if heure is None:
            h = horaires.get(lien) or {}
            heure = (h.get("exceptions") or {}).get(jour[5:]) or h.get("defaut")
        events.append(Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=sp["titre"],
            subtitle=None,
            category=sp["genre"],
            date_start=jour,
            date_end=None,
            time=heure,
            url=lien,
            image=sp["image"],
        ))

    if inconnus:
        # Le calendrier nomme un spectacle absent des cartes : sa série
        # est sans doute passée. Signalé, car l'inverse — des cartes sans
        # dates — indiquerait un calendrier mal lu.
        print(f"[Comédie Odéon] {len(inconnus)} spectacle(s) du calendrier "
              f"sans carte correspondante", file=sys.stderr)
    return events
