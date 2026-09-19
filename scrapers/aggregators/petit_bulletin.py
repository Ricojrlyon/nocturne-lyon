"""Petit Bulletin aggregator scraper.

Fetches https://www.petit-bulletin.fr/agenda-recherche.html and parses the
list of upcoming events. The page structure is regular: each event has a
title in an h-tag with a stable URL (/agenda-NNNNNN-slug.html), a category
in parens on the next sibling line, then a list with venue and date.

UN SEUL filtre éditorial : quatre catégories d'arts plastiques (voir
CATEGORIES_ECARTEES). Pour le reste tout l'agenda remonte, et c'est la
déduplication en trois passes (scrapers/dedup.py) qui écarte les doublons
quand un événement est aussi publié par la salle elle-même.

Historique, parce que la décision a changé deux fois. Deux filtres
existaient ici — musées et galeries d'un côté, une liste de catégories de
l'autre — parce que leurs accrochages, courant sur des mois, saturaient le
feed. Ils ont été retirés en août 2026, au motif que le frontend regroupe
chaque journée en quatre familles qu'on éteint d'un bouton, et que c'est
au lecteur de dire qu'il ne veut pas d'expositions ce soir.

Le filtre de catégories revient en septembre 2026, resserré à quatre
libellés, et pour une raison différente de la première : les événements
longs sont désormais affichés sur CHACUN de leurs jours, seuil supprimé.
Un accrochage de trois mois pesait une carte, il en pèse quatre-vingt-dix.

L'agenda est paginé (`?p=N`) et fetch() suit toutes les pages.

Dates :
  * jour unique          → un Event
  * plage ≤ 7 jours      → un Event par jour (petits festivals)
  * plage > 7 jours      → UN Event à plage (date_start..date_end)
  * « Jusqu'au X »       → UN Event à plage, du jour courant à X
Rien n'est jeté : le frontend déploie les plages sur chacun de leurs
jours, avec un badge de progression.
"""
from __future__ import annotations
import re
import sys
import time
import unicodedata
from datetime import date, timedelta
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from ..base import Event, get as base_get

URL = "https://www.petit-bulletin.fr/agenda-recherche.html"
BASE = "https://www.petit-bulletin.fr"

USER_AGENT = (
    "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
    "+https://github.com/Ricojrlyon/nocturne-lyon)"
)

# Garde-fou anti-boucle, PAS une limite de lecture : fetch() s'arrête de
# lui-même dès qu'une page n'apporte plus aucune URL nouvelle.
# Il doit rester largement au-dessus de la taille réelle de l'agenda,
# sinon il tronque en silence. Fixé à 15 quand l'agenda tenait en 9 pages,
# il en comptait 21 six mois plus tard : les pages 16 à 21 étaient perdues,
# soit près d'un tiers du contenu.
MAX_PAGES = 40

# Au-delà de ce nombre de jours, une plage devient UN événement à plage
# plutôt qu'un événement par jour.
LONG_RUN_DAYS = 7

MONTHS_FR = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "aout": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "decembre": 12,
}


def _normalize(s: str) -> str:
    """Lowercase, strip accents, punctuation → space, collapse whitespace."""
    s = (s or "").lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Catégories écartées : les arts plastiques du Petit Bulletin. Ce sont des
# ACCROCHAGES, ouverts tous les jours pendant des semaines, et depuis que
# le frontend déploie les événements longs sur chacun de leurs jours, ils
# pèsent leur durée entière. Mesuré à l'introduction de la règle : 99
# événements, dont 68 en galerie et 9 en musée non scrappé, et 1 443 jours
# cumulés d'accrochage sur l'horizon.
#
# Les libellés sont comparés normalisés (casse, accents et ponctuation
# écrasés) : « Design & Architecture » et « design et architecture »
# tombent donc pareil.
CATEGORIES_ECARTEES = frozenset(_normalize(c) for c in (
    "Peinture & Dessin",
    "Art contemporain et numérique",
    "Photographie",
    "Design & Architecture",
))


# Le Petit Bulletin sert son HTML avec les apostrophes ÉCHAPPÉES — la
# signature d'un addslashes() PHP appliqué à la sortie plutôt qu'à l'entrée
# d'une requête. Le texte contient donc littéralement « Théâtre de l\'Élysée »,
# antislash compris, et get_text() le rend tel quel. Vu le 2026-09-14 sur
# quatre lieux et neuf événements ; aucun titre touché ce jour-là, mais rien
# ne l'en protège, d'où l'application aux deux champs.
#
# Conséquence si on ne corrige pas : l'antislash s'affiche sur la carte, et
# surtout le lieu devient une salle À PART — le frontend indexe l'arrondis-
# sement et regroupe les cartes sur la chaîne EXACTE. Le Théâtre de l'Élysée
# comptait ainsi trois entrées pour une salle.
#
# On ne retire que l'antislash qui précède une apostrophe ou un guillemet,
# seul cas produit par addslashes : un antislash isolé dans un titre — rare
# mais légitime — survit.
_ANTISLASH_APOSTROPHE = re.compile(r"\\(['’\"])")


def _desechappe(s: str) -> str:
    return _ANTISLASH_APOSTROPHE.sub(r"\1", s or "")


def _slugify(s: str) -> str:
    s = (s or "").lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def _parse_date_str(s: str) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """Parse une chaîne de date du Petit Bulletin.

    Renvoie une LISTE de triplets (date_start, heure, date_end) :
      "Mardi 26 mai 2026 à 20h"        -> [("2026-05-26", "20:00", None)]
      "Du 26 au 28 mai 2026, à 19h"    -> 3 entrées, date_end None
      "Du 1 au 30 juin 2026"           -> [("2026-06-01", None, "2026-06-30")]
      "Jusqu'au 16 août 2026, ..."     -> [(aujourd'hui, None, "2026-08-16")]
      "30 mai et 31 mai à 20h"         -> [("2026-05-30", "20:00", None)]

    Liste vide seulement si aucune date n'est lisible ou si l'événement est
    entièrement passé.
    """
    norm = _normalize(s)
    today = date.today()
    today_iso = today.isoformat()

    MOIS = (r"(janvier|fevrier|mars|avril|mai|juin|juillet|aout|"
            r"septembre|octobre|novembre|decembre)")

    # Heure : "à HHh", "à HHhMM", "de HHh à HHh"
    time_str: Optional[str] = None
    time_m = re.search(r"\b(\d{1,2})h(\d{0,2})\b", norm)
    if time_m:
        hh = int(time_m.group(1))
        mm = int(time_m.group(2)) if time_m.group(2) else 0
        if 0 <= hh < 24 and 0 <= mm < 60:
            time_str = f"{hh:02d}:{mm:02d}"

    def _year_for(month: int, day: int, explicit: Optional[str]) -> int:
        """Année explicite si présente, sinon la prochaine occurrence."""
        if explicit:
            return int(explicit)
        y = today.year
        try:
            if date(y, month, day) < today:
                y += 1
        except ValueError:
            pass
        return y

    def _expand(start: date, end: date) -> List[Tuple[str, Optional[str], Optional[str]]]:
        """Plage courte -> un événement par jour ; longue -> un seul à plage."""
        if end < start:
            return []
        if (end - start).days > LONG_RUN_DAYS:
            # Événement long (expo, festival au long cours) : un seul Event
            # à plage. Il n'est plus jeté comme avant, et le frontend sait
            # l'afficher — avec un badge « en cours » au-delà de 30 jours.
            eff = max(start, today)
            return [(eff.isoformat(), time_str, end.isoformat())]
        out = []
        d = start
        while d <= end:
            if d.isoformat() >= today_iso:
                out.append((d.isoformat(), time_str, None))
            d += timedelta(days=1)
        return out

    has_du_range = bool(re.search(
        r"\bdu\s+\d+(?:er)?\s+(?:\w+\s+)?au\s+\d+(?:er)?\s", norm))

    # 1) "Jusqu'au X" sans "Du" : événement en cours, sans début connu.
    if "jusqu" in norm and not has_du_range:
        m = re.search(r"jusqu.{0,4}au\s+(\d{1,2})(?:er)?\s+" + MOIS
                      + r"(?:\s+(\d{4}))?\b", norm)
        if not m:
            return []
        day, month = int(m.group(1)), MONTHS_FR[m.group(2)]
        try:
            end = date(_year_for(month, day, m.group(3)), month, day)
        except ValueError:
            return []
        if end < today:
            return []
        return [(today_iso, time_str, end.isoformat())]

    # 2) Plage dans le même mois : "Du X au Y mois YYYY"
    m = re.search(r"\bdu\s+(\d{1,2})(?:er)?\s+au\s+(\d{1,2})(?:er)?\s+"
                  + MOIS + r"(?:\s+(\d{4}))?", norm)
    if m:
        d1, d2, month = int(m.group(1)), int(m.group(2)), MONTHS_FR[m.group(3)]
        year = _year_for(month, d1, m.group(4))
        try:
            return _expand(date(year, month, d1), date(year, month, d2))
        except ValueError:
            return []

    # 3) Plage à cheval sur deux mois : "Du 28 mai au 3 juin 2026"
    m = re.search(r"\bdu\s+(\d{1,2})(?:er)?\s+" + MOIS
                  + r"\s+au\s+(\d{1,2})(?:er)?\s+" + MOIS
                  + r"(?:\s+(\d{4}))?", norm)
    if m:
        d1, m1 = int(m.group(1)), MONTHS_FR[m.group(2)]
        d2, m2 = int(m.group(3)), MONTHS_FR[m.group(4)]
        year_end = _year_for(m2, d2, m.group(5))
        # "du 30 decembre au 2 janvier 2027" : l'année écrite est celle de la fin.
        year_start = year_end - 1 if m1 > m2 else year_end
        try:
            return _expand(date(year_start, m1, d1), date(year_end, m2, d2))
        except ValueError:
            return []

    # 4) Date unique : premier "DD mois [YYYY]" rencontré
    m = re.search(r"\b(\d{1,2})(?:er)?\s+" + MOIS + r"(?:\s+(\d{4}))?", norm)
    if not m:
        return []
    day, month = int(m.group(1)), MONTHS_FR[m.group(2)]
    year = _year_for(month, day, m.group(3))
    try:
        d = date(year, month, day)
    except ValueError:
        return []
    if d.isoformat() < today_iso:
        return []
    return [(d.isoformat(), time_str, None)]


def _extract_events_from_soup(soup: BeautifulSoup) -> List[Event]:
    """Find every event in the parsed page and return Event objects."""
    today_iso = date.today().isoformat()
    events: List[Event] = []

    # Find every "title link" — an <a> inside an h-tag that points to an
    # /agenda-NNNNNN-slug.html URL. The same URL may appear several times
    # on the page (title, venue, date all link to it); we only want the
    # title occurrence.
    seen_urls: set[str] = set()
    title_links = soup.find_all(
        "a",
        href=re.compile(r"/agenda-\d+-[^.]+\.html")
    )

    for a in title_links:
        h_parent = a.find_parent(["h1", "h2", "h3", "h4"])
        if h_parent is None:
            continue
        # We only treat the FIRST link in the h-tag as the title link
        first_a = h_parent.find("a")
        if first_a is not a:
            continue

        href = a.get("href", "")
        if not href or href in seen_urls:
            continue
        seen_urls.add(href)

        title = _desechappe(a.get_text(strip=True))
        if not title:
            continue

        # Walk forward through siblings to find category, venue, date.
        category: Optional[str] = None
        venue: Optional[str] = None
        date_str: Optional[str] = None

        cur = h_parent
        depth = 0
        while True:
            cur = cur.find_next_sibling()
            depth += 1
            if cur is None or depth > 30:
                break
            if cur.name in ("h1", "h2", "h3", "h4"):
                break  # next event starts

            text = cur.get_text(strip=True)

            # Category line: "(Foo)" alone
            if category is None and text:
                cm = re.match(r"^\(([^)]+)\)\s*$", text)
                if cm:
                    category = cm.group(1).strip()
                    continue

            # Venue + date in a <ul>
            if venue is None and cur.name == "ul":
                lis = cur.find_all("li", recursive=False)
                if len(lis) >= 1:
                    va = lis[0].find("a")
                    venue = _desechappe((va or lis[0]).get_text(strip=True))
                if len(lis) >= 2:
                    da = lis[1].find("a")
                    date_str = (da or lis[1]).get_text(strip=True)
                # don't break — there might be more useful sibs, but
                # typically nothing else relevant follows the ul
                break

        # La catégorie est OPTIONNELLE : certains blocs ont un paragraphe de
        # description là où se trouve d'habitude la ligne « (Catégorie) ».
        # Seuls le lieu et la date sont exigés — ils suffisent à identifier un
        # vrai bloc d'événement. Sans cet assouplissement, une quinzaine
        # d'événements par passage restaient invisibles.
        if not venue or not date_str:
            continue

        date_times = _parse_date_str(date_str)
        if not date_times:
            continue

        url = href if href.startswith("http") else BASE + href

        for date_iso, time_str, date_end in date_times:
            # Un événement à plage est conservé tant qu'il n'est pas terminé.
            if (date_end or date_iso) < today_iso:
                continue
            events.append(Event(
                venue=venue,
                venue_slug=_slugify(venue),
                title=title,
                subtitle=None,
                category=category,
                date_start=date_iso,
                date_end=date_end,
                time=time_str,
                url=url,
                image=None,
            ))

    return events


def fetch() -> List[Event]:
    """Parcourt toutes les pages de l'agenda Petit Bulletin.

    L'agenda est paginé (`?p=N`, 164 événements sur 9 pages aujourd'hui) et
    seule la première page était lue : les 8 autres — soit ~85 % du contenu —
    n'arrivaient jamais dans nocturne. La boucle s'arrête dès qu'une page
    n'apporte plus aucune URL nouvelle, ou au cap de MAX_PAGES.
    """
    headers = {"User-Agent": USER_AGENT}
    events: List[Event] = []
    seen_urls: set[str] = set()
    ecartes = 0

    for page in range(1, MAX_PAGES + 1):
        url = URL if page == 1 else f"{URL}?p={page}"
        try:
            r = base_get(url, headers=headers, timeout=30)
        except requests.RequestException as exc:
            if page == 1:
                raise
            print(f"[Petit Bulletin] page {page} injoignable ({exc}) — arrêt",
                  file=sys.stderr)
            break
        if r.status_code != 200:
            if page == 1:
                r.raise_for_status()
            break

        page_events = _extract_events_from_soup(BeautifulSoup(r.text, "html.parser"))
        fresh = [e for e in page_events if e.url not in seen_urls]
        if not fresh:
            break                      # page vide ou déjà vue : fin de l'agenda
        for e in fresh:
            seen_urls.add(e.url)
        # Le filtre s'applique APRÈS le test de fraîcheur : une page qui ne
        # contiendrait que des arts plastiques serait sinon prise pour la fin
        # de l'agenda, et la pagination s'arrêterait là.
        gardes = [e for e in page_events
                  if _normalize(e.category) not in CATEGORIES_ECARTEES]
        ecartes += len(page_events) - len(gardes)
        events.extend(gardes)

        if page < MAX_PAGES:
            time.sleep(0.4)            # on ne martèle pas le serveur

    if ecartes:
        print(f"[Petit Bulletin] {ecartes} événement(s) d'arts plastiques "
              f"écarté(s) ({', '.join(sorted(CATEGORIES_ECARTEES))})")
    return events
