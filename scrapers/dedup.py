"""Cross-source event deduplication + venue name canonicalization.

Strategy:
  1. Group by (canonical_venue, date_start)
  2. Within a group of 1: keep as-is.
  3. Within a group of N: fuzzy-cluster by title similarity (>=0.7);
     keep the highest-priority event from each cluster.
  4. Rewrite each surviving event's `venue` field to its canonical display
     name, so the frontend doesn't show duplicate chips (e.g. "Le Sonic"
     from a venue scraper alongside "sonic" from Ville Morte).

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
# Canonical venue names
# ============================================================================
#
# Format: canonical_display_name -> list of normalized alternative forms
# (lowercase, no accents, no leading articles).
#
# After deduplication, every surviving event's `venue` field is rewritten
# to the canonical display name. This is what guarantees that the frontend
# (which does exact-string matching on `e.venue`) sees ONE chip per real
# venue, no matter how many sources spell it differently.
#
# Add entries here as you spot variations in production logs.
#
VENUE_CANONICAL: dict[str, list[str]] = {
    # === Venues we scrape directly ===
    "Le Périscope":           ["periscope"],
    "Le Sucre":               ["sucre"],
    "Le Sonic":               ["sonic"],
    "Le Petit Salon":         ["petit salon"],
    "Le Transbordeur":        ["transbordeur"],
    "La Rayonne":             ["rayonne", "cco la rayonne", "cco-la rayonne",
                               "cco rayonne"],
    "Les Subsistances":       ["subsistances", "subs"],
    "La Commune":             ["commune"],
    "Marché Gare":            ["marche gare"],
    "Radiant-Bellevue":       ["radiant", "radiant bellevue"],
    "Opéra national de Lyon": ["opera lyon", "opera national de lyon",
                               "opera de lyon"],
    "TNG":                    ["tng", "theatre nouvelle generation"],
    "HEAT":                   ["heat"],
    "La Halle Tony Garnier":  ["halle tony garnier", "halle tony-garnier"],
    "Bourse du Travail":      ["bourse du travail"],
    # === New venues from aggregators (canonical names) ===
    "Toï Toï le Zinc":        ["toi toi le zinc", "toi toi", "toitoi"],
    "Grrrnd Zero":            ["grrrnd zero", "grrnd zero", "grrrnd-zero",
                               "grrrnd zero fort"],
    "L'Épicerie Moderne":     ["epicerie moderne"],
    # Added in v34.2 — venues seen in Ville Morte we want in specific groups
    "A Thou Bout d'Chant":    ["a thou bout d chant", "thou bout d chant",
                               "a thoubout d chant"],
    "Boskop":                 ["boskop"],
    "Maison de l'écologie":   ["maison de l ecologie",
                               "maison ecologie",
                               "maison de lecologie"],
    "Agend'arts":             ["agend arts", "agendarts"],
    "Big White":              ["big white"],
}

# Build reverse lookup: normalized_form -> canonical_display
_CANONICAL_LOOKUP: dict[str, str] = {}


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


# Initialize the reverse lookup after _normalize_text is defined
for _canonical, _alts in VENUE_CANONICAL.items():
    _norm_canonical = _normalize_text(_canonical)
    _CANONICAL_LOOKUP[_norm_canonical] = _canonical
    for _alt in _alts:
        _CANONICAL_LOOKUP[_alt] = _canonical


def canonical_venue_name(venue_str: str) -> str:
    """Return the canonical display name for a venue, or the input unchanged.

    Examples:
      "sonic"           -> "Le Sonic"
      "Le Sonic"        -> "Le Sonic"
      "LE PERISCOPE"    -> "Le Périscope"
      "Toï toï"         -> "Toï Toï le Zinc"
      "Inconnu Random"  -> "Inconnu Random"  (no canonical form known)
    """
    if not venue_str:
        return venue_str
    norm = _normalize_text(venue_str)
    return _CANONICAL_LOOKUP.get(norm, venue_str)


def _venue_key(venue: str) -> str:
    """Canonical key for grouping — same canonical form, normalized.

    Two venues that share a canonical display name get the same key here,
    which is what makes events from different sources cluster together.
    """
    return _normalize_text(canonical_venue_name(venue))


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
    """Deduplicate events across sources + canonicalize venue display names.

    Args:
      tagged_events: list of (event, source_priority) tuples.
        Higher priority means more authoritative.

    Returns:
      Deduplicated list of events, each with its `venue` field rewritten
      to the canonical display name. Order is by group iteration (not sorted).
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

    # Final pass: canonicalize each surviving event's venue display name
    # so the frontend doesn't render duplicate chips for "sonic" vs "Le Sonic".
    for e in result:
        e.venue = canonical_venue_name(e.venue)

    return result
