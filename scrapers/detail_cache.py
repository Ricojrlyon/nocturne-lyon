"""Persistent cache for event detail-page times.

Several venue scrapers (Le Sucre, Radiant, HEAT, TNG, Opéra,
Transbordeur) must fetch one detail page per event just to extract the
show time. Night after night those pages are the same — this cache
persists url → time in detail_times.json (committed by the workflow,
same pattern as venue_arrondissements.json) and eliminates ~90% of the
detail requests.

Entry format:
  { "<url>": {"time": "20:30" | null, "fetched_at": "2026-07-12"} }

TTLs:
  - time found    : re-fetched after 30 days (a schedule can change)
  - no time found : re-fetched after 7 days (the venue may publish the
    time later)
  - entries not refreshed for 60 days are pruned on save (past events'
    URLs stop being requested, so they age out naturally)

Rate limiting lives here too: at least 0.4 s between two REAL fetches.
Cache hits don't sleep at all — which is the whole point.
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Callable, Optional

_CACHE_FILE = Path(__file__).parent.parent / "detail_times.json"

_TTL_HIT_DAYS = 30    # entry with a time
_TTL_MISS_DAYS = 7    # entry without a time (retry sooner)
_PRUNE_DAYS = 60      # drop entries not refreshed for this long

_MIN_FETCH_INTERVAL = 0.4  # seconds between two real fetches

_cache: Optional[dict] = None
_dirty = False
_last_fetch_monotonic: Optional[float] = None


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}
        if not isinstance(_cache, dict):
            _cache = {}
    return _cache


def _age_days(entry: dict) -> int:
    try:
        fetched = date.fromisoformat(entry.get("fetched_at", ""))
    except (TypeError, ValueError):
        return 10 ** 6  # unknown age: treat as stale
    return (date.today() - fetched).days


def _is_fresh(entry: dict) -> bool:
    ttl = _TTL_HIT_DAYS if entry.get("time") else _TTL_MISS_DAYS
    return _age_days(entry) <= ttl


def _throttle() -> None:
    """Ensure at least _MIN_FETCH_INTERVAL between two real fetches."""
    global _last_fetch_monotonic
    now = time.monotonic()
    if _last_fetch_monotonic is not None:
        wait = _MIN_FETCH_INTERVAL - (now - _last_fetch_monotonic)
        if wait > 0:
            time.sleep(wait)
    _last_fetch_monotonic = time.monotonic()


def get_time(url: str, fetcher: Callable[[str], Optional[str]]) -> Optional[str]:
    """Return the cached time for url, or fetcher(url) — cached, throttled."""
    global _dirty
    cache = _load()
    entry = cache.get(url)
    if isinstance(entry, dict) and _is_fresh(entry):
        return entry.get("time")
    _throttle()
    t = fetcher(url)
    # Don't overwrite a known time with a one-off miss: the fetcher
    # returns None both on "no time on the page" and on transient network
    # errors — keep the old value and just refresh its date.
    if t is None and isinstance(entry, dict) and entry.get("time"):
        t = entry["time"]
    cache[url] = {"time": t, "fetched_at": date.today().isoformat()}
    _dirty = True
    return t


def get_details(url: str, fetcher: Callable[[str], Optional[dict]],
                fields: tuple = ("time", "image")) -> dict:
    """Multi-field variant of get_time (e.g. {"time": …, "image": …}).

    The fetcher returns a dict of fields (or None on network error).
    An entry is only considered complete when every requested field KEY
    exists (a null value means "checked, not found" and is not re-fetched
    before its TTL) — so entries written by get_time are transparently
    upgraded on the next run. Known values are never overwritten by a
    one-off miss.
    """
    global _dirty
    cache = _load()
    entry = cache.get(url)
    if (isinstance(entry, dict) and _is_fresh(entry)
            and all(f in entry for f in fields)):
        return entry
    _throttle()
    found = fetcher(url) or {}
    new_entry = {"fetched_at": date.today().isoformat()}
    for f in fields:
        v = found.get(f)
        if v is None and isinstance(entry, dict):
            v = entry.get(f)
        new_entry[f] = v
    cache[url] = new_entry
    _dirty = True
    return new_entry


def save_if_dirty(verbose: bool = True) -> None:
    """Write the cache if modified, pruning entries not refreshed recently."""
    global _dirty
    if _cache is None:
        return
    if not _dirty and _CACHE_FILE.exists():
        return
    pruned = {
        url: entry for url, entry in _cache.items()
        if isinstance(entry, dict) and _age_days(entry) <= _PRUNE_DAYS
    }
    _CACHE_FILE.write_text(
        json.dumps(pruned, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _dirty = False
    if verbose:
        print(f"[detail-cache] saved {len(pruned)} entries "
              f"to {_CACHE_FILE.name}")
