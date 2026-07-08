"""Scraper for La Commune (lacommune.co/programme/).

Listing pages give date, title, URL. Time is on each detail page.
Strategy: collect stubs from listing, then fetch each /event/<slug>/ page
to extract the time.
"""
from typing import List, Optional
from datetime import date as Date
import re
import sys
import time as _time
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


def _parse_time(text: str) -> Optional[str]:
    """Extract event time from arbitrary text. Accepts 16h-03h range."""
    m = re.search(
        r"(?:ouverture|portes?|début|debut|heure|horaire|à partir|opening|start|"
        r"dès|à\s+partir)\s*[:\-]?\s*(\d{1,2})[h:](\d{0,2})",
        text, re.IGNORECASE,
    )
    if m:
        hh = int(m.group(1))
        mm_s = m.group(2)
        mm = int(mm_s) if mm_s else 0
        if (16 <= hh <= 23) or (hh <= 3):
            return f"{hh:02d}:{mm:02d}"
    for m2 in re.finditer(r"\b(\d{1,2})[h:](\d{2})\b", text):
        hh, mm = int(m2.group(1)), int(m2.group(2))
        if (16 <= hh <= 23) or (hh <= 3):
            return f"{hh:02d}:{mm:02d}"
    for m2 in re.finditer(r"(?:à|dès)\s*(\d{1,2})h\b", text, re.IGNORECASE):
        hh = int(m2.group(1))
        if (16 <= hh <= 23) or (hh <= 3):
            return f"{hh:02d}:00"
    return None


def _fetch_detail_time(url: str) -> Optional[str]:
    """Fetch a /event/<slug>/ page and extract time."""
    try:
        r = requests.get(url, timeout=10, headers=HEADERS)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        # Dedicated time/schedule elements
        for selector in (
            "[class*='time']", "[class*='horaire']", "[class*='heure']",
            "[class*='schedule']", "[class*='date']", "time",
        ):
            for el in soup.select(selector)[:4]:
                t = _parse_time(el.get_text(" ", strip=True))
                if t:
                    return t
        # Visible text — first 600 chars (header region)
        visible = soup.get_text(" ", strip=True)
        return _parse_time(visible[:600])
    except requests.RequestException:
        return None


def _scrape_page(url: str) -> List[dict]:
    """Scrape one listing page, returning event stubs."""
    try:
        resp = requests.get(url, timeout=20, headers=HEADERS)
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    stubs: List[dict] = []
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
        stubs.append({"date": d, "title": title, "url": href,
                       "category": category, "image": image})

    return stubs


def fetch() -> List[Event]:
    # Collect all stubs
    all_stubs: List[dict] = []
    seen_urls: set = set()
    for url in URLS:
        for stub in _scrape_page(url):
            if stub["url"] not in seen_urls:
                seen_urls.add(stub["url"])
                all_stubs.append(stub)

    # Fetch detail pages for time
    events: List[Event] = []
    for i, stub in enumerate(all_stubs):
        if i > 0:
            _time.sleep(0.4)
        time_str = _fetch_detail_time(stub["url"])
        events.append(Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=stub["title"],
            subtitle=None,
            category=stub["category"],
            date_start=iso(stub["date"]),
            date_end=None,
            time=time_str,
            url=stub["url"],
            image=stub["image"],
        ))

    if not events:
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

    events.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time or "  -  ", "·", e.title, "·", e.url)
