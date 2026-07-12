"""Aggregator: run all venue scrapers and write a unified events.json.

Each scraper returns a list of Event objects. Failures in one venue do NOT
abort the run — the bad venue is skipped, the others succeed. This is
critical: in a daily cron job, if one venue's HTML changes you don't want
the whole pipeline to break.

v34 changes:
  - Removed Célestins, TNP, Croix-Rousse, Comédie Odéon (theatres dropped)
  - Added Ville Morte as an aggregator (cross-venue source)
  - Cross-source deduplication: when the same event is reported by both a
    venue scraper and an aggregator, the venue scraper wins (it's
    authoritative). See scrapers/dedup.py.

Theatre policy (juillet 2026):
  - TNG est le seul théâtre scrappé en direct.
  - Petit Bulletin : catégories Théâtre et lieux « théâtre » bloqués,
    SAUF le lieu « Théâtres romains de Fourvière » (concerts, pas de
    pièces) — voir ALLOWED_VENUES dans petit_bulletin.py.
  - Ville Morte : aucun blocage lié au théâtre.
"""
from __future__ import annotations
import json
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, List

from scrapers import Event
from scrapers import (
    le_sucre, les_subs, marche_gare, radiant, la_rayonne, transbordeur,
    petit_salon, sonic, periscope, la_commune,
    heat, halle_tony_garnier,
    opera_lyon, tng,
    bourse_du_travail,
)
from scrapers.aggregators import villemorte, petit_bulletin
from scrapers.dedup import deduplicate
from scrapers.detail_cache import save_if_dirty as save_detail_cache
from scrapers.geo import resolve_new_venues

# Venue-specific scrapers — priority 100 (authoritative for their venue).
# Each entry is (display_name, callable returning List[Event]).
SCRAPERS: list[tuple[str, Callable[[], List[Event]]]] = [
    ("Le Sucre",                le_sucre.fetch),
    ("Les Subsistances",        les_subs.fetch),
    ("Marché Gare",             marche_gare.fetch),
    ("Radiant-Bellevue",        radiant.fetch),
    ("La Rayonne",              la_rayonne.fetch),
    ("Le Transbordeur",         transbordeur.fetch),
    ("Le Petit Salon",          petit_salon.fetch),
    ("Le Sonic",                sonic.fetch),
    ("Le Périscope",            periscope.fetch),
    ("La Commune",              la_commune.fetch),
    ("HEAT",                    heat.fetch),
    ("La Halle Tony Garnier",   halle_tony_garnier.fetch),
    ("Opéra de Lyon",           opera_lyon.fetch),
    ("TNG",                     tng.fetch),
    ("Bourse du Travail",       bourse_du_travail.fetch),
]

# Aggregators — priority lower than venue scrapers (lose against them on
# duplicates). Among themselves, higher priority wins.
# Each entry is (display_name, callable, priority).
AGGREGATORS: list[tuple[str, Callable[[], List[Event]], int]] = [
    ("Petit Bulletin",          petit_bulletin.fetch, 60),
    ("Ville Morte",             villemorte.fetch,     50),
]


def main() -> int:
    # Each event is tagged with a (source) priority for deduplication.
    all_tagged: list[tuple[Event, int]] = []
    report = []

    # 1) Venue-specific scrapers
    for name, fn in SCRAPERS:
        try:
            events = fn()
            for e in events:
                all_tagged.append((e, 100))
            report.append((name, len(events), None))
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc(limit=2)
            report.append((name, 0, f"{type(e).__name__}: {e}"))
            print(f"[FAIL] {name}: {tb}", file=sys.stderr)

    # 2) Aggregators (multi-venue sources)
    for name, fn, prio in AGGREGATORS:
        try:
            events = fn()
            for e in events:
                all_tagged.append((e, prio))
            report.append((name, len(events), None))
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc(limit=2)
            report.append((name, 0, f"{type(e).__name__}: {e}"))
            print(f"[FAIL aggregator] {name}: {tb}", file=sys.stderr)

    # 2.5) Persist the detail-page time cache (url → time), committed by
    # the workflow like venue_arrondissements.json. Without this save,
    # every run would re-fetch the same detail pages from scratch.
    save_detail_cache()

    # 3) Drop past events. An event is upcoming as long as it hasn't ENDED:
    # keep ongoing runs (date_start in the past but date_end today or later,
    # e.g. multi-day shows) — several scrapers preserve those on purpose and
    # the frontend knows how to render them.
    today_iso = date.today().isoformat()
    upcoming_tagged = [
        (e, p) for e, p in all_tagged
        if e.date_start and (e.date_end or e.date_start) >= today_iso
    ]

    # 4) Sanity-check URLs. Any event whose URL is not absolute (http/https)
    # gets logged and replaced with the empty string — which the frontend
    # treats as "no link" rather than rendering a relative href that would
    # 404 on GitHub Pages. We do NOT drop such events; their info is still
    # useful even without a clickable source link.
    bad_urls = 0
    for e, _ in upcoming_tagged:
        if not e.url or not (e.url.startswith("http://")
                             or e.url.startswith("https://")):
            print(f"[URL!] {e.venue} — non-absolute url for {e.title!r}: "
                  f"{e.url!r}", file=sys.stderr)
            e.url = ""
            bad_urls += 1
    if bad_urls:
        print(f"[URL!] {bad_urls} event(s) had non-absolute URLs — cleared.",
              file=sys.stderr)

    # 5) Sanity-check titles. Events with empty/missing title are silently
    # dropped (they would render as visually empty cards in the UI).
    bad_titles = 0
    clean_tagged = []
    for e, p in upcoming_tagged:
        if not e.title or not e.title.strip():
            print(f"[TITLE!] {e.venue} — empty title for event on {e.date_start} "
                  f"(url: {e.url!r}) — dropping",
                  file=sys.stderr)
            bad_titles += 1
            continue
        clean_tagged.append((e, p))
    if bad_titles:
        print(f"[TITLE!] {bad_titles} event(s) had empty titles — dropped.",
              file=sys.stderr)
    upcoming_tagged = clean_tagged

    # 6) Cross-source deduplication. Groups events by (venue, date), then
    # fuzzy-matches titles within each group. On duplicates, keeps the
    # highest-priority source.
    before = len(upcoming_tagged)
    unique = deduplicate(upcoming_tagged)
    print(f"\n[dedup] {before} candidates → {len(unique)} unique "
          f"(-{before - len(unique)})")

    # 7) Sort by date then time then venue.
    unique.sort(key=lambda e: (e.date_start, e.time or "00:00", e.venue))


    # 8) Geocode any new venues not already in the frontend's hardcoded
    #    VENUE_ARRONDISSEMENT map. Results are cached in
    #    venue_arrondissements.json. Only truly new venues trigger HTTP
    #    requests (1 req/sec). The frontend merges this file with its
    #    hardcoded map at load time (hardcoded entries win on conflict).
    all_venues = list({e.venue for e in unique})
    # Venues already hardcoded in index.html — no need to geocode.
    FRONTEND_HARDCODED = {
        "Opéra national de Lyon", "Les Subsistances", "Musée des Beaux-Arts",
        "A Thou Bout d'Chant", "Maison de l'écologie", "La Salle de Bains",
        "Alternatibar", "Kraspek Myzik", "Le Bec de Jazz", "Hot Club",
        "Salle Rameau", "Le Sucre", "Marché Gare", "Le Périscope",
        "Chapelle de la Trinité", "Musée des Confluences", "Goethe-Institut",
        "Galerie Henri Chartier", "Comédie Odéon", "Fnac Bellecour",
        "Bourse du Travail", "Auditorium de Lyon", "La Marquise",
        "Agend'arts", "Musées Gadagne", "Salle Molière", "Le Sonic",
        "Big White", "Musée d'Art Contemporain", "La Halle Tony Garnier",
        "La Commune", "Le Petit Salon", "Boskop", "Galerie Roger Tator",
        "La Boulangerie du Prado", "Le Ninkasi", "Le 6e Continent",
        "Maison de la Danse", "HEAT", "Institut Lumière", "TNG",
        "Bar Rock'n Eat", "Le Transbordeur", "La Rayonne",
        "Toï Toï le Zinc", "Café Nanoum", "Radiant-Bellevue", "LDLC Arena",
        "L'Épicerie Moderne", "Grrrnd Zero", "La Machinerie - Bizarre !",
        "Domaine de Lacroix-Laval", "Espace Gerson",
    }
    resolve_new_venues(all_venues, known_venues=FRONTEND_HARDCODED, verbose=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(unique),
        "events": [e.to_dict() for e in unique],
    }

    out = Path(__file__).parent / "events.json"

    # Safety net: if every scraper failed, do NOT overwrite the existing
    # events.json. The committed events.json shouldn't be wiped because of
    # a transient network blip or because every site changed format on the
    # same day (extremely unlikely but possible).
    #
    # A scraper that returns 0 events WITHOUT raising counts as a failure
    # here too: every registered source is a real implementation, so an
    # empty result almost certainly means the site changed layout or the
    # scraper swallowed a network error internally — several of them catch
    # their own RequestException and return [] instead of raising.
    all_failed = bool(report) and all(
        err is not None or n == 0 for _, n, err in report
    )
    if all_failed and out.exists():
        print("\n[!] All implemented scrapers failed — keeping previous events.json.",
              file=sys.stderr)
        wrote = False
    else:
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        wrote = True

    # Pretty CLI summary
    if wrote:
        print(f"\nWrote {len(unique)} upcoming events to {out}")
    else:
        print(f"\nKept previous events.json ({out}) — no fresh data this run.")
    print("\nPer-venue report:")
    for name, n, err in report:
        if err:
            print(f"  ✗ {name:30s}  ERROR: {err}")
        elif n == 0:
            print(f"  ! {name:30s}  0 events — possible silent failure "
                  f"(site changed? request swallowed?)")
        else:
            print(f"  ✓ {name:30s}  {n} events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
