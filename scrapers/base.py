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
        """Id for intra-scraper deduplication.

        NOT stable across runs: it hashes `time`, which the dedup passes
        can enrich from another source (a run where the aggregator was
        down yields a different id for the same event). Don't use it as
        a durable external identifier.
        """
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
    "juil": 7, "juill": 7, "juillet": 7,
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
    # Find day number. "(?:er)?" handles the French ordinal "1er mai":
    # \b(\d{1,2})\b alone cannot match it (no word boundary between "1"
    # and "er"), so events on the 1st were silently dropped.
    m_day = re.search(r"\b(\d{1,2})(?:er)?\b", txt)
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


def img_src(img_tag, host: Optional[str] = None) -> Optional[str]:
    """Best usable URL of an <img> tag, lazy-load aware.

    Lazy-loading themes put a placeholder (base64 / svg spacer) in `src`
    and the real file in data-src / data-lazy-src / data-original /
    (data-)srcset — which is why several scrapers extracted 0 images.
    Returns an absolute http(s) URL (relative paths resolved against
    `host` when given), or None.
    """
    if img_tag is None:
        return None

    def _clean(val: str) -> Optional[str]:
        val = (val or "").strip()
        if not val or val.startswith("data:") or val.endswith(".svg"):
            return None
        if val.startswith("http"):
            return val
        if host and val.startswith("/") and not val.startswith("//"):
            return host.rstrip("/") + val
        return None

    # "nitro-lazy-src" : NitroCDN (utilisé par La Commune) met un pixel
    # base64 dans src et la vraie URL dans cet attribut propriétaire.
    for attr in ("data-src", "data-lazy-src", "nitro-lazy-src",
                 "data-original", "src"):
        url = _clean(img_tag.get(attr) or "")
        if url:
            return url
    srcset = (img_tag.get("data-srcset") or img_tag.get("nitro-lazy-srcset")
              or img_tag.get("srcset") or "").strip()
    if srcset:
        first = srcset.split(",")[0].strip().split(" ")[0]
        return _clean(first)
    return None


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
