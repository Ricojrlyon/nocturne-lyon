"""Geocoding module for venue → arrondissement resolution.

Uses Nominatim (OpenStreetMap) to resolve venue names to their arrondissement
in Lyon or Villeurbanne. Results are cached in venue_arrondissements.json and
only new venues trigger network requests (1 req/s rate limit enforced).

Cache format (venue_arrondissements.json):
  {
    "Parc de Gerland": {"arr": "7e",   "confidence": "high"},
    "Blue Monday":     {"arr": null,   "confidence": "failed"},
    "La Luttine":      {"arr": "Autre","confidence": "low"}
  }

Confidence levels:
  "high"    — postcode matched a Lyon/Villeurbanne arrondissement exactly
  "low"     — city matched but postcode outside Lyon/Villeurbanne
  "failed"  — no usable result from Nominatim
  "skip"    — venue is too generic to geocode (e.g. "Lyon", "Centre-ville")
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import requests

_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_UA = (
    "nocturne-lyon-events/1.0 geocoder "
    "(+https://github.com/Ricojrlyon/nocturne-lyon)"
)

# Lyon arrondissements by postcode
_POSTCODE_ARR: dict[str, str] = {
    "69001": "1er", "69002": "2e", "69003": "3e",
    "69004": "4e",  "69005": "5e", "69006": "6e",
    "69007": "7e",  "69008": "8e", "69009": "9e",
    "69100": "Villeurbanne",
}

# Venue names that are too generic to geocode reliably — skip them.
_SKIP_NAMES = frozenset({
    "lyon", "villeurbanne", "france", "centre-ville",
    "divers", "various", "online", "en ligne",
    # single words that hit city-level results
    "parc", "place", "rue", "salle",
})

# File written alongside events.json
_CACHE_FILE = Path(__file__).parent.parent / "venue_arrondissements.json"


def _load_cache() -> dict[str, dict]:
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict[str, dict]) -> None:
    _CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _geocode_one(name: str) -> Optional[dict]:
    """Query Nominatim for a single venue.

    Returns a cache entry dict, or None on a transient network error —
    in that case the caller must NOT cache the result, so the venue is
    retried on the next run. ("failed" is reserved for a definitive
    no-result answer from Nominatim.)
    """
    # Skip venues too generic to geocode
    if name.lower().strip() in _SKIP_NAMES or len(name.strip()) <= 3:
        return {"arr": None, "confidence": "skip"}

    try:
        resp = requests.get(
            _NOMINATIM,
            params={
                "q": f"{name}, Lyon, France",
                "format": "json",
                "addressdetails": 1,
                "limit": 5,
                "countrycodes": "fr",
            },
            headers={"User-Agent": _UA},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
    except Exception as exc:
        print(f"  [geo] ERROR querying Nominatim for {name!r}: {exc}")
        return None  # transient network error — not cacheable

    for r in results:
        addr = r.get("address", {})
        postcode = addr.get("postcode", "").strip()
        # Nominatim uses "city", "town", or "village" depending on result type
        city = (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("municipality")
            or ""
        ).lower().strip()

        # Must be in Lyon or Villeurbanne
        if city not in ("lyon", "villeurbanne"):
            continue

        arr = _POSTCODE_ARR.get(postcode)
        if arr:
            return {"arr": arr, "confidence": "high"}

        if city == "villeurbanne":
            return {"arr": "Villeurbanne", "confidence": "high"}

        # City matched but postcode not a Lyon/Villeurbanne one — nearby suburb
        commune = addr.get("city") or addr.get("town") or "Autre"
        return {"arr": "Autre", "confidence": "low", "commune": commune}

    # No Lyon/Villeurbanne result found — mark as failed so we don't retry
    # every run, but store commune if we got anything at all
    if results:
        addr = results[0].get("address", {})
        commune = (
            addr.get("city") or addr.get("town") or addr.get("village") or ""
        )
        if commune and commune.lower() not in ("lyon", "villeurbanne"):
            # It's a real venue but outside Lyon metro
            return {"arr": "Autre", "confidence": "low", "commune": commune}

    return {"arr": None, "confidence": "failed"}


def resolve_new_venues(
    venues: list[str],
    *,
    known_venues: Optional[set[str]] = None,
    verbose: bool = True,
) -> dict[str, dict]:
    """Geocode any venues not already in the cache.

    Args:
        venues: all unique venue names seen in the current run.
        known_venues: optional set of venues already hardcoded in the
            frontend (VENUE_ARRONDISSEMENT). These are skipped even if
            absent from the cache — no point re-resolving them.
        verbose: print progress to stdout.

    Returns:
        The full updated cache dict (venue → {arr, confidence, ...}).
    """
    cache = _load_cache()
    known_venues = known_venues or set()

    to_resolve = [
        v for v in venues
        if v not in cache
        and v not in known_venues
    ]

    if not to_resolve:
        if verbose:
            print(f"[geo] all {len(venues)} venues already resolved — no requests needed")
        return cache

    if verbose:
        print(f"[geo] {len(to_resolve)} new venue(s) to geocode:")

    for i, venue in enumerate(to_resolve):
        if verbose:
            print(f"  [{i+1}/{len(to_resolve)}] {venue!r} … ", end="", flush=True)

        entry = _geocode_one(venue)
        if entry is None:
            # Transient network error — do not cache, retry next run.
            if verbose:
                print("network error — not cached")
        else:
            cache[venue] = entry
            if verbose:
                arr = entry.get("arr") or "?"
                conf = entry.get("confidence", "?")
                commune = entry.get("commune", "")
                extra = f" ({commune})" if commune else ""
                print(f"{arr}{extra}  [{conf}]")

        # Nominatim rate limit: 1 req/sec
        if i < len(to_resolve) - 1:
            time.sleep(1.1)

    _save_cache(cache)
    if verbose:
        print(f"[geo] saved {len(cache)} entries to {_CACHE_FILE.name}")

    return cache
