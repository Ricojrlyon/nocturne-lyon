"""Scraper for Marché Gare (marchegare.fr/agenda).

The site is built on Drupal. Each event card on the agenda page is wrapped
in a link to /agenda/<slug>, and contains the date and a category pill.
"""
from typing import List
import re
import requests
from bs4 import BeautifulSoup

from .base import Event, parse_french_date, iso

VENUE = "Marché Gare"
SLUG = "marche-gare"
URL = "https://marchegare.fr/agenda"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def fetch() -> List[Event]:
    resp = requests.get(URL, timeout=20, headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    events: List[Event] = []
    seen = set()

    for a in soup.select('a[href*="/agenda/"]'):
        href = a.get("href", "")
        if not href or href.endswith("/agenda") or href.endswith("/agenda/"):
            continue
        if href.startswith("/"):
            href = "https://marchegare.fr" + href
        if href in seen:
            continue
        seen.add(href)

        text = a.get_text(" ", strip=True)
        # Card text starts with day-of-week marker, e.g. "jeudiJeu. 30. avril04 20:30"
        # Followed by category and title.
        # Find date "30. avril" pattern.
        m = re.search(r"(\d{1,2})\.?\s+(\w+)", text)
        if not m:
            continue
        d = parse_french_date(f"{m.group(1)} {m.group(2)}")
        if not d:
            continue

        # Time: "20:30" or "20h30"
        m_time = re.search(r"(\d{1,2})[h:](\d{2})", text)
        time_str = f"{m_time.group(1):0>2}:{m_time.group(2)}" if m_time else None

        # Strip the leading date/time block to recover title + category.
        # Heuristic: title is everything after the time, before the trailing CTA.
        # Find biggest <span> or text after time.
        # Simpler: take the link's "title" or last visible text chunk.
        title_text = text
        # Cut off everything before the time
        if m_time:
            title_text = text[m_time.end():].strip()
        # Some cards have "Complet" as a status word at the start
        title_text = re.sub(r"^(Complet|Sold out)\s*", "", title_text, flags=re.I)
        # Title is usually all caps or has the category before it
        title = title_text.strip(" -·|")

        if not title:
            continue

        # Image
        img_tag = a.find("img")
        image = None
        if img_tag:
            src = img_tag.get("src") or ""
            # Skip embedded base64 spacers
            if src.startswith("http"):
                image = src

        events.append(Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=title[:140],
            subtitle=None,
            category=None,  # Could be parsed from a <span> with class .field--name-field-genre
            date_start=iso(d),
            date_end=None,
            time=time_str,
            url=href,
            image=image,
        ))

    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time or "  -  ", "·", e.title, "·", e.url)
