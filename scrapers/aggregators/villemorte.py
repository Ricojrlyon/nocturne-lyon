"""Scraper for Ville Morte (agenda.villemorte.fr) — uses Gancio API.

Ville Morte runs on Gancio, an open-source decentralized event calendar.
The Gancio API exposes /api/events which returns upcoming events as JSON.

We filter out food-focused events via a tag blocklist, since the user only
cares about cultural events (concerts, théâtre, expos, etc.) and not
restos/cafés/dégustations.
"""
from __future__ import annotations
from datetime import datetime, date
from typing import List, Optional
import re
import requests

from ..base import Event

API_URL = "https://agenda.villemorte.fr/api/events"

# Tags (case-insensitive, normalized) we exclude entirely. If ANY tag of an
# event matches, the event is dropped. Keep this conservative — bias toward
# letting things through, then tune.
EXCLUDED_TAGS = {
    "bouffe", "restauration", "food", "nourriture",
    "cafe", "brunch", "apero", "aperitif",
    "degustation", "vin", "biere",
    "bar a vin", "bar a vins", "bar a biere", "bar a bieres",
    "repas", "buffet", "diner", "dîner",
    "marché", "marche",
}

# Venues we don't want from Ville Morte. Match is SUBSTRING on the
# normalized venue name (lowercase, no accents, punctuation → space).
# Each pattern is already normalized — add new entries in normalized form.
EXCLUDED_VENUE_PATTERNS = [
    "radio canut",
    "amicale du futur",
    "comete",              # matches "La Comète", "Comète Bar", etc.
    "ens site descartes",
    "ens descartes",
    "librairie",           # all bookstores
    # Added in v34.2
    "rita plage",          # also matches "Rita-Plage"
    "bulle de son",
    "trokson",
    "grandes voisines",    # matches "Les Grandes Voisines"
    "warmaudio",
    "la multi",
    "rontalon",
]


def _norm_tag(t: str) -> str:
    """Normalize a tag or venue name for blocklist comparison.

    Lowercase, strip accents, replace all punctuation with spaces, collapse
    consecutive whitespace. So "Rita-Plage" and "Rita Plage" both become
    "rita plage" and a single pattern matches both.
    """
    import unicodedata
    s = (t or "").lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Map Gancio tags to our internal category buckets (musique/théâtre/danse/etc.)
# The keys are tag patterns (substring match on normalized tags).
TAG_TO_CATEGORY = [
    (("concert", "musique live", "dj", "techno", "rock", "rap", "jazz", "electro",
      "punk", "metal", "hip-hop", "hip hop", "musique"), "musique"),
    (("theatre", "spectacle"), "théâtre"),
    (("danse", "dance"), "danse"),
    (("cinema", "projection", "film"), "cinéma"),
    (("exposition", "expo", "vernissage", "art"), "expo"),
    (("conference", "rencontre", "debat"), "rencontre"),
    (("performance", "lecture"), "performance"),
]


def _category_from_tags(tags: List[str]) -> Optional[str]:
    """Pick a category bucket based on tags."""
    norm = [_norm_tag(t) for t in tags]
    for patterns, cat in TAG_TO_CATEGORY:
        for p in patterns:
            if any(p in t for t in norm):
                return cat
    return None


def _slugify(s: str) -> str:
    """Crude slug: lowercase, accents stripped, non-alphanum → -."""
    import unicodedata
    s = (s or "").lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "unknown"


def fetch() -> List[Event]:
    """Fetch upcoming events from Ville Morte's Gancio API.

    Endpoint behavior: /api/events returns upcoming events as a JSON list.
    Each item has:
      - title (str)
      - slug (str)
      - start_datetime (int — Unix timestamp in seconds)
      - end_datetime (int, optional)
      - place: { name, address, ... }
      - tags: [str]
      - description (str, HTML)
    """
    headers = {
        "User-Agent": "lyon-events-aggregator/1.0 (+https://github.com/Ricojrlyon/lyon-events)",
        "Accept": "application/json",
    }
    resp = requests.get(API_URL, headers=headers, timeout=20)
    resp.raise_for_status()
    items = resp.json() or []

    events: List[Event] = []
    today_iso = date.today().isoformat()

    for item in items:
        # ----- Filtering: food tags -----
        tags = item.get("tags") or []
        # Tags can be either list of strings or list of dicts {tag: "..."}
        tag_names = []
        for t in tags:
            if isinstance(t, str):
                tag_names.append(t)
            elif isinstance(t, dict) and "tag" in t:
                tag_names.append(t["tag"])
        if any(_norm_tag(t) in EXCLUDED_TAGS for t in tag_names):
            continue

        # ----- Filtering: excluded venues -----
        place = item.get("place") or {}
        venue_name_raw = (place.get("name") or "").strip()
        venue_norm = _norm_tag(venue_name_raw)  # reuse norm helper
        if any(p in venue_norm for p in EXCLUDED_VENUE_PATTERNS):
            continue

        # ----- Date/time parsing -----
        start_ts = item.get("start_datetime")
        if start_ts is None:
            continue
        try:
            dt = datetime.fromtimestamp(int(start_ts))
        except (ValueError, OSError, TypeError):
            continue

        date_start = dt.date().isoformat()
        if date_start < today_iso:
            continue  # past event

        time_str = dt.strftime("%H:%M")

        # Multi-day end
        date_end = None
        end_ts = item.get("end_datetime")
        if end_ts:
            try:
                end_dt = datetime.fromtimestamp(int(end_ts))
                if end_dt.date() > dt.date():
                    date_end = end_dt.date().isoformat()
            except (ValueError, OSError, TypeError):
                pass

        # ----- Venue info -----
        venue_name = venue_name_raw or "Inconnu"
        venue_slug = _slugify(venue_name)

        # ----- URL -----
        slug = item.get("slug") or ""
        url = f"https://agenda.villemorte.fr/event/{slug}" if slug else "https://agenda.villemorte.fr/"

        # ----- Title -----
        title = (item.get("title") or "").strip()
        if not title:
            continue

        events.append(Event(
            venue=venue_name,
            venue_slug=venue_slug,
            title=title,
            subtitle=None,
            category=_category_from_tags(tag_names),
            date_start=date_start,
            date_end=date_end,
            time=time_str,
            url=url,
            image=None,
        ))

    return events
