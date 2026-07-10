"""Scraper for Le Périscope (periscope-lyon.com/concerts/).

Page structure (verified):
- A list of event cards, each card is wrapped in an <a href="/concerts/<slug>/">.
- Inside each card: <h3> title, optional <h4> subtitle, <h5> with date
  ("Mercredi 06 mai"), then a tag like "Le Péri" or "Grande Scène", then a
  price like "8/12/14€".
- The page text shows month section headings like "mai 2026" / "juin 2026"
  to disambiguate years.

Strategy: anchor on /concerts/<slug>/ links, walk up until the parent
contains both an <h3> and an <h5>, then extract.
"""
from typing import List, Optional
from datetime import date as Date
import re
import sys
import requests
from bs4 import BeautifulSoup, Tag

from .base import Event, parse_french_date, iso, FR_MONTHS

VENUE = "Le Périscope"
SLUG = "periscope"
URL = "https://www.periscope-lyon.com/concerts/"
HOST = "https://www.periscope-lyon.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# "Mercredi 06 mai" — captures day_num, month name
DATE_RE = re.compile(
    r"(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+"
    r"(\d{1,2})\s+(\w+)",
    re.IGNORECASE,
)
# "mai 2026" section heading — captures month name and year
MONTH_HEADING_RE = re.compile(
    r"\b(janvier|f[eé]vrier|mars|avril|mai|juin|juillet|"
    r"ao[uû]t|septembre|octobre|novembre|d[eé]cembre)\s+(\d{4})\b",
    re.IGNORECASE,
)


def _smart_year(month: int, day: int) -> int:
    today = Date.today()
    try:
        candidate = Date(today.year, month, day)
    except ValueError:
        return today.year
    return today.year + 1 if candidate < today else today.year


def fetch() -> List[Event]:
    resp = requests.get(URL, timeout=20, headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    events: List[Event] = []
    seen_urls: set = set()

    for a in soup.select('a[href*="/concerts/"]'):
        href = a.get("href", "")
        if href.startswith("/"):
            href = HOST + href
        if not href.startswith("http"):
            continue
        # Skip the agenda page itself, language toggles, and the festival
        # landing pages
        if href.rstrip("/") in (
                HOST + "/concerts",
                HOST + "/agenda-concerts",
                HOST + "/en/concerts",
        ):
            continue
        if href in seen_urls:
            continue

        # The card is the link itself (or its content)
        card = a
        text = card.get_text(" ", strip=True)

        m = DATE_RE.search(text)
        if not m:
            continue
        d_str, mo_str = m.group(1), m.group(2)
        month_lower = mo_str.lower()
        if month_lower not in FR_MONTHS:
            continue

        try:
            day = int(d_str)
            month = FR_MONTHS[month_lower]
            year = _smart_year(month, day)
            d = Date(year, month, day)
        except ValueError:
            continue

        # Title from h3 inside the link
        title_el = card.find("h3")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        if not title:
            continue
        if len(title) < 2 or len(title) > 250:
            continue

        # Subtitle from h4 (e.g. "En partenariat avec Wabi-Sabi Tapes")
        subtitle: Optional[str] = None
        sub_el = card.find("h4")
        if sub_el:
            cand = sub_el.get_text(" ", strip=True)
            if cand and cand != title and len(cand) < 250:
                subtitle = cand

        # Image
        image: Optional[str] = None
        img = card.find("img")
        if img and img.get("src", "").startswith("http"):
            image = img["src"]

        # Category: the page lets users filter by jazz/expé/électronique/etc.
        # Without the filter info per card, we mark all as "musique".
        category = "musique"

        seen_urls.add(href)
        events.append(Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=title,
            subtitle=subtitle,
            category=category,
            date_start=iso(d),
            date_end=None,
            time=None,
            url=href,
            image=image,
        ))

    if not events:
        print("=" * 60, file=sys.stderr)
        print("DIAGNOSTIC: Le Périscope — 0 events", file=sys.stderr)
        links = soup.select('a[href*="/concerts/"]')
        print(f"  /concerts/ links: {len(links)}", file=sys.stderr)
        for a in links[:5]:
            print(f"    - {a.get('href', '')!r}", file=sys.stderr)
        h3_count = len(soup.find_all("h3"))
        h5_count = len(soup.find_all("h5"))
        print(f"  h3: {h3_count}, h5: {h5_count}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    events.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, "·", e.title, "·", e.subtitle or "", "·", e.url)
