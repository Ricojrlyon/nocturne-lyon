"""Scraper for Improvidence, café-théâtre d'improvisation (Lyon 3e).

Pourquoi PAS improvidence.fr/agenda/ : la page est rendue côté client et
son HTML ne contient aucune date. Son endpoint WordPress
(admin-ajax.php, action « custom_query ») ne répond rien hors navigateur,
et la page mélange les salles de Lyon et de Bordeaux.

On passe donc par leur billetterie Mapado, qui est rendue serveur. Mapado
est un Next.js : chaque page embarque son état d'hydratation dans
<script id="__NEXT_DATA__">, sous forme d'objets d'API au format Hydra.
C'est ce JSON qu'on lit, PAS le HTML — les classes CSS de Mapado sont des
hachages de styled-components (« TicketingItem__Container-sc-1uevklp-0 »)
qui changent à chaque déploiement, alors que la structure des objets est
stable.

Deux étapes :
  1. la boutique liste les spectacles (objets Ticketing) — un seul appel,
     la collection n'est pas paginée (hydra:totalItems == nombre de
     membres) ;
  2. chaque page spectacle porte ses séances (objets EventDate) — un
     appel chacune.

Le filtrage ne repose sur aucune heuristique : chaque Ticketing porte son
Venue avec la ville en clair, et un champ « type » qui distingue les
spectacles datés des bons cadeaux (« offer ») et des événements sans date.

Pas de detail_cache ici, contrairement aux autres scrapers à pages
détail : son TTL de 30 jours convient à une heure de début, qui ne bouge
pas, mais pas à une LISTE de séances, qui s'enrichit au fil des semaines —
on sous-déclarerait systématiquement les dates ajoutées récemment. Les
~45 pages sont donc relues à chaque passage, avec 0,4 s entre deux
requêtes, soit une vingtaine de secondes.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date as Date, timedelta
from typing import List, Optional

import requests

from .base import Event

# Même graphie que le Petit Bulletin et que VENUE_ARRONDISSEMENT : c'est
# ce qui permet à la dédup de regrouper les deux sources, qui indexe par
# (lieu canonique, jour) — voir dedup.py.
VENUE = "Improvidence"
SLUG = "improvidence"
SHOP = "https://improvidence.mapado.com"
CITY = "Lyon"                   # Improvidence exploite aussi Bordeaux
IMG_HOST = "https://img.mapado.net"
IMG_SIZE = "600-600"            # les cartes font 392 px de large

# Catégorie fixe, et c'est délibéré. Mapado n'expose que des regroupements
# marketing (« Immanquables », « Divertissement ») inexploitables, et les
# catégories du Petit Bulletin classent MAL ces spectacles : « impro »
# tombe dans le bucket jazz du frontend (son motif contient \bimpro\b) et
# « classique et lyrique » dans classique. « café-théâtre » tombe dans
# humour, ce qui est juste — et ne contient pas « impro » comme mot isolé,
# donc n'est pas capté au passage par le bucket jazz.
CATEGORY = "café-théâtre"

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


def _next_data(html: str) -> Optional[dict]:
    """État d'hydratation Next.js embarqué dans la page."""
    m = _NEXT_DATA_RE.search(html or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _collect(node, wanted: str, out: list) -> list:
    """Tous les objets @type == wanted, à n'importe quelle profondeur.

    La forme exacte de l'arbre Next.js (pageProps, état du store…) n'est
    pas contractuelle ; parcourir en profondeur évite de dépendre d'un
    chemin qui bougerait au prochain déploiement.
    """
    if isinstance(node, dict):
        if node.get("@type") == wanted:
            out.append(node)
        for v in node.values():
            _collect(v, wanted, out)
    elif isinstance(node, list):
        for v in node:
            _collect(v, wanted, out)
    return out


def _image_url(ticketing: dict) -> Optional[str]:
    media = ticketing.get("mediaList") or []
    if not media or not isinstance(media[0], dict):
        return None
    path = (media[0].get("path") or "").strip()
    if not path:
        return None
    # Mapado sert une vignette redimensionnée sous <chemin>_thumbs/<taille>.
    ext = path.rsplit(".", 1)[-1] if "." in path else "jpeg"
    return f"{IMG_HOST}/{path}_thumbs/{IMG_SIZE}.{ext}"


def _shows(session: requests.Session) -> List[dict]:
    """Spectacles datés de la salle de Lyon."""
    r = session.get(SHOP + "/", headers=HEADERS, timeout=25)
    r.raise_for_status()
    data = _next_data(r.text)
    if data is None:
        raise RuntimeError("__NEXT_DATA__ introuvable sur la boutique Mapado")

    par_slug: dict = {}
    for t in _collect(data, "Ticketing", []):
        slug = (t.get("slug") or "").strip()
        if not slug:
            continue
        # Le même Ticketing apparaît plusieurs fois dans l'arbre, sous des
        # formes plus ou moins complètes : on garde la plus riche.
        if slug not in par_slug or len(t) > len(par_slug[slug]):
            par_slug[slug] = t

    shows = []
    for t in par_slug.values():
        if t.get("type") != "dated_events":
            continue                                  # bon cadeau, offre…
        if ((t.get("venue") or {}).get("city") or "") != CITY:
            continue                                  # Bordeaux
        shows.append(t)
    return shows


def _sessions(session: requests.Session, slug: str) -> List[str]:
    """Dates de début des séances d'un spectacle, en ISO avec fuseau."""
    r = session.get(f"{SHOP}/event/{slug}", headers=HEADERS, timeout=25)
    r.raise_for_status()
    data = _next_data(r.text)
    if data is None:
        return []
    out = set()
    for ed in _collect(data, "EventDate", []):
        start = ed.get("startDate")
        if isinstance(start, str) and start:
            out.add(start)
    return sorted(out)


def fetch() -> List[Event]:
    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    today_iso, horizon_iso = today.isoformat(), horizon.isoformat()

    session = requests.Session()
    shows = _shows(session)
    if not shows:
        # Boutique lisible mais aucun spectacle : la forme des objets a
        # probablement changé. On le signale, plutôt que de renvoyer une
        # liste vide silencieuse qu'aggregate.py ne distinguerait pas
        # d'une panne.
        print("[Improvidence] aucun spectacle daté à Lyon dans la boutique — "
              "structure Mapado modifiée ?", file=sys.stderr)
        return []

    events: List[Event] = []
    illisibles = 0
    for i, show in enumerate(shows):
        slug = show["slug"]
        title = (show.get("title") or "").strip()
        if not title:
            continue
        if i:
            time.sleep(MIN_INTERVAL)
        try:
            starts = _sessions(session, slug)
        except requests.RequestException as exc:
            # Une page qui tombe ne doit pas emporter les 44 autres.
            print(f"[Improvidence] {slug}: {exc}", file=sys.stderr)
            illisibles += 1
            continue

        url = f"{SHOP}/event/{slug}"
        image = _image_url(show)
        for start in starts:
            # « 2026-08-19T19:30:00+02:00 » — on ne garde que le jour et
            # l'heure locale, le fuseau étant toujours celui de la salle.
            day, _, reste = start.partition("T")
            if day < today_iso or day > horizon_iso:
                continue
            hhmm = reste[:5] if len(reste) >= 5 else None
            events.append(Event(
                venue=VENUE,
                venue_slug=SLUG,
                title=title,
                subtitle=None,
                category=CATEGORY,
                date_start=day,
                date_end=None,
                time=hhmm,
                url=url,
                image=image,
            ))

    if illisibles:
        print(f"[Improvidence] {illisibles} page(s) spectacle illisible(s)",
              file=sys.stderr)
    return events
