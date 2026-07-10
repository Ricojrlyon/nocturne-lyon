"""Scraper for Le Sonic (sonic-lyon.fr).

The site couldn't be inspected during development (it blocks automated
fetchers from outside GitHub Actions). This scraper tries multiple
common patterns:
1. WordPress REST API endpoints (/wp-json/wp/v2/event etc.)
2. Generic agenda HTML scraping with date pattern matching

If both fail, prints a diagnostic to help iterate.
"""
from typing import List, Optional, Any
from datetime import datetime, date as Date
import re
import sys
import requests
from bs4 import BeautifulSoup

from .base import Event, parse_french_date, iso, FR_MONTHS

VENUE = "Le Sonic"
SLUG = "sonic"
SITE = "https://sonic-lyon.fr"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# Try multiple homepage candidates
PAGE_CANDIDATES = [
    SITE + "/",
    SITE + "/agenda",
    SITE + "/agenda/",
    SITE + "/concerts",
    SITE + "/programmation",
]

WP_ENDPOINTS = [
    "/wp-json/wp/v2/event?per_page=100&_embed=1",
    "/wp-json/wp/v2/evenement?per_page=100&_embed=1",
    "/wp-json/wp/v2/concert?per_page=100&_embed=1",
    "/wp-json/wp/v2/posts?per_page=50&_embed=1",
]

# Date patterns
DATE_LONG = re.compile(
    r"(?:\w+\s+)?(\d{1,2})\s+(\w+)\s+(\d{4})",
    re.IGNORECASE,
)
DATE_NUMERIC = re.compile(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?")
DATE_SHORT_FR = re.compile(
    r"(\d{1,2})\s+(janv|f[eé]vr|mars|avr|mai|juin|juil|ao[uû]t|sept|oct|nov|d[eé]c)",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"\b(\d{1,2})\s*[h:]\s*(\d{2})?")


def _strip_html(html_text: str) -> str:
    if not html_text:
        return ""
    return BeautifulSoup(html_text, "html.parser").get_text(" ", strip=True)


def _french_month_num(s: str) -> Optional[int]:
    return FR_MONTHS.get(s.lower())


def _smart_year(month: int, day: int) -> int:
    today = Date.today()
    try:
        candidate = Date(today.year, month, day)
    except ValueError:
        return today.year
    return today.year + 1 if candidate < today else today.year


def _parse_date_in_text(text: str) -> Optional[Date]:
    m = DATE_LONG.search(text)
    if m:
        d, mo, y = m.groups()
        month = _french_month_num(mo)
        if month:
            try:
                return Date(int(y), month, int(d))
            except ValueError:
                pass
    m = DATE_SHORT_FR.search(text)
    if m:
        d_obj = parse_french_date(f"{m.group(1)} {m.group(2)}")
        if d_obj:
            return d_obj
    m = DATE_NUMERIC.search(text)
    if m:
        d, mo, y = m.groups()
        try:
            d_int = int(d)
            mo_int = int(mo)
            if y:
                return Date(int(y), mo_int, d_int)
            return Date(_smart_year(mo_int, d_int), mo_int, d_int)
        except ValueError:
            pass
    return None


def _try_wp_api() -> List[Event]:
    for endpoint in WP_ENDPOINTS:
        try:
            resp = requests.get(SITE + endpoint, timeout=20, headers=HEADERS)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        try:
            data = resp.json()
        except ValueError:
            continue
        if not isinstance(data, list) or not data:
            continue

        events: List[Event] = []
        today = Date.today()
        for post in data:
            if not isinstance(post, dict):
                continue
            # Try ACF date keys
            acf = post.get("acf") or {}
            d: Optional[Date] = None
            if isinstance(acf, dict):
                for k in ("date_event", "event_date", "date_concert",
                          "date", "date_evenement"):
                    val = acf.get(k)
                    if isinstance(val, str):
                        try:
                            d = Date.fromisoformat(val[:10])
                            break
                        except ValueError:
                            continue
            # Excerpt scan
            if not d:
                excerpt = (post.get("excerpt") or {}).get("rendered", "") if isinstance(
                    post.get("excerpt"), dict) else ""
                d = _parse_date_in_text(_strip_html(excerpt))
            # Fallback to publish date
            if not d:
                v = post.get("date")
                if isinstance(v, str):
                    try:
                        d = datetime.fromisoformat(v.replace("Z", "+00:00")).date()
                    except ValueError:
                        pass
            if not d or d < today:
                continue

            title_field = post.get("title") or ""
            if isinstance(title_field, dict):
                title = _strip_html(title_field.get("rendered", "")).strip()
            else:
                title = _strip_html(str(title_field)).strip()
            if not title:
                continue

            events.append(Event(
                venue=VENUE,
                venue_slug=SLUG,
                title=title,
                subtitle=None,
                category="concert",
                date_start=iso(d),
                date_end=None,
                time=None,
                url=post.get("link") or SITE,
                image=None,
            ))
        if events:
            print(f"[Sonic] {len(events)} events via {endpoint}", file=sys.stderr)
            return events
    return []


def _try_html_scrape() -> List[Event]:
    """Try fetching the homepage / agenda page and scraping headings + dates."""
    for url in PAGE_CANDIDATES:
        try:
            resp = requests.get(url, timeout=20, headers=HEADERS)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        events: List[Event] = []
        today = Date.today()

        # Strategy: look at every heading or article. Find a date in or near it.
        for el in soup.find_all(["h2", "h3", "article", "li"]):
            text = el.get_text(" ", strip=True)
            if not text or len(text) < 6 or len(text) > 500:
                continue
            d = _parse_date_in_text(text)
            if not d or d < today:
                continue

            # Title: try a heading inside, else the first sentence-ish chunk
            heading = el.find(["h2", "h3", "h4"])
            if heading:
                title = heading.get_text(" ", strip=True)
            else:
                # Take first 80 chars before the date
                title = re.split(r"\d", text, maxsplit=1)[0].strip(" -·•|")
            if not title or len(title) < 3:
                continue

            time_str: Optional[str] = None
            m_time = TIME_RE.search(text)
            if m_time:
                hh = int(m_time.group(1))
                mm = m_time.group(2) or "00"
                if 0 <= hh <= 23:
                    time_str = f"{hh:02d}:{mm}"

            link_el = el.find("a", href=True)
            href = url
            if link_el:
                cand = link_el["href"]
                if cand.startswith("http"):
                    href = cand
                elif cand.startswith("/"):
                    href = SITE + cand

            events.append(Event(
                venue=VENUE,
                venue_slug=SLUG,
                title=title[:200],
                subtitle=None,
                category="concert",
                date_start=iso(d),
                date_end=None,
                time=time_str,
                url=href,
                image=None,
            ))

        if events:
            print(f"[Sonic] {len(events)} events via HTML on {url}", file=sys.stderr)
            return events
    return []


def _diagnose():
    print("=" * 60, file=sys.stderr)
    print("DIAGNOSTIC: Le Sonic — 0 events", file=sys.stderr)
    for url in PAGE_CANDIDATES[:3]:
        try:
            resp = requests.get(url, timeout=15, headers=HEADERS)
            print(f"  {url} -> {resp.status_code} ({len(resp.text)} bytes)",
                  file=sys.stderr)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                h2s = soup.find_all("h2")
                links = soup.find_all("a", href=True)
                print(f"    h2: {len(h2s)}, links: {len(links)}", file=sys.stderr)
                for h in h2s[:5]:
                    print(f"      - {h.get_text(strip=True)[:80]!r}",
                          file=sys.stderr)
        except requests.RequestException as e:
            print(f"  {url} -> failed: {e}", file=sys.stderr)
    # Probe wp-json
    try:
        resp = requests.get(SITE + "/wp-json/", timeout=15, headers=HEADERS)
        print(f"  /wp-json/ -> {resp.status_code}", file=sys.stderr)
        if resp.status_code == 200:
            try:
                data = resp.json()
                routes = list((data.get("routes") or {}).keys())
                relevant = [r for r in routes if any(
                    k in r.lower() for k in ("event", "concert", "agenda", "post"))]
                print(f"    relevant routes ({len(relevant)}):", file=sys.stderr)
                for r in relevant[:15]:
                    print(f"      - {r}", file=sys.stderr)
            except ValueError:
                pass
    except requests.RequestException:
        pass
    print("=" * 60, file=sys.stderr)


def fetch() -> List[Event]:
    events = _try_wp_api()
    if not events:
        events = _try_html_scrape()
    if not events:
        _diagnose()
    seen, unique = set(), []
    for e in events:
        if e.id not in seen:
            seen.add(e.id)
            unique.append(e)
    unique.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return unique


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time or "  -  ", "·", e.title, "·", e.url)
