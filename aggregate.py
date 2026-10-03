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
  - Pour toutes les sources, une exception : un événement que sa source
    dit annulé n'est pas publié (BUG-19, voir _sans_les_annules).
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
    chapelle_trinite,
    agendarts, auditorium, confluences, beaux_arts, iac, mac_lyon,
    asvel, asvel_feminin, volley, handball, rugby,
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
    ("Chapelle de la Trinité",  chapelle_trinite.fetch),
    ("Agend'arts",              agendarts.fetch),
    ("Auditorium de Lyon",      auditorium.fetch),
    ("Musée des Confluences",   confluences.fetch),
    ("Musée des Beaux-Arts",    beaux_arts.fetch),
    ("IAC Villeurbanne",        iac.fetch),
    ("Musée d'Art Contemporain", mac_lyon.fetch),
    ("LDLC ASVEL",               asvel.fetch),
    ("LDLC ASVEL Féminin",       asvel_feminin.fetch),
    ("Volley national lyonnais", volley.fetch),
    ("Handball national lyonnais", handball.fetch),
    ("LOU Rugby",               rugby.fetch),
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

# CE QUE L'ON FAIT D'UN EFFONDREMENT. La premiere version refusait de
# reecrire events.json et sortait en 1, ce qui faisait passer le run au
# rouge. Deux runs consecutifs l'ont montree trop brutale : le 2026-09-19,
# le Periscope puis le Transbordeur ont chacun lache une fois, et le fil
# ENTIER est reste sans mise a jour deux fois de suite pour une seule
# salle.
#
# Desormais la salle tombee GARDE SES EVENEMENTS DE LA VEILLE et le reste
# du fil se publie normalement. Rien ne disparait, rien ne se fige : les
# evenements repris vieillissent comme les autres et sortent du fil a leur
# date. Une panne d'un jour ne se voit donc plus du tout cote lecteur.
#
# Reprendre indefiniment serait pire que bloquer : un scraper mort pour de
# bon garderait ses evenements en vie des mois. La reprise est donc BORNEE
# et le fil garde la trace du premier jour de reprise de chaque salle,
# sous la cle « reprises ». Passe le delai, on laisse la salle se vider,
# avec un avertissement plus dur.
#
# NOCTURNE_FORCER_ECRITURE=1 publie ce qui a ete reellement scrappe, sans
# rien reprendre : c'est la sortie quand la perte est vraie — salle
# fermee, saison finie. Sur GitHub, c'est la case « Forcer la
# publication » d'un lancement a la main (OPT-1, voir update.yml).

# Au-delà, la panne n'est plus passagère et la salle doit pouvoir se vider.
# Sept jours : de quoi couvrir une coupure de plusieurs jours sans laisser
# un scraper mort peupler le fil de fantômes pendant des mois.
REPRISE_JOURS_MAX = 7

# LE VOLUME TOTAL, en dernier recours. Le garde-fou ci-dessus regarde
# chaque salle, et ne voit donc pas une perte RÉPARTIE : un bug de code qui
# retirerait trois cartes sur dix partout, une dédup devenue trop gourmande,
# un filtre mal écrit. Aucune salle ne s'effondre, et le fil entier maigrit
# sans un mot avant d'être publié.
#
# On refuse donc de publier un fil qui tombe sous les TROIS QUARTS de ce que
# la veille comptait encore à venir — les événements de la veille passés
# entre-temps ne sont pas une perte. Rejoué sur les 131 couples de
# publications consécutives de l'historique (juillet à septembre 2026), la
# plus forte baisse légitime est de 15 %, le 12 juillet, 631 → 539 ; aucune
# n'a dépassé 20 %. Le seuil n'aurait jamais sonné, et il attrape une perte
# de trente pour cent.
#
# Il ne peut pas bloquer pour UNE salle : la plus grosse, Le Complexe, pèse
# onze pour cent du fil, et celles qui s'effondrent ont de toute façon été
# reprises à l'étape 6b, avant ce contrôle.
#
# Ce garde-fou ne peut qu'EMPÊCHER une publication, jamais modifier le fil.
# Au pire, le site garde les données de la veille un jour de trop. Le run
# sort alors en 1 — rouge, avec un courriel —, parce que le site n'a pas été
# mis à jour et qu'il faut qu'un humain regarde.
#
# UN BLOCAGE N'EST PAS ÉTERNEL. Une baisse RÉELLE — une source retirée
# exprès, une fin de saison — ferait sonner le garde-fou chaque matin, le
# fichier de la veille ne changeant plus : le site resterait figé pour de
# bon. Passé VOLUME_FIGE_JOURS_MAX jours sans publication, on publie donc
# quand même, avec une alerte. NOCTURNE_FORCER_ECRITURE=1 publie tout de
# suite, comme pour le garde-fou par salle.
VOLUME_PART_MIN = 0.75
VOLUME_FIGE_JOURS_MAX = 3

# Une source directe se reconnaît à son HÔTE : tout ce qui n'est ni le
# Petit Bulletin ni Ville Morte vient du site d'une salle. Les boutiques
# Mapado (improvidence.mapado.com, espacegerson.mapado.com) en font
# partie — ce sont les billetteries des salles, pas un agrégateur.
_HOTES_AGREGATEURS = ("petit-bulletin.fr", "villemorte.fr")

# LES AGRÉGATEURS AUSSI. Le garde-fou par salle ne compte que les
# événements DIRECTS, et c'est voulu : un agrégateur qui remonte encore
# trois dates ne doit pas masquer la chute d'une salle. Mais la même panne
# frappe un agrégateur, et ce qu'il publie SEUL — les lieux qu'aucun
# scraper ne couvre — disparaissait alors du site jusqu'au passage
# suivant : 324 événements pour le Petit Bulletin et 158 pour Ville Morte
# au 2026-09-30, seize pour cent du fil.
#
# Rejoué sur les 131 couples de publications consécutives de l'historique,
# le cas s'est produit DEUX fois, deux fois chez Ville Morte, deux fois
# jusqu'à zéro :
#
#   Ville Morte   116 → 0   2026-08-25
#   Ville Morte   159 → 0   2026-09-19   502 Proxy Error
#
# Les plus fortes baisses normales sont à −21 %, chez l'un comme chez
# l'autre. Le seuil des salles — moins du quart — attrape donc les deux
# pannes et rien d'autre : on reprend la même règle, le même plancher, le
# même délai de reprise, le même journal et le même forçage. Seule la
# priorité change à la reprise : un événement repris d'un agrégateur garde
# SA priorité, et continue de perdre face à la salle qui publie le même
# spectacle aujourd'hui.
_AGREGATEURS_SURVEILLES = (("Petit Bulletin", "petit-bulletin.fr"),
                           ("Ville Morte", "villemorte.fr"))

# Dans le journal « reprises », un agrégateur s'écrit avec ce préfixe : le
# journal des salles est indexé par nom de lieu, et les deux ne doivent
# jamais pouvoir se croiser.
_PREFIXE_JOURNAL_AGREGATEUR = "agrégateur:"


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


# LES PETITES SALLES (SUIVI-2). Sous EFFONDREMENT_PLANCHER, le garde-fou
# ci-dessus ne regarde pas : une salle à six dates qui en perd cinq peut
# être une vraie fin de programme. Mais quand son COLLECTEUR LÈVE — page
# introuvable, site en panne —, il n'y a pas de doute : le 2 octobre 2026,
# l'agenda du Petit Salon a répondu 404, et la salle est passée de cinq
# soirées à deux sur le site, sans reprise ni alerte. Une petite salle dont
# le collecteur lève reprend donc la veille comme une grande, avec le même
# délai et le même journal. Une petite salle qui rend simplement moins de
# dates, elle, n'est pas reprise.
#
# RIEN n'est pas « moins » (BUG-25). Le même 2 octobre, la page du Petit
# Salon s'est deux fois affichée sans une soirée, mais sans erreur : ses
# huit dates ont quitté le site jusqu'au passage suivant, celle du soir
# comprise. Un collecteur qui ne rend AUCUN événement, alors que ses lieux
# annonçaient la veille des dates encore à venir, est donc en échec lui
# aussi. S'il ne leur restait que des dates passées, son silence est une
# vraie fin de programme.
#
# Le garde-fou compte par LIEU, une panne se lit par COLLECTEUR : le
# handball joue dans deux gymnases, le volley dans deux autres. Le fil
# garde donc les lieux que chaque collecteur a rendus, sous la clé
# « lieux_des_collecteurs », et un collecteur en échec garde ceux de la
# veille. Sans cette trace — au premier passage —, son lieu est son nom,
# ce qui vaut pour toutes les salles.
def _lieux_des_collecteurs(lieux: dict, chemin: Path,
                           today_iso: str) -> tuple[dict, dict]:
    """(lieux à écrire dans le fil, lieux des collecteurs en échec).

    `lieux` vient de _collecter : pour chaque collecteur de salle, les
    lieux qu'il a rendus, ou None s'il a levé. Un collecteur qui n'a rien
    rendu est en échec quand ses lieux annonçaient hier des dates encore à
    venir (BUG-25).
    """
    try:
        precedent = json.loads(chemin.read_text(encoding="utf-8"))
        hier = precedent.get("lieux_des_collecteurs") or {}
        anciens = precedent.get("events") or []
    except (OSError, ValueError, AttributeError):
        hier, anciens = {}, []
    if not isinstance(hier, dict):
        hier = {}
    if not isinstance(anciens, list):
        anciens = []
    # Même règle qu'à l'étape 3 : un événement vit jusqu'à sa fin.
    attendus = set(_compte_direct(
        (e.get("venue"), e.get("url")) for e in anciens
        if isinstance(e, dict)
        and (e.get("date_end") or e.get("date_start") or "") >= today_iso))
    # Une liste vide est une trace : hier, le collecteur n'a rien rendu.
    en_panne = {}
    for nom, rendus in lieux.items():
        trace = hier.get(nom, [canonical_venue_name(nom)])
        if rendus is None or (not rendus and attendus.intersection(trace or [])):
            en_panne[nom] = trace
    return ({nom: en_panne.get(nom, rendus) for nom, rendus in lieux.items()},
            en_panne)


def _petites_salles_en_panne(nouveaux: List[Event], chemin: Path,
                             en_panne: dict) -> list[tuple[str, int, int]]:
    """Petites salles dont le collecteur a levé, ou n'a rien rendu (BUG-25) :
    (lieu, hier, aujourd'hui).

    Petite : moins de EFFONDREMENT_PLANCHER événements directs dans le fil
    précédent. `en_panne` donne les lieux de chaque collecteur en échec.
    """
    if not en_panne:
        return []
    try:
        anciens = json.loads(chemin.read_text(encoding="utf-8"))["events"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    avant = _compte_direct((e.get("venue"), e.get("url")) for e in anciens)
    apres = _compte_direct((e.venue, e.url) for e in nouveaux)
    lieux = {lieu for rendus in en_panne.values() for lieu in rendus}
    return sorted((lieu, avant[lieu], apres.get(lieu, 0)) for lieu in lieux
                  if 0 < avant.get(lieu, 0) < EFFONDREMENT_PLANCHER)


def _chute_de_volume(n_nouveau: int, chemin: Path,
                     today_iso: str) -> tuple[int, int] | None:
    """(encore à venir dans le fil précédent, son âge en jours), si le
    nouveau fil tombe sous VOLUME_PART_MIN de ce nombre.

    None quand tout va bien — ou quand il n'y a pas de point de
    comparaison, première exécution ou fichier illisible : on ne bloque
    rien sur une référence qu'on n'a pas.
    """
    try:
        precedent = json.loads(chemin.read_text(encoding="utf-8"))
        anciens = precedent["events"]
        genere = date.fromisoformat(str(precedent["generated_at"])[:10])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    # Même règle qu'à l'étape 3 : un événement vit jusqu'à sa fin.
    encore = sum(1 for e in anciens
                 if (e.get("date_end") or e.get("date_start") or "")
                 >= today_iso)
    if not encore or n_nouveau >= encore * VOLUME_PART_MIN:
        return None
    return encore, (date.fromisoformat(today_iso) - genere).days


def _priorite(url: str) -> int:
    """Priorité de dédup d'un événement déjà publié, relue sur son hôte.

    Les priorités ne survivent pas à events.json — seuls les événements y
    sont écrits. On les reconstruit donc de la même façon que _compte_direct
    reconnaît une source directe.
    """
    u = url or ""
    if "petit-bulletin.fr" in u:
        return 60
    if "villemorte.fr" in u:
        return 50
    return 100


def _event_depuis_dict(d: dict) -> Event:
    """Un Event reconstruit depuis sa forme sérialisée."""
    return Event(
        venue=d.get("venue") or "",
        venue_slug=d.get("venue_slug") or "",
        title=d.get("title") or "",
        subtitle=d.get("subtitle"),
        category=d.get("category"),
        date_start=d.get("date_start") or "",
        date_end=d.get("date_end"),
        time=d.get("time"),
        url=d.get("url") or "",
        image=d.get("image"),
        offsite_venue=d.get("offsite_venue"),
    )


def _alerte(titre: str, message: str) -> None:
    """Un avertissement qui se voit.

    Sur GitHub Actions, l'annotation remonte en tête de la page du run —
    sans quoi un run VERT porterait la panne enfouie dans mille lignes de
    journal, et personne ne la verrait jamais. Ailleurs, stderr suffit.
    """
    print(message, file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("::warning title=%s::%s" % (titre, message.replace("\n", "%0A")))


def _reprendre(unique: List[Event], chemin: Path,
               effondres: list, today_iso: str) -> tuple:
    """Remet les événements de la veille pour les salles effondrées.

    Ne reprend que les événements DIRECTS de ces salles : ce qu'un
    agrégateur publiait hier, il l'a republié aujourd'hui, et le reprendre
    ferait doublon. Ne reprend que ce qui n'est pas passé, avec la même
    règle que l'étape 3.

    Rend (événements repris, journal des reprises, salles abandonnées).
    """
    try:
        fil = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], {}, []
    anciens = fil.get("events") or []
    journal_avant = fil.get("reprises") or {}

    lieux = {lieu for lieu, _, _ in effondres}
    journal, abandons, repris = {}, [], []
    for lieu in lieux:
        depuis = journal_avant.get(lieu, today_iso)
        try:
            jours = (date.fromisoformat(today_iso)
                     - date.fromisoformat(depuis)).days
        except ValueError:
            depuis, jours = today_iso, 0
        if jours >= REPRISE_JOURS_MAX:
            abandons.append((lieu, depuis, jours))
            continue
        journal[lieu] = depuis

    for d in anciens:
        lieu = canonical_venue_name(d.get("venue") or "")
        if lieu not in journal:
            continue
        if any(h in (d.get("url") or "") for h in _HOTES_AGREGATEURS):
            continue
        if (d.get("date_end") or d.get("date_start") or "") < today_iso:
            continue
        repris.append(_event_depuis_dict(d))

    return repris, journal, abandons


def _effondrements_agregateurs(nouveaux: List[Event], chemin: Path,
                               today_iso: str) -> list[tuple[str, int, int]]:
    """Agrégateurs dont la part PROPRE tombe sous le quart de la veille.

    La part propre, c'est ce qui reste d'un agrégateur une fois le fil
    dédoublonné : les événements qu'aucune salle scrappée ne couvre. On la
    compare à ce que la veille en comptait ENCORE à venir. Sans fichier
    précédent, rien.
    """
    try:
        anciens = json.loads(chemin.read_text(encoding="utf-8"))["events"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    pertes = []
    for nom, hote in _AGREGATEURS_SURVEILLES:
        avant = sum(1 for e in anciens
                    if hote in (e.get("url") or "")
                    and (e.get("date_end") or e.get("date_start") or "")
                    >= today_iso)
        if avant < EFFONDREMENT_PLANCHER:
            continue
        apres = sum(1 for e in nouveaux if hote in (e.url or ""))
        if apres < avant * EFFONDREMENT_PART:
            pertes.append((nom, avant, apres))
    return pertes


def _reprendre_agregateurs(chemin: Path, effondres: list,
                           today_iso: str) -> tuple:
    """Remet les événements de la veille des agrégateurs effondrés.

    Même bornage que pour les salles : le journal garde le premier jour de
    reprise, et passé REPRISE_JOURS_MAX jours l'agrégateur n'est plus
    repris. Ne reprend que ce qui n'est pas passé.

    Rend (événements repris, journal des reprises, agrégateurs abandonnés).
    """
    try:
        fil = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], {}, []
    if not isinstance(fil, dict):
        return [], {}, []
    anciens = fil.get("events") or []
    journal_avant = fil.get("reprises") or {}
    hote_de = dict(_AGREGATEURS_SURVEILLES)

    journal, abandons, hotes = {}, [], []
    for nom, _, _ in effondres:
        cle = _PREFIXE_JOURNAL_AGREGATEUR + nom
        depuis = journal_avant.get(cle, today_iso)
        try:
            jours = (date.fromisoformat(today_iso)
                     - date.fromisoformat(depuis)).days
        except ValueError:
            depuis, jours = today_iso, 0
        if jours >= REPRISE_JOURS_MAX:
            abandons.append((nom, depuis, jours))
            continue
        journal[cle] = depuis
        hotes.append(hote_de[nom])

    repris = [_event_depuis_dict(d) for d in anciens
              if any(h in (d.get("url") or "") for h in hotes)
              and (d.get("date_end") or d.get("date_start") or "")
              >= today_iso]
    return repris, journal, abandons


def _collecter() -> tuple[list[tuple[Event, int]], list, dict]:
    """Étapes 1 et 2 : chaque collecteur de salle, puis chaque agrégateur.

    Rend les événements, chacun étiqueté de la priorité de sa source, le
    compte rendu (nom, nombre d'événements, erreur) de chaque source, et
    les lieux que chaque collecteur de salle a rendus — None s'il a levé.
    Une source qui lève est notée en échec sans arrêter les autres, et
    signalée en tête du passage (SUIVI-2) : enfouie dans le journal d'un
    run vert, la panne du Petit Salon n'aurait été vue de personne.
    """
    # Each event is tagged with a (source) priority for deduplication.
    all_tagged: list[tuple[Event, int]] = []
    report = []
    lieux: dict = {}

    # 1) Venue-specific scrapers
    for name, fn in SCRAPERS:
        try:
            events = fn()
            for e in events:
                all_tagged.append((e, 100))
            report.append((name, len(events), None))
            lieux[name] = sorted({canonical_venue_name(e.venue or "")
                                  for e in events} - {""})
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc(limit=2)
            report.append((name, 0, f"{type(e).__name__}: {e}"))
            lieux[name] = None
            print(f"[FAIL] {name}: {tb}", file=sys.stderr)
            _alerte("source en échec",
                    f"[source en échec] {name} — {type(e).__name__}: {e}")

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
            _alerte("source en échec",
                    f"[source en échec] {name} — {type(e).__name__}: {e}")
    return all_tagged, report, lieux


def _ecarter_les_plages_d_agregateur(all_tagged: list[tuple[Event, int]]
                                     ) -> list[tuple[Event, int]]:
    """Étape 2.4 : sans les plages d'agrégateur sur un lieu scrappé."""
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
    #
    # Un lieu scrappé se reconnaît ici à ses événements du jour : quand son
    # collecteur tombe, c'est l'étape 6b qui applique la règle, au moment
    # de reprendre ses événements de la veille (BUG-21).
    scraped_venues = {
        canonical_venue_name(e.venue) for e, p in all_tagged if p >= 100
    }
    before_ranges = len(all_tagged)
    all_tagged = [
        (e, p) for e, p in all_tagged
        if not _plage_d_agregateur_sur(e, p, scraped_venues)
    ]
    dropped_ranges = before_ranges - len(all_tagged)
    if dropped_ranges:
        print(f"[plages] {dropped_ranges} plage(s) d'agrégateur écartée(s) "
              f"sur un lieu scrappé en direct")
    return all_tagged


def _plage_d_agregateur_sur(e: Event, prio: int, salles: set) -> bool:
    """Une plage d'agrégateur sur l'une de ces salles lues en direct ?"""
    return (prio < 100 and bool(e.date_end) and e.date_end != e.date_start
            and canonical_venue_name(e.venue) in salles)


def _appliquer_les_regles_des_salles(all_tagged: list[tuple[Event, int]]
                                     ) -> list[tuple[Event, int]]:
    """Étape 2.4b : les exclusions d'un scraper de salle, appliquées aussi
    aux agrégateurs sur son lieu."""
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
    return all_tagged


def _a_venir(all_tagged: list[tuple[Event, int]],
             today_iso: str) -> list[tuple[Event, int]]:
    """Étape 3 : sans les événements terminés."""
    # 3) Drop past events. An event is upcoming as long as it hasn't ENDED:
    # keep ongoing runs (date_start in the past but date_end today or later,
    # e.g. multi-day shows) — several scrapers preserve those on purpose and
    # the frontend knows how to render them.
    return [
        (e, p) for e, p in all_tagged
        if e.date_start and (e.date_end or e.date_start) >= today_iso
    ]


def _effacer_les_liens_non_absolus(upcoming_tagged: list[tuple[Event, int]]
                                   ) -> None:
    """Étape 4 : un lien qui n'est pas absolu est vidé, l'événement gardé."""
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


def _sans_titre_vide(upcoming_tagged: list[tuple[Event, int]]
                     ) -> list[tuple[Event, int]]:
    """Étape 5 : sans les événements au titre vide."""
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
    return clean_tagged


def _garde_fou_des_salles(unique: List[Event], out: Path, today_iso: str,
                          en_panne: dict) -> tuple[List[Event], dict]:
    """Étape 6b : une salle effondrée reprend ses événements de la veille,
    une petite salle aussi quand son collecteur a levé (SUIVI-2) ou n'a
    rien rendu (BUG-25).

    `en_panne` : les lieux de chaque collecteur en échec. Rend le fil,
    complété des reprises, et le journal des reprises.
    """
    # 6b) Garde-fou : une salle scrappée en direct ne s'effondre pas seule.
    #     Voir le chapeau d'EFFONDREMENT_PART pour la mesure et la règle.
    #     La détection est faite ICI, sur le fil dédoublonné, parce que
    #     c'est sous cette forme que le fil précédent est écrit : comparer
    #     un décompte d'avant-dédup à un décompte d'après ne voudrait rien
    #     dire.
    effondres = _effondrements(unique, out) if out.exists() else []
    # Les petites salles dont le collecteur a levé, ou n'a rien rendu : voir
    # le chapeau de _lieux_des_collecteurs.
    pannes = (_petites_salles_en_panne(unique, out, en_panne)
              if out.exists() else [])
    reprises: dict = {}
    forcer = os.environ.get("NOCTURNE_FORCER_ECRITURE") == "1"
    if (effondres or pannes) and forcer:
        _alerte("garde-fou contourné",
                "[garde-fou] effondrement(s) publié(s) tels quels "
                "(NOCTURNE_FORCER_ECRITURE=1) : "
                + ", ".join("%s %d→%d" % t for t in effondres + pannes))
        effondres, pannes = [], []

    if effondres or pannes:
        repris, reprises, abandons = _reprendre(unique, out,
                                                effondres + pannes, today_iso)
        if repris:
            # BUG-21 : l'étape 2.4 n'a pas reconnu ces salles, faute de leurs
            # événements du jour, et a laissé passer les plages d'agrégateur
            # qu'elles portent. Le Complexe refusé par son hébergeur, la plage
            # « Un grand cri d'amour » du Petit Bulletin (2 octobre → 28
            # décembre) s'affichait tous les soirs, quand le spectacle ne se
            # joue que le lundi. On les écarte ici, avant la dédup : le fil
            # d'une salle reprise est celui d'un jour où elle répond.
            salles_reprises = {canonical_venue_name(e.venue) for e in repris}
            avant_plages = len(unique)
            unique = [e for e in unique
                      if not _plage_d_agregateur_sur(e, _priorite(e.url),
                                                     salles_reprises)]
            if len(unique) < avant_plages:
                print(f"[plages] {avant_plages - len(unique)} plage(s) "
                      f"d'agrégateur écartée(s) sur une salle reprise de la "
                      f"veille")
            # On repasse par la dédup avec le fil complet : les événements
            # repris n'ont PAS été confrontés aux publications du jour, et
            # un agrégateur a pu annoncer entre-temps un spectacle que la
            # salle annonçait hier. Les priorités sont reconstruites sur
            # l'hôte, faute d'être écrites dans events.json.
            avant_reprise = len(unique)
            unique = deduplicate([(e, _priorite(e.url)) for e in unique]
                                 + [(e, 100) for e in repris])

            lieux_repris = [canonical_venue_name(e.venue) for e in repris]

            def detail(salles):
                return ", ".join("%s %d→%d, %d repris"
                                 % (lieu, a, b, lieux_repris.count(lieu))
                                 for lieu, a, b in salles)
            if effondres:
                _alerte(
                    "salle reprise du fil précédent",
                    "[garde-fou] EFFONDREMENT : " + detail(effondres) + ".\n"
                    "Le fil est publié, et ces salles gardent leurs "
                    "événements de la veille.\n"
                    "Une salle ne perd pas les trois quarts de son programme "
                    "en une nuit : son\n"
                    "scraper a rendu une liste courte sans lever. Si la "
                    "perte est RÉELLE — salle\n"
                    "fermée, saison finie —, relancer à la main en cochant "
                    "« Forcer la publication »\n"
                    "(NOCTURNE_FORCER_ECRITURE=1).")
            if pannes:
                _alerte(
                    "petite salle reprise du fil précédent",
                    "[garde-fou] PANNE : " + detail(pannes) + ".\n"
                    "Le collecteur de ces petites salles a levé, ou n'a rien "
                    "rendu : elles gardent\n"
                    "leurs événements de la veille, %d jours au plus."
                    % REPRISE_JOURS_MAX)
            print("[garde-fou] %d + %d repris → %d après dédup"
                  % (avant_reprise, len(repris), len(unique)))
        for lieu, depuis, jours in abandons:
            _alerte(
                "salle abandonnée après %d jours" % jours,
                "[garde-fou] %s s'effondre depuis le %s, soit %d jours. "
                "Au-delà de %d la panne n'est plus passagère : la salle "
                "n'est PLUS reprise et va se vider du fil. Son scraper est "
                "à réparer." % (lieu, depuis, jours, REPRISE_JOURS_MAX))
    return unique, reprises


def _garde_fou_des_agregateurs(unique: List[Event], out: Path,
                               today_iso: str, reprises: dict) -> List[Event]:
    """Étape 6c : un agrégateur effondré reprend ses événements de la veille.

    Rend le fil, complété des reprises ; le journal des reprises est
    complété en place.
    """
    # 6c) Le même garde-fou pour les AGRÉGATEURS. Voir le chapeau de
    #     _AGREGATEURS_SURVEILLES : ce qu'un agrégateur publie seul
    #     disparaissait du site le jour où il tombait. La 6b n'est pas
    #     touchée ; cette étape vient après elle, sur le fil qu'elle rend.
    effondres_agr = (_effondrements_agregateurs(unique, out, today_iso)
                     if out.exists() else [])
    if effondres_agr and os.environ.get("NOCTURNE_FORCER_ECRITURE") == "1":
        _alerte("garde-fou contourné",
                "[garde-fou] effondrement(s) d'agrégateur publié(s) tels "
                "quels (NOCTURNE_FORCER_ECRITURE=1) : "
                + ", ".join("%s %d→%d" % t for t in effondres_agr))
        effondres_agr = []

    if effondres_agr:
        repris_agr, journal_agr, abandons_agr = _reprendre_agregateurs(
            out, effondres_agr, today_iso)
        reprises.update(journal_agr)
        if repris_agr:
            # Chacun garde SA priorité, relue sur son hôte : un événement
            # repris du Petit Bulletin doit continuer de perdre face à la
            # salle qui publie le même spectacle aujourd'hui.
            avant_reprise = len(unique)
            unique = deduplicate([(e, _priorite(e.url)) for e in unique]
                                 + [(e, _priorite(e.url)) for e in repris_agr])
            hote_de = dict(_AGREGATEURS_SURVEILLES)
            detail = ", ".join(
                "%s %d→%d, %d repris"
                % (nom, a, b,
                   sum(1 for e in repris_agr if hote_de[nom] in (e.url or "")))
                for nom, a, b in effondres_agr)
            _alerte(
                "agrégateur repris du fil précédent",
                "[garde-fou] EFFONDREMENT d'agrégateur : " + detail + ".\n"
                "Le fil est publié, et les lieux que seul cet agrégateur "
                "couvre gardent leurs événements de la veille.\n"
                "Si la perte est RÉELLE, relancer à la main en cochant "
                "« Forcer la publication »\n"
                "(NOCTURNE_FORCER_ECRITURE=1).")
            print("[garde-fou] %d + %d repris → %d après dédup"
                  % (avant_reprise, len(repris_agr), len(unique)))
        for nom, depuis, jours in abandons_agr:
            _alerte(
                "agrégateur abandonné après %d jours" % jours,
                "[garde-fou] %s s'effondre depuis le %s, soit %d jours. "
                "Au-delà de %d la panne n'est plus passagère : ses "
                "événements ne sont PLUS repris et vont quitter le fil. Son "
                "scraper est à réparer."
                % (nom, depuis, jours, REPRISE_JOURS_MAX))
    return unique


# BUG-19 : un événement que sa source dit annulé était publié comme les
# autres — « (annulé) Electric Doom Synthesis… » (Grrrnd Zero, par Ville
# Morte), « [annulé] AG de la RiV » (Biéristan), « Annulé Formation… »
# (Marché Gare, par la salle). Formes relevées sur 84 jours de fils publiés,
# de juillet à octobre 2026 : le mot en tête du titre (« Annulé … »,
# « ANNULE // … », « (Annulé) … »), seul entre parenthèses ou crochets
# (« … [annulé] »), ou un sous-titre qui n'est que l'avis (« Concert
# annulé »). Le mot en fin de titre (« … - ANNULÉ ») suit la même logique.
#
# Ailleurs, le mot ne suffit pas : « Almond Butyl - annulé / remplacé par
# Viviane Cavale » est une soirée qui a bien lieu, et un sous-titre peut
# raconter une tournée annulée l'an passé. Un titre qui annonce un
# remplacement est toujours gardé.
_ANNULE = r"annul[eé](?:e|s|es)?"
_AVIS_D_ANNULATION = (r"(?:(?:concert|spectacle|soir[eé]e|date|[eé]v[eé]nement|"
                      r"s[eé]ance|repr[eé]sentation)\s+)?" + _ANNULE)
_TITRE_ANNULE = re.compile(r"^\W*" + _ANNULE + r"\b"
                           r"|[(\[]\s*" + _AVIS_D_ANNULATION + r"[\s!.]*[)\]]"
                           r"|[-–—:/|]\s*" + _ANNULE + r"\W*$", re.I)
_AVIS_SEUL = re.compile(r"^\W*" + _AVIS_D_ANNULATION + r"\W*$", re.I)


def _est_annule(e: Event) -> bool:
    """La source dit-elle l'événement annulé ? Voir _TITRE_ANNULE."""
    titre, sous_titre = e.title or "", e.subtitle or ""
    if re.search(r"remplac", titre + " " + sous_titre, re.I):
        return False
    return bool(_TITRE_ANNULE.search(titre) or _AVIS_SEUL.match(titre)
                or _AVIS_SEUL.match(sous_titre))


def _sans_les_annules(unique: List[Event]) -> List[Event]:
    """Étape 6d : sans les événements que leur source dit annulés (BUG-19).

    APRÈS la dédup et les garde-fous, pour deux raisons. Quand la salle
    écrit « Annulé » et qu'un agrégateur republie le même spectacle sans le
    dire, la dédup les a fondus sous le titre de la salle : écarter l'annulé
    avant elle laisserait la version de l'agrégateur, et le spectacle annulé
    resterait affiché. Et un événement repris de la veille par un garde-fou
    passe ainsi lui aussi par ce filtre.
    """
    gardes = []
    for e in unique:
        if _est_annule(e):
            # Nommé dans le journal : une lecture fautive doit se voir.
            print(f"[annulés] écarté : {e.venue}, {e.date_start} — {e.title!r}")
            continue
        gardes.append(e)
    return gardes


def _geocoder_les_nouveaux_lieux(unique: List[Event]) -> None:
    """Étape 8 : l'arrondissement des lieux que la page ne connaît pas."""
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


def _contenu_du_fil(unique: List[Event], reprises: dict,
                    lieux: dict) -> dict:
    """Ce qu'events.json contiendra : l'en-tête, les événements, le
    journal des reprises s'il y en a, et les lieux des collecteurs."""
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(unique),
        # Le nombre de sources que lit le site, salles et agendas, pour le
        # pied de page : il suit ainsi les deux listes du haut de ce
        # fichier, sans chiffre à tenir à la main — la page affichait
        # encore « 17 sources » quand le site en lisait 37.
        "sources": len(SCRAPERS) + len(AGGREGATORS),
        "events": [e.to_dict() for e in unique],
    }
    # Le journal des reprises voyage avec le fil : c'est lui qui permet au
    # run suivant de savoir depuis QUAND une salle est reprise, et donc de
    # l'abandonner passé le délai. Absent quand tout va bien. Le frontend
    # ne le lit pas.
    if reprises:
        payload["reprises"] = reprises
    # Les lieux que chaque collecteur a rendus : c'est par eux que le run
    # suivant sait quelles petites salles reprendre si l'un d'eux lève
    # (SUIVI-2). Le frontend ne les lit pas.
    if lieux:
        payload["lieux_des_collecteurs"] = lieux
    return payload


def _publier(payload: dict, report: list, out: Path, today_iso: str) -> bool:
    """Écrit events.json, sauf si TOUTES les sources ont échoué ou si le
    volume s'effondre sans explication. Vrai si le fil a été écrit."""
    nombre = payload["count"]
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

    # L'effondrement d'UNE salle ne bloque plus rien : il a été traité à
    # l'étape 6b, par reprise du fil précédent. Ne reste ici que le filet
    # d'origine, qui demande que TOUT ait échoué.
    if all_failed and out.exists():
        print("\n[!] All implemented scrapers failed — keeping previous events.json.",
              file=sys.stderr)
        wrote = False
    else:
        # Dernier contrôle : le VOLUME total, que le garde-fou par salle ne
        # voit pas. Voir le chapeau de VOLUME_PART_MIN.
        wrote = True
        chute = _chute_de_volume(nombre, out, today_iso)
        if chute:
            encore, age = chute
            constat = ("%d événements à publier contre %d encore à venir dans "
                       "le fil précédent, soit %d %%"
                       % (nombre, encore,
                          round(100 * nombre / encore)))
            if os.environ.get("NOCTURNE_FORCER_ECRITURE") == "1":
                _alerte("garde-fou de volume contourné",
                        "[garde-fou] " + constat + ". Publié quand même "
                        "(NOCTURNE_FORCER_ECRITURE=1).")
            elif age > VOLUME_FIGE_JOURS_MAX:
                _alerte("site figé depuis %d jours : publication forcée" % age,
                        "[garde-fou] " + constat + ".\nLe site n'a plus été "
                        "mis à jour depuis %d jours : on publie quand même, "
                        "la baisse est sans doute réelle. À vérifier." % age)
            else:
                _alerte("baisse de volume : fil NON publié",
                        "[garde-fou] " + constat + ".\nLe fichier de la veille "
                        "est conservé : une perte aussi large, sans salle "
                        "effondrée, ressemble à un bug.\nSi la baisse est "
                        "réelle, relancer à la main en cochant « Forcer la "
                        "publication »\n(NOCTURNE_FORCER_ECRITURE=1) ; "
                        "sinon le site publiera de lui-même dans %d jour(s)."
                        % (VOLUME_FIGE_JOURS_MAX + 1 - age))
                wrote = False
        if wrote:
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return wrote


def _resumer(report: list, wrote: bool, nombre: int, out: Path) -> None:
    """Le compte rendu du passage, source par source."""
    # Pretty CLI summary
    if wrote:
        print(f"\nWrote {nombre} upcoming events to {out}")
    else:
        print(f"\nKept previous events.json ({out}) — not overwritten this run.")
    print("\nPer-venue report:")
    for name, n, err in report:
        if err:
            print(f"  ✗ {name:30s}  ERROR: {err}")
        elif n == 0:
            print(f"  ! {name:30s}  0 events — possible silent failure "
                  f"(site changed? request swallowed?)")
        else:
            print(f"  ✓ {name:30s}  {n} events")


def main() -> int:
    """Un passage complet, étape par étape : collecte, nettoyage,
    dédoublonnage, garde-fous, puis publication d'events.json. Rend 0, ou
    1 quand le fil n'a pas été écrit."""
    all_tagged, report, lieux = _collecter()
    all_tagged = _ecarter_les_plages_d_agregateur(all_tagged)
    all_tagged = _appliquer_les_regles_des_salles(all_tagged)

    # 2.5) Persist the detail-page time cache (url → time), committed by
    # the workflow like venue_arrondissements.json. Without this save,
    # every run would re-fetch the same detail pages from scratch.
    save_detail_cache()

    today_iso = date.today().isoformat()
    upcoming_tagged = _a_venir(all_tagged, today_iso)
    _effacer_les_liens_non_absolus(upcoming_tagged)
    upcoming_tagged = _sans_titre_vide(upcoming_tagged)

    # 6) Cross-source deduplication. Groups events by (venue, date), then
    # fuzzy-matches titles within each group. On duplicates, keeps the
    # highest-priority source.
    before = len(upcoming_tagged)
    unique = deduplicate(upcoming_tagged)
    print(f"\n[dedup] {before} candidates → {len(unique)} unique "
          f"(-{before - len(unique)})")

    out = Path(__file__).parent / "events.json"
    lieux, en_panne = _lieux_des_collecteurs(lieux, out, today_iso)
    unique, reprises = _garde_fou_des_salles(unique, out, today_iso, en_panne)
    unique = _garde_fou_des_agregateurs(unique, out, today_iso, reprises)
    unique = _sans_les_annules(unique)

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

    _geocoder_les_nouveaux_lieux(unique)
    wrote = _publier(_contenu_du_fil(unique, reprises, lieux), report, out,
                     today_iso)
    _resumer(report, wrote, len(unique), out)
    # Un effondrement ne fait plus échouer le run : le fil est publié, la
    # salle tombée reprise, et l'alerte remonte en annotation GitHub. Seul
    # l'échec de TOUS les scrapers sort en 1.
    return 0 if wrote else 1

if __name__ == "__main__":
    sys.exit(main())
