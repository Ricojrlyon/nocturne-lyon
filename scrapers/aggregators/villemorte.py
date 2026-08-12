"""Scraper for Ville Morte (agenda.villemorte.fr) — uses Gancio API.

Ville Morte runs on Gancio, an open-source decentralized event calendar.
The Gancio API exposes /api/events which returns upcoming events as JSON.

AUCUN filtre éditorial : tout ce que publie l'agenda remonte. Les anciennes
listes de blocage (tags « bouffe » et assimilés, et une douzaine de lieux
écartés à la main) ont été retirées. C'est la déduplication en trois passes
(scrapers/dedup.py) qui écarte les doublons quand un événement est aussi
publié par la salle elle-même.
"""
from __future__ import annotations
from datetime import datetime, date, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo
import re
import requests

from ..base import Event

API_URL = "https://agenda.villemorte.fr/api/events"

# Gancio timestamps are absolute (Unix seconds); render them in the
# venue's local time, NOT the runner's — GitHub Actions runs in UTC,
# which shifted every displayed time by 1-2 hours.
_TZ = ZoneInfo("Europe/Paris")

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
        "User-Agent": "lyon-events-aggregator/1.0 (+https://github.com/Ricojrlyon/nocturne-lyon)",
        "Accept": "application/json",
    }
    resp = requests.get(API_URL, headers=headers, timeout=20)
    resp.raise_for_status()
    items = resp.json() or []

    events: List[Event] = []
    today_iso = date.today().isoformat()

    for item in items:
        # Les tags ne servent plus qu'à déduire une catégorie : plus aucun
        # événement n'est écarté sur ce critère.
        tags = item.get("tags") or []
        # Tags can be either list of strings or list of dicts {tag: "..."}
        tag_names = []
        for t in tags:
            if isinstance(t, str):
                tag_names.append(t)
            elif isinstance(t, dict) and "tag" in t:
                tag_names.append(t["tag"])

        place = item.get("place") or {}
        venue_name_raw = (place.get("name") or "").strip()

        # ----- Date/time parsing -----
        start_ts = item.get("start_datetime")
        if start_ts is None:
            continue
        try:
            dt = datetime.fromtimestamp(int(start_ts), tz=_TZ)
        except (ValueError, OSError, TypeError):
            continue

        date_start = dt.date().isoformat()
        if date_start < today_iso:
            continue  # past event

        time_str = dt.strftime("%H:%M")

        # Multi-day end. A party ending at 02:00 has its end_datetime on the
        # next calendar day but is NOT a 2-day event — only treat the event
        # as multi-day if it actually spans more than ~20 hours.
        date_end = None
        end_ts = item.get("end_datetime")
        if end_ts:
            try:
                end_dt = datetime.fromtimestamp(int(end_ts), tz=_TZ)
                if (end_dt.date() > dt.date()
                        and end_dt - dt > timedelta(hours=20)):
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

        # ----- Image -----
        # L'API Gancio expose media: [{url: "<fichier>", ...}] ; le fichier
        # est servi sur /media/<fichier> et une miniature (~60 Ko, adaptée
        # à la grille) sur /media/thumb/<fichier>.
        image = None
        media = item.get("media") or []
        if media and isinstance(media[0], dict):
            fname = (media[0].get("url") or "").strip()
            if fname and "/" not in fname:
                image = f"https://agenda.villemorte.fr/media/thumb/{fname}"

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
            image=image,
        ))

    return events
