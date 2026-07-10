"""Scraper for Le Sucre (le-sucre.eu/agenda).

Listing gives date + title. Time is on each /events/<slug>/ detail page.
Le Sucre is a club — expect late-night times like "23:00" or "22:00".
"""
from datetime import date, timedelta
from typing import List, Optional
import re
import time as _time
import requests
from bs4 import BeautifulSoup

from .base import Event, parse_french_date, iso

VENUE = "Le Sucre"
SLUG  = "le-sucre"
URL   = "https://le-sucre.eu/agenda/"
HOST  = "https://le-sucre.eu"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def _parse_time(text: str) -> Optional[str]:
    """Extract event time. Le Sucre often shows 'Opening : 23h00' or '23:00 — 05:00'.
    Accept all plausible hours for a club (16h-03h + late-night 22h-03h).
    """
    # Contextual: "Opening : 23h", "Ouverture : 22h30", "Début : 21h"
    m = re.search(
        r"(?:opening|ouverture|portes?|début|debut|start|heure|horaire)"
        r"\s*[:\-]?\s*(\d{1,2})[h:](\d{0,2})",
        text, re.IGNORECASE,
    )
    if m:
        hh = int(m.group(1))
        mm_s = m.group(2)
        mm = int(mm_s) if mm_s else 0
        if (16 <= hh <= 23) or (hh <= 5):
            return f"{hh:02d}:{mm:02d}"
    # "23:00 — 05:00" style (first time in range)
    m2 = re.search(r"\b(\d{1,2})[h:](\d{2})\s*(?:—|→|-|>|au?)\s*\d{1,2}[h:]\d{2}", text)
    if m2:
        hh, mm = int(m2.group(1)), int(m2.group(2))
        if (16 <= hh <= 23) or (hh <= 5):
            return f"{hh:02d}:{mm:02d}"
    # Standalone HH:MM or HHhMM
    for m3 in re.finditer(r"\b(\d{1,2})[h:](\d{2})\b", text):
        hh, mm = int(m3.group(1)), int(m3.group(2))
        if (16 <= hh <= 23) or (hh <= 5):
            return f"{hh:02d}:{mm:02d}"
    return None


def _fetch_detail_time(url: str) -> Optional[str]:
    """Fetch event detail page and extract time."""
    try:
        r = requests.get(url, timeout=10, headers=HEADERS)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        # Dedicated time elements
        for selector in (
            "[class*='time']", "[class*='horaire']", "[class*='heure']",
            "[class*='schedule']", "[class*='opening']", "time",
        ):
            for el in soup.select(selector)[:4]:
                t = _parse_time(el.get_text(" ", strip=True))
                if t:
                    return t
        # Scan first 600 chars of page text (header/summary region)
        visible = soup.get_text(" ", strip=True)
        return _parse_time(visible[:600])
    except requests.RequestException:
        return None


def fetch() -> List[Event]:
    headers = HEADERS
    resp = requests.get(URL, timeout=20, headers=headers)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    stubs: List[dict] = []

    for a in soup.select('a[href*="/events/"]'):
        href = a.get("href", "")
        if "agenda-archives" in href or not href.startswith("http"):
            continue

        h2 = a.find("h2")
        if not h2:
            continue
        title = h2.get_text(strip=True)

        h3s = a.find_all("h3")
        subtitle = " · ".join(h.get_text(strip=True) for h in h3s) if h3s else None

        text = a.get_text(" ", strip=True)
        head = text[:50]
        d = parse_french_date(head)
        if not d:
            d = parse_french_date(text)
        if not d:
            continue

        category = None
        for token in ("Club", "Concert", "Event"):
            if token in head:
                category = token.lower()
                break

        img_tag = a.find("img")
        image = img_tag.get("src") if img_tag else None

        stubs.append({"date": d, "title": title, "subtitle": subtitle,
                       "category": category, "url": href, "image": image})

    # Cap horizon: drop events more than ~6 months out BEFORE the
    # detail-page fetch phase — keeps the daily run fast and the JSON lean.
    horizon = date.today() + timedelta(days=180)
    stubs = [s for s in stubs if s["date"] <= horizon]

    # Fetch detail pages for time
    events: List[Event] = []
    for i, stub in enumerate(stubs):
        if i > 0:
            _time.sleep(0.4)
        time_str = _fetch_detail_time(stub["url"])
        events.append(Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=stub["title"],
            subtitle=stub["subtitle"],
            category=stub["category"],
            date_start=iso(stub["date"]),
            date_end=None,
            time=time_str,
            url=stub["url"],
            image=stub["image"],
        ))

    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time or "  -  ", "·", e.title, "—", e.subtitle or "", "·", e.url)
