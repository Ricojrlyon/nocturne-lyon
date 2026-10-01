"""Scraper for Les Célestins, théâtre de Lyon (place des Célestins, 2e).

Le site tourne sous Roadiz, dont l'API Platform est PUBLIQUE : son
robots.txt interdit /api/docs mais pas /api, et /api/docs.json rend la
spécification OpenAPI complète. On lit donc une API JSON documentée
plutôt que du HTML — le cas le plus confortable du dépôt.

  /api/event_dates  une ligne par REPRÉSENTATION, avec l'horaire exact,
                    le spectacle et le lieu complet (nom, slug, adresse)
  /api/events       une ligne par SPECTACLE, seule à porter l'affiche

Les deux appels sont nécessaires : la sérialisation des event_dates
n'embarque ni les documents ni les tags du spectacle. C'est justement ce
que le Petit Bulletin ne donnait pas — il remontait cette salle sans une
seule image, alors que l'API en a une pour chacun de ses spectacles.

ATTENTION AU LIEU. Les Célestins programment HORS LES MURS : sur les 228
représentations des six prochains mois, 17 se jouent au TNP, au TNG et
au Théâtre de la Croix-Rousse — dont une salle que nocturne scrappe
déjà. Les publier sous « Célestins » créerait des doublons attribués au
mauvais lieu, que la déduplication ne rattraperait pas puisqu'elle
regroupe justement PAR lieu. D'où `_chez_eux`, appliqué au lieu que
l'API donne en clair pour chaque représentation.

La règle retient le slug contenant « celestin » OU l'adresse « place des
Célestins ». Les deux moitiés sont nécessaires : la Grande salle et le
Foyer du public n'ont pas « celestin » dans leur slug, et les espaces
extérieurs (place, chapiteau, parking) n'ont pas d'adresse renseignée.
Mesuré à l'écriture : 211 représentations gardées sur 228, et les 17
écartées sont exactement les trois salles extérieures.

Pagination : l'API plafonne à 50 éléments par page quoi qu'on demande
dans itemsPerPage — une demande de 400 en rend 50. Il faut donc paginer,
sans quoi on ne verrait qu'un mois de programmation.
"""
from __future__ import annotations

import sys
import time
import unicodedata
import urllib.parse
from datetime import date as Date, timedelta
from typing import Dict, List, Optional

import requests

from .base import Event, get as base_get

# Graphie du Petit Bulletin, qui remonte aussi cette salle : c'est ce qui
# permet à la dédup de regrouper les deux sources.
VENUE = "Célestins, théâtre de Lyon"
SLUG = "celestins"
BASE = "https://www.theatredescelestins.com"

# Transformation d'image Roadiz : qualité 75, largeur 600. Les cartes font
# 400 px de large au plus, 600 couvre donc les écrans denses.
IMAGE_BASE = BASE + "/assets/q75-w600/"

HORIZON_DAYS = 180
PAGE_SIZE = 50                            # plafond de l'API, pas un choix
MAX_PAGES = 30                            # garde-fou anti-boucle
MIN_INTERVAL = 0.3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
                  "+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept": "application/json",
}

# Catégorie déduite du type d'événement de l'API. « théâtre » par défaut
# pour une représentation : c'est un théâtre, et le tag de genre n'existe
# que sur une poignée de spectacles (voir TAGS_GENRE).
TYPES = {
    "live_performance": "théâtre",
    "workshop": "atelier",
    "guided_tour": "visite",
}

# Le type « other » est ÉCARTÉ. Il ne porte pas des spectacles mais les à-côtés
# d'une journée portes ouvertes — « Bar et restauration », une station d'écoute
# de podcast dans le hall. Publier une carte « Bar et restauration » n'aurait
# pas de sens, et c'est la même ligne que pour les formations professionnelles
# de La Rayonne : une programmation parallèle, pas un choix éditorial.
TYPES_ECARTES = {"other"}

# Tags de genre qui l'emportent sur le type. Le vocabulaire des tags est
# surtout fait de noms de salle et de tranches d'âge — « Grande salle »,
# « dès 15 ans » — d'où cette liste courte plutôt qu'une lecture générale.
TAGS_GENRE = {
    "danse": "danse",
    "musique": "musique",
    "concert": "musique",
    "cirque": "cirque",
    "humour": "humour",
}


def _norm(s: Optional[str]) -> str:
    s = (s or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _chez_eux(place: dict) -> bool:
    """La représentation se joue-t-elle bien AUX Célestins ?"""
    if not place:
        return False
    if "celestin" in _norm(place.get("slug")):
        return True
    rue = (place.get("address") or {}).get("streetAddress")
    return "celestins" in _norm(rue)


def _pages(session: requests.Session, chemin: str, params: dict) -> List[dict]:
    """Toutes les pages d'une collection, concaténées."""
    out: List[dict] = []
    for page in range(1, MAX_PAGES + 1):
        if page > 1:
            time.sleep(MIN_INTERVAL)
        q = dict(params, itemsPerPage=PAGE_SIZE, page=page)
        url = BASE + chemin + "?" + urllib.parse.urlencode(q)
        r = base_get(url, session=session, headers=HEADERS, timeout=30)
        r.raise_for_status()
        lot = r.json()
        if not isinstance(lot, list) or not lot:
            break
        out.extend(lot)
        if len(lot) < PAGE_SIZE:
            break
    else:
        print(f"[Célestins] {MAX_PAGES} pages atteintes sur {chemin} — "
              f"pagination probablement tronquée", file=sys.stderr)
    return out


def _spectacles(session: requests.Session, apres: str) -> Dict[str, dict]:
    """slug du spectacle → {image, tags}.

    Second appel indispensable : ni les documents ni les tags ne sont
    sérialisés dans /api/event_dates.
    """
    out: Dict[str, dict] = {}
    for e in _pages(session, "/api/events", {"dates.startDate[after]": apres}):
        slug = e.get("slug")
        if not slug:
            continue
        chemin = next((d.get("relativePath") for d in (e.get("mainDocuments") or [])
                       if d.get("type") == "image" and d.get("relativePath")), None)
        out[slug] = {
            "image": IMAGE_BASE + chemin if chemin else None,
            "tags": [t.get("name") for t in (e.get("mainTags") or []) if t.get("name")],
        }
    return out


def _categorie(ev: dict, tags: List[str]) -> Optional[str]:
    for t in tags:
        genre = TAGS_GENRE.get(_norm(t))
        if genre:
            return genre
    return TYPES.get(ev.get("type"))


def fetch() -> List[Event]:
    today = Date.today()
    debut = today.isoformat()
    fin = (today + timedelta(days=HORIZON_DAYS)).isoformat()

    session = requests.Session()
    dates = _pages(session, "/api/event_dates", {
        "order[startDate]": "asc",
        "startDate[after]": debut,
        "startDate[before]": fin,
    })
    if not dates:
        # API joignable mais vide : la structure a changé, ou la saison
        # n'est pas publiée. On le signale plutôt que de rendre une liste
        # vide qu'aggregate.py ne distinguerait pas d'une panne.
        print("[Célestins] /api/event_dates ne rend aucune représentation",
              file=sys.stderr)
        return []

    spectacles = _spectacles(session, debut)

    events: List[Event] = []
    ailleurs = hors_sujet = 0
    for d in dates:
        if not _chez_eux(d.get("place") or {}):
            ailleurs += 1
            continue
        ev = d.get("event") or {}
        if ev.get("type") in TYPES_ECARTES:
            hors_sujet += 1
            continue
        depart = d.get("startDate") or ""
        if len(depart) < 16 or not ev.get("name"):
            continue
        slug = ev.get("slug") or ""
        info = spectacles.get(slug, {})
        chemin = ev.get("url") or ""
        events.append(Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=ev["name"].strip(),
            subtitle=None,
            category=_categorie(ev, info.get("tags", [])),
            date_start=depart[:10],
            date_end=None,
            time=depart[11:16],
            url=BASE + chemin if chemin.startswith("/") else (chemin or BASE),
            image=info.get("image"),
        ))

    if ailleurs or hors_sujet:
        print(f"[Célestins] écartées : {ailleurs} hors les murs, "
              f"{hors_sujet} hors sujet", file=sys.stderr)
    return events
