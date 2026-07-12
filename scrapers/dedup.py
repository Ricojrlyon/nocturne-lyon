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
from datetime import date, timedelta
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
    # Added in v34.3: Bar Rock'n Eat (PB) === Rock'n Eat (Ville Morte)
    "Bar Rock'n Eat":         ["bar rock n eat", "rock n eat", "rocknreat",
                               "bar rock n'eat", "rock n'eat"],
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


# Titles that legitimately recur at several venues on the same day —
# the cross-venue (secondary) pass must NEVER merge them. Compared on
# normalized form (lowercase, accents stripped — see _normalize_text).
GENERIC_TITLES = frozenset({
    "fete de la musique", "concert", "karaoke", "jam session", "atelier",
    "soiree", "projection", "exposition", "vernissage",
})

# Below this length, a title is too generic to be safely merged across
# venues ("Concert", "Karaoké", "Fête de la musique"…).
_MIN_CROSS_VENUE_TITLE_LEN = 15


def _is_unmergeable_across_venues(title: str) -> bool:
    """True if the title is too short or too generic for cross-venue dedup."""
    t = (title or "").strip()
    if len(t) < _MIN_CROSS_VENUE_TITLE_LEN:
        return True
    return _normalize_text(t) in GENERIC_TITLES


def _pick_best(cluster: list[tuple[Event, int]]) -> tuple[Event, int]:
    """Highest priority wins; tie-break on info completeness.

    Then ENRICH the winner with missing fields from the losers in the
    cluster. Identity fields (title, url, venue) stay as the winner's
    (the venue scraper is authoritative for those), but fill-in fields
    (time, category, subtitle, image) get filled from any cluster member
    that has them. This way, if the venue scraper has the event but no
    time, and Petit Bulletin has the same event WITH time, we keep the
    venue scraper's identity but gain the time.
    """
    best = max(
        cluster,
        key=lambda x: (
            x[1],                          # priority
            1 if x[0].time else 0,         # has time
            len(x[0].subtitle or ""),
            len(x[0].category or ""),
        )
    )
    winner_event = best[0]
    # Fields we can safely import from losers
    ENRICHABLE = ("time", "category", "subtitle", "image")
    for field in ENRICHABLE:
        if not getattr(winner_event, field, None):
            for ev, _ in cluster:
                if ev is winner_event:
                    continue
                val = getattr(ev, field, None)
                if val:
                    setattr(winner_event, field, val)
                    break
    return best


def _days_covered(ev: Event, max_days: int = 30) -> List[str]:
    """ISO days covered by the event (date_start..date_end inclusive).

    Multi-day ranges are expanded so that a per-day duplicate from another
    source (e.g. Petit Bulletin emits one event per day of a run) lands in
    the same (venue, day) group as the ranged event, even though their
    date_start differ. Capped at max_days to keep the index bounded for
    long expos.
    """
    if not ev.date_end or ev.date_end <= ev.date_start:
        return [ev.date_start]
    try:
        start = date.fromisoformat(ev.date_start)
        end = date.fromisoformat(ev.date_end)
    except ValueError:
        return [ev.date_start]
    days: List[str] = []
    d = start
    while d <= end and len(days) < max_days:
        days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def _primary_dedup(tagged_events: List[Tuple[Event, int]]) -> List[Tuple[Event, int]]:
    """Group by (canonical_venue, day) then fuzzy-cluster titles >= 0.7.

    Events are indexed on EVERY day of their range (see _days_covered):
    a ranged event thus collides with per-day duplicates whose date_start
    falls inside the range. Because a multi-day event can appear in several
    groups, winners are emitted at most once (tracked by object identity).
    """
    groups: dict[tuple[str, str], list[tuple[Event, int]]] = defaultdict(list)
    for ev, prio in tagged_events:
        for day_iso in _days_covered(ev):
            groups[(_venue_key(ev.venue), day_iso)].append((ev, prio))

    result: List[Tuple[Event, int]] = []
    emitted_ids: set[int] = set()

    def _emit(item: Tuple[Event, int]) -> None:
        if id(item[0]) not in emitted_ids:
            emitted_ids.add(id(item[0]))
            result.append(item)

    for key, group in groups.items():
        if len(group) == 1:
            _emit(group[0])
            continue
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
        for cluster in clusters:
            _emit(_pick_best(cluster))
    return result


def _secondary_dedup(events_with_prio: List[Tuple[Event, int]]) -> List[Tuple[Event, int]]:
    """Cross-venue dedup pass: same date + very high title similarity.

    Catches cases where the same event appears at different venue spellings
    that aren't covered by canonical aliases. Example: "FeFan" listed at
    "Toï Toï le Zinc" in one source vs at "Dans toute la ville" in another.

    Uses a stricter threshold (0.85) than the primary pass to avoid merging
    distinct events that happen to have similar names.
    """
    by_date: dict[str, list[tuple[Event, int]]] = defaultdict(list)
    for ev, prio in events_with_prio:
        by_date[ev.date_start].append((ev, prio))

    result: List[Tuple[Event, int]] = []
    for date_iso, group in by_date.items():
        if len(group) <= 1:
            result.extend(group)
            continue
        clusters: list[list[tuple[Event, int]]] = []
        for ev, prio in group:
            placed = False
            # Short or generic titles ("Concert", "Fête de la musique"…)
            # legitimately recur at several venues the same day — isolate
            # them in their own cluster, never merge across venues.
            if _is_unmergeable_across_venues(ev.title):
                clusters.append([(ev, prio)])
                continue
            for cluster in clusters:
                ref_ev = cluster[0][0]
                if _is_unmergeable_across_venues(ref_ev.title):
                    continue
                if _title_similarity(ev.title, ref_ev.title) >= 0.85:
                    cluster.append((ev, prio))
                    placed = True
                    break
            if not placed:
                clusters.append([(ev, prio)])
        for cluster in clusters:
            result.append(_pick_best(cluster))
    return result


def _tertiary_dedup(events_with_prio: List[Tuple[Event, int]]) -> List[Tuple[Event, int]]:
    """Venue+date count-matching pairing pass.

    Catches duplicates where the SAME event is reported by the venue scraper
    (priority >= 100) with a lineup-style title (e.g. "ARTIST1 + ARTIST2 + ...")
    and by an aggregator (priority < 100) with an event-name title (e.g.
    "Festival X" or "Soirée Y") — titles too different for fuzzy matching.

    Rules at each (venue, date_start):
      * Bucket events: SCRAPER (prio >= 100) vs AGGREGATOR (prio < 100).
      * If one of the buckets is empty: nothing to pair, leave alone.
      * If counts are equal (N scrapers == N aggregators):
          - Sort both by (time or 'zz', title) to align them.
          - Pair them index-by-index.
          - Each scraper wins identity (title, url, venue).
          - Scraper inherits missing fields (time, category, subtitle, image).
          - Aggregator is dropped.
        Time-safety: if ANY aligned pair has two known times more than
        4 hours apart, the pairing is unreliable (e.g. afternoon kids
        show vs evening rock concert) — leave the whole group alone.
      * If counts differ: ambiguous, leave alone.

    Real-world examples this catches in production:
      * Le Transbordeur 2026-05-30: 2 scraper untimed lineups + 2 PB timed
        event names ("Transcendia x Transbo open-air", "23:59 X Organik").
      * HEAT 2026-07-02: "Intérieur Queer : Comedy Club" (scraper) vs
        "IQ comedy club" (PB 18:00).
      * Radiant 06-26/27/28: same "COMPAGNIE DCA / PHILIPPE DECOUFLÉ" (scraper)
        vs "Extra Bal, un karaoké de la danse" (PB) on each of 3 nights.
    """
    SCRAPER_PRIO_MIN = 100  # priorities >= this are venue scrapers

    by_venue_date: dict[tuple[str, str], list[tuple[Event, int]]] = defaultdict(list)
    for ev, prio in events_with_prio:
        by_venue_date[(_venue_key(ev.venue), ev.date_start)].append((ev, prio))

    result: List[Tuple[Event, int]] = []
    for key, group in by_venue_date.items():
        if len(group) < 2:
            result.extend(group)
            continue

        scrapers = [(e, p) for e, p in group if p >= SCRAPER_PRIO_MIN]
        aggs = [(e, p) for e, p in group if p < SCRAPER_PRIO_MIN]

        # Nothing to pair (single source only)
        if not scrapers or not aggs:
            result.extend(group)
            continue

        # Counts must match for a deterministic pairing
        if len(scrapers) != len(aggs):
            result.extend(group)
            continue

        # Pair by sort order: untimed events go last, then alphabetical.
        sort_key = lambda x: (x[0].time or "zz:zz", (x[0].title or "").lower())
        scrapers_sorted = sorted(scrapers, key=sort_key)
        aggs_sorted = sorted(aggs, key=sort_key)

        # Time-safety check on EVERY aligned pair (previously only N == 1):
        # if any pair has two known times more than 4 hours apart, they're
        # probably distinct events (e.g. afternoon kids show vs evening
        # rock concert) and the whole alignment is suspect — leave the
        # group alone rather than merge blindly.
        time_mismatch = any(
            s_ev.time and a_ev.time
            and _time_diff_minutes(s_ev.time, a_ev.time) > 240
            for (s_ev, _), (a_ev, _) in zip(scrapers_sorted, aggs_sorted)
        )
        if time_mismatch:
            result.extend(group)
            continue

        for (s_ev, s_prio), (a_ev, _) in zip(scrapers_sorted, aggs_sorted):
            # Enrich scraper with missing fields from aggregator
            for field in ("time", "category", "subtitle", "image"):
                if not getattr(s_ev, field, None):
                    val = getattr(a_ev, field, None)
                    if val:
                        setattr(s_ev, field, val)
            result.append((s_ev, s_prio))
        # Aggregator events dropped

    return result


def _time_diff_minutes(t1: str, t2: str) -> int:
    """Difference between 'HH:MM' time strings in minutes (circular,
    handles midnight wraparound so 23:30 and 00:30 are 60 minutes apart).
    """
    try:
        h1, m1 = t1.split(':')
        h2, m2 = t2.split(':')
        mins1 = int(h1) * 60 + int(m1)
        mins2 = int(h2) * 60 + int(m2)
    except (ValueError, AttributeError):
        return 99999  # invalid time string treated as very distant
    diff = abs(mins1 - mins2)
    return min(diff, 1440 - diff)


def deduplicate(tagged_events: List[Tuple[Event, int]]) -> List[Event]:
    """Deduplicate events across sources + canonicalize venue display names.

    Three-pass strategy:
      1. PRIMARY — group by (canonical_venue, date_start), fuzzy-cluster
         titles >= 0.7. Catches same-venue duplicates from multiple sources
         when their titles are similar enough.
      2. SECONDARY — group by date_start only, fuzzy-cluster titles >= 0.85.
         Catches cross-venue duplicates (e.g. FeFan reported at Toï Toï by
         one source and at "Dans toute la ville" by another).
      3. TERTIARY — at each (venue, date), if N venue-scraper events ==
         N aggregator events, pair by sort order. Catches duplicates where
         the venue scraper has a lineup-style title ("ARTIST1 + ARTIST2 + ...")
         and the aggregator has an event-name title ("Festival X") — too
         different for fuzzy matching.

    Args:
      tagged_events: list of (event, source_priority) tuples.
        Higher priority means more authoritative.

    Returns:
      Deduplicated list of events, each with its `venue` field rewritten
      to the canonical display name. Order is by group iteration (not sorted).
    """
    primary_result = _primary_dedup(tagged_events)
    secondary_result = _secondary_dedup(primary_result)
    tertiary_result = _tertiary_dedup(secondary_result)
    final = [ev for ev, _ in tertiary_result]
    # Canonicalize venue display names so the frontend doesn't render
    # duplicate chips for "sonic" vs "Le Sonic".
    for e in final:
        e.venue = canonical_venue_name(e.venue)
    return final
