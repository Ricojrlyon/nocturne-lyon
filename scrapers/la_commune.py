"""Scraper for La Commune (lacommune.co/programme/).

Page structure (verified):
- A list of cards. Each card is wrapped in an <a href="/event/<slug>/">.
- Inside: image, <h2> with title, then "DD month YYYY" date, then
  "La Commune Gerland", then a category line ("Concert", "Atelier",
  "Karaoké et blind test", "Bien-être", "Danse", etc.)
- The /programme/ page shows the next ~8 events. For all events we fetch
  /hub-evenements/ which is the full listing.

Time: not published on the website — left None.
"""
from typing import List, Optional
from datetime import date as Date
import re
import sys
import requests
from bs4 import BeautifulSoup

from .base import Event, iso, FR_MONTHS

VENUE = "La Commune"
SLUG  = "la-commune"
HOST  = "https://lacommune.co"
URLS  = [
    HOST + "/hub-evenements/",
    HOST + "/programme/",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

DATE_RE = re.compile(
    r"\b(\d{1,2})\s+(janvier|f[eé]vrier|mars|avril|mai|juin|juillet|"
    r"ao[uû]t|septembre|octobre|novembre|d[eé]cembre)\s+(\d{4})\b",
    re.IGNORECASE,
)


def _french_month_num(s: str) -> Optional[int]:
    return FR_MONTHS.get(s.lower())


def _scrape_page(url: str) -> List[Event]:
    try:
        resp = requests.get(url, timeout=20, headers=HEADERS)
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    events: List[Event] = []
    seen_urls: set = set()
    today = Date.today()

    for a in soup.select('a[href*="/event/"]'):
        href = a.get("href", "")
        if href.startswith("/"):
            href = HOST + href
        if not href.startswith("http"):
            continue
        if href in seen_urls:
            continue
        if href.rstrip("/") == HOST + "/event":
            continue

        text = a.get_text(" ", strip=True)
        m = DATE_RE.search(text)
        if not m:
            continue
        d_str, mo_str, yr_str = m.groups()
        month = _french_month_num(mo_str)
        if not month:
            continue
        try:
            d = Date(int(yr_str), month, int(d_str))
        except ValueError:
            continue
        if d < today:
            continue

        title_el = a.find("h2")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        if not title or len(title) < 2 or len(title) > 250:
            continue

        category: Optional[str] = None
        idx = text.lower().find("la commune gerland")
        if idx >= 0:
            tail = text[idx + len("la commune gerland"):].strip(" -·•|")
            if tail and len(tail) < 60:
                category = tail.lower()

        image: Optional[str] = None
        img = a.find("img")
        if img and img.get("src", "").startswith("http"):
            image = img["src"]

        seen_urls.add(href)
        events.append(Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=title,
            subtitle=None,
            category=category,
            date_start=iso(d),
            date_end=None,
            time=None,
            url=href,
            image=image,
        ))

    return events


def fetch() -> List[Event]:
    all_events: List[Event] = []
    for url in URLS:
        all_events.extend(_scrape_page(url))

    seen, unique = set(), []
    for e in all_events:
        if e.id not in seen:
            seen.add(e.id)
            unique.append(e)

    if not unique:
        print("=" * 60, file=sys.stderr)
        print("DIAGNOSTIC: La Commune — 0 events", file=sys.stderr)
        for url in URLS:
            try:
                resp = requests.get(url, timeout=15, headers=HEADERS)
                print(f"  {url} -> {resp.status_code} ({len(resp.text)} bytes)",
                      file=sys.stderr)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    links = soup.select('a[href*="/event/"]')
                    h2s = soup.find_all("h2")
                    print(f"    /event/ links: {len(links)}, h2: {len(h2s)}",
                          file=sys.stderr)
            except requests.RequestException as e:
                print(f"  {url} -> failed: {e}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    unique.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return unique


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, "·", e.title, "·", e.category or "", "·", e.url)
