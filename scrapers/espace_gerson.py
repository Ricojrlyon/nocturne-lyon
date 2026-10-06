"""Scraper for Espace Gerson, café-théâtre (Lyon 5e).

Le site espacegerson.com/programmation est un Roadiz CMS thématisé par
Mapado, rendu serveur : il porte de la microdata schema.org exploitable
(itemprop startDate, name, url). On lit pourtant la BILLETTERIE Mapado
plutôt que le site, pour une raison décisive : le site n'indique pas le
lieu de chaque spectacle autrement que dans le TITRE - « … Bourse du
Travail Lyon 3ème », « … Salle Victor Hugo 69006 ». Filtrer là-dessus
serait une heuristique fragile.

Or l'Espace Gerson programme dans quatre salles. Relevé sur la boutique
au moment de l'écriture, sur 69 spectacles :

    46  Espace Gerson        Lyon 69005   <- les seuls qu'on veut
    13  Salle Victor Hugo    Lyon 69006
     7  Bourse du Travail    Lyon 69003   <- déjà scrappée en direct
     1  Salle Paul Garcin    Lyon 69001

Attribuer les 23 autres à Gerson serait faux, et les 7 de la Bourse du
Travail créeraient en plus des doublons au mauvais lieu, cette salle
ayant son propre scraper. La billetterie, elle, porte le Venue en clair :
le filtre se fait donc sur le NOM du lieu, sans heuristique.

Toute la mécanique de lecture est dans scrapers/mapado.py, partagée avec
les autres salles de la même plateforme.
"""
from __future__ import annotations

from typing import List

from . import mapado
from .base import Event

# Même graphie que le Petit Bulletin et que VENUE_ARRONDISSEMENT : c'est
# ce qui permet à la dédup de regrouper les deux sources - le Petit
# Bulletin remonte aussi les spectacles de cette salle.
VENUE = "Espace Gerson"
SLUG = "espace-gerson"
SHOP = "https://espacegerson.mapado.com"

# Nom du lieu tel que Mapado le renvoie. Comparé après normalisation
# (casse et accents), pour ne pas casser sur un « Espace Gerson  » avec
# une espace de trop ou une majuscule qui bouge.
VENUE_MAPADO = "espace gerson"

# Catégorie fixe : Mapado n'expose que des regroupements marketing.
# « café-théâtre » tombe dans le bucket humour du frontend, ce qui est
# juste pour cette salle - et évite les classements erronés du Petit
# Bulletin, qui range une partie de cette programmation en « Théâtre ».
CATEGORY = "café-théâtre"


def _est_gerson(venue: dict) -> bool:
    nom = (venue.get("name") or "").strip().lower()
    # Pas d'accents à retirer ici, mais on reste tolérant sur les espaces.
    return " ".join(nom.split()) == VENUE_MAPADO


def fetch() -> List[Event]:
    return mapado.fetch_venue(
        shop=SHOP,
        venue=VENUE,
        slug=SLUG,
        category=CATEGORY,
        garder=_est_gerson,
    )
