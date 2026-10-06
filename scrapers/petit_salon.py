"""Scraper for Le Petit Salon (lpslyon.fr/programmation/).

Site refait le 2026-10-02 : l'ancienne page agenda
(/evenements-le-petit-salon/) répond 404. La programmation est désormais
sur /programmation/, une carte par soirée, datée « ven 02 Oct » et non
plus « 02/10 » (le JJ/MM ne reste que sur les soirées passées).

Previous version failed because _find_preceding_date walked through DOM
siblings BEFORE the h2, but on this site the date pill is nested as a
sibling INSIDE the same parent block as the h2. The fix: walk UP to the
parent block then search the whole block's text for the date.
"""
from typing import List, Optional
from datetime import date as Date
import re
import sys
from bs4 import BeautifulSoup, Tag

from .base import Event, iso, FR_MONTHS, get as base_get

VENUE = "Le Petit Salon"
SLUG = "petit-salon"
URL = "https://www.lpslyon.fr/programmation/"
HOST = "https://www.lpslyon.fr"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
                  "+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# « ven 02 Oct » : le quantième, puis le mois, abrégé comme WordPress
# l'écrit en français (« Sep », « Fév », « Juil ») ou en toutes lettres.
# Seuls des noms de mois sont reconnus : un titre comme « 10 ANS DU
# CLUB » ne doit pas passer pour une date.
_MOIS = (r"janv?(?:ier)?|f[ée]vr?(?:ier)?|mars?|avr(?:il)?|mai|juin|juil(?:let)?"
         r"|ao[ûu]t?|sept?(?:embre)?|oct(?:obre)?|nov(?:embre)?|d[ée]c(?:embre)?")
DATE_RE = re.compile(r"\b(\d{1,2})\s+(%s)\b" % _MOIS, re.IGNORECASE)


def _mois(mot: str) -> Optional[int]:
    """Numéro du mois. « Sep », « Fév », « Jan », « Mar » sont plus courts
    que les clés de FR_MONTHS (« sept », « févr »…) : on prend alors la
    clé qui commence par eux."""
    mot = mot.lower()
    return FR_MONTHS.get(mot) or next(
        (n for cle, n in FR_MONTHS.items() if cle.startswith(mot)), None)


def _smart_year(month: int, day: int) -> int:
    today = Date.today()
    try:
        candidate = Date(today.year, month, day)
    except ValueError:
        return today.year
    # Grâce de 15 jours (comme tng.py) : une date passée de quelques jours
    # est un listing pas encore purgé de CETTE année, pas l'annonce de
    # l'année prochaine — sans quoi elle devenait un événement fantôme à +1 an.
    return today.year + 1 if (today - candidate).days > 15 else today.year


def _find_event_block(h2: Tag, max_levels: int = 6) -> Optional[Tag]:
    """Walk up from h2 to find the smallest ancestor that contains a DD/MM."""
    el: Optional[Tag] = h2
    for _ in range(max_levels):
        parent = el.parent if el else None
        if parent is None or parent.name in ("html", "body"):
            break
        el = parent
        text = el.get_text(" ", strip=True)
        if DATE_RE.search(text):
            return el
    return None


def _date_for_block(block: Tag, h2: Tag) -> Optional[str]:
    """Find a DD/MM pattern in `block`, but ONLY in text BEFORE the h2.
    This avoids picking up dates from a different event further down.
    """
    # Get text up to (but not including) the h2 element
    parts: List[str] = []
    for el in block.descendants:
        if el is h2:
            break
        if isinstance(el, str):
            parts.append(el)
    text_before = " ".join(parts)
    m = DATE_RE.search(text_before)
    if m:
        return m.group(0)
    # Fallback: any DD/MM in the whole block
    m = DATE_RE.search(block.get_text(" ", strip=True))
    return m.group(0) if m else None


def fetch() -> List[Event]:
    resp = base_get(URL, timeout=20, headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    events: List[Event] = []
    seen_keys: set = set()
    h2_list = soup.find_all("h2")

    for h2 in h2_list:
        title = h2.get_text(" ", strip=True)
        if not title or len(title) < 3 or len(title) > 250:
            continue
        if title.lower() in ("nos évènements", "menu", "accès"):
            continue

        # La carte entière de la soirée (refonte du 2026-10-02) : sa date
        # précède le titre, l'affiche et le bouton « Réserver » y sont. Un
        # titre qui contient une date (« HALLOWEEN 31 OCT ») ne trompe ni
        # sur le jour ni sur le lien. Sans carte, le plus petit bloc daté.
        block = h2.find_parent("article", class_="card-event") or _find_event_block(h2)
        if block is None:
            continue
        date_str = _date_for_block(block, h2)
        if not date_str:
            continue

        m = DATE_RE.match(date_str)
        if not m:
            continue
        day, month = int(m.group(1)), _mois(m.group(2))
        if not (month and 1 <= day <= 31):
            continue
        try:
            year = _smart_year(month, day)
            d = Date(year, month, day)
        except ValueError:
            continue

        # Image
        image: Optional[str] = None
        img = block.find("img")
        if img and img.get("src", "").startswith("http"):
            image = img["src"]

        # URL: prefer the "Réserver" link inside the block
        href = URL
        link_el = None
        for a in block.find_all("a", href=True):
            if ("yp.events" in a["href"] or "shotgun.live" in a["href"]
                    or "billetterie" in a["href"].lower()):
                link_el = a
                break
        if link_el is None:
            link_el = block.find("a", href=True)
        if link_el:
            cand = link_el["href"]
            if cand.startswith("http"):
                href = cand
            elif cand.startswith("/"):
                href = HOST + cand

        key = (title, iso(d))
        if key in seen_keys:
            continue
        seen_keys.add(key)

        events.append(Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=title,
            subtitle=None,
            category="club",
            date_start=iso(d),
            date_end=None,
            # LPS ne publie pas d'heure sur la page listing — ne pas
            # fabriquer une valeur qui serait affichée comme réelle.
            time=None,
            url=href,
            image=image,
        ))

    if not events:
        print("=" * 60, file=sys.stderr)
        print("DIAGNOSTIC: Le Petit Salon — 0 events", file=sys.stderr)
        print(f"  h2 count: {len(h2_list)}", file=sys.stderr)
        for h2 in h2_list[:5]:
            block = _find_event_block(h2)
            block_info = f"block={block.name if block else None}"
            if block:
                block_info += f" text[:80]={block.get_text(' ', strip=True)[:80]!r}"
            print(f"    h2={h2.get_text(strip=True)[:60]!r} | {block_info}",
                  file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    events.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time, "·", e.title, "·", e.url)
