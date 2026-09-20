"""Scraper de la Chapelle de la Trinité, « musiques baroques et
irrégulières » (Lyon 2e).

La salle n'avait jusqu'ici que ce que les agrégateurs voulaient bien en
dire — onze cartes, quand sa saison en compte trente-quatre. Sa
billetterie est une boutique Mapado, trinitelyon.mapado.com, et toute la
mécanique de lecture est dans scrapers/mapado.py, partagée avec
l'Improvidence et l'Espace Gerson.

TOUS LES SPECTACLES DE LA BOUTIQUE SE JOUENT DANS LES MURS — les 34 y
portent le même Venue, « Chapelle de la Trinité », 29-31 rue de la
Bourse. Le prédicat de lieu ne sert donc à rien aujourd'hui ; il reste
parce qu'une boutique n'est pas une salle, et que le jour où la Trinité
programmera ailleurs (la Villa Gillet vient s'y réfugier pendant ses
travaux, l'inverse est possible), on ne veut pas publier ces soirées-là
sous ce nom.

LE TITRE SE COUPE EN DEUX, et c'est nécessaire au dédoublonnage. Mapado
écrit « Prima Donna - Le Concert de l'Hostel Dieu & Blandine de Sansal »
là où le Petit Bulletin écrit « Prima Donna ». La dédup compare les
titres à 0,70 de similarité : sans la coupe, la même soirée sortirait
deux fois, une fois par source. On publie donc le titre court et le
reste en sous-titre — ce que le champ attend, et ce que les autres
scrapers de salle font déjà.

LA CATÉGORIE SE DEVINE AU TITRE, faute de mieux : Mapado laisse
ticketingCategory à null sur les 34 spectacles. Trois mots suffisent à
séparer ce qui n'est pas un concert, relevé sur la saison 2026-2027 :

    chorégraphique          1   Uppercut, Clash Chorégraphique
    conférence, visite      2   Une expo dans le vent
    atelier                 1   Atelier de yoga sonore
    le reste               30   classique

« Danser sur l'Apocalypse » et « Danser sous l'orage » sont des
concerts : le motif de la danse exige donc le mot entier, pas son verbe.
Le défaut est « classique » parce que c'est l'identité de la salle, et
parce qu'une erreur y range l'événement dans la bonne famille du fil —
la musique — là où « autre » l'aurait exilé.
"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Optional, Tuple

from . import mapado
from .base import Event

# Même graphie que le Petit Bulletin et que VENUE_ARRONDISSEMENT : c'est
# ce qui permet à la dédup de regrouper les deux sources, qui indexe par
# (lieu canonique, jour) — voir dedup.py.
VENUE = "Chapelle de la Trinité"
SLUG = "chapelle-de-la-trinite"
SHOP = "https://trinitelyon.mapado.com"

# Nom du lieu tel que Mapado le renvoie, comparé sans casse ni accents :
# la boutique écrit « Chapelle de la Trinité » avec une espace de trop en
# fin de champ, et rien ne garantit que l'accent tienne.
VENUE_MAPADO = "chapelle de la trinite"

CATEGORIE_DEFAUT = "classique"
CATEGORIES = (
    # « Danser sur l'Apocalypse » est un concert : le mot entier, donc.
    (re.compile(r"chor[ée]graph|\bdanses?\b|ballet", re.I), "danse"),
    (re.compile(r"conf[ée]rence|visite\s+guid[ée]e|table\s+ronde|"
                r"rencontre\s+avec", re.I), "conférence"),
    (re.compile(r"\batelier\b|\bstage\b|masterclass", re.I), "atelier"),
)

# « Titre - sous-titre », tel que la billetterie nomme ses spectacles.
COUPE = " - "


def _norm(t: str) -> str:
    sans = "".join(c for c in unicodedata.normalize("NFD", (t or "").lower())
                   if unicodedata.category(c) != "Mn")
    return " ".join(sans.split())


def _est_la_trinite(venue: dict) -> bool:
    return _norm(venue.get("name")) == VENUE_MAPADO


def _coupe(titre: str) -> Tuple[str, Optional[str]]:
    tete, sep, reste = (titre or "").partition(COUPE)
    tete, reste = tete.strip(), reste.strip()
    if not sep or not tete or not reste:
        return (titre or "").strip(), None
    return tete, reste


def _categorie(titre: str, sous_titre: Optional[str]) -> str:
    texte = "%s %s" % (titre or "", sous_titre or "")
    for motif, nom in CATEGORIES:
        if motif.search(texte):
            return nom
    return CATEGORIE_DEFAUT


def fetch() -> List[Event]:
    events = mapado.fetch_venue(
        shop=SHOP,
        venue=VENUE,
        slug=SLUG,
        category=None,          # posée ci-dessous, spectacle par spectacle
        garder=_est_la_trinite,
    )
    for e in events:
        e.title, e.subtitle = _coupe(e.title)
        e.category = _categorie(e.title, e.subtitle)
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time, "·", e.category, "·", e.title,
              "·", e.subtitle or "-")
