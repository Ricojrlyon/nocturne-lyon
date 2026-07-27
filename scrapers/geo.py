"""Geocoding module for venue → arrondissement resolution.

Uses Nominatim (OpenStreetMap) to resolve venue names to their arrondissement
in Lyon or Villeurbanne. Results are cached in venue_arrondissements.json and
only new venues trigger network requests (1 req/s rate limit enforced).

Cache format (venue_arrondissements.json):
  {
    "Parc de Gerland": {"arr": "7e", "confidence": "high",
                        "checked_at": "2026-07-18", "seen_at": "2026-07-18"},
    "Blue Monday":     {"arr": null,    "confidence": "failed", ...},
    "La Luttine":      {"arr": "Autre", "confidence": "low", ...}
  }

Confidence levels:
  "high"    — postcode matched a Lyon/Villeurbanne arrondissement exactly
  "low"     — city matched but postcode outside Lyon/Villeurbanne
  "failed"  — no usable result from Nominatim (retried after 30 days)
  "skip"    — venue is too generic to geocode (e.g. "Lyon", "Centre-ville")

Dates:
  "checked_at" — when Nominatim was last queried for this venue
  "seen_at"    — last run where the venue still had events (refreshed at
                 most weekly to keep the committed diff quiet). Entries
                 unseen for 180 days are pruned: venues coming and going
                 with the aggregators shouldn't pile up forever.
"""
from __future__ import annotations

import json
import time
from datetime import date
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

_TTL_FAILED_DAYS = 30    # re-tenter un échec de géocodage après 30 jours
_PRUNE_DAYS = 180        # oublier un lieu sans événement depuis 6 mois
_RESTAMP_DAYS = 7        # ne rafraîchir seen_at qu'une fois par semaine


def _age_days(iso_day: Optional[str]) -> Optional[int]:
    """Âge en jours d'une date ISO, ou None si absente/illisible."""
    try:
        return (date.today() - date.fromisoformat(iso_day or "")).days
    except (TypeError, ValueError):
        return None


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


def _search(query: str) -> Optional[list]:
    """One Nominatim request. Returns the JSON result list, or None on a
    transient network error (not cacheable)."""
    try:
        resp = requests.get(
            _NOMINATIM,
            params={
                "q": query,
                "format": "json",
                "addressdetails": 1,
                "limit": 5,
                "countrycodes": "fr",
            },
            headers={"User-Agent": _UA},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        print(f"  [geo] ERROR querying Nominatim for {query!r}: {exc}")
        return None


def _match_lyon(results: list) -> Optional[dict]:
    """Scan results for a Lyon/Villeurbanne match → cache entry, else None."""
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
    return None


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

    results = _search(f"{name}, Lyon, France")
    if results is None:
        return None  # transient network error — not cacheable

    entry = _match_lyon(results)
    if entry:
        return entry

    # La requête « …, Lyon, France » biaise Nominatim contre les lieux de
    # Villeurbanne : seconde tentative avant de conclure à l'échec.
    # (Pause 1,1 s — politique Nominatim 1 req/s.)
    time.sleep(1.1)
    results_v = _search(f"{name}, Villeurbanne, France")
    if results_v:
        entry = _match_lyon(results_v)
        if entry:
            return entry

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
    today = date.today().isoformat()
    dirty = False

    # 1) Marquer les lieux encore vivants (au plus une fois par semaine,
    #    pour ne pas faire bouger le fichier committé chaque nuit).
    for v in venues:
        entry = cache.get(v)
        if not isinstance(entry, dict):
            continue
        age = _age_days(entry.get("seen_at"))
        if age is None or age >= _RESTAMP_DAYS:
            entry["seen_at"] = today
            dirty = True

    # 2) Purger les lieux sans événement depuis _PRUNE_DAYS. Les entrées
    #    héritées (sans seen_at) viennent d'être marquées ci-dessus, elles
    #    ne peuvent donc pas être purgées à tort au premier run.
    expired = [v for v, e in cache.items()
               if isinstance(e, dict)
               and (_age_days(e.get("seen_at")) or 0) > _PRUNE_DAYS]
    for v in expired:
        del cache[v]
    if expired:
        dirty = True
        if verbose:
            print(f"[geo] pruned {len(expired)} venue(s) unseen for "
                  f"{_PRUNE_DAYS}+ days: {', '.join(sorted(expired)[:5])}"
                  + (" …" if len(expired) > 5 else ""))

    # 3) À géocoder : les inconnus, plus les échecs assez vieux pour mériter
    #    une seconde chance (un lieu absent d'OSM peut y être ajouté).
    def _retry_failed(entry: dict) -> bool:
        if entry.get("confidence") != "failed":
            return False
        age = _age_days(entry.get("checked_at"))
        return age is None or age > _TTL_FAILED_DAYS

    to_resolve = [
        v for v in venues
        if v not in known_venues
        and (not isinstance(cache.get(v), dict) or _retry_failed(cache[v]))
    ]

    if not to_resolve:
        if verbose:
            print(f"[geo] all {len(venues)} venues already resolved — no requests needed")
        if dirty:
            _save_cache(cache)
        return cache

    if verbose:
        print(f"[geo] {len(to_resolve)} venue(s) to geocode:")

    for i, venue in enumerate(to_resolve):
        if verbose:
            print(f"  [{i+1}/{len(to_resolve)}] {venue!r} … ", end="", flush=True)

        entry = _geocode_one(venue)
        if entry is None:
            # Transient network error — do not cache, retry next run.
            if verbose:
                print("network error — not cached")
        else:
            entry["checked_at"] = today
            entry["seen_at"] = today
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
