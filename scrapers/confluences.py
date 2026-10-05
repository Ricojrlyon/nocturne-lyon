"""Scraper for the Musée des Confluences (2e, pointe de la Confluence).

Le seul site du dépôt à exposer une JSON:API Drupal, et la source la
mieux structurée qu'on ait rencontrée : deux taxonomies y donnent
gratuitement ce qu'il a fallu deviner ailleurs. `field_activites` porte
le genre en cinq valeurs, et `field_public` sépare le grand public des
classes et des groupes — à l'Auditorium, la même distinction se lisait
dans une phrase tarifaire.

ON NE PREND PAS TOUT, ET C'EST LE CHOIX CENTRAL DE CE SCRAPER. Le musée
publie 6 447 séances sur six mois, plus du double du site entier, parce
qu'il exprime ses visites et ses ateliers récurrents en RRULE : une
seule visite jouée mercredi, jeudi, samedi et dimanche pendant cinq mois
pèse quatre-vingt-dix séances. Le type `slot`, lui, est pire encore —
1 200 créneaux pour une seule semaine, dont 583 à neuf heures : c'est
une grille de réservation de groupes, pas une programmation.

On retient donc les conférences, le cinéma, les spectacles et les
concerts, pour le grand public seulement. Vérification faite, AUCUNE des
fiches ainsi retenues ne porte de RRULE : ce sont des dates uniques,
listées en clair. Le morceau difficile disparaît avec le filtre — mais
si une récurrence apparaissait un jour, elle serait signalée plutôt que
tronquée en silence à sa première date.

LES EXPOSITIONS TEMPORAIRES viennent d'un second type de nœud. Les
agrégateurs les publient déjà, mais avec des dates approximatives ; ici
`field_state` dit « En cours », « À venir » ou « Passées », et la
période est écrite en toutes lettres. Le scraper l'emporte sur eux par
sa priorité, et le parcours permanent est écarté — cinq nœuds, qui ne
sont pas des sorties datées.

HORS LES MURS. Le musée programme au Cinéma Comœdia et au Cinéma Le
Zola — ce dernier est déjà dans nocturne. `field_place` nomme la salle
et `field_site` marque explicitement « Hors les murs ». Comme partout
ailleurs dans le dépôt, on retient une liste blanche de lieux maison et
l'on signale ce qui n'y figure pas, pour qu'une salle nouvelle se voie
au lieu de disparaître.

LES TITRES d'exposition portent un suffixe interne — « Corée du Nord
_expoFR ». Le vrai titre est dans `field_title`.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from datetime import date as Date, timedelta
from typing import Dict, List, Optional, Tuple

import requests

from .base import Event, get as base_get

VENUE = "Musée des Confluences"
SLUG = "musee-des-confluences"
BASE = "https://museedesconfluences.fr"
API = BASE + "/jsonapi"

HORIZON_DAYS = 180
PAR_PAGE = 50
PAGES_MAX = 40          # 14 pages à l'écriture ; la marge couvre la croissance

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
                  "+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept": "application/vnd.api+json",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# Les deux activités qui relèvent d'une sortie. Les trois autres —
# « Visite », « Atelier ou animation », « Découverte du bâtiment » — sont
# écartées : voir l'en-tête.
ACTIVITES = {
    "conference ou cinema": "conférence",
    "spectacle ou concert": "concert",
}
PUBLICS = ("adultes", "des enfants / en famille")

# Salles du musée. « Au musée », les niveaux et le jardin en font partie ;
# les cinémas partenaires non.
LIEUX_MAISON = ("grand auditorium", "petit auditorium", "au musee",
                "hall d'entree du musee", "galerie emile guimet", "niveau",
                "terrasse du musee", "jardin", "librairie-boutique")

ETATS_EXPO = ("en cours", "a venir")

MOIS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
        "juin": 6, "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10,
        "novembre": 11, "decembre": 12}

# « Du 03 avril 2026 au 07 février 2027 »
_PERIODE = re.compile(r"du\s+(\d{1,2})\s+([a-z]+)\s+(20\d{2})\s+au\s+"
                      r"(\d{1,2})\s+([a-z]+)\s+(20\d{2})")
# Fin d'une série récurrente, dans la RRULE : UNTIL=20270212T143000Z.
_UNTIL = re.compile(r"UNTIL=(\d{4})(\d{2})(\d{2})")
# Les projections se reconnaissent au titre : la taxonomie les mélange
# aux conférences sous une seule étiquette « Conférence ou cinéma ».
_CINE = re.compile(r"^\s*cin[ée]|cin[ée]-", re.I)
# Sous-titre qui ne fait que répéter la date : inutile sur la carte.
_SOUS_TITRE_DATE = re.compile(r"^\s*(?:à partir du|du)\s+\d", re.I)


def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _texte(v) -> str:
    """Un champ Drupal est tantôt une chaîne, tantôt un dict formaté."""
    if isinstance(v, dict):
        v = v.get("value") or v.get("#plain_text") or ""
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _tout(session: requests.Session, type_noeud: str,
          include: str) -> Tuple[list, Dict[str, dict]]:
    """Tous les nœuds d'un type, avec l'index des ressources incluses."""
    noeuds: list = []
    inclus: Dict[str, dict] = {}
    url: Optional[str] = f"{API}/node/{type_noeud}"
    params: Optional[dict] = {"page[limit]": PAR_PAGE, "include": include}
    for _ in range(PAGES_MAX):
        if not url:
            break
        try:
            r = base_get(url, session=session, params=params,
                         headers=HEADERS, timeout=40)
            r.raise_for_status()
            j = r.json()
        except (requests.RequestException, ValueError) as exc:
            print(f"[Confluences] {type_noeud}: {exc}", file=sys.stderr)
            break
        noeuds.extend(j.get("data") or [])
        for i in j.get("included") or []:
            inclus[i["id"]] = i
        url = ((j.get("links") or {}).get("next") or {}).get("href")
        params = None          # le lien « next » porte déjà la requête
    return noeuds, inclus


def _lies(noeud: dict, champ: str) -> list:
    d = ((noeud.get("relationships") or {}).get(champ) or {}).get("data")
    if isinstance(d, dict):
        return [d]
    return d or []


def _noms(noeud: dict, champ: str, inclus: Dict[str, dict]) -> List[str]:
    out = []
    for d in _lies(noeud, champ):
        a = (inclus.get(d["id"]) or {}).get("attributes") or {}
        nom = a.get("name") or a.get("title")
        if nom:
            out.append(nom)
    return out


def _image(noeud: dict, inclus: Dict[str, dict]) -> Optional[str]:
    """L'affiche est à deux relations de distance : média puis fichier."""
    for champ in ("field_media", "field_media2", "field_image"):
        for d in _lies(noeud, champ):
            media = inclus.get(d["id"]) or {}
            cibles = _lies(media, "field_media_image") or [d]
            for c in cibles:
                a = (inclus.get(c["id"]) or {}).get("attributes") or {}
                u = (a.get("uri") or {}).get("url")
                if u:
                    return BASE + u if u.startswith("/") else u
    return None


def _taille_carte(session: requests.Session, url: str) -> str:
    """L'affiche dans la taille « carte » de l'agenda du musée, 800 × 500.

    L'API ne donne que l'original : jusqu'à 9,5 Mo pour une carte de
    400 px. Pour son agenda, le site en fabrique une version recadrée de
    800 × 500 (style d'image Drupal card_desktop_2x), quatre fois plus
    légère en moyenne. Son adresse se déduit de celle de l'original, mais
    le musée ne la cite qu'avec un jeton (itok) impossible à calculer :
    elle est donc vérifiée avant d'être prise. À défaut, l'original, comme
    avant.
    """
    racine = BASE + "/sites/default/files/"
    if not url.startswith(racine):
        return url
    carte = racine + "styles/card_desktop_2x/public/" + url[len(racine):]
    try:
        r = base_get(carte, session=session, headers=HEADERS, timeout=20,
                     methode="HEAD")
    except requests.RequestException:
        return url
    if r.status_code == 200 and \
            (r.headers.get("Content-Type") or "").startswith("image/"):
        return carte
    return url


def _url(noeud: dict) -> Optional[str]:
    alias = ((noeud.get("attributes") or {}).get("path") or {}).get("alias")
    return BASE + alias if alias else None


def _chez_eux(lieux: List[str], sites: List[str]) -> bool:
    if any(_norm(s) == "hors les murs" for s in sites):
        return False
    # Sans lieu précisé — près de la moitié des fiches — on suppose le
    # musée : c'est le cas par défaut, et l'inverse perdrait des séances.
    return all(any(m in _norm(l) for m in LIEUX_MAISON) for l in lieux)


def _recurrence_vive(f: dict, today: Date) -> bool:
    """La série récurrente atteint-elle encore aujourd'hui ?

    Les archives du musée en sont pleines — la dernière s'arrête en mai
    2026 — et leurs dates tombent de toute façon hors de l'horizon. Ne
    signaler que les séries encore vivantes évite un avertissement qui
    crierait au loup à chaque passage.
    """
    if not f.get("rrule"):
        return False
    m = _UNTIL.search(f["rrule"])
    fin = ("-".join(m.groups()) if m
           else (f.get("end_value") or f.get("value") or "")[:10])
    return fin >= today.isoformat()


def _periode(txt: str) -> Optional[Tuple[Date, Date]]:
    m = _PERIODE.search(_norm(txt))
    if not m:
        return None
    d, md, ad, f, mf, af = m.groups()
    if md not in MOIS or mf not in MOIS:
        return None
    try:
        return Date(int(ad), MOIS[md], int(d)), Date(int(af), MOIS[mf], int(f))
    except ValueError:
        return None


def _evenements(session: requests.Session, today: Date,
                horizon: Date) -> Tuple[List[Event], Dict[str, int], int]:
    noeuds, inclus = _tout(
        session, "event",
        "field_activites,field_public,field_place,field_site,"
        "field_media.field_media_image")
    events: List[Event] = []
    ailleurs: Dict[str, int] = {}
    recurrents = 0
    for n in noeuds:
        a = n.get("attributes") or {}
        activites = [_norm(x) for x in _noms(n, "field_activites", inclus)]
        genre = next((ACTIVITES[x] for x in activites if x in ACTIVITES), None)
        if genre is None:
            continue
        publics = [_norm(x) for x in _noms(n, "field_public", inclus)]
        if not any(p in PUBLICS for p in publics):
            continue

        dates = a.get("field_date_recu") or []
        retenues = []
        for f in dates:
            if _recurrence_vive(f, today):
                # Aucune série encore vive n'en portait à l'écriture. Si
                # cela change, on ne publiera pas la seule première date
                # en taisant les autres : on le dit.
                recurrents += 1
            val = (f.get("value") or "")[:10]
            if today.isoformat() <= val <= horizon.isoformat():
                retenues.append((val, (f.get("value") or "")[11:16] or None))
        if not retenues:
            continue

        lieux = _noms(n, "field_place", inclus)
        if not _chez_eux(lieux, _noms(n, "field_site", inclus)):
            cle = ", ".join(lieux) or "hors les murs"
            ailleurs[cle] = ailleurs.get(cle, 0) + len(retenues)
            continue

        titre = _texte(a.get("title"))
        if genre == "conférence" and _CINE.search(titre):
            genre = "projection"
        sous = _texte(a.get("field_subtitle"))
        image = _image(n, inclus)
        lien = _url(n)
        for jour, heure in retenues:
            events.append(Event(
                venue=VENUE, venue_slug=SLUG, title=titre,
                subtitle=sous or None, category=genre,
                date_start=jour, date_end=None, time=heure,
                url=lien, image=image,
            ))
    return events, ailleurs, recurrents


def _expositions(session: requests.Session, today: Date,
                 horizon: Date) -> Tuple[List[Event], int]:
    noeuds, inclus = _tout(
        session, "exposition",
        "field_state,field_media.field_media_image,"
        "field_media2.field_media_image")
    events: List[Event] = []
    sans_periode = 0
    for n in noeuds:
        a = n.get("attributes") or {}
        if not any(_norm(e) in ETATS_EXPO
                   for e in _noms(n, "field_state", inclus)):
            continue
        bornes = _periode(_texte(a.get("temporary_exposition_dates")))
        if bornes is None:
            sans_periode += 1
            continue
        debut, fin = bornes
        if fin < today or debut > horizon:
            continue
        # Une exposition déjà ouverte commence, pour nous, aujourd'hui :
        # le feed ne porte que des dates à venir.
        debut = max(debut, today)

        titre = _texte(a.get("field_title")) or re.sub(
            r"\s*_expo\w*$", "", _texte(a.get("title")))
        sous = _texte(a.get("field_subtitle"))
        if _SOUS_TITRE_DATE.match(sous):
            sous = ""          # ne fait que répéter la période
        events.append(Event(
            venue=VENUE, venue_slug=SLUG, title=titre,
            subtitle=sous or None, category="exposition",
            date_start=debut.isoformat(), date_end=fin.isoformat(),
            time=None, url=_url(n), image=_image(n, inclus),
        ))
    return events, sans_periode


def fetch() -> List[Event]:
    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    session = requests.Session()

    events, ailleurs, recurrents = _evenements(session, today, horizon)
    expos, sans_periode = _expositions(session, today, horizon)

    # Une vérification par affiche publiée, pas par séance.
    cartes: Dict[str, str] = {}
    for e in events + expos:
        if e.image:
            if e.image not in cartes:
                cartes[e.image] = _taille_carte(session, e.image)
            e.image = cartes[e.image]

    if ailleurs:
        detail = ", ".join(f"{k} ({v})" for k, v in sorted(ailleurs.items()))
        print(f"[Confluences] hors les murs, écartés : {detail}",
              file=sys.stderr)
    if recurrents:
        print(f"[Confluences] {recurrents} date(s) récurrente(s) parmi les "
              "fiches retenues : la RRULE n'est pas développée, des séances "
              "manquent", file=sys.stderr)
    if sans_periode:
        print(f"[Confluences] {sans_periode} exposition(s) en cours ou à "
              "venir sans période lisible", file=sys.stderr)
    if not events and not expos:
        print("[Confluences] aucune séance retenue — structure JSON:API "
              "modifiée ?", file=sys.stderr)
    return events + expos
