"""Scraper for Les Subsistances (les-subs.com/agenda).

Approach: raw-HTML regex matching, associating each date occurrence with
the most recent /evenement/ link that precedes it in document order.

Why raw HTML and not BS4 traversal: the dates are split across multiple
text nodes ("mer. 6 Mai", "|", "18:30" in three different spans).
Walking NavigableStrings individually misses the pattern. A regex on the
flat HTML string with a non-greedy character class between parts catches
the date even when fragmented by tags and whitespace.

Title fallback: most event links wrap an image and have no text. We use
the URL slug (e.g. "prova-wael-ali-simon-dubois" -> "Prova Wael Ali Simon
Dubois"). Imperfect but readable.
"""
from typing import List, Optional, Dict
import re
import sys
import requests

from .base import Event, parse_french_date, iso

VENUE = "Les Subsistances"
SLUG = "les-subs"
URL = "https://www.les-subs.com/agenda/"
HOST = "https://www.les-subs.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# Match an /evenement/<slug>/ link in raw HTML.
LINK_RE = re.compile(
    r'<a\b[^>]*\bhref=["\']([^"\']*?/evenement/[^"\']+?)["\']',
    re.IGNORECASE,
)

# Date with time, allowing whitespace + HTML tags between the date and the time.
# Group 1: day number, Group 2: month name, Group 3: hour, Group 4: minute.
DATE_RE = re.compile(
    r"\b(\d{1,2})\s+(janvier|f[eé]vrier|mars|avril|mai|juin|juillet|"
    r"ao[uû]t|septembre|octobre|novembre|d[eé]cembre)"
    r"[\s\S]{0,400}?\|[\s\S]{0,80}?"
    r"(\d{1,2}):(\d{2})",
    re.IGNORECASE,
)


def _slug_to_title(url: str) -> str:
    """Convert /evenement/some-slug/ to 'Some Slug'."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    # Strip 'ouverture-ete-2026-' prefix that's common on Les Subs URLs
    slug = re.sub(r"^ouverture-ete-\d{4}-", "", slug)
    words = slug.replace("-", " ").split()
    # Title-case words but keep small uppercase tokens (numbers stay)
    return " ".join(w[:1].upper() + w[1:].lower() if w else w for w in words)


def fetch() -> List[Event]:
    resp = requests.get(URL, timeout=20, headers=HEADERS)
    resp.raise_for_status()
    html = resp.text

    # Collect all event link positions in document order.
    link_positions: List[tuple] = []  # (start_pos, normalized_url)
    seen_urls = set()
    for m in LINK_RE.finditer(html):
        url = m.group(1)
        if url.startswith("/"):
            url = HOST + url
        link_positions.append((m.start(), url))
        seen_urls.add(url)

    # For each date match, find the most recent preceding link.
    events_by_url: Dict[str, dict] = {}
    for m in DATE_RE.finditer(html):
        date_pos = m.start()
        day_num, month_str, hour, minute = m.groups()
        # Walk link_positions to find largest start_pos <= date_pos.
        # link_positions is already sorted in document order.
        chosen_url: Optional[str] = None
        for link_pos, link_url in link_positions:
            if link_pos <= date_pos:
                chosen_url = link_url
            else:
                break
        if not chosen_url:
            continue
        info = events_by_url.setdefault(chosen_url, {"dates": set()})
        info["dates"].add((day_num, month_str, hour, minute))

    events: List[Event] = []
    for url, info in events_by_url.items():
        title = _slug_to_title(url)
        if not title or len(title) < 2:
            continue
        for day_num, month_str, hour, minute in info["dates"]:
            d = parse_french_date(f"{day_num} {month_str}")
            if not d:
                continue
            events.append(Event(
                venue=VENUE,
                venue_slug=SLUG,
                title=title,
                subtitle=None,
                category=None,
                date_start=iso(d),
                date_end=None,
                time=f"{int(hour):02d}:{minute}",
                url=url,
                image=None,
            ))

    if not events:
        print("=" * 60, file=sys.stderr)
        print("DIAGNOSTIC: Les Subs scraper found 0 events.", file=sys.stderr)
        print(f"  Total /evenement/ link positions: {len(link_positions)}",
              file=sys.stderr)
        print(f"  Distinct URLs: {len(seen_urls)}", file=sys.stderr)
        date_count = sum(1 for _ in DATE_RE.finditer(html))
        print(f"  Date matches in raw HTML: {date_count}", file=sys.stderr)
        print(f"  events_by_url entries: {len(events_by_url)}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    events.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time, "·", e.title, "·", e.url)
