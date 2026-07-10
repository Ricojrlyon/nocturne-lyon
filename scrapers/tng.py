"""Scraper for Théâtre Nouvelle Génération (tng-lyon.fr).

Listing page: <a href="/evenement/<slug>/"> wraps all card data.
Time: on the detail page. TNG shows are typically 20h30 or 15h (matinée).
Strategy: collect stubs from listing, then fetch each detail page for time.
"""
from typing import List, Optional, Tuple
from datetime import date as Date
import re
import sys
import time as _time
import requests
from bs4 import BeautifulSoup, Tag

from .base import Event, iso, FR_MONTHS

VENUE = "TNG"
SLUG  = "tng"
HOST  = "https://www.tng-lyon.fr"
URL   = HOST + "/programme/"

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

DATE_RANGE = re.compile(
    r"(\d{1,2})\s+([\wéèêôû]+)\s*[>→–\-]\s*(\d{1,2})\s+([\wéèêôû]+)",
    re.IGNORECASE | re.DOTALL,
)
DATE_SINGLE = re.compile(
    r"(\d{1,2})\s+([\wéèêôû]+)",
    re.IGNORECASE,
)


def _normalize_month(s: str) -> Optional[int]:
    s = s.lower().rstrip(".")
    s = (s.replace("é","e").replace("è","e").replace("ê","e")
           .replace("ô","o").replace("û","u"))
    if s in FR_MONTHS:
        return FR_MONTHS[s]
    return SHORT_MONTHS.get(s)


def _slug_from_href(href: str) -> str:
    return href.split("?")[0].split("#")[0].rstrip("/").lower() if href else ""


def _smart_year(month: int, day: int, ref: Date) -> int:
    grace = 15
    try:
        candidate = Date(ref.year, month, day)
    except ValueError:
        return ref.year
    return ref.year + 1 if (ref - candidate).days > grace else ref.year


def _extract_dates(text: str) -> Tuple[Optional[Date], Optional[Date]]:
    today = Date.today()
    m = DATE_RANGE.search(text)
    if m:
        d1, mo1, d2, mo2 = m.groups()
        month1, month2 = _normalize_month(mo1), _normalize_month(mo2)
        if month1 and month2:
            try:
                day1, day2 = int(d1), int(d2)
                if 1 <= day1 <= 31 and 1 <= day2 <= 31:
                    if month1 == month2:
                        year = _smart_year(month1, day1, today)
                        return Date(year, month1, day1), Date(year, month2, day2)
                    else:
                        start_year = _smart_year(month1, day1, today)
                        end_year = start_year + 1 if month1 > month2 else start_year
                        return Date(start_year, month1, day1), Date(end_year, month2, day2)
            except ValueError:
                pass
    for m2 in DATE_SINGLE.finditer(text):
        d, mo = m2.group(1), m2.group(2)
        month = _normalize_month(mo)
        if month:
            try:
                day = int(d)
                if 1 <= day <= 31:
                    year = _smart_year(month, day, today)
                    return Date(year, month, day), None
            except ValueError:
                continue
    return None, None


def _parse_time(text: str) -> Optional[str]:
    """Extract time. TNG: theatre times 10h-22h range."""
    m = re.search(
        r"(?:à|heure|horaire|début|debut|représentation|seance|séance)"
        r"\s*[:\-]?\s*(\d{1,2})[h:](\d{0,2})",
        text, re.IGNORECASE,
    )
    if m:
        hh = int(m.group(1))
        mm_s = m.group(2)
        mm = int(mm_s) if mm_s else 0
        if 10 <= hh <= 22:
            return f"{hh:02d}:{mm:02d}"
    for m2 in re.finditer(r"\b(\d{1,2})[h:](\d{2})\b", text):
        hh, mm = int(m2.group(1)), int(m2.group(2))
        if 10 <= hh <= 22:
            return f"{hh:02d}:{mm:02d}"
    return None


def _fetch_detail_time(url: str) -> Optional[str]:
    """Fetch /evenement/<slug>/ and extract time."""
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
    try:
        resp = requests.get(URL, timeout=20, headers=HEADERS)
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    stubs: List[dict] = []
    seen_slugs: set = set()
    today = Date.today()

    for a in soup.select('a[href*="/evenement/"]'):
        href = a.get("href", "")
        if href.startswith("/"):
            href = HOST + href
        slug = _slug_from_href(href)
        if not slug or slug in seen_slugs:
            continue
        if slug.endswith("/evenement"):
            continue

        h2 = a.find(["h2", "h3"])
        if not h2:
            continue
        title = h2.get_text(" ", strip=True)
        if not title or len(title) < 2 or len(title) > 250:
            continue

        text = a.get_text(" ", strip=True)
        d_start, d_end = _extract_dates(text)
        if not d_start:
            continue
        if d_start < today and (d_end is None or d_end < today):
            continue

        subtitle: Optional[str] = None
        for tn in a.stripped_strings:
            if tn == title:
                continue
            tn_lower = tn.lower()
            if (DATE_RANGE.fullmatch(tn) or DATE_SINGLE.fullmatch(tn) or
                    re.fullmatch(r"\d{1,2}", tn)):
                continue
            if tn_lower in (">", "→", "tng-vaise", "ateliers - presqu'île",
                            "en famille", "réserver", "plus d'infos",
                            "voir plus", "gratuit", "spectacle", "atelier"):
                continue
            if tn_lower.startswith("dès ") or _normalize_month(tn):
                continue
            if len(tn) < 3 or len(tn) > 250:
                continue
            subtitle = tn
            break

        image: Optional[str] = None
        img = a.find("img")
        if img:
            src = img.get("src", "") or ""
            if src.startswith("http"):
                image = src

        seen_slugs.add(slug)
        stubs.append({
            "title": title, "subtitle": subtitle, "d_start": d_start,
            "d_end": d_end, "url": href.split("?")[0], "image": image,
        })

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
            category="théâtre",
            date_start=iso(stub["d_start"]),
            date_end=iso(stub["d_end"]) if stub["d_end"] else None,
            time=time_str,
            url=stub["url"],
            image=stub["image"],
        ))

    if not events:
        print("=" * 60, file=sys.stderr)
        print("DIAGNOSTIC: TNG — 0 events", file=sys.stderr)
        try:
            resp2 = requests.get(URL, timeout=15, headers=HEADERS)
            soup2 = BeautifulSoup(resp2.text, "html.parser")
            ev_links = soup2.select('a[href*="/evenement/"]')
            print(f"  /evenement/ <a> count: {len(ev_links)}", file=sys.stderr)
            for a in ev_links[:3]:
                print(f"  href={a.get('href','')!r} | text: {a.get_text(' ',strip=True)[:100]!r}",
                      file=sys.stderr)
        except requests.RequestException as e:
            print(f"  failed: {e}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    events.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, "→", e.date_end or "  -  ", e.time or "  -  ", "·", e.title)
