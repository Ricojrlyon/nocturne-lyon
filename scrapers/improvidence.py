"""Scraper for Improvidence, café-théâtre d'improvisation (Lyon 3e).

Pourquoi PAS improvidence.fr/agenda/ : la page est rendue côté client et
son HTML ne contient aucune date. Son endpoint WordPress
(admin-ajax.php, action « custom_query ») ne répond rien hors navigateur,
et la page mélange les salles de Lyon et de Bordeaux.

On passe donc par leur billetterie Mapado. Toute la mécanique de lecture
est dans scrapers/mapado.py, partagée avec les autres salles de la même
plateforme ; il ne reste ici que ce qui est propre à Improvidence.

Le filtre de lieu porte sur la VILLE : la boutique couvre Lyon et
Bordeaux, mais la salle lyonnaise est la seule à y figurer sous ce nom.
"""
from __future__ import annotations

from typing import List

from . import mapado
from .base import Event

# Même graphie que le Petit Bulletin et que VENUE_ARRONDISSEMENT : c'est
# ce qui permet à la dédup de regrouper les deux sources, qui indexe par
# (lieu canonique, jour) - voir dedup.py.
VENUE = "Improvidence"
SLUG = "improvidence"
SHOP = "https://improvidence.mapado.com"
CITY = "Lyon"                   # Improvidence exploite aussi Bordeaux

# Catégorie fixe, et c'est délibéré. Mapado n'expose que des regroupements
# marketing (« Immanquables », « Divertissement ») inexploitables, et les
# catégories du Petit Bulletin classent MAL ces spectacles : « impro »
# tombe dans le bucket jazz du frontend (son motif contient \bimpro\b) et
# « classique et lyrique » dans classique. « café-théâtre » tombe dans
# humour, ce qui est juste - et ne contient pas « impro » comme mot isolé,
# donc n'est pas capté au passage par le bucket jazz.
CATEGORY = "café-théâtre"


def fetch() -> List[Event]:
    return mapado.fetch_venue(
        shop=SHOP,
        venue=VENUE,
        slug=SLUG,
        category=CATEGORY,
        garder=lambda v: (v.get("city") or "") == CITY,
    )
