"""Scraper for Le Sonic (sonic-lyon.fr).

Structure verified live (July 2026): the homepage lists every upcoming
event as an <article class="event-card">:

    <article class="event-card">
      <img src="...">
      <span class="event-tag ..."> Club </span>       (or "Live", ...)
      <h2><a href="https://sonic-lyon.fr/evenement/<slug>/">TITLE</a></h2>
      <p class="event-date">lundi 13.07.26</p>        (DD.MM.YY)
      <p class="event-time"> 21:00 - 04:00 </p>       (or "20:00")
      <span class="event-price">8€</span>             (ignored)
    </article>

Two passes:
  1. _scrape_cards   — targeted parse of article.event-card (nominal path).
  2. _scrape_generic — fallback if the theme changes: generic headings+dates
     scan, hardened against the false positives of the previous version
     (price sequences like "8/12/14€" parsed as dates, duplicate events
     from nested container elements).

The site runs WordPress but exposes NO usable REST endpoint (checked
July 2026: /wp/v2/event, /evenement and /concert all 404, /posts returns
an empty list). The old _try_wp_api pass was removed — its "publish date
as event date" fallback could turn blog posts into ghost events.
"""
from typing import List, Optional
from datetime import date as Date
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

# ── Date patterns ──────────────────────────────────────────────────────
# "13 juillet 2026" (named month, explicit year)
DATE_LONG = re.compile(
    r"(\d{1,2})(?:er)?\s+(\w+)\s+(\d{4})",
    re.IGNORECASE,
)
# "13 juil" (short named month, no year — year inferred)
DATE_SHORT_FR = re.compile(
    r"(\d{1,2})(?:er)?\s+(janv|f[eé]vr|mars|avr|mai|juin|juil|ao[uû]t|sept|oct|nov|d[eé]c)",
    re.IGNORECASE,
)
# "13.07.26" / "13.07.2026" — dotted form REQUIRES a year, otherwise a
# version number like "1.5" would become May 1st.
DATE_NUM_DOT = re.compile(
    r"(?<!\d)(?<!\.)(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?!\d)(?!\.\d)"
)
# "13/07" / "13/07/2026" — slashed form, year optional but 4-digit only.
# The lookarounds reject price sequences ("8/12/14€") that the previous
# version happily parsed as dates, producing ghost events.
DATE_NUM_SLASH = re.compile(
    r"(?<!\d)(?<!/)(?<!\.)(\d{1,2})/(\d{1,2})(?:/(\d{4}))?(?!\d)(?!/\d)(?!\.\d)"
)
TIME_RE = re.compile(r"\b(\d{1,2})[h:](\d{2})\b")


def _french_month_num(s: str) -> Optional[int]:
    return FR_MONTHS.get(s.lower())


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
    m = DATE_NUM_DOT.search(text)
    if m:
        d_s, mo_s, y_s = m.groups()
        year = int(y_s)
        if year < 100:
            year += 2000
        try:
            return Date(year, int(mo_s), int(d_s))
        except ValueError:
            pass
    m = DATE_NUM_SLASH.search(text)
    if m:
        d_s, mo_s, y_s = m.groups()
        try:
            d_i, mo_i = int(d_s), int(mo_s)
            if y_s:
                return Date(int(y_s), mo_i, d_i)
            return Date(_smart_year(mo_i, d_i), mo_i, d_i)
        except ValueError:
            pass
    return None


def _parse_time_in_text(text: str) -> Optional[str]:
    """First plausible HH:MM / HHhMM ("21:00 - 04:00" → opening time)."""
    m = TIME_RE.search(text)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"
    return None


def _scrape_cards(soup: BeautifulSoup) -> List[Event]:
    """Nominal path: parse the article.event-card blocks."""
    events: List[Event] = []
    seen_keys: set = set()
    today = Date.today()

    for card in soup.select("article.event-card"):
        link = card.select_one("h2 a[href]")
        if link is None:
            continue
        href = link.get("href", "")
        if href.startswith("/"):
            href = SITE + href
        title = link.get_text(" ", strip=True)
        if not title or len(title) < 2 or len(title) > 250:
            continue

        date_el = card.select_one(".event-date")
        date_text = (date_el.get_text(" ", strip=True) if date_el
                     else card.get_text(" ", strip=True))
        d = _parse_date_in_text(date_text)
        if not d or d < today:
            continue

        time_str: Optional[str] = None
        time_el = card.select_one(".event-time")
        if time_el:
            time_str = _parse_time_in_text(time_el.get_text(" ", strip=True))

        category = "concert"
        tag_el = card.select_one(".event-tag")
        if tag_el and "club" in tag_el.get_text(" ", strip=True).lower():
            category = "club"

        image: Optional[str] = None
        img = card.find("img")
        if img and (img.get("src") or "").startswith("http"):
            image = img["src"]

        key = (href or title.lower(), d.isoformat())
        if key in seen_keys:
            continue
        seen_keys.add(key)

        events.append(Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=title,
            subtitle=None,
            category=category,
            date_start=iso(d),
            date_end=None,
            time=time_str,
            url=href or SITE,
            image=image,
        ))

    return events


def _scrape_generic(soup: BeautifulSoup, page_url: str) -> List[Event]:
    """Fallback if the theme changes: containers/headings + date scan."""
    events: List[Event] = []
    seen_keys: set = set()
    today = Date.today()

    for el in soup.find_all(["article", "li", "h2", "h3"]):
        # Leaf preference: a container whose descendants include another
        # candidate carrying its own date would produce a duplicate of the
        # same event with a diverging title — let the inner one handle it.
        if any(
            _parse_date_in_text(c.get_text(" ", strip=True))
            for c in el.find_all(["article", "li", "h2", "h3"], limit=6)
        ):
            continue

        text = el.get_text(" ", strip=True)
        if not text or len(text) < 6 or len(text) > 500:
            continue
        d = _parse_date_in_text(text)
        if not d or d < today:
            continue

        heading = el.find(["h2", "h3", "h4"])
        if heading:
            title = heading.get_text(" ", strip=True)
        else:
            title = re.split(r"\d", text, maxsplit=1)[0].strip(" -·•|")
        if not title or len(title) < 3:
            continue

        time_str = _parse_time_in_text(text)

        link_el = el.find("a", href=True)
        href = page_url
        if link_el:
            cand = link_el["href"]
            if cand.startswith("http"):
                href = cand
            elif cand.startswith("/"):
                href = SITE + cand

        # Dedup on (title, date) AND, when a real link exists, (href, date):
        # nested variants of the same event share the link even when their
        # extracted titles diverge.
        key_title = (title.lower(), d.isoformat())
        if key_title in seen_keys:
            continue
        seen_keys.add(key_title)
        if link_el:
            key_href = (href, d.isoformat())
            if key_href in seen_keys:
                continue
            seen_keys.add(key_href)

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

    return events


def _diagnose(soup: BeautifulSoup) -> None:
    print("=" * 60, file=sys.stderr)
    print("DIAGNOSTIC: Le Sonic — 0 events", file=sys.stderr)
    cards = soup.select("article.event-card")
    ev_links = soup.select('a[href*="/evenement/"]')
    h2s = soup.find_all("h2")
    print(f"  article.event-card: {len(cards)}, /evenement/ links: "
          f"{len(ev_links)}, h2: {len(h2s)}", file=sys.stderr)
    for h in h2s[:5]:
        print(f"    - {h.get_text(strip=True)[:80]!r}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)


def fetch() -> List[Event]:
    url = SITE + "/"
    resp = requests.get(url, timeout=20, headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    events = _scrape_cards(soup)
    if not events:
        events = _scrape_generic(soup, url)
        if events:
            print(f"[Sonic] event-cards absent — {len(events)} events via "
                  f"generic scan (theme changed?)", file=sys.stderr)
    if not events:
        _diagnose(soup)

    events.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time or "  -  ", "·", e.title, "·", e.url)
