"""Scraper for HEAT (h-eat.eu/events/).

Listing page gives date + title. Time is on each event's detail page.
Strategy: collect all event URLs from listing, then fetch each detail
page to extract the time (rate-limited to 0.4 s/request).
"""
from typing import List, Optional
from datetime import date as Date
import re
import sys
import time as _time
import requests
from bs4 import BeautifulSoup

from .base import Event, iso

VENUE = "HEAT"
SLUG  = "heat"
URL   = "https://h-eat.eu/events/"
HOST  = "https://h-eat.eu"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

SHORT_MONTHS = {
    "janv": 1, "fevr": 2, "févr": 2, "mars": 3, "avr": 4, "mai": 5,
    "juin": 6, "juil": 7, "juill": 7, "aout": 8, "août": 8, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12, "déc": 12,
}

DATE_RE = re.compile(
    r"\b\w+\.?\s+(\d{1,2})\s+(janv|f[eé]vr|mars|avr|mai|juin|juill?|"
    r"ao[uû]t|sept|oct|nov|d[eé]c)\.?",
    re.IGNORECASE,
)

CATEGORIES = (
    "afterwork", "atelier", "blindtest", "blind test", "club labo",
    "comedy lab", "danse", "dj set", "festival", "jeux", "market",
    "open air", "sport",
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


def _normalize_month(s: str) -> Optional[int]:
    s = s.lower().rstrip(".")
    s = s.replace("é","e").replace("è","e").replace("ê","e").replace("ô","o").replace("û","u")
    return SHORT_MONTHS.get(s)


def _parse_time(text: str) -> Optional[str]:
    """Extract event time from arbitrary text.

    HEAT shows times like "19:00 — 20:00" or "19h00" near the title.
    Accepts evening/night hours only (16h-03h).
    """
    # "HH:MM" or "HHhMM" — prefer the first one in the plausible range
    for m in re.finditer(r"\b(\d{1,2})[h:](\d{2})\b", text):
        hh, mm = int(m.group(1)), int(m.group(2))
        if (16 <= hh <= 23) or (hh <= 3):
            return f"{hh:02d}:{mm:02d}"
    # "HHh" alone
    for m in re.finditer(r"\b(\d{1,2})h\b", text, re.IGNORECASE):
        hh = int(m.group(1))
        if (16 <= hh <= 23) or (hh <= 3):
            return f"{hh:02d}:00"
    return None


def _fetch_detail_time(url: str) -> Optional[str]:
    """Fetch an event detail page and extract the time."""
    try:
        r = requests.get(url, timeout=10, headers=HEADERS)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        # Try dedicated time elements first
        for selector in (
            "[class*='time']", "[class*='horaire']", "[class*='heure']",
            "[class*='schedule']", "time", "[class*='date']",
        ):
            for el in soup.select(selector)[:3]:
                t = _parse_time(el.get_text(" ", strip=True))
                if t:
                    return t
        # Try the first 400 chars of visible text (header area)
        visible = soup.get_text(" ", strip=True)
        # Contextual search
        m = re.search(
            r"(?:ouverture|portes?|début|debut|horaire|heure|à partir|opening|start)"
            r"\s*[:\-]?\s*(\d{1,2})[h:](\d{0,2})",
            visible[:600], re.IGNORECASE,
        )
        if m:
            hh = int(m.group(1))
            mm_s = m.group(2)
            mm = int(mm_s) if mm_s else 0
            if (16 <= hh <= 23) or (hh <= 3):
                return f"{hh:02d}:{mm:02d}"
        return _parse_time(visible[:600])
    except requests.RequestException:
        return None


def fetch() -> List[Event]:
    resp = requests.get(URL, timeout=20, headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Collect event stubs from listing
    stubs: List[dict] = []
    seen_urls: set = set()

    for a in soup.select('a[href*="/events/"]'):
        href = a.get("href", "")
        if href.startswith("/"):
            href = HOST + href
        if not href.startswith("http"):
            continue
        if href.rstrip("/") in (HOST + "/events", HOST + "/events-archives"):
            continue
        if href in seen_urls:
            continue

        text = a.get_text(" ", strip=True)
        m = DATE_RE.search(text)
        if not m:
            continue
        day = int(m.group(1))
        month = _normalize_month(m.group(2))
        if not month:
            continue
        try:
            year = _smart_year(month, day)
            d = Date(year, month, day)
        except ValueError:
            continue

        title_el = a.find("h2")
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        if not title or len(title) < 2 or len(title) > 250:
            continue

        category: Optional[str] = None
        text_lower = text.lower()
        for kw in CATEGORIES:
            if kw in text_lower:
                category = kw.replace(" ", "-")
                break

        image: Optional[str] = None
        img = a.find("img")
        if img:
            src = img.get("src", "") or ""
            if src.startswith("http"):
                image = src

        seen_urls.add(href)
        stubs.append({"date": d, "title": title, "url": href,
                       "category": category, "image": image})

    # Fetch detail pages for time (rate-limited)
    events: List[Event] = []
    for i, stub in enumerate(stubs):
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
        print("DIAGNOSTIC: HEAT — 0 events", file=sys.stderr)
        links = soup.select('a[href*="/events/"]')
        print(f"  /events/ links: {len(links)}", file=sys.stderr)
        for a in links[:5]:
            t = a.get_text(" ", strip=True)[:100]
            print(f"    - {a.get('href', '')!r} | text: {t!r}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    events.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time or "  -  ", "·", e.title, "·", e.url)
