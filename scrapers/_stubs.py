"""Templates for the remaining 16 venues.

Each function `fetch()` returns an empty list by default. To activate a venue:
1. Open the agenda URL in a browser, view source.
2. Identify the repeated event block (typically a <div>, <article> or <a>).
3. Pull out: title, date, time, url, image, category.
4. Replace the body below with real parsing logic — use le_sucre.py / les_subs.py
   as references; they cover the two most common patterns:
     - Le Sucre: each event is a single <a> with predictable inner structure.
     - Les Subs: each event is a wrapper card containing repeated date pills.

Once a scraper is implemented, register it in aggregate.py.
"""
from typing import List
import requests
from bs4 import BeautifulSoup

from .base import Event, parse_french_date, iso  # noqa: F401

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


# -----------------------------------------------------------------------------
# Concert venues
# -----------------------------------------------------------------------------

def fetch_radiant_bellevue() -> List[Event]:
    """https://radiant-bellevue.fr/  — TODO: agenda page selector."""
    return []


def fetch_la_rayonne() -> List[Event]:
    """https://larayonne.com/  — TODO."""
    return []


def fetch_transbordeur() -> List[Event]:
    """https://www.transbordeur.fr/  — TODO."""
    return []


def fetch_petit_salon() -> List[Event]:
    """Le Petit Salon — TODO: confirm URL (often https://www.lepetitsalon.fr/)."""
    return []


def fetch_sonic() -> List[Event]:
    """Le Sonic — péniche on the Saône. https://lesonic.fr/  — TODO."""
    return []


def fetch_heat() -> List[Event]:
    """HEAT — https://h-eat.eu/ — sister venue of Le Sucre, similar Arty Farty
    network so the structure may be very close. TODO."""
    return []


def fetch_station_mue() -> List[Event]:
    """Station Mue — TODO: confirm URL."""
    return []


def fetch_la_commune() -> List[Event]:
    """La Commune (Place Mazagran, Lyon 7e) — https://lacommune-lyon.com/  — TODO."""
    return []


# -----------------------------------------------------------------------------
# Theatres
# -----------------------------------------------------------------------------

def fetch_celestins() -> List[Event]:
    """Théâtre des Célestins — https://www.theatredescelestins.com/  — TODO."""
    return []


def fetch_tnp() -> List[Event]:
    """TNP — https://tnp-villeurbanne.com/  — TODO."""
    return []


def fetch_croix_rousse() -> List[Event]:
    """Théâtre de la Croix-Rousse — https://www.croix-rousse.com/  — TODO."""
    return []


def fetch_comedie_odeon() -> List[Event]:
    """Comédie Odéon — https://www.comedieodeon.com/  — TODO."""
    return []


# -----------------------------------------------------------------------------
# Museums
# -----------------------------------------------------------------------------

def fetch_confluences() -> List[Event]:
    """Musée des Confluences — https://www.museedesconfluences.fr/agenda  — TODO.

    Tip: this site exposes an internal JSON endpoint at /api/events on some
    sections. Inspect the network tab in browser dev-tools.
    """
    return []


def fetch_beaux_arts() -> List[Event]:
    """Musée des Beaux-Arts de Lyon — https://www.mba-lyon.fr/  — TODO."""
    return []


def fetch_mac() -> List[Event]:
    """Musée d'Art Contemporain — https://www.mac-lyon.com/  — TODO."""
    return []


def fetch_mac_bar() -> List[Event]:
    """MAC Bar (le bar du Musée d'Art Contemporain). The agenda is usually
    embedded in the MAC site; events tend to be DJ sets and listening sessions.
    TODO: identify the dedicated page or Instagram feed.
    """
    return []
