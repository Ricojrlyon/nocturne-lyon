"""Scraper for Le Périscope (periscope-lyon.com/concerts/).

Page structure (re-verified July 2026 — the site did NOT switch to JS
rendering, contrary to what a broken run suggested):
- A list of event cards, each wrapped in an <a href="/concerts/<slug>/">.
- Inside each card: <h3> title, optional <h4> subtitle, <h5> with date
  ("Mercredi 15 juill" — note the double-L "juill" abbreviation), then a
  venue line (div .tsmall.tb600: "Le Périscope"/"Grande Scène", or the
  real off-site venue for summer shows, e.g. "Jardin Envie Partagée quai
  Rambaud - à côté du square Delfosse").
- The listing is PAGINATED (/concerts/page/2/ …). Some page numbers
  return the same content as page 1, so pagination stops when a page
  yields no NEW event, not on 404.
- Detail pages carry no reliable show time (only publication metadata):
  time stays None.

Off-site venues are kept as-is (same naming as Ville Morte), so the
dedup passes can converge both sources instead of duplicating.
"""
from typing import List, Optional
from datetime import date as Date
import re
import sys
import unicodedata
import requests
from bs4 import BeautifulSoup

from .base import Event, iso, FR_MONTHS

VENUE = "Le Périscope"
SLUG = "periscope"
URL = "https://www.periscope-lyon.com/concerts/"
HOST = "https://www.periscope-lyon.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# "Mercredi 06 mai" / "Mercredi 15 juill" — captures day_num, month name
DATE_RE = re.compile(
    r"(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+"
    r"(\d{1,2})\s+(\w+)",
    re.IGNORECASE,
)

# La ligne lieu des cartes référence soit le Périscope lui-même (dont ses
# scènes), soit un vrai lieu hors les murs à garder tel quel.
_PERISCOPE_MARKERS = ("peri", "grande scene", "petite scene")


def _norm(s: str) -> str:
    """Lowercase + strip accents (détection des lieux hors les murs)."""
    return "".join(
        c for c in unicodedata.normalize("NFD", (s or "").lower())
        if unicodedata.category(c) != "Mn"
    )


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


def _scrape_page(soup: BeautifulSoup, seen_urls: set) -> List[Event]:
    """Extract the event cards of one listing page (new URLs only)."""
    events: List[Event] = []

    for a in soup.select('a[href*="/concerts/"]'):
        href = a.get("href", "")
        if href.startswith("/"):
            href = HOST + href
        if not href.startswith("http"):
            continue
        # Pages liste, pagination, flux RSS et variantes anglaises — pas
        # des cartes d'événement.
        if href.rstrip("/") in (
                HOST + "/concerts",
                HOST + "/agenda-concerts",
                HOST + "/en/concerts",
        ):
            continue
        if "/concerts/page/" in href or href.rstrip("/").endswith("/feed"):
            continue
        if "/en/concerts/" in href:
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

        # Subtitle from h4 (e.g. "Diversions · Escapade estivale")
        subtitle: Optional[str] = None
        sub_el = card.find("h4")
        if sub_el:
            cand = sub_el.get_text(" ", strip=True)
            if cand and cand != title and len(cand) < 250:
                subtitle = cand

        # Venue: summer shows happen off-site and the card carries the real
        # venue ("Jardin Envie Partagée quai Rambaud - à côté du square…").
        # Keep it (same naming as Ville Morte → the dedup converges both
        # sources); anything referencing the Périscope itself stays VENUE.
        venue = VENUE
        venue_el = card.select_one(".tsmall.tb600")
        if venue_el:
            place = venue_el.get_text(" ", strip=True).split(" - ")[0].strip()
            if place and not any(k in _norm(place)
                                 for k in _PERISCOPE_MARKERS):
                venue = place
                # Garder l'événement retrouvable en cherchant « périscope »
                # (le subtitle est indexé par la recherche du frontend)
                # alors que la carte affiche le lieu réel.
                subtitle = (f"Le Périscope hors les murs · {subtitle}"
                            if subtitle else "Le Périscope hors les murs")

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
            venue=venue,
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

    return events


def fetch() -> List[Event]:
    events: List[Event] = []
    seen_urls: set = set()
    first_soup: Optional[BeautifulSoup] = None

    # Le listing est paginé. On s'arrête quand une page n'apporte aucun
    # NOUVEL événement : le site renvoie parfois le même contenu sous
    # plusieurs numéros de page, un test 404 ne suffit donc pas.
    # Cap de sécurité à 6 pages.
    for page in range(1, 7):
        page_url = URL if page == 1 else f"{URL}page/{page}/"
        try:
            resp = requests.get(page_url, timeout=20, headers=HEADERS)
        except requests.RequestException:
            if page == 1:
                raise
            break
        if resp.status_code != 200:
            if page == 1:
                resp.raise_for_status()
            break
        soup = BeautifulSoup(resp.text, "html.parser")
        if first_soup is None:
            first_soup = soup
        new_events = _scrape_page(soup, seen_urls)
        if not new_events:
            break
        events.extend(new_events)

    if not events and first_soup is not None:
        print("=" * 60, file=sys.stderr)
        print("DIAGNOSTIC: Le Périscope — 0 events", file=sys.stderr)
        links = first_soup.select('a[href*="/concerts/"]')
        print(f"  /concerts/ links: {len(links)}", file=sys.stderr)
        for a in links[:5]:
            print(f"    - {a.get('href', '')!r}", file=sys.stderr)
        h3_count = len(first_soup.find_all("h3"))
        h5_count = len(first_soup.find_all("h5"))
        print(f"  h3: {h3_count}, h5: {h5_count}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    events.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, "·", e.venue, "·", e.title, "·",
              e.subtitle or "", "·", e.url)
