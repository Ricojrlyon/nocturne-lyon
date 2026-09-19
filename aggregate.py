"""Aggregator: run all venue scrapers and write a unified events.json.

Each scraper returns a list of Event objects. Failures in one venue do NOT
abort the run — the bad venue is skipped, the others succeed. This is
critical: in a daily cron job, if one venue's HTML changes you don't want
the whole pipeline to break.

v34 changes:
  - Removed Célestins, TNP, Croix-Rousse, Comédie Odéon (theatres dropped)
    — REVENU DEPUIS : Célestins, TNP, Maison de la Danse et Croix-Rousse
    la Comédie Odéon sont de nouveau scrappés en direct (sept. 2026). C'étaient les
    quatre plus gros écarts entre ce qu'une salle programme et ce que
    le feed en montrait, chacune remontée sans une seule affiche par le
    seul Petit Bulletin.
  - Added Ville Morte as an aggregator (cross-venue source)
  - Cross-source deduplication: when the same event is reported by both a
    venue scraper and an aggregator, the venue scraper wins (it's
    authoritative). See scrapers/dedup.py.

Politique éditoriale (septembre 2026) :
  - Ville Morte remonte l'intégralité de son agenda, sans aucun filtre.
  - Petit Bulletin remonte tout SAUF quatre catégories d'arts plastiques
    (voir petit_bulletin.CATEGORIES_ECARTEES). Ce filtre, levé en août,
    revient pour une raison nouvelle : le frontend affiche désormais les
    événements longs sur CHACUN de leurs jours, si bien qu'un accrochage
    de trois mois pèse quatre-vingt-dix cartes et non plus une.
  - Les événements longs (expos, festivals au long cours) ne sont pas
    jetés : ils deviennent des événements à plage date_start..date_end,
    que le frontend déploie jour par jour dans son horizon.
  - C'est la déduplication en trois passes (scrapers/dedup.py) qui écarte
    les doublons quand un événement est publié à la fois par un
    agrégateur et par la salle elle-même. Les scrapers de salle gardent
    la priorité (100 contre 60 et 50).
  - Théâtres scrappés en direct : TNG, Célestins, TNP, Maison de la
    Danse, Croix-Rousse et Comédie Odéon.
"""
from __future__ import annotations
import json
import os
import re
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, List

from scrapers import Event
from scrapers.base import OFFSITE_PLUSIEURS
from scrapers import (
    le_sucre, les_subs, marche_gare, radiant, la_rayonne, transbordeur,
    petit_salon, sonic, periscope, la_commune,
    heat, halle_tony_garnier,
    opera_lyon, tng,
    bourse_du_travail, improvidence, espace_gerson, complexe,
    celestins, tnp, maison_de_la_danse, croix_rousse, comedie_odeon,
    agendarts, auditorium, confluences, beaux_arts, iac, mac_lyon,
    asvel,
)
from scrapers.aggregators import villemorte, petit_bulletin
from scrapers.categorie import combler as combler_categories
from scrapers.dedup import deduplicate, canonical_venue_name
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
    ("Improvidence",            improvidence.fetch),
    ("Espace Gerson",           espace_gerson.fetch),
    ("Le Complexe",             complexe.fetch),
    ("Célestins",               celestins.fetch),
    ("TNP",                     tnp.fetch),
    ("Maison de la Danse",      maison_de_la_danse.fetch),
    ("Théâtre de la Croix-Rousse", croix_rousse.fetch),
    ("Comédie Odéon",           comedie_odeon.fetch),
    ("Agend'arts",              agendarts.fetch),
    ("Auditorium de Lyon",      auditorium.fetch),
    ("Musée des Confluences",   confluences.fetch),
    ("Musée des Beaux-Arts",    beaux_arts.fetch),
    ("IAC Villeurbanne",        iac.fetch),
    ("Musée d'Art Contemporain", mac_lyon.fetch),
    ("LDLC ASVEL",               asvel.fetch),
]

# Exclusions qu'un scraper de salle décide et que les agrégateurs doivent
# respecter sur son lieu (cf. étape 2.4b). La règle appartient au scraper,
# qui la documente ; cette table ne fait que la désigner.
FILTRES_DE_SALLE: dict[str, Callable[[str], bool]] = {
    "IAC Villeurbanne": iac.exclu,
}

# Aggregators — priority lower than venue scrapers (lose against them on
# duplicates). Among themselves, higher priority wins.
# Each entry is (display_name, callable, priority).
AGGREGATORS: list[tuple[str, Callable[[], List[Event]], int]] = [
    ("Petit Bulletin",          petit_bulletin.fetch, 60),
    ("Ville Morte",             villemorte.fetch,     50),
]

# --- Frontend venue map -----------------------------------------------------
# index.html est la source de vérité de la carte lieu → arrondissement
# (VENUE_ARRONDISSEMENT, entrées vérifiées à la main avec adresses en
# commentaire). On la parse à chaque run au lieu d'en maintenir une copie
# Python : l'ancienne copie (FRONTEND_HARDCODED) avait dérivé de ~24 lieux,
# re-géocodés chaque nuit pour rien.

_VENUE_MAP_BLOCK_RE = re.compile(
    r"const\s+VENUE_ARRONDISSEMENT\s*=\s*\{(.*?)\n\s*\}\s*;",
    re.DOTALL,
)
# Une entrée par ligne : 'Nom du lieu': '1er',  // commentaire optionnel
# Les clés utilisent ' ou " (backreference \1) — les apostrophes dans les
# noms ("Bar Rock'n Eat") sont entre guillemets doubles dans index.html.
_VENUE_MAP_ENTRY_RE = re.compile(
    r"""^\s*(['"])(?P<venue>.+?)\1\s*:\s*(['"])(?P<arr>.+?)\3\s*,""",
    re.MULTILINE,
)


def frontend_hardcoded_venues() -> set:
    """Noms de lieux hardcodés dans VENUE_ARRONDISSEMENT (index.html).

    En cas d'échec de lecture ou de parsing (refonte d'index.html),
    retourne un set vide : ces lieux seront géocodés inutilement —
    dégradation sans danger, le frontend ignore le cache pour ses
    entrées en dur.
    """
    html_path = Path(__file__).parent / "index.html"
    try:
        html = html_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[geo] index.html unreadable ({exc}) — no hardcoded venues",
              file=sys.stderr)
        return set()
    m = _VENUE_MAP_BLOCK_RE.search(html)
    if not m:
        print("[geo] VENUE_ARRONDISSEMENT not found in index.html — "
              "no hardcoded venues", file=sys.stderr)
        return set()
    venues = {e.group("venue")
              for e in _VENUE_MAP_ENTRY_RE.finditer(m.group(1))}
    if len(venues) < 20:
        # La map réelle compte ~77 entrées : un si petit nombre signale
        # un parser cassé par une refonte d'index.html.
        print(f"[geo] only {len(venues)} venue(s) parsed from index.html — "
              "parser probably broken, check _VENUE_MAP_*_RE",
              file=sys.stderr)
    return venues


# ---------------------------------------------------------------------------
# Garde-fou : une salle scrappée en direct ne s'effondre pas toute seule
# ---------------------------------------------------------------------------
#
# Un scraper qui rend 7 événements au lieu de 335 ne LÈVE pas : il rend une
# liste courte, la passe traverse le pipeline sans un mot, et le workflow
# commite. Le site perd alors une salle entière jusqu'au run suivant.
#
# Ce n'est pas une crainte de principe. Rejoué sur les 106 versions
# d'events.json de l'historique, le cas s'est produit HUIT fois, réparties
# sur SEPT runs — le 7 septembre en a perdu deux d'un coup —, et chaque
# fois la version a été commitée :
#
#   Le Complexe café-théâtre  324 → 0   2026-09-08
#   Le Complexe café-théâtre  335 → 0   2026-09-09 10:33 UTC
#   Le Complexe café-théâtre  335 → 0   2026-09-10 10:24 UTC
#   Le Complexe café-théâtre  341 → 0   2026-09-13 10:55 UTC
#   Bourse du Travail          93 → 0   2026-08-31
#   Le Transbordeur            73 → 0   2026-09-07
#   La Halle Tony Garnier      54 → 0   2026-07-20
#   Le Sucre                   47 → 0   2026-09-07
#
# Le Complexe échoue sur le cron et jamais en local, environ un run sur
# quatre — ce qui ressemble à un blocage du site contre les runners
# GitHub plutôt qu'à un bug de lecture.
#
# LE SEUIL VIENT DE CETTE MESURE. Sur les 105 couples de versions
# consécutives, publier moins du QUART bloque exactement ces sept runs et
# aucun autre. Les baisses légitimes les plus fortes de l'historique sont
# à 33 % (TNG, run de validation local) et 44 % (Auditorium, le jour où
# ses ateliers ont été filtrés) : un seuil à 40 % ou 50 % les prendrait
# pour des pannes. Le plancher, lui, n'a jamais rien changé — les huit
# pannes sont toutes des chutes à zéro depuis un grand nombre ; il est là
# pour qu'une petite salle à 6 événements qui en perd 5 ne réveille
# personne.
EFFONDREMENT_PART = 0.25
EFFONDREMENT_PLANCHER = 10

# Une source directe se reconnaît à son HÔTE : tout ce qui n'est ni le
# Petit Bulletin ni Ville Morte vient du site d'une salle. Les boutiques
# Mapado (improvidence.mapado.com, espacegerson.mapado.com) en font
# partie — ce sont les billetteries des salles, pas un agrégateur.
_HOTES_AGREGATEURS = ("petit-bulletin.fr", "villemorte.fr")


def _compte_direct(paires) -> dict[str, int]:
    """Par lieu canonique, le nombre d'événements venus de la salle même."""
    n: dict[str, int] = {}
    for venue, url in paires:
        if any(h in (url or "") for h in _HOTES_AGREGATEURS):
            continue
        cle = canonical_venue_name(venue or "")
        if cle:
            n[cle] = n.get(cle, 0) + 1
    return n


def _effondrements(nouveaux: List[Event],
                   chemin: Path) -> list[tuple[str, int, int]]:
    """Lieux qui publient moins du quart de ce qu'ils publiaient hier.

    La comparaison porte sur les événements DIRECTS seulement : sans ce
    tri, un agrégateur qui remonte encore trois dates masquerait la
    disparition des trois cents autres.

    Sans fichier précédent — première exécution, fichier illisible — il
    n'y a pas de point de comparaison et on ne bloque rien.
    """
    try:
        anciens = json.loads(chemin.read_text(encoding="utf-8"))["events"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    avant = _compte_direct((e.get("venue"), e.get("url")) for e in anciens)
    apres = _compte_direct((e.venue, e.url) for e in nouveaux)
    pertes = []
    for lieu, n_avant in avant.items():
        if n_avant < EFFONDREMENT_PLANCHER:
            continue
        n_apres = apres.get(lieu, 0)
        if n_apres < n_avant * EFFONDREMENT_PART:
            pertes.append((lieu, n_avant, n_apres))
    pertes.sort(key=lambda x: x[2] - x[1])
    return pertes


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

    # 2.4) Écarter les PLAGES d'agrégateur sur un lieu qu'on scrappe.
    #
    # Un agrégateur qui voit un spectacle joué plusieurs soirs le publie
    # souvent comme UNE plage « du 18 au 28 août ». Le frontend déploie
    # une plage sur chacun de ses jours : elle double alors les séances
    # que le scraper de la salle rapporte précisément, et en invente les
    # soirs où le spectacle ne joue pas.
    #
    # La dédup ne peut pas rattraper ça : elle indexe bien la plage sur
    # tous ses jours, mais il suffit qu'elle gagne UN seul jour — typique-
    # ment aujourd'hui, quand la billetterie a déjà retiré la séance en
    # cours — pour être émise, et elle repeint ensuite toute sa durée.
    #
    # Mesuré à l'introduction de la règle : 2 plages, toutes deux à
    # Improvidence, 22 jours affichés dont 14 en collision directe.
    scraped_venues = {
        canonical_venue_name(e.venue) for e, p in all_tagged if p >= 100
    }
    before_ranges = len(all_tagged)
    all_tagged = [
        (e, p) for e, p in all_tagged
        if not (p < 100 and e.date_end and e.date_end != e.date_start
                and canonical_venue_name(e.venue) in scraped_venues)
    ]
    dropped_ranges = before_ranges - len(all_tagged)
    if dropped_ranges:
        print(f"[plages] {dropped_ranges} plage(s) d'agrégateur écartée(s) "
              f"sur un lieu scrappé en direct")

    # 2.4b) Appliquer aux agrégateurs les exclusions qu'un scraper de
    # salle décide. Un scraper qui écarte volontairement une partie de la
    # programmation — les visites guidées de l'IAC, hors celles du
    # week-end — n'obtient rien si l'agrégateur la republie derrière lui.
    # Mesuré à l'introduction de la règle : neuf visites revenues par le
    # Petit Bulletin sur les vingt-deux entrées du scraper de l'IAC.
    #
    # La règle vit DANS le scraper, qui en est l'auteur ; on ne fait ici
    # que l'appliquer aux événements des agrégateurs, sur son lieu.
    before_filtres = len(all_tagged)
    all_tagged = [
        (e, p) for e, p in all_tagged
        if not (p < 100
                and (f := FILTRES_DE_SALLE.get(canonical_venue_name(e.venue)))
                and f(e.title))
    ]
    dropped_filtres = before_filtres - len(all_tagged)
    if dropped_filtres:
        print(f"[filtres] {dropped_filtres} événement(s) d'agrégateur "
              f"écarté(s) par la règle de la salle")

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


    # 7b) Combler les catégories manquantes. APRÈS la déduplication, et
    #     c'est important : quand un même événement remonte de deux
    #     sources, la dédup a déjà hérité la catégorie de celle qui en
    #     avait une. On ne déduit donc que pour ce qui en manque
    #     réellement, et jamais par-dessus une catégorie de source.
    comble, sans_cat = combler_categories(unique)
    print(f"[catégories] {comble} comblée(s) par déduction, "
          f"{sans_cat} restée(s) sans catégorie")

    # 8) Geocode any new venues not already in the frontend's hardcoded
    #    VENUE_ARRONDISSEMENT map (parsée en direct depuis index.html —
    #    source de vérité unique, voir frontend_hardcoded_venues).
    #    Results are cached in venue_arrondissements.json. Only truly new
    #    venues trigger HTTP requests (1 req/sec). The frontend merges this
    #    file with its hardcoded map at load time (hardcoded entries win
    #    on conflict).
    #    Les salles HORS LES MURS y passent aussi : la carte d'un concert
    #    donne l'arrondissement de la salle où il se joue, pas de celle
    #    qui le programme, et c'est donc cette salle-là qu'il faut
    #    géocoder. La sentinelle des productions jouées dans plusieurs
    #    salles n'est pas un nom de lieu et reste dehors.
    all_venues = list(
        {e.venue for e in unique}
        | {e.offsite_venue for e in unique
           if e.offsite_venue and e.offsite_venue != OFFSITE_PLUSIEURS}
    )
    resolve_new_venues(all_venues,
                       known_venues=frontend_hardcoded_venues(),
                       verbose=True)

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

    # Second filet, plus fin que le premier : celui-ci ne demande pas que
    # TOUT échoue, il suffit qu'une salle s'effondre.
    effondres = _effondrements(unique, out) if out.exists() else []
    if effondres and os.environ.get("NOCTURNE_FORCER_ECRITURE") == "1":
        print("\n[garde-fou] effondrement(s) passé(s) outre "
              "(NOCTURNE_FORCER_ECRITURE=1).", file=sys.stderr)
        effondres = []

    if all_failed and out.exists():
        print("\n[!] All implemented scrapers failed — keeping previous events.json.",
              file=sys.stderr)
        wrote = False
    elif effondres:
        print("\n[garde-fou] EFFONDREMENT — events.json n'est PAS réécrit.",
              file=sys.stderr)
        for lieu, n_avant, n_apres in effondres:
            print("    %-34s %4d → %4d événements propres"
                  % (lieu, n_avant, n_apres), file=sys.stderr)
        print("    Une salle ne perd pas les trois quarts de son programme "
              "en une nuit :\n"
              "    son scraper a rendu une liste courte sans lever. "
              "Relancer suffit\n"
              "    d'ordinaire, la panne étant passagère. Si la perte est "
              "RÉELLE — salle\n"
              "    fermée, saison terminée —, relancer avec "
              "NOCTURNE_FORCER_ECRITURE=1.",
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
    # Sortie non nulle sur effondrement : l'étape de commit du workflow est
    # alors sautée et le run passe au rouge, ce qui est tout l'intérêt —
    # une panne silencieuse devient une panne visible.
    return 1 if effondres else 0


if __name__ == "__main__":
    sys.exit(main())
