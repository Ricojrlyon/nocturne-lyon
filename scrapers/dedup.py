"""Cross-source event deduplication.

Strategy:
  1. Group by (canonical_venue, date_start)
  2. Within a group of 1: keep as-is.
  3. Within a group of N: fuzzy-cluster by title similarity (>=0.7);
     keep the highest-priority event from each cluster.

Priority is provided by the caller (e.g. venue scrapers = 100,
aggregators like Ville Morte = 50). On ties, prefer the event with
more info (has time, longer subtitle/category).
"""
from __future__ import annotations
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from typing import List, Tuple

from .base import Event


# ============================================================================
# Venue aliases — different spellings of the same physical place
# ============================================================================
#
# canonical_normalized_form: [alternative_normalized_forms]
# Add entries as you spot real duplicates in production.
#
VENUE_ALIASES: dict[str, list[str]] = {
    "periscope":     ["le periscope"],
    "sucre":         ["le sucre"],
    "petit salon":   ["le petit salon"],
    "transbordeur":  ["le transbordeur"],
    "sonic":         ["le sonic"],
    "rayonne":       ["la rayonne", "cco la rayonne", "cco-la rayonne"],
    "subsistances":  ["les subsistances", "les subs"],
    "commune":       ["la commune"],
    "marche gare":   ["marché gare", "la marche gare"],
    "radiant":       ["radiant-bellevue", "radiant bellevue", "le radiant",
                      "le radiant-bellevue"],
    "opera lyon":    ["opera national de lyon", "opera de lyon",
                      "operanational de lyon", "l opera de lyon"],
    "tng":           ["theatre nouvelle generation"],
    "confluences":   ["musee des confluences"],
    "beaux-arts":    ["musee des beaux-arts", "musee des beaux arts"],
    "mac":           ["musee d art contemporain", "musee d'art contemporain"],
    "heat":          ["le heat"],
}

# Build reverse-lookup: alt_form -> canonical
_ALIAS_REVERSE: dict[str, str] = {}
for canonical, alts in VENUE_ALIASES.items():
    _ALIAS_REVERSE[canonical] = canonical
    for alt in alts:
        _ALIAS_REVERSE[alt] = canonical


def _normalize_text(s: str) -> str:
    """Lowercase, strip accents, strip leading articles, collapse punct."""
    s = (s or "").lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    for prefix in ("le ", "la ", "les ", "l'", "l’"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _venue_key(venue: str) -> str:
    """Canonical venue key: normalize + alias lookup."""
    norm = _normalize_text(venue)
    return _ALIAS_REVERSE.get(norm, norm)


def _title_similarity(a: str, b: str) -> float:
    """Fuzzy title similarity in [0, 1].

    Returns 1.0 if one normalized title is contained in the other
    (handles "Soirée Funk" ↔ "Soirée Funk au Périscope" style variations).
    Otherwise returns SequenceMatcher ratio.
    """
    na, nb = _normalize_text(a), _normalize_text(b)
    if not na or not nb:
        return 0.0
    # Substring containment: one fully contains the other (whole-word boundary)
    if len(na) >= 4 and len(nb) >= 4:
        if (f" {na} " in f" {nb} ") or (f" {nb} " in f" {na} "):
            return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def deduplicate(tagged_events: List[Tuple[Event, int]]) -> List[Event]:
    """Deduplicate events across sources.

    Args:
      tagged_events: list of (event, source_priority) tuples.
        Higher priority means more authoritative.

    Returns:
      Deduplicated list of events (in the same relative order as input).
    """
    # Group by (venue_key, date_start)
    groups: dict[tuple[str, str], list[tuple[Event, int]]] = defaultdict(list)
    for ev, prio in tagged_events:
        groups[(_venue_key(ev.venue), ev.date_start)].append((ev, prio))

    result: List[Event] = []
    for key, group in groups.items():
        if len(group) == 1:
            result.append(group[0][0])
            continue

        # Cluster by fuzzy title match (O(n^2) but n is small per group)
        clusters: list[list[tuple[Event, int]]] = []
        for ev, prio in group:
            placed = False
            for cluster in clusters:
                ref_ev = cluster[0][0]
                if _title_similarity(ev.title, ref_ev.title) >= 0.7:
                    cluster.append((ev, prio))
                    placed = True
                    break
            if not placed:
                clusters.append([(ev, prio)])

        # Per cluster: pick highest priority; tie-break on info completeness
        for cluster in clusters:
            best = max(
                cluster,
                key=lambda x: (
                    x[1],                                # priority
                    1 if x[0].time else 0,               # has time
                    len(x[0].subtitle or ""),
                    len(x[0].category or ""),
                )
            )
            result.append(best[0])

    return result
