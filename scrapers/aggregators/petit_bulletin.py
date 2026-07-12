"""Petit Bulletin aggregator scraper.

Fetches https://www.petit-bulletin.fr/agenda-recherche.html and parses the
list of upcoming events. The page structure is regular: each event has a
title in an h-tag with a stable URL (/agenda-NNNNNN-slug.html), a category
in parens on the next sibling line, then a list with venue and date.

Filters applied:
  * Category blocklist (exact match on normalized form): Conférences,
    Théâtre, Rencontres et Dédicaces. Exact match to avoid blocking
    "Humour & Café Théâtre" which is a different category (comedy).
  * Venue substring blocklist: librairie (bookstores).

Multi-day events ("Du X au Y MOIS YYYY") emit one Event per day in range;
ranges longer than 7 days are skipped ENTIRELY (not truncated) — like the
open-ended exhibitions ("Jusqu'au X" without "Du"), they don't fit the
daily-events model.
"""
from __future__ import annotations
import re
import unicodedata
from datetime import date, timedelta
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from ..base import Event

URL = "https://www.petit-bulletin.fr/agenda-recherche.html"
BASE = "https://www.petit-bulletin.fr"

USER_AGENT = (
    "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
    "+https://github.com/Ricojrlyon/nocturne-lyon)"
)

# Categories to exclude. EXACT match on normalized form (lowercase, accents
# stripped, punctuation → space). This is critical: "Humour & Café Théâtre"
# normalizes to "humour cafe theatre" which is NOT equal to "theatre", so
# comedy stays.
EXCLUDED_CATEGORIES = {
    "conferences",
    "conference",
    "theatre",
    "theatres",
    "rencontres et dedicaces",
}

# Venue patterns to exclude. SUBSTRING match on normalized form. Note
# "bibliothèque" (public library) is intentionally not here — those are
# civic spaces, not bookstores.
EXCLUDED_VENUE_PATTERNS = [
    "librairie",
    "theatre",
    # Umbrella festival listings without a specific venue. These create
    # visual duplicates with the per-concert listings at real venues.
    "dans toute la ville",
]

# Venues matching an excluded pattern but kept anyway. EXACT match on
# normalized form. Les Théâtres romains de Fourvière accueillent des
# concerts (Nuits de Fourvière), pas du théâtre — le pattern "theatre"
# les bloquait à tort. Variantes de nommage vues ou plausibles sur PB.
ALLOWED_VENUES = {
    "theatres romains de fourviere",
    "theatre antique de fourviere",
    "theatre de fourviere",
    "theatre gallo romain de fourviere",
}

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


def _slugify(s: str) -> str:
    s = (s or "").lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def _parse_date_str(s: str) -> List[Tuple[str, Optional[str]]]:
    """Parse a Petit Bulletin date string into (date_iso, time_hhmm) tuples.

    Returns a LIST because multi-day events ("Du X au Y") produce one tuple
    per day in the range. Single-day events return a list of one. Returns
    an empty list if no date can be extracted or the event has already
    happened.

    Examples:
      "Mardi 26 mai 2026 à 20h"             -> [("2026-05-26", "20:00")]
      "Mardi 26 mai 2026 à 18h30"           -> [("2026-05-26", "18:30")]
      "Mardi 26 mai 2026 de 18h à 1h"       -> [("2026-05-26", "18:00")]
      "Du 26 au 28 mai 2026, à 19h"         -> 3 entries, all at 19:00
      "Du 27 au 30 mai 2026, à 20h sauf X"  -> 4 entries (sauf clause ignored)
      "Jusqu'au 30 mai 2026, ..."           -> [] (skipped — open-ended)
      "30 mai et 31 mai vendredi à 20h"     -> [("2026-05-30", "20:00")]
    """
    norm = _normalize(s)
    today_iso = date.today().isoformat()

    # Open-ended exhibitions: "Jusqu'au X" with no "Du" → skip
    # "(?:er)?" everywhere below: the French ordinal "1er" has no word
    # boundary between the digit and "er", so \d+ / (\d{1,2}) alone never
    # match "du 1er au 3 mai" or "1er mai".
    # "(?:\w+\s+)?" : accepte aussi la forme cross-mois "du 28 mai au 3 juin"
    has_du_range = bool(re.search(
        r"\bdu\s+\d+(?:er)?\s+(?:\w+\s+)?au\s+\d+(?:er)?\s", norm))
    has_jusqu = "jusqu" in norm
    if has_jusqu and not has_du_range:
        return []

    # Extract time first: "à HHh", "à HHhMM", "de HHh à HHh"
    time_m = re.search(r"\b(\d{1,2})h(\d{0,2})\b", norm)
    time_str: Optional[str] = None
    if time_m:
        hh = int(time_m.group(1))
        mm_s = time_m.group(2)
        mm = int(mm_s) if mm_s else 0
        if 0 <= hh < 24 and 0 <= mm < 60:
            time_str = f"{hh:02d}:{mm:02d}"

    # Try range first: "Du X au Y mois YYYY"
    range_m = re.search(
        r"\bdu\s+(\d{1,2})(?:er)?\s+au\s+(\d{1,2})(?:er)?\s+"
        r"(janvier|fevrier|mars|avril|mai|juin|juillet|aout|"
        r"septembre|octobre|novembre|decembre)\s+(\d{4})",
        norm
    )
    if range_m:
        start_day = int(range_m.group(1))
        end_day = int(range_m.group(2))
        month = MONTHS_FR[range_m.group(3)]
        year = int(range_m.group(4))
        try:
            start = date(year, month, start_day)
            end = date(year, month, end_day)
        except ValueError:
            return []
        if end < start:
            return []
        # Ranges longer than 7 days are exhibition-like: skip the event
        # entirely (no truncation).
        if (end - start).days > 7:
            return []
        results: List[Tuple[str, Optional[str]]] = []
        d = start
        while d <= end:
            d_iso = d.isoformat()
            if d_iso >= today_iso:
                results.append((d_iso, time_str))
            d += timedelta(days=1)
        return results

    # Range across two months: "Du 28 mai au 3 juin 2026". Sans ce cas,
    # seule la date de fin était captée par le fallback date simple et
    # l'événement n'apparaissait qu'au dernier jour.
    range_xm = re.search(
        r"\bdu\s+(\d{1,2})(?:er)?\s+"
        r"(janvier|fevrier|mars|avril|mai|juin|juillet|aout|"
        r"septembre|octobre|novembre|decembre)\s+"
        r"au\s+(\d{1,2})(?:er)?\s+"
        r"(janvier|fevrier|mars|avril|mai|juin|juillet|aout|"
        r"septembre|octobre|novembre|decembre)\s+(\d{4})",
        norm
    )
    if range_xm:
        start_day = int(range_xm.group(1))
        start_month = MONTHS_FR[range_xm.group(2)]
        end_day = int(range_xm.group(3))
        end_month = MONTHS_FR[range_xm.group(4)]
        year = int(range_xm.group(5))
        # "du 30 decembre au 2 janvier 2027" : l'année écrite est celle
        # de la fin de plage.
        start_year = year - 1 if start_month > end_month else year
        try:
            start = date(start_year, start_month, start_day)
            end = date(year, end_month, end_day)
        except ValueError:
            return []
        if end < start:
            return []
        if (end - start).days > 7:
            return []
        results = []
        d = start
        while d <= end:
            d_iso = d.isoformat()
            if d_iso >= today_iso:
                results.append((d_iso, time_str))
            d += timedelta(days=1)
        return results

    # Single date: take the FIRST occurrence of "DD mois YYYY"
    single_m = re.search(
        r"\b(\d{1,2})(?:er)?\s+"
        r"(janvier|fevrier|mars|avril|mai|juin|juillet|aout|"
        r"septembre|octobre|novembre|decembre)\s+(\d{4})",
        norm
    )
    if single_m:
        day = int(single_m.group(1))
        month = MONTHS_FR[single_m.group(2)]
        year = int(single_m.group(3))
    else:
        # Fallback: "DD mois" without year (e.g. compound dates like
        # "29 mai et 30 mai vendredi à 20h").
        fb_m = re.search(
            r"\b(\d{1,2})(?:er)?\s+"
            r"(janvier|fevrier|mars|avril|mai|juin|juillet|aout|"
            r"septembre|octobre|novembre|decembre)\b",
            norm
        )
        if not fb_m:
            return []
        day = int(fb_m.group(1))
        month = MONTHS_FR[fb_m.group(2)]
        # No year on the page: assume the NEXT occurrence of that date —
        # same heuristic as base.parse_french_date. "15 janvier" scraped
        # in July means next January; with the current year it would be
        # in the past and silently dropped.
        today = date.today()
        year = today.year
        try:
            if date(year, month, day) < today:
                year += 1
        except ValueError:
            pass
    try:
        iso = f"{year:04d}-{month:02d}-{day:02d}"
        date(year, month, day)  # validate
    except ValueError:
        return []
    if iso < today_iso:
        return []
    return [(iso, time_str)]


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

        title = a.get_text(strip=True)
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
                    venue = (va or lis[0]).get_text(strip=True)
                if len(lis) >= 2:
                    da = lis[1].find("a")
                    date_str = (da or lis[1]).get_text(strip=True)
                # don't break — there might be more useful sibs, but
                # typically nothing else relevant follows the ul
                break

        if not category or not venue or not date_str:
            continue

        # Category filter (EXACT match on normalized form)
        if _normalize(category) in EXCLUDED_CATEGORIES:
            continue

        # Venue filter (substring match, sauf whitelist exacte)
        venue_norm = _normalize(venue)
        if (venue_norm not in ALLOWED_VENUES
                and any(p in venue_norm for p in EXCLUDED_VENUE_PATTERNS)):
            continue

        # Date filter & expansion
        date_times = _parse_date_str(date_str)
        if not date_times:
            continue

        url = href if href.startswith("http") else BASE + href

        for date_iso, time_str in date_times:
            if date_iso < today_iso:
                continue
            events.append(Event(
                venue=venue,
                venue_slug=_slugify(venue),
                title=title,
                subtitle=None,
                category=category,
                date_start=date_iso,
                date_end=None,
                time=time_str,
                url=url,
                image=None,
            ))

    return events


def fetch() -> List[Event]:
    """Fetch and parse the Petit Bulletin agenda."""
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(URL, headers=headers, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    return _extract_events_from_soup(soup)
