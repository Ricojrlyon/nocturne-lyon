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
    # Le TNG a deux sites, Vaise et Les Ateliers. Le Petit Bulletin
    # nomme le premier « TNG-VAISE » : sans cette entrée il devenait un
    # lieu à part entière, avec un seul événement, alors que la salle
    # est déjà scrappée en direct.
    "TNG":                    ["tng", "theatre nouvelle generation",
                               "tng vaise", "tng les ateliers"],
    "HEAT":                   ["heat"],
    "La Halle Tony Garnier":  ["halle tony garnier", "halle tony-garnier"],
    "Bourse du Travail":      ["bourse du travail"],
    # Mapado nomme la salle "Improvidence Cafe-Theatre", le Petit
    # Bulletin "Improvidence" : sans cette entree les deux sources
    # tomberaient dans deux groupes distincts et la dedup ne les
    # verrait jamais se croiser.
    "Improvidence":           ["improvidence",
                               "improvidence cafe theatre",
                               "l improvidence",
                               "improvidence lyon"],
    # Le Petit Bulletin remonte aussi cette salle ; sans l'entree les deux
    # sources tomberaient dans deux groupes de dedup distincts.
    "Espace Gerson":          ["espace gerson", "gerson",
                               "l espace gerson"],
    # Le Petit Bulletin l'ecrit « Le Complexe cafe-theatre ».
    "Le Complexe café-théâtre": ["le complexe cafe theatre",
                                 "complexe cafe theatre",
                                 "le complexe", "complexe"],
    # === New venues from aggregators (canonical names) ===
    "Comédie Odéon":          ["comedie odeon", "la comedie odeon",
                               "theatre comedie odeon"],
    # PAS d'alias « croix rousse » nu : c'est un QUARTIER de Lyon avant
    # d'être une salle, et il capturerait tout lieu ainsi nommé — un
    # marché, un bar. Seules les formes qui désignent le théâtre.
    "Théâtre de la Croix-Rousse": ["theatre de la croix rousse",
                                   "theatre croix rousse", "txr"],
    # Le site écrit « Maison de la danse » sans majuscule à danse, le
    # Petit Bulletin avec. La normalisation gomme la casse, mais pas la
    # variante « - Grande salle » ni l'article.
    "Maison de la Danse":     ["maison de la danse", "la maison de la danse",
                               "maison de la danse grande salle"],
    # Le TNP s'écrit de bien des façons. « tnp » seul suffirait presque,
    # mais le nom complet et sa version sans tiret circulent aussi.
    "TNP - Théâtre National Populaire": ["tnp", "tnp villeurbanne",
                                         "theatre national populaire",
                                         "tnp theatre national populaire"],
    # Le Petit Bulletin écrit « Célestins, théâtre de Lyon », le site
    # lui-même « Les Célestins » et l'usage « Théâtre des Célestins ».
    "Célestins, théâtre de Lyon": ["celestins theatre de lyon", "les celestins",
                                   "theatre des celestins", "celestins"],
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
    # Pas d'alias « auditorium » seul : le mot est générique, et un
    # auditorium d'entreprise ou de musée s'y rangerait à tort.
    "Auditorium de Lyon":     ["auditorium de lyon", "auditorium lyon",
                               "auditorium orchestre national de lyon",
                               "auditorium onl", "auditorium de lyon onl"],
    # Pas d'alias « confluence » seul : le quartier porte ce nom, et la
    # MJC Confluence est un autre lieu du feed.
    "Musée des Confluences":  ["musee des confluences", "musee confluences",
                               "museedesconfluences"],
    # Pas d'alias « beaux arts » seul : une école des beaux-arts s'y
    # rangerait à tort.
    "Musée des Beaux-Arts":   ["musee des beaux arts", "musee des beaux-arts",
                               "musee des beaux arts de lyon", "mba lyon",
                               "musee beaux arts"],
    # Le Petit Bulletin publie ce lieu sous deux noms — « IAC
    # Villeurbanne » et « Institut d'Art Contemporain », ce dernier avec
    # une apostrophe échappée restée dans la donnée. Sans cette entrée,
    # le dédoublonnage y voyait deux salles, et le regroupement par jour
    # ne se faisait pas. La normalisation écrase la ponctuation, si bien
    # qu'un seul alias couvre les deux graphies.
    "IAC Villeurbanne":       ["iac villeurbanne", "iac frac rhone alpes",
                               "institut d art contemporain",
                               "institut d art contemporain villeurbanne"],
    # Le macLYON. Pas d'alias « art contemporain » nu : l'Institut d'art
    # contemporain de Villeurbanne le porte aussi, et les deux musées
    # tomberaient dans le même groupe de dédup.
    "Musée d'Art Contemporain": ["musee d art contemporain", "maclyon",
                                 "mac lyon",
                                 "musee d art contemporain de lyon",
                                 "musee art contemporain lyon"],
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


def _seances_distinctes(a: Event, prio_a: int, b: Event, prio_b: int) -> bool:
    """Deux séances distinctes du même spectacle, plutôt qu'un doublon ?

    Le critère est la SOURCE, pas l'écart d'horaire. Au sein d'une même
    source — même priorité, donc même scraper ou même agrégateur — deux
    horaires connus et différents sont toujours deux représentations : une
    source ne publie pas deux fois la même séance. Entre deux sources en
    revanche, un écart d'horaire est banal (20:00 chez la salle, 20:30 au
    Petit Bulletin) et doit se fusionner.

    Un seuil temporel ne ferait pas l'affaire : les vraies doubles séances
    mesurées vont de 60 minutes (ateliers des Célestins à 14h et 15h) à
    5 heures, et aucun seuil ne sépare les 60 minutes d'un simple écart de
    saisie. La source, elle, tranche sans ambiguïté.

    Les événements sans horaire ne sont jamais concernés : il faut DEUX
    horaires connus pour conclure.
    """
    return (prio_a == prio_b
            and bool(a.time) and bool(b.time)
            and a.time != b.time)


# Au-delà de ce nombre de jours, une plage ne décrit plus une série de
# représentations mais un accrochage ou un festival au long cours. La
# valeur reprend le seuil que le frontend utilisait pour la même
# distinction (LONG_RUN_THRESHOLD, index.html).
SEUIL_PLAGE = 30


def _duree(e: Event) -> int:
    if not e.date_end or e.date_end == e.date_start:
        return 1
    try:
        return (date.fromisoformat(e.date_end)
                - date.fromisoformat(e.date_start)).days + 1
    except ValueError:
        return 1


def _plage_et_seance(a: Event, prio_a: int, b: Event, prio_b: int) -> bool:
    """Une exposition et l'un de ses rendez-vous, plutôt qu'un doublon ?

    Un accrochage de six mois et une conférence d'une heure ne sont pas le
    même événement, même si le titre concorde et même s'ils tombent le même
    jour — c'est au contraire le cas ORDINAIRE, le vernissage ou la
    conférence inaugurale portant le nom de l'exposition qu'ils ouvrent.

    Mesuré aux Beaux-Arts : « Musée sentimental », du 11 septembre au 14
    mars, était absorbée par « Conférence : Musée sentimental » du 11
    septembre à 15h, et c'est la conférence qui gagnait. L'exposition
    disparaissait du site.

    Comme pour les doubles séances, le critère est la SOURCE : au sein
    d'une même priorité, une plage longue et une date unique sont deux
    publications volontairement distinctes. Entre deux sources, un
    agrégateur qui résume une série en plage doit continuer de fusionner
    avec les dates que la salle publie — c'est tout l'objet de la dédup.
    """
    if prio_a != prio_b:
        return False
    da, db = _duree(a), _duree(b)
    return (da == 1) != (db == 1) and max(da, db) > SEUIL_PLAGE



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
                if _title_similarity(ev.title, ref_ev.title) < 0.7:
                    continue
                # Le titre concorde, mais est-ce bien le même événement ?
                # Comparé à TOUS les membres et non au seul premier : un
                # groupe peut déjà contenir la séance de 14h et celle de
                # 15h d'une autre source, et n'en rejeter qu'une serait
                # arbitraire.
                if any(_seances_distinctes(ev, prio, m_ev, m_prio)
                       or _plage_et_seance(ev, prio, m_ev, m_prio)
                       for m_ev, m_prio in cluster):
                    continue
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
                if _title_similarity(ev.title, ref_ev.title) < 0.85:
                    continue
                # Même garde qu'en passe 1, et elle est indispensable ici :
                # cette passe regroupe par DATE SEULE, elle refait donc se
                # croiser deux séances d'un même lieu que la passe 1 venait
                # justement de séparer. Sans ce test, l'atelier de 14h et
                # celui de 15h se retrouvaient fusionnés un cran plus loin.
                if any(_seances_distinctes(ev, prio, m_ev, m_prio)
                       or _plage_et_seance(ev, prio, m_ev, m_prio)
                       for m_ev, m_prio in cluster):
                    continue
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


def _accents(s: str) -> int:
    """Nombre de signes diacritiques portés par la chaîne."""
    return sum(1 for c in unicodedata.normalize("NFD", s or "")
               if unicodedata.category(c) == "Mn")


def _unifie_orthographes(events: List[Event]) -> None:
    """Une clé de lieu, une seule orthographe affichée.

    canonical_venue_name() ne réécrit que les lieux INSCRITS dans
    VENUE_CANONICAL. Un lieu absent de la table garde donc l'orthographe
    de chaque source, et comme le frontend indexe l'arrondissement et
    regroupe les cartes sur la chaîne EXACTE de `venue`, deux graphies
    font deux salles : deux pastilles dans le filtre, deux compilations
    là où il n'y a qu'un théâtre.

    Mesuré sur le fil du 2026-09-14 : 3 clés sur 152 portaient plusieurs
    graphies, soit 156 noms de salle affichés pour 152 salles. Le Théâtre
    de l'Élysée en avait trois, dont une due à l'antislash du Petit
    Bulletin corrigé à la source juste avant ; il en reste deux, qui ne
    diffèrent que par la forme de l'apostrophe et l'accent de l'É. C'est
    dire que corriger les sources une à une ne suffit pas — deux graphies
    également correctes suffisent à scinder une salle.

    L'élection se fait d'abord sur les ACCENTS, avant la fréquence. Une
    source française laisse tomber les accents, elle n'en invente pas :
    la graphie la plus accentuée est la moins dégradée. C'est le cas ici
    même, où la fréquence aurait élu « Théâtre de l'Elysée » (7 events,
    Ville Morte) contre « Théâtre de l'Élysée » (5, Petit Bulletin).
    Viennent ensuite la fréquence, puis la longueur et l'ordre alphabé-
    tique, qui ne servent qu'à rendre le choix total et donc stable d'un
    run à l'autre.

    Ce n'est qu'un filet : la table VENUE_CANONICAL reste le moyen de
    fixer un nom d'affichage à la main, et elle passe AVANT — un lieu
    qu'elle couvre arrive ici avec une seule graphie, et l'élection ne
    trouve rien à faire.
    """
    par_cle: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for e in events:
        par_cle[_venue_key(e.venue)][e.venue] += 1
    elus = {}
    for cle, graphies in par_cle.items():
        if len(graphies) < 2:
            continue
        elus[cle] = max(graphies,
                        key=lambda g: (_accents(g), graphies[g], -len(g), g))
    if not elus:
        return
    for e in events:
        elu = elus.get(_venue_key(e.venue))
        if elu:
            e.venue = elu


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
    # Puis, pour les lieux que la table ne couvre pas, élire une graphie.
    _unifie_orthographes(final)
    return final
