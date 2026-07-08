"""Scraper for Radiant-Bellevue (radiant-bellevue.fr).

Homepage lists upcoming events. Time is on each /spectacles/<slug>/ detail page.
Strategy: collect stubs from homepage, dedupe by URL, then fetch each detail page
once for time (in-request dedup avoids hitting the same URL twice for multi-date shows).
"""
from typing import List, Optional
from datetime import date as Date
import re
import time as _time
import requests
from bs4 import BeautifulSoup

from .base import Event, parse_french_date, iso, FR_MONTHS

VENUE = "Radiant-Bellevue"
SLUG  = "radiant-bellevue"
URL   = "https://radiant-bellevue.fr/"
HOST  = "https://radiant-bellevue.fr"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

DATE_SINGLE = re.compile(
    r"(?:\w+\s+)?(\d{1,2})\s+(\w+)\s+(\d{4})",
    re.IGNORECASE,
)
DATE_AMP = re.compile(
    r"(\d{1,2})\s*&\s*(\d{1,2})\s+(\w+)\s+(\d{4})",
    re.IGNORECASE,
)
DATE_TRIPLE = re.compile(
    r"(\d{1,2}),\s*(\d{1,2})\s*&\s*(\d{1,2})\s+(\w+)\s+(\d{4})",
    re.IGNORECASE,
)

CATEGORIES = (
    "Musique", "Chanson", "Humour", "Magie", "Théâtre", "Danse",
    "Famille", "Scolaires", "Club Bellevue", "Nouveauté",
)


def _french_month_num(s: str) -> Optional[int]:
    return FR_MONTHS.get(s.lower())


def _find_card(link, max_levels: int = 6):
    el = link
    year_re = re.compile(r"\b20\d{2}\b")
    for _ in range(max_levels):
        parent = el.parent
        if parent is None or parent.name in ("html", "body"):
            return el
        el = parent
        if year_re.search(el.get_text(" ", strip=True)):
            return el
    return el


def _parse_time(text: str) -> Optional[str]:
    """Extract show time. Radiant shows are typically 20h00 or 20h30.
    Accept 14h-22h range (matinées included).
    """
    m = re.search(
        r"(?:à|heure|horaire|début|debut|ouverture|représentation|spectacle)"
        r"\s*[:\-]?\s*(\d{1,2})[h:](\d{0,2})",
        text, re.IGNORECASE,
    )
    if m:
        hh = int(m.group(1))
        mm_s = m.group(2)
        mm = int(mm_s) if mm_s else 0
        if 14 <= hh <= 22:
            return f"{hh:02d}:{mm:02d}"
    for m2 in re.finditer(r"\b(\d{1,2})[h:](\d{2})\b", text):
        hh, mm = int(m2.group(1)), int(m2.group(2))
        if 14 <= hh <= 22:
            return f"{hh:02d}:{mm:02d}"
    return None


def _fetch_detail_time(url: str) -> Optional[str]:
    """Fetch /spectacles/<slug>/ and extract time."""
    try:
        r = requests.get(url, timeout=10, headers=HEADERS)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        for selector in (
            "[class*='horaire']", "[class*='time']", "[class*='heure']",
            "[class*='schedule']", "[class*='seance']", "[class*='date']", "time",
        ):
            for el in soup.select(selector)[:4]:
                t = _parse_time(el.get_text(" ", strip=True))
                if t:
                    return t
        visible = soup.get_text(" ", strip=True)
        return _parse_time(visible[:800])
    except requests.RequestException:
        return None


def fetch() -> List[Event]:
    resp = requests.get(URL, timeout=20, headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Pass 1: collect stubs from listing
    raw_stubs: List[dict] = []
    seen_urls: set = set()

    for a in soup.select('a[href*="/spectacles/"]'):
        href = a.get("href", "")
        if href.startswith("/"):
            href = HOST + href
        if not href.startswith("http"):
            continue
        if "/spectacles/" not in href or href.endswith("/spectacles/"):
            continue
        if href in seen_urls:
            continue

        card = _find_card(a)
        text = card.get_text(" ", strip=True)

        date_starts: List[str] = []
        m_triple = DATE_TRIPLE.search(text)
        m_amp    = DATE_AMP.search(text)
        m_single = DATE_SINGLE.search(text)

        if m_triple:
            d1, d2, d3, mo, yr = m_triple.groups()
            month = _french_month_num(mo)
            year  = int(yr)
            if month:
                for d_s in (d1, d2, d3):
                    try:
                        date_starts.append(Date(year, month, int(d_s)).isoformat())
                    except ValueError:
                        pass
        elif m_amp:
            d1, d2, mo, yr = m_amp.groups()
            month = _french_month_num(mo)
            year  = int(yr)
            if month:
                for d_s in (d1, d2):
                    try:
                        date_starts.append(Date(year, month, int(d_s)).isoformat())
                    except ValueError:
                        pass
        elif m_single:
            d_s, mo, yr = m_single.groups()
            month = _french_month_num(mo)
            year  = int(yr)
            if month:
                try:
                    date_starts.append(Date(year, month, int(d_s)).isoformat())
                except ValueError:
                    pass

        if not date_starts:
            continue

        title_el = card.find(["h2", "h3"])
        title = title_el.get_text(strip=True) if title_el else a.get_text(" ", strip=True)
        if not title or len(title) < 2:
            continue

        category: Optional[str] = None
        for kw in CATEGORIES:
            if kw in text:
                category = kw.lower()
                break

        image: Optional[str] = None
        for img in card.find_all("img"):
            src = img.get("src") or ""
            if src.startswith("http") and not src.endswith(".svg"):
                image = src
                break

        seen_urls.add(href)
        raw_stubs.append({
            "date_starts": date_starts,
            "title": title, "category": category,
            "url": href, "image": image,
        })

    # Pass 2: fetch each unique URL once for time
    url_to_time: dict = {}
    for i, stub in enumerate(raw_stubs):
        if i > 0:
            _time.sleep(0.4)
        url_to_time[stub["url"]] = _fetch_detail_time(stub["url"])

    # Build events (one per date occurrence)
    events: List[Event] = []
    seen_ids: set = set()
    for stub in raw_stubs:
        time_str = url_to_time.get(stub["url"])
        for ds in stub["date_starts"]:
            ev = Event(
                venue=VENUE,
                venue_slug=SLUG,
                title=stub["title"],
                subtitle=None,
                category=stub["category"],
                date_start=ds,
                date_end=None,
                time=time_str,
                url=stub["url"],
                image=stub["image"],
            )
            if ev.id not in seen_ids:
                seen_ids.add(ev.id)
                events.append(ev)

    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time or "  -  ", "·", e.title, "·", e.url)
