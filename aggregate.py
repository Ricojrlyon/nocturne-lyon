"""Aggregator: run all venue scrapers and write a unified events.json.

Each scraper returns a list of Event objects. Failures in one venue do NOT
abort the run — the bad venue is skipped, the others succeed. This is
critical: in a daily cron job, if one venue's HTML changes you don't want
the whole pipeline to break.
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
    opera_lyon, celestins, croix_rousse,
    tnp, comedie_odeon, tng,
    bourse_du_travail,
)

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
    ("Théâtre des Célestins",   celestins.fetch),
    ("Théâtre de la Croix-Rousse", croix_rousse.fetch),
    ("TNP",                     tnp.fetch),
    ("Comédie Odéon",           comedie_odeon.fetch),
    ("TNG",                     tng.fetch),
    ("Bourse du Travail",       bourse_du_travail.fetch),
]


def main() -> int:
    all_events: list[Event] = []
    report = []

    for name, fn in SCRAPERS:
        try:
            events = fn()
            all_events.extend(events)
            report.append((name, len(events), None))
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc(limit=2)
            report.append((name, 0, f"{type(e).__name__}: {e}"))
            print(f"[FAIL] {name}: {tb}", file=sys.stderr)

    # Drop past events (keep today and future).
    today = date.today()
    upcoming = [
        e for e in all_events
        if e.date_start and e.date_start >= today.isoformat()
    ]

    # Sanity-check URLs. Any event whose URL is not absolute (http/https)
    # gets logged and replaced with the empty string — which the frontend
    # treats as "no link" rather than rendering a relative href that would
    # 404 on GitHub Pages. We do NOT drop such events; their info is still
    # useful even without a clickable source link.
    bad_urls = 0
    for e in upcoming:
        if not e.url or not (e.url.startswith("http://")
                             or e.url.startswith("https://")):
            print(f"[URL!] {e.venue} — non-absolute url for {e.title!r}: "
                  f"{e.url!r}", file=sys.stderr)
            e.url = ""
            bad_urls += 1
    if bad_urls:
        print(f"[URL!] {bad_urls} event(s) had non-absolute URLs — cleared.",
              file=sys.stderr)

    # Sanity-check titles. Events with empty/missing title are silently dropped
    # (they would render as visually empty cards in the UI).
    bad_titles = 0
    clean_upcoming = []
    for e in upcoming:
        if not e.title or not e.title.strip():
            print(f"[TITLE!] {e.venue} — empty title for event on {e.date_start} "
                  f"(url: {e.url!r}) — dropping",
                  file=sys.stderr)
            bad_titles += 1
            continue
        clean_upcoming.append(e)
    if bad_titles:
        print(f"[TITLE!] {bad_titles} event(s) had empty titles — dropped.",
              file=sys.stderr)
    upcoming = clean_upcoming

    # Sort by date then time then venue.
    upcoming.sort(key=lambda e: (e.date_start, e.time or "00:00", e.venue))

    # Deduplicate by stable id
    seen = set()
    unique = []
    for e in upcoming:
        if e.id in seen:
            continue
        seen.add(e.id)
        unique.append(e)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(unique),
        "events": [e.to_dict() for e in unique],
    }

    out = Path(__file__).parent / "events.json"

    # Safety net: if every implemented scraper failed, do NOT overwrite the
    # existing events.json. A user pointing GitHub Actions to a freshly cloned
    # repo with a populated seed file shouldn't lose everything because of a
    # transient network blip or because every site changed format on the same
    # day (extremely unlikely but possible).
    implemented = [(name, n, err) for name, n, err in report
                   if err is not None or n > 0]
    all_failed = implemented and all(err is not None for _, _, err in implemented)
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
            print(f"  · {name:30s}  (stub)")
        else:
            print(f"  ✓ {name:30s}  {n} events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
