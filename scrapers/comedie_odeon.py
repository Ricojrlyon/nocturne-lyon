"""Scraper for Comédie Odéon (comedieodeon.com).

v4: anchor on h2, walk UP the tree to find a common ancestor that
contains BOTH the h2 AND a /spectacle/<slug>/ link. This is the card.
"""
from typing import List, Optional, Tuple
from datetime import date as Date
import re
import sys
import requests
from bs4 import BeautifulSoup, Tag

from .base import Event, iso, FR_MONTHS

VENUE = "Comédie Odéon"
SLUG = "comedie-odeon"
HOST = "https://www.comedieodeon.com"
URL = HOST + "/spectacle/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

DATE_RANGE_SAME_MONTH = re.compile(
    r"\b[Dd]u\s+(\d{1,2})\s+au\s+(\d{1,2})\s+([\wéèêôû]+)\s+(\d{4})\b",
    re.IGNORECASE,
)
DATE_RANGE_DIFF_MONTH = re.compile(
    r"\b[Dd]u\s+(\d{1,2})\s+([\wéèêôû]+)\s+au\s+(\d{1,2})\s+([\wéèêôû]+)(?:\s+(\d{4}))?\b",
    re.IGNORECASE,
)
DATE_RANGE_NO_YEAR = re.compile(
    r"\b[Dd]u\s+(\d{1,2})\s+au\s+(\d{1,2})\s+([\wéèêôû]+)(?!\s+\d{4})\b",
    re.IGNORECASE,
)
DATE_UNTIL = re.compile(
    r"[Jj]usqu['’]au\s+(\d{1,2})\s+([\wéèêôû]+)(?:\s+(\d{4}))?",
    re.IGNORECASE,
)
DAY_NAME_DATE_NO_YEAR = re.compile(
    r"\b(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+"
    r"(\d{1,2})\s+([\wéèêôû]+)(?!\s+\d{4})\b",
    re.IGNORECASE,
)
DAY_NAME_DATE_YEAR = re.compile(
    r"\b(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+"
    r"(\d{1,2})\s+([\wéèêôû]+)\s+(\d{4})\b",
    re.IGNORECASE,
)
DATE_TWO = re.compile(
    r"\b(\d{1,2})\s+et\s+(\d{1,2})\s+([\wéèêôû]+)\s+(\d{4})\b",
    re.IGNORECASE,
)
DATE_BARE = re.compile(
    r"\b(\d{1,2})\s+([\wéèêôû]+)\s+(\d{4})\b",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"à\s+(\d{1,2})h(\d{2})?", re.IGNORECASE)


def _normalize_month(s: str) -> Optional[int]:
    s = s.lower().rstrip(".")
    s = (s.replace("é", "e").replace("è", "e").replace("ê", "e")
           .replace("ô", "o").replace("û", "u"))
    return FR_MONTHS.get(s)


def _smart_year(month: int, day: int) -> int:
    today = Date.today()
    try:
        candidate = Date(today.year, month, day)
    except ValueError:
        return today.year
    return today.year + 1 if candidate < today else today.year


def _slug_from_href(href: str) -> str:
    if not href:
        return ""
    href = href.split("?")[0].split("#")[0]
    return href.rstrip("/").lower()


def _extract_dates(text: str) -> Tuple[Optional[Date], Optional[Date]]:
    m = DATE_RANGE_DIFF_MONTH.search(text)
    if m:
        d1, mo1, d2, mo2, yr = m.groups()
        month1 = _normalize_month(mo1)
        month2 = _normalize_month(mo2)
        if month1 and month2 and month1 != month2:
            try:
                if yr:
                    year = int(yr)
                    start_year = year - 1 if month1 > month2 else year
                    return (Date(start_year, month1, int(d1)),
                            Date(year, month2, int(d2)))
                else:
                    start_year = _smart_year(month1, int(d1))
                    end_year = start_year + 1 if month1 > month2 else start_year
                    return (Date(start_year, month1, int(d1)),
                            Date(end_year, month2, int(d2)))
            except ValueError:
                pass

    m = DATE_RANGE_SAME_MONTH.search(text)
    if m:
        d1, d2, mo, yr = m.groups()
        month = _normalize_month(mo)
        if month:
            try:
                year = int(yr)
                return (Date(year, month, int(d1)), Date(year, month, int(d2)))
            except ValueError:
                pass

    m = DATE_RANGE_NO_YEAR.search(text)
    if m:
        d1, d2, mo = m.groups()
        month = _normalize_month(mo)
        if month:
            try:
                year = _smart_year(month, int(d1))
                return (Date(year, month, int(d1)), Date(year, month, int(d2)))
            except ValueError:
                pass

    m = DATE_UNTIL.search(text)
    if m:
        d, mo, yr = m.groups()
        month = _normalize_month(mo)
        if month:
            try:
                year = int(yr) if yr else _smart_year(month, int(d))
                end = Date(year, month, int(d))
                today = Date.today()
                return today, end
            except ValueError:
                pass

    m = DAY_NAME_DATE_YEAR.search(text)
    if m:
        d, mo, yr = m.groups()
        month = _normalize_month(mo)
        if month:
            try:
                return Date(int(yr), month, int(d)), None
            except ValueError:
                pass

    m = DAY_NAME_DATE_NO_YEAR.search(text)
    if m:
        d, mo = m.groups()
        month = _normalize_month(mo)
        if month:
            try:
                year = _smart_year(month, int(d))
                return Date(year, month, int(d)), None
            except ValueError:
                pass

    m = DATE_TWO.search(text)
    if m:
        d1, d2, mo, yr = m.groups()
        month = _normalize_month(mo)
        if month:
            try:
                year = int(yr)
                return (Date(year, month, int(d1)), Date(year, month, int(d2)))
            except ValueError:
                pass

    m = DATE_BARE.search(text)
    if m:
        d, mo, yr = m.groups()
        month = _normalize_month(mo)
        if month:
            try:
                return Date(int(yr), month, int(d)), None
            except ValueError:
                pass

    return None, None


def _extract_time(text: str) -> Optional[str]:
    m = TIME_RE.search(text)
    if m:
        hh = int(m.group(1))
        mm = m.group(2) or "00"
        if 0 <= hh <= 23:
            return f"{hh:02d}:{mm}"
    return None


def _find_card_for_h2(h2: Tag, max_levels: int = 8) -> Optional[Tag]:
    """Walk UP from h2 until reaching an ancestor that contains a
    /spectacle/<slug>/ link, with exactly one distinct slug."""
    el: Optional[Tag] = h2
    for _ in range(max_levels):
        parent = el.parent if el else None
        if parent is None or parent.name in ("html", "body"):
            return None
        el = parent
        # Find /spectacle/ links inside, dedupe by slug
        links = el.select('a[href*="/spectacle/"]')
        distinct = set()
        for a in links:
            slug = _slug_from_href(a.get("href", ""))
            if slug and not slug.endswith("/spectacle"):
                distinct.add(slug)
        if len(distinct) == 1:
            return el
        if len(distinct) >= 2:
            # Walked too high — stop
            return None
        # Zero links: keep walking up
    return None


def fetch() -> List[Event]:
    try:
        resp = requests.get(URL, timeout=20, headers=HEADERS)
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    events: List[Event] = []
    seen_slugs: set = set()
    today = Date.today()

    for h2 in soup.find_all("h2"):
        title = h2.get_text(" ", strip=True)
        if not title or len(title) < 2 or len(title) > 250:
            continue
        if title.lower() in ("toutes nos productions", "saison 2025 2026",
                              "saison 2025/2026", "spectacles passés",
                              "nos productions"):
            continue

        card = _find_card_for_h2(h2)
        if card is None:
            continue

        # Get the slug
        href = None
        for a in card.select('a[href*="/spectacle/"]'):
            h = a.get("href", "")
            if h.startswith("/"):
                h = HOST + h
            slug = _slug_from_href(h)
            if slug and not slug.endswith("/spectacle"):
                href = h
                break
        if not href:
            continue
        slug = _slug_from_href(href)
        if slug in seen_slugs:
            continue

        text = card.get_text(" ", strip=True)
        d_start, d_end = _extract_dates(text)
        if not d_start:
            continue
        if d_start < today and (d_end is None or d_end < today):
            continue

        time_str = _extract_time(text)

        image: Optional[str] = None
        img = card.find("img")
        if img:
            src = img.get("src", "") or ""
            if src.startswith("http"):
                image = src

        seen_slugs.add(slug)
        events.append(Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=title,
            subtitle=None,
            category="théâtre",
            date_start=iso(d_start),
            date_end=iso(d_end) if d_end else None,
            time=time_str,
            url=href.split("?")[0],
            image=image,
        ))

    if not events:
        print("=" * 60, file=sys.stderr)
        print("DIAGNOSTIC: Comédie Odéon — 0 events", file=sys.stderr)
        try:
            resp2 = requests.get(URL, timeout=15, headers=HEADERS)
            soup2 = BeautifulSoup(resp2.text, "html.parser")
            h2s = soup2.find_all("h2")
            print(f"  h2 count: {len(h2s)}", file=sys.stderr)
            for h2 in h2s[:4]:
                title = h2.get_text(' ', strip=True)
                card = _find_card_for_h2(h2)
                if card:
                    text = card.get_text(' ', strip=True)[:200]
                    d_start, d_end = _extract_dates(text)
                    href = None
                    for a in card.select('a[href*="/spectacle/"]'):
                        h = a.get('href', '')
                        slug = _slug_from_href(h)
                        if slug and not slug.endswith('/spectacle'):
                            href = h
                            break
                    print(f"  h2={title[:40]!r} card.tag={card.name}", file=sys.stderr)
                    print(f"    href={href!r}", file=sys.stderr)
                    print(f"    text[:200]={text!r}", file=sys.stderr)
                    print(f"    extracted: {d_start} → {d_end}", file=sys.stderr)
                else:
                    # Walk up showing each level
                    print(f"  h2={title[:40]!r}: no card found", file=sys.stderr)
                    el = h2
                    for lvl in range(5):
                        p = el.parent
                        if p is None:
                            break
                        el = p
                        links = el.select('a[href*="/spectacle/"]') if hasattr(el, 'select') else []
                        distinct = set()
                        for a in links:
                            s = _slug_from_href(a.get('href', ''))
                            if s and not s.endswith('/spectacle'):
                                distinct.add(s)
                        print(f"    lvl{lvl}: <{el.name}> distinct_slugs={len(distinct)}",
                              file=sys.stderr)
        except requests.RequestException as e:
            print(f"  failed: {e}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    events.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, "→", e.date_end or "  -  ", e.time or "", "·", e.title)
