"""Scraper for La Halle Tony Garnier (halle-tony-garnier.com).

Page structure (verified July 2026):
- Each event is an <a href="/fr/programmation/<slug>">.
- Inner text: "TITLE TITLE DD.MM HHhMM"  (title duplicated — once from img
  alt, once from the visible span; date WITHOUT year; time "HHhMM").
- Some events span multiple days: "DD.MM au DD.MM" (year also implicit).

Change vs original scraper:
- Dates are now DD.MM (no year) instead of DD.MM.YY. Year is inferred:
  if the date is already past in the current year, use next year.
- Titles appear twice in the link text; we strip date/time then
  deduplicate adjacent repetitions.
"""
from __future__ import annotations
from typing import List, Optional
from datetime import date as Date
import re
import sys
import requests
from bs4 import BeautifulSoup

from .base import Event, img_src, iso

VENUE = "La Halle Tony Garnier"
SLUG = "halle-tony-garnier"
HOST = "https://www.halle-tony-garnier.com"
URLS = [HOST + "/fr/programmation", HOST + "/"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# DD.MM  or  DD.MM.YY  (year optional)
_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})(?:\.(\d{2}))?\b")
# DD.MM [.YY]  au  DD.MM [.YY]  — la partie "au …" est obligatoire :
# les dates simples sont gérées par _DATE_RE.
_RANGE_RE = re.compile(
    r"\b(\d{2})\.(\d{2})(?:\.(\d{2}))?\s+au\s+(\d{2})\.(\d{2})(?:\.(\d{2}))?",
    re.IGNORECASE,
)
# HHhMM  or  HHh
_TIME_RE = re.compile(r"\b(\d{1,2})h(\d{2})?\b")


def _infer_year(month: int, day: int) -> int:
    """Return the nearest future year for a DD.MM date."""
    today = Date.today()
    year = today.year
    try:
        d = Date(year, month, day)
        return year if d >= today else year + 1
    except ValueError:
        return year + 1


def _parse_date(dd: str, mm: str, yy: Optional[str]) -> Optional[Date]:
    m, d = int(mm), int(dd)
    year = (2000 + int(yy)) if yy else _infer_year(m, d)
    try:
        return Date(year, m, d)
    except ValueError:
        return None


def _extract_title(raw: str) -> str:
    """Strip date/time from link text and de-duplicate the title.

    The page renders each link as "TITLE TITLE DD.MM HHhMM"; after removing
    date and time tokens the title is left doubled ("TITLE TITLE"). We detect
    and collapse that repetition.
    """
    cleaned = _DATE_RE.sub(" ", raw)
    cleaned = _TIME_RE.sub(" ", cleaned)
    # Remove stray 'au' left by range dates
    cleaned = re.sub(r"\bau\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Collapse adjacent duplication: "FOO BAR FOO BAR" → "FOO BAR"
    words = cleaned.split()
    n = len(words)
    for half in range(n // 2, 0, -1):
        if words[:half] == words[half : half * 2]:
            return " ".join(words[:half]).strip()

    return cleaned


def _title_case(s: str) -> str:
    """Convert ALL-CAPS titles to Title Case for readability."""
    if not s:
        return s
    # Only convert if entirely uppercase (ignore numbers / symbols)
    letters = [c for c in s if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return s.title()
    return s


def _scrape_url(url: str) -> List[Event]:
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

    for a in soup.select('a[href*="/fr/programmation/"]'):
        href = a.get("href", "")
        if href.startswith("/"):
            href = HOST + href
        # Skip the listing page itself
        if href.rstrip("/") == HOST + "/fr/programmation":
            continue
        if href in seen_urls:
            continue

        raw_text = a.get_text(" ", strip=True)

        # ── Parse date(s) ──────────────────────────────────────────────
        d_start: Optional[Date] = None
        d_end: Optional[Date] = None

        # Try range first ("DD.MM au DD.MM" or "DD.MM.YY au DD.MM.YY")
        m_range = _RANGE_RE.search(raw_text)
        if m_range:
            d1, m1, y1, d2, m2, y2 = m_range.groups()
            d_start = _parse_date(d1, m1, y1 or y2)
            d_end   = _parse_date(d2, m2, y2)
        else:
            m_single = _DATE_RE.search(raw_text)
            if m_single:
                dd, mm, yy = m_single.groups()
                d_start = _parse_date(dd, mm, yy)

        if not d_start:
            continue
        # Drop events fully in the past (allow ongoing ranges)
        if d_start < today and (not d_end or d_end < today):
            continue

        # ── Extract title ───────────────────────────────────────────────
        title = _title_case(_extract_title(raw_text))
        if not title or len(title) < 2 or len(title) > 200:
            continue
        # Skip tokens that look like a bare date that wasn't stripped
        if _DATE_RE.fullmatch(title.strip()):
            continue

        # ── Extract time ────────────────────────────────────────────────
        time_str: Optional[str] = None
        m_time = _TIME_RE.search(raw_text)
        if m_time:
            hh = int(m_time.group(1))
            mm_s = m_time.group(2) or "00"
            if 0 <= hh <= 23:
                time_str = f"{hh:02d}:{mm_s}"

        # ── Extract image (lazy-load aware) ────────────────────────────
        image = img_src(a.find("img"), host=HOST)

        seen_urls.add(href)
        events.append(Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=title,
            subtitle=None,
            category="concert",
            date_start=iso(d_start),
            date_end=iso(d_end) if d_end else None,
            time=time_str,
            url=href,
            image=image,
        ))

    return events


def fetch() -> List[Event]:
    all_events: List[Event] = []
    for url in URLS:
        events = _scrape_url(url)
        all_events.extend(events)
        if events:
            break  # First URL with results wins

    # Deduplicate
    seen, unique = set(), []
    for e in all_events:
        if e.id not in seen:
            seen.add(e.id)
            unique.append(e)

    if not unique:
        # Diagnostic output to help debug future breakage
        print("=" * 60, file=sys.stderr)
        print("DIAGNOSTIC: Halle Tony Garnier — 0 events", file=sys.stderr)
        for url in URLS:
            try:
                resp = requests.get(url, timeout=15, headers=HEADERS)
                print(f"  {url} -> {resp.status_code} ({len(resp.text)} bytes)",
                      file=sys.stderr)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    links = soup.select('a[href*="/fr/programmation/"]')
                    print(f"    /fr/programmation/ links: {len(links)}",
                          file=sys.stderr)
                    for a in links[:5]:
                        t = a.get_text(" ", strip=True)[:80]
                        print(f"      - {a.get('href','')!r} | {t!r}",
                              file=sys.stderr)
            except requests.RequestException as exc:
                print(f"  {url} -> failed: {exc}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    unique.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return unique


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time or "  -  ", "·", e.title, "·", e.url)
