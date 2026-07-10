"""Scraper for Théâtre de la Croix-Rousse (croix-rousse.com).

Page /au-programme/ lists events. Each event card has this structure:
    <li> (or <article>)
      <a href="/au-programme/<slug>/"><img></a>           <!-- image link -->
      <a href="/au-programme/<slug>/"><h3>TITLE</h3></a>  <!-- title link -->
      <p>5 → 7 mai 2026</p>                               <!-- date in sibling -->
      <p>dès 13 ans</p>
      <p>Eva Doumbia</p>
      <a href="/au-programme/<slug>/">En savoir plus</a>
      <a href="...">réserver</a>
    </li>

Critical: the date is in a SIBLING <p> of the link, not inside it. So we
anchor on the title link, then walk UP to the smallest parent that contains
both the link AND a date pattern.
"""
from typing import List, Optional, Tuple
from datetime import date as Date
import re
import sys
import requests
from bs4 import BeautifulSoup, Tag

from .base import Event, iso, FR_MONTHS

VENUE = "Théâtre de la Croix-Rousse"
SLUG = "croix-rousse"
HOST = "https://www.croix-rousse.com"
URL = HOST + "/au-programme/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# "5 → 7 mai 2026" — also accepts "-" or "–"
DATE_RANGE = re.compile(
    r"\b(\d{1,2})\s*[→–\-]\s*(\d{1,2})\s+([\wéèêôû]+)\s+(\d{4})\b",
    re.IGNORECASE,
)
# "lundi 18 mai 2026" or "18 mai 2026"
DATE_SINGLE = re.compile(
    r"\b(\d{1,2})\s+([\wéèêôû]+)\s+(\d{4})\b",
    re.IGNORECASE,
)
# Any date (loose, for finding cards)
ANY_DATE = re.compile(r"\d{1,2}\s+\w+\s+\d{4}", re.IGNORECASE)


def _normalize_month(s: str) -> Optional[int]:
    s = s.lower().rstrip(".")
    s = (s.replace("é", "e").replace("è", "e").replace("ê", "e")
           .replace("ô", "o").replace("û", "u"))
    return FR_MONTHS.get(s)


def _extract_dates(text: str) -> Tuple[Optional[Date], Optional[Date]]:
    m = DATE_RANGE.search(text)
    if m:
        d1, d2, mo, yr = m.groups()
        month = _normalize_month(mo)
        if month:
            try:
                start = Date(int(yr), month, int(d1))
                end = Date(int(yr), month, int(d2))
                if end < start:
                    return start, None
                return start, end
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


def _find_card(link: Tag, target_href: str, max_levels: int = 8) -> Optional[Tag]:
    """Walk up from the link until we find an ancestor that:
    - contains a date pattern in its text,
    - AND does NOT contain links to OTHER /au-programme/ slugs.
    """
    el: Optional[Tag] = link
    for _ in range(max_levels):
        parent = el.parent if el else None
        if parent is None or parent.name in ("html", "body"):
            return None
        el = parent
        text = el.get_text(" ", strip=True)
        if not ANY_DATE.search(text):
            continue
        # Make sure we haven't walked too far (would contain other events)
        other_urls = set()
        for a in el.select('a[href*="/au-programme/"]'):
            href = a.get("href", "")
            if href.startswith("/"):
                href = HOST + href
            # Strip query strings
            href = href.split("?")[0].rstrip("/")
            target_clean = target_href.split("?")[0].rstrip("/")
            if href and href != target_clean:
                # Allow nav menu links to /au-programme/ root
                path_part = href.replace(HOST, "").strip("/")
                if path_part == "au-programme":
                    continue
                other_urls.add(href)
        if not other_urls:
            return el
        # Card is contaminated, give up
        return None
    return None


def _scrape(url: str) -> List[Event]:
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

    # Anchor on h3 (title) elements wrapped in or next to /au-programme/ links
    for a in soup.select('a[href*="/au-programme/"]'):
        href = a.get("href", "")
        if href.startswith("/"):
            href = HOST + href
        href_clean = href.split("?")[0]
        if href_clean in seen_urls:
            continue
        # Filter the path - must be /au-programme/<slug>/
        path_part = href_clean.replace(HOST, "").strip("/")
        parts = [p for p in path_part.split("/") if p]
        if len(parts) != 2 or parts[0] != "au-programme":
            continue
        # Skip festiv·iel and other sub-pages
        if parts[1].lower() in ("festiv·iel", "festivael", "festival"):
            continue

        # Find the card containing this link
        card = _find_card(a, href_clean)
        if card is None:
            continue
        text = card.get_text(" ", strip=True)

        d_start, d_end = _extract_dates(text)
        if not d_start:
            continue
        if d_start < today and (d_end is None or d_end < today):
            continue

        # Title: prefer h3 inside the card
        title_el = card.find(["h3", "h2"])
        if title_el:
            title = title_el.get_text(" ", strip=True)
        else:
            # Fallback: link text itself
            title = a.get_text(" ", strip=True)
        if not title or len(title) < 2 or len(title) > 250:
            continue
        # Skip "En savoir plus" etc.
        if title.lower() in ("en savoir plus", "réserver", "voir plus"):
            continue

        # Subtitle: first non-meta, non-title, non-date text in card
        subtitle: Optional[str] = None
        for tn in card.stripped_strings:
            tn_lower = tn.lower()
            if tn == title:
                continue
            if DATE_RANGE.fullmatch(tn) or DATE_SINGLE.fullmatch(tn):
                continue
            if ANY_DATE.search(tn):
                continue
            if tn_lower.startswith("dès "):
                continue
            if tn_lower in ("en savoir plus", "réserver", "hors les murs",
                            "voir toute la programmation",
                            "quartier libre - jeunesse en création"):
                continue
            if len(tn) < 3 or len(tn) > 200:
                continue
            subtitle = tn
            break

        # Image
        image: Optional[str] = None
        img = card.find("img")
        if img:
            src = img.get("src", "") or ""
            if src.startswith("http"):
                image = src

        seen_urls.add(href_clean)
        events.append(Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=title,
            subtitle=subtitle,
            category="théâtre",
            date_start=iso(d_start),
            date_end=iso(d_end) if d_end else None,
            time=None,
            url=href_clean,
            image=image,
        ))

    return events


def fetch() -> List[Event]:
    all_events: List[Event] = []

    # Main page
    all_events.extend(_scrape(URL))

    # Iterate through coming months to catch events filtered out by default
    today = Date.today()
    for offset in range(0, 14):
        month = today.month + offset
        year = today.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        date_param = f"{year}-{month:02d}-01"
        for season in ("saison-25-26", "saison-26-27"):
            url_with_filter = f"{URL}?paged=1&season={season}&date={date_param}"
            all_events.extend(_scrape(url_with_filter))

    seen, unique = set(), []
    for e in all_events:
        if e.id not in seen:
            seen.add(e.id)
            unique.append(e)

    if not unique:
        print("=" * 60, file=sys.stderr)
        print("DIAGNOSTIC: Croix-Rousse — 0 events", file=sys.stderr)
        try:
            resp = requests.get(URL, timeout=15, headers=HEADERS)
            print(f"  {URL} -> {resp.status_code} ({len(resp.text)} bytes)",
                  file=sys.stderr)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                links = soup.select('a[href*="/au-programme/"]')
                print(f"  /au-programme/ links: {len(links)}", file=sys.stderr)
                # Show distinct slug URLs
                slugs = set()
                for a in links:
                    h = a.get("href", "").split("?")[0]
                    parts = [p for p in h.replace(HOST, "").strip("/").split("/") if p]
                    if len(parts) == 2 and parts[0] == "au-programme":
                        slugs.add(parts[1])
                print(f"  Distinct event slugs: {len(slugs)}", file=sys.stderr)
                for s in list(slugs)[:8]:
                    print(f"    - {s}", file=sys.stderr)
                # Show date matches anywhere on page
                page_text = soup.get_text(" ", strip=True)
                ranges = DATE_RANGE.findall(page_text)
                singles = DATE_SINGLE.findall(page_text)
                print(f"  Date ranges (5 → 7 mai 2026): {len(ranges)}",
                      file=sys.stderr)
                print(f"    First 3: {ranges[:3]}", file=sys.stderr)
                print(f"  Date singles (18 mai 2026): {len(singles)}",
                      file=sys.stderr)
                # h3 count
                h3s = soup.find_all("h3")
                print(f"  h3 count: {len(h3s)}", file=sys.stderr)
                for h in h3s[:5]:
                    print(f"    - {h.get_text(strip=True)[:80]!r}",
                          file=sys.stderr)
        except requests.RequestException as e:
            print(f"  failed: {e}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    unique.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return unique


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, "→", e.date_end or "  -  ", "·", e.title)
