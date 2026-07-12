"""Scraper for La Rayonne / CCO (larayonne.org/agenda).

The site uses client-side JavaScript to filter events by type — the
`type=24` URL parameter is NOT processed server-side, so a plain HTTP
request always returns ALL event types (concerts, formations, ateliers…).

We therefore apply our own filtering in Python:

  1. TITLE TIME-RANGE RULE (primary, definitive)
     Formation events embed their scheduled hours in the title, e.g.:
       "Comprendre les aides à l'emploi… 18h30 > 20h30"
       "Mettre en place une comptabilité… 17h30 > 20h30"
     Concert titles never contain a "HHh > HHh" pattern.
     → Any title matching  r'\\d{1,2}h\\d*\\s*>' is a formation → skip.

  2. PROFESSIONAL-KEYWORD RULE (secondary belt-and-suspenders)
     Normalised keyword list covering formations even if they drop the
     time range from their title in future seasons.

  3. CARD-TEXT TYPE RULE (tertiary, original logic kept)
     If the surrounding card text explicitly mentions one of the non-
     programmation sections ("rencontres et formations", etc.) → skip.
"""
from __future__ import annotations
from typing import List, Optional
import re
import unicodedata
import requests
from bs4 import BeautifulSoup

from .base import Event, parse_french_date, iso

VENUE = "La Rayonne"
SLUG = "la-rayonne"
# The type=24 parameter targets "Programmation" on the La Rayonne website.
# Even though it is a client-side JS filter, we keep it in the URL so that
# future server-side upgrades would filter for free.
URL = "https://larayonne.org/agenda/?univers=&type=24&saison=&st="
HOST = "https://larayonne.org"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

DATE_RE = re.compile(r"\b(\w+)\.\s+(\d{1,2})\s+(\w+)", re.IGNORECASE)
RANGE_END_RE = re.compile(r"au\s+(\w+)\.\s+(\d{1,2})\s+(\w+)", re.IGNORECASE)
TIME_RE = re.compile(r"(\d{1,2})h(\d{2})?")

CATEGORIES = (
    "Musique", "Théâtre", "Danse", "Humour", "Spectacle", "Rencontre",
    "Cinéma", "Exposition", "Performance",
)

# ── Filter 1: sections in card text that signal a non-concert event type ──
_SKIP_CARD_TYPES = (
    "rencontres et formations",
    "activités et ateliers",
    "mémoires vives",
)

# ── Filter 2: time range embedded in title — definitive formation signal ──
# La Rayonne formats formation titles as "Topic [start]h > [end]h".
# Concerts never embed a HH h > HH h range inside their title.
_TITLE_TIME_RANGE_RE = re.compile(r"\d{1,2}h\d*\s*(?:>|à|->)\s*\d{1,2}h", re.IGNORECASE)

# ── Filter 3: professional/formation keywords in normalised title ──
_FORMATION_KEYWORDS = (
    "emploi dans le spectacle",
    "dossier de diffusion",
    "strategie de diffusion",
    "budget de production",
    "budget et un plan",
    "plan de tresorerie",
    "comptabilite",
    "reseaux sociaux",
    "formaliser sa production",
    "aides a l emploi",
    "obligations des associations",
    "association employeuse",
    "mettre en place une",
    "elaborer un dossier",
    "comprendre les principales",
)

_SKIP_TITLES = (
    "filtre", "la prog", "rencontres et formations",
    "activités et ateliers", "voir tous", "agenda",
)


def _norm(s: str) -> str:
    """Lowercase + strip accents for keyword matching."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    )


def _is_formation(title: str, card_text: str) -> bool:
    """Return True if this event is a professional formation / atelier."""
    # 1. Time range in title (e.g. "18h30 > 20h30") — definitive
    if _TITLE_TIME_RANGE_RE.search(title):
        return True
    # 2. Professional keywords in normalised title
    nt = _norm(title)
    if any(kw in nt for kw in _FORMATION_KEYWORDS):
        return True
    # 3. Explicit section label in card text
    ct = card_text.lower()
    if any(t in ct for t in _SKIP_CARD_TYPES):
        return True
    return False


def _find_card(link, max_levels: int = 6):
    el = link
    for _ in range(max_levels):
        parent = el.parent
        if parent is None or parent.name in ("html", "body"):
            return el
        el = parent
        if DATE_RE.search(el.get_text(" ", strip=True)):
            return el
    return el


def fetch() -> List[Event]:
    resp = requests.get(URL, timeout=20, headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    events: List[Event] = []
    seen_urls: set = set()
    skipped_formations = 0

    for a in soup.select('a[href*="/evenement/"]'):
        href = a.get("href", "")
        if href.startswith("/"):
            href = HOST + href
        if not href.startswith("http"):
            continue
        if href in seen_urls:
            continue

        title = a.get_text(" ", strip=True)
        if not title or len(title) < 3:
            continue
        if any(skip in title.lower() for skip in _SKIP_TITLES):
            continue

        card = _find_card(a)
        card_text = card.get_text(" ", strip=True)

        # Apply all formation filters
        if _is_formation(title, card_text):
            skipped_formations += 1
            continue

        m = DATE_RE.search(card_text)
        if not m:
            continue
        d = parse_french_date(f"{m.group(2)} {m.group(3)}")
        if not d:
            continue

        date_end_iso = None
        m_end = RANGE_END_RE.search(card_text)
        if m_end:
            d_end = parse_french_date(f"{m_end.group(2)} {m_end.group(3)}")
            if d_end:
                date_end_iso = iso(d_end)

        # Extract time — but never from the title (formation titles embed
        # their hours, e.g. "… 18h30 > 20h30", which would pollute the
        # time field). Previous approach card_text.replace(title, "", 1)
        # failed silently when whitespace normalization differed between
        # the <a> text and the ancestor card text; instead, skip any time
        # match whose text also appears in the title.
        time_str: Optional[str] = None
        for m_time in TIME_RE.finditer(card_text):
            if m_time.group(0) in title:
                continue
            hh = int(m_time.group(1))
            mm = m_time.group(2) or "00"
            time_str = f"{hh:02d}:{mm}"
            break

        category: Optional[str] = None
        for kw in CATEGORIES:
            if kw in card_text:
                category = kw.lower()
                break

        image: Optional[str] = None
        for img in card.find_all("img"):
            src = img.get("src") or ""
            if src.startswith("http") and not src.endswith(".svg"):
                image = src
                break

        seen_urls.add(href)
        events.append(Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=title,
            subtitle=None,
            category=category,
            date_start=iso(d),
            date_end=date_end_iso,
            time=time_str,
            url=href,
            image=image,
        ))

    if skipped_formations:
        print(f"[La Rayonne] skipped {skipped_formations} formation/atelier event(s)")

    seen, unique = set(), []
    for e in events:
        if e.id not in seen:
            seen.add(e.id)
            unique.append(e)
    return unique


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time or "  -  ", "·", e.title, "·", e.url)
