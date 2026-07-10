"""Common types and helpers for venue scrapers."""
from dataclasses import dataclass, asdict
from datetime import datetime, date
from typing import Optional
import hashlib
import re


@dataclass
class Event:
    """A unified cultural event."""
    venue: str                  # Display name of the venue
    venue_slug: str             # Lowercase slug, e.g. "le-sucre"
    title: str
    subtitle: Optional[str]     # Artist names, sub-info, etc.
    category: Optional[str]     # "concert", "theatre", "club", "expo", etc.
    date_start: str             # ISO date "YYYY-MM-DD"
    date_end: Optional[str]     # ISO date for multi-day events
    time: Optional[str]         # "20:30" if known
    url: str                    # Link back to source page
    image: Optional[str]        # URL of cover image

    @property
    def id(self) -> str:
        """Stable id for deduplication."""
        key = f"{self.venue_slug}|{self.title}|{self.date_start}|{self.time or ''}"
        return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        return d


# French month abbreviations -> month number (1-12).
FR_MONTHS = {
    "janv": 1, "janvier": 1,
    "fevr": 2, "févr": 2, "fevrier": 2, "février": 2,
    "mars": 3,
    "avr": 4, "avril": 4,
    "mai": 5,
    "juin": 6,
    "juil": 7, "juillet": 7,
    "aout": 8, "août": 8,
    "sept": 9, "septembre": 9,
    "oct": 10, "octobre": 10,
    "nov": 11, "novembre": 11,
    "dec": 12, "déc": 12, "decembre": 12, "décembre": 12,
}


def parse_french_date(text: str, default_year: Optional[int] = None) -> Optional[date]:
    """Parse messy French date strings like 'jeu. 30 avr.' or '5 mai 2026'.

    Returns None if no parse is possible.
    """
    if not text:
        return None
    txt = text.lower()
    # Find day number
    m_day = re.search(r"\b(\d{1,2})\b", txt)
    if not m_day:
        return None
    day = int(m_day.group(1))
    # Find month token
    month = None
    for token, num in FR_MONTHS.items():
        # match the token as a whole word (with optional period)
        if re.search(rf"\b{token}\b", txt):
            month = num
            break
    if not month:
        return None
    # Find year, fall back to default
    m_year = re.search(r"\b(20\d{2})\b", txt)
    if m_year:
        year = int(m_year.group(1))
    elif default_year is not None:
        year = default_year
    else:
        # Guess: if month is in the past relative to today, use next year.
        today = date.today()
        if month < today.month or (month == today.month and day < today.day):
            year = today.year + 1
        else:
            year = today.year
    try:
        return date(year, month, day)
    except ValueError:
        return None


def iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def absolutize_url(url: str, host: str) -> str:
    """Make sure a URL is absolute. Handles common forms:
    - "https://..." → returned as-is
    - "//foo.com/..." → prefixed with "https:"
    - "/path" → prefixed with host
    - "path" (relative, no slash) → prefixed with host + "/"
    - "" or None → returned as empty string

    `host` should be a full origin like "https://www.example.com" (no trailing slash).
    """
    if not url:
        return ""
    url = url.strip()
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return host.rstrip("/") + url
    # Pure relative URL — assume it sits at host root
    return host.rstrip("/") + "/" + url
