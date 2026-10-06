"""Lecture commune des billetteries Mapado.

Mapado est une plateforme de billetterie très répandue chez les salles
françaises, et plusieurs salles de nocturne l'utilisent (Improvidence,
Espace Gerson). Leurs boutiques sont des Next.js : chaque page embarque
son état d'hydratation en JSON dans <script id="__NEXT_DATA__">, sous
forme d'objets d'API au format Hydra.

C'est ce JSON qu'on lit, PAS le HTML. Les classes CSS de Mapado sont des
hachages de styled-components (« TicketingItem__Container-sc-1uevklp-0 »)
qui changent à chaque déploiement ; la structure des objets, elle, est
stable. Le chemin exact dans l'arbre Next.js (pageProps, état du store…)
n'est pas contractuel non plus, d'où le parcours en profondeur de
`collect` plutôt qu'un accès par chemin.

Deux étapes, une seule requête pour la première :
  1. la boutique liste les spectacles - objets Ticketing, portant titre,
     slug, visuel, type et LIEU ;
  2. chaque page spectacle porte ses séances - objets EventDate.

Le filtrage par lieu ne repose sur aucune heuristique : chaque Ticketing
porte son Venue avec nom, adresse, code postal et ville en clair. C'est
indispensable, une boutique n'étant PAS synonyme d'une salle :
  * Improvidence exploite aussi une salle à Bordeaux ;
  * Espace Gerson programme aussi à la Salle Victor Hugo, à la Salle Paul
    Garcin et à la Bourse du Travail - cette dernière étant déjà scrappée
    en direct par nocturne, l'attribuer à Gerson créerait des doublons au
    mauvais lieu.
Chaque scraper fournit donc son propre prédicat `garder`.

Pas de detail_cache : son TTL de 30 jours convient à une heure de début,
qui ne bouge pas, mais pas à une LISTE de séances, qui s'enrichit au fil
des semaines - on sous-déclarerait les dates ajoutées récemment.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date as Date, timedelta
from typing import Callable, List, Optional

import requests

from .base import Event, get as base_get

IMG_HOST = "https://img.mapado.net"
IMG_SIZE = "600-600"            # les cartes font 392 px de large
HORIZON_DAYS = 180
MIN_INTERVAL = 0.4              # secondes entre deux requêtes

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
                  "+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def next_data(html: str) -> Optional[dict]:
    """État d'hydratation Next.js embarqué dans la page."""
    m = _NEXT_DATA_RE.search(html or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def collect(node, wanted: str, out: list) -> list:
    """Tous les objets @type == wanted, à n'importe quelle profondeur."""
    if isinstance(node, dict):
        if node.get("@type") == wanted:
            out.append(node)
        for v in node.values():
            collect(v, wanted, out)
    elif isinstance(node, list):
        for v in node:
            collect(v, wanted, out)
    return out


def image_url(ticketing: dict) -> Optional[str]:
    media = ticketing.get("mediaList") or []
    if not media or not isinstance(media[0], dict):
        return None
    path = (media[0].get("path") or "").strip()
    if not path:
        return None
    # Mapado sert une vignette redimensionnée sous <chemin>_thumbs/<taille>.
    ext = path.rsplit(".", 1)[-1] if "." in path else "jpeg"
    return f"{IMG_HOST}/{path}_thumbs/{IMG_SIZE}.{ext}"


def shows(session: requests.Session, shop: str,
          garder: Callable[[dict], bool]) -> List[dict]:
    """Spectacles datés de la boutique retenus par `garder`.

    `garder` reçoit le dict Venue du spectacle (jamais None : un
    dictionnaire vide si le Ticketing n'en porte pas).
    """
    r = base_get(shop + "/", session=session, headers=HEADERS, timeout=25)
    r.raise_for_status()
    data = next_data(r.text)
    if data is None:
        raise RuntimeError(f"__NEXT_DATA__ introuvable sur {shop}")

    par_slug: dict = {}
    for t in collect(data, "Ticketing", []):
        slug = (t.get("slug") or "").strip()
        if not slug:
            continue
        # Le même Ticketing apparaît plusieurs fois dans l'arbre, sous des
        # formes plus ou moins complètes : on garde la plus riche.
        if slug not in par_slug or len(t) > len(par_slug[slug]):
            par_slug[slug] = t

    retenus = []
    for t in par_slug.values():
        if t.get("type") != "dated_events":
            continue                              # bon cadeau, offre…
        if not garder(t.get("venue") or {}):
            continue                              # autre salle
        retenus.append(t)
    return retenus


def sessions(session: requests.Session, shop: str, slug: str) -> List[str]:
    """Dates de début des séances d'un spectacle, en ISO avec fuseau."""
    r = base_get(f"{shop}/event/{slug}", session=session, headers=HEADERS,
                 timeout=25)
    r.raise_for_status()
    data = next_data(r.text)
    if data is None:
        return []
    out = set()
    for ed in collect(data, "EventDate", []):
        start = ed.get("startDate")
        if isinstance(start, str) and start:
            out.add(start)
    return sorted(out)


def fetch_venue(shop: str, venue: str, slug: str, category: Optional[str],
                garder: Callable[[dict], bool],
                etiquette: Optional[str] = None) -> List[Event]:
    """Un Event par séance d'une salle, sur l'horizon du projet.

    `etiquette` ne sert qu'aux messages d'erreur ; par défaut le nom du
    lieu.
    """
    tag = etiquette or venue
    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    today_iso, horizon_iso = today.isoformat(), horizon.isoformat()

    session = requests.Session()
    retenus = shows(session, shop, garder)
    if not retenus:
        # Boutique lisible mais aucun spectacle : la forme des objets a
        # probablement changé, ou le prédicat de lieu ne correspond plus.
        # On le signale, plutôt que de renvoyer une liste vide silencieuse
        # qu'aggregate.py ne distinguerait pas d'une panne.
        print(f"[{tag}] aucun spectacle daté retenu dans la boutique - "
              "structure Mapado ou nom de lieu modifié ?", file=sys.stderr)
        return []

    events: List[Event] = []
    illisibles = 0
    for i, show in enumerate(retenus):
        sl = show["slug"]
        title = (show.get("title") or "").strip()
        if not title:
            continue
        if i:
            time.sleep(MIN_INTERVAL)
        try:
            starts = sessions(session, shop, sl)
        except requests.RequestException as exc:
            # Une page qui tombe ne doit pas emporter les autres.
            print(f"[{tag}] {sl}: {exc}", file=sys.stderr)
            illisibles += 1
            continue

        url = f"{shop}/event/{sl}"
        image = image_url(show)
        for start in starts:
            # « 2026-08-19T19:30:00+02:00 » - on ne garde que le jour et
            # l'heure locale, le fuseau étant toujours celui de la salle.
            day, _, reste = start.partition("T")
            if day < today_iso or day > horizon_iso:
                continue
            events.append(Event(
                venue=venue,
                venue_slug=slug,
                title=title,
                subtitle=None,
                category=category,
                date_start=day,
                date_end=None,
                time=reste[:5] if len(reste) >= 5 else None,
                url=url,
                image=image,
            ))

    if illisibles:
        print(f"[{tag}] {illisibles} page(s) spectacle illisible(s)",
              file=sys.stderr)
    return events
