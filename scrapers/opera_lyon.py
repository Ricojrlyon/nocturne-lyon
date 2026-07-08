"""Scraper for L'Opéra national de Lyon (opera-lyon.com).

Each production has a detail page listing individual performance times.
Strategy:
1. Scrape listing page → get productions (title, date range, URL)
2. For each production, fetch the detail page to get the first/main
   performance time (Opera shows "20h00" or "15h00" for matinées).
"""
from typing import List, Optional, Tuple
from datetime import date as Date
import re
import sys
import time as _time
import requests
from bs4 import BeautifulSoup, Tag

from .base import Event, iso, FR_MONTHS

VENUE = "Opéra national de Lyon"
SLUG  = "opera-lyon"
HOST  = "https://www.opera-lyon.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

URLS = [
    HOST + "/programmation-reservations/saison-2025-2026",
    HOST + "/programmation-reservations/saison-2026-2027",
]

URL_CATEGORY_MAP = {
    "opera": "opéra",
    "danse": "danse",
    "concert": "concert",
    "evenement": "événement",
    "opera-underground": "underground",
    "visites": "visite",
    "festival": "festival",
}

SHORT_MONTHS = {
    "janv": 1, "fevr": 2, "févr": 2, "mars": 3, "avr": 4, "mai": 5,
    "juin": 6, "juil": 7, "aout": 8, "août": 8, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12, "déc": 12,
}

DATE_SINGLE = re.compile(
    r"\b(\d{1,2})\s+([\wéèêôû]+\.?)\s+(\d{4})\b",
    re.IGNORECASE,
)
DATE_RANGE_SAME = re.compile(
    r"\b(\d{1,2})\s+([\wéèêôû]+\.?)\s*[-–]\s*(\d{1,2})\s+([\wéèêôû]+\.?)\s+(\d{4})\b",
    re.IGNORECASE,
)


def _normalize_month(s: str) -> Optional[int]:
    s = s.lower().rstrip(".")
    s = (s.replace("é","e").replace("è","e").replace("ê","e")
           .replace("ô","o").replace("û","u"))
    if s in FR_MONTHS:
        return FR_MONTHS[s]
    return SHORT_MONTHS.get(s)


def _extract_dates(text: str) -> Tuple[Optional[Date], Optional[Date]]:
    m = DATE_RANGE_SAME.search(text)
    if m:
        d1, mo1, d2, mo2, yr = m.groups()
        month1, month2 = _normalize_month(mo1), _normalize_month(mo2)
        year = int(yr)
        if month1 and month2:
            try:
                start_year = year - 1 if month1 > month2 else year
                return Date(start_year, month1, int(d1)), Date(year, month2, int(d2))
            except ValueError:
                pass
    m = DATE_SINGLE.search(text)
    if m:
        d, mo, yr = m.groups()
        month = _normalize_month(mo)
        if month:
            try:
                return Date(int(yr), month, int(d)), None
            except ValueError:
                pass
    return None, None


def _category_from_url(href: str) -> Optional[str]:
    m = re.search(r"/programmation/saison-\d{4}-\d{4}/([^/]+)/", href)
    if m:
        return URL_CATEGORY_MAP.get(m.group(1).lower(), m.group(1))
    return None


def _parse_time(text: str) -> Optional[str]:
    """Extract time from Opera page text.

    Opera times are typically 20h00, 19h30, 15h00 (matinée).
    Accept a broader range (10h-22h30) for classical shows.
    """
    # Contextual: "à 20h00", "Heure : 19h30"
    m = re.search(
        r"(?:à|heure|horaire|début|representation|représentation|séance)"
        r"\s*[:\-]?\s*(\d{1,2})[h:](\d{0,2})",
        text, re.IGNORECASE,
    )
    if m:
        hh = int(m.group(1))
        mm_s = m.group(2)
        mm = int(mm_s) if mm_s else 0
        if 10 <= hh <= 23:
            return f"{hh:02d}:{mm:02d}"
    # Standalone HHhMM or HH:MM
    for m2 in re.finditer(r"\b(\d{1,2})[h:](\d{2})\b", text):
        hh, mm = int(m2.group(1)), int(m2.group(2))
        if 10 <= hh <= 23:
            return f"{hh:02d}:{mm:02d}"
    return None


def _fetch_detail_time(url: str) -> Optional[str]:
    """Fetch production detail page and extract the first performance time."""
    try:
        r = requests.get(url, timeout=10, headers=HEADERS)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        # Look for a schedule/calendar section
        for selector in (
            "[class*='schedule']", "[class*='calendar']", "[class*='seance']",
            "[class*='representation']", "[class*='horaire']", "[class*='time']",
            "table", "[class*='date']",
        ):
            for el in soup.select(selector)[:3]:
                t = _parse_time(el.get_text(" ", strip=True))
                if t:
                    return t
        # Scan first 800 chars of visible text
        visible = soup.get_text(" ", strip=True)
        return _parse_time(visible[:800])
    except requests.RequestException:
        return None


def _scrape_url(url: str) -> List[dict]:
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

    for a in soup.select('a[href*="/programmation/saison-"]'):
        href = a.get("href", "")
        if href.startswith("/"):
            href = HOST + href
        if "/programmation/saison-" not in href:
            continue
        if href in (url, url + "/"):
            continue
        if href in seen_urls:
            continue

        text = a.get_text(" ", strip=True)
        d_start, d_end = _extract_dates(text)
        if not d_start:
            continue
        if d_start < today and (d_end is None or d_end < today):
            continue

        text_nodes = [t for t in a.stripped_strings]
        candidates: List[str] = []
        for tn in text_nodes:
            if DATE_SINGLE.fullmatch(tn) or DATE_RANGE_SAME.fullmatch(tn):
                continue
            if tn.lower() in ("réserver", "programme", "filtrer", "+", "concert",
                              "opéra", "danse", "évènement", "festival", "visites",
                              "visite guidée", "opéra underground", "voir tout",
                              "plus", "en savoir +"):
                continue
            if tn.lower().startswith("dès "):
                continue
            if len(tn) < 2 or len(tn) > 200:
                continue
            candidates.append(tn)
        if not candidates:
            continue
        title = candidates[0]
        subtitle = candidates[1] if len(candidates) > 1 else None
        if subtitle and subtitle.lower() in ("dès 12 ans", "dès 14 ans"):
            subtitle = None

        category = _category_from_url(href) or "spectacle"

        image: Optional[str] = None
        img = a.find("img")
        if img:
            src = img.get("src", "") or ""
            if src.startswith("http"):
                image = src

        seen_urls.add(href)
        stubs.append({
            "title": title, "subtitle": subtitle, "category": category,
            "d_start": d_start, "d_end": d_end, "url": href, "image": image,
        })

    return stubs


def fetch() -> List[Event]:
    all_stubs: List[dict] = []
    seen_urls: set = set()
    for url in URLS:
        for stub in _scrape_url(url):
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
            subtitle=stub["subtitle"],
            category=stub["category"],
            date_start=iso(stub["d_start"]),
            date_end=iso(stub["d_end"]) if stub["d_end"] else None,
            time=time_str,
            url=stub["url"],
            image=stub["image"],
        ))

    if not events:
        print("=" * 60, file=sys.stderr)
        print("DIAGNOSTIC: Opéra de Lyon — 0 events", file=sys.stderr)
        for url in URLS:
            try:
                resp = requests.get(url, timeout=15, headers=HEADERS)
                print(f"  {url} -> {resp.status_code} ({len(resp.text)} bytes)",
                      file=sys.stderr)
            except requests.RequestException as e:
                print(f"  {url} -> failed: {e}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    events.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, "→", e.date_end or "  -  ", e.time or "  -  ",
              "·", e.category, "·", e.title)
