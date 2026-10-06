"""Scraper for the macLYON - Musée d'art contemporain (6e, Cité internationale).

Drupal sans JSON:API, mais dont la LISTE suffit, et c'est le choix
central de ce scraper : elle porte le titre, le sous-titre, la période,
l'affiche ET le type de chaque entrée. Aucune fiche à ouvrir - deux
requêtes pour toute la programmation.

Elle est même plus complète que les fiches. Le concert de musique de
chambre n'a AUCUN champ de date sur la sienne - ni « Date », ni
« Informations horaires » - et sa date, « Samedi 17 octobre 2026 », n'est
écrite que sur la liste. Lire les fiches ferait donc perdre un
événement, pas en gagner un.

HORS LES MURS, ET C'EST LE PIÈGE DE CE MUSÉE. Le macLYON répertorie sous
son propre agenda des expositions qui se tiennent ailleurs, et le champ
`.bold` de la liste le dit en clair : « Hors les murs » y figure au même
titre que « Exposition » ou « Concert ». Les deux entrées ainsi marquées,
à l'écriture, sont :

  Jeune création internationale  →  à l'IAC de Villeurbanne
  Musée sentimental              →  au Musée des Beaux-Arts de Lyon

soit précisément les deux expositions que nocturne scrappe déjà chez leur
véritable hôte. Sans ce filtre elles seraient publiées deux fois, sous
deux lieux différents - et la déduplication ne pourrait rien y voir,
puisqu'elle groupe PAR lieu.

Le champ « Lieu » des fiches dit la même chose, mais en prose (« Au Musée
des Beaux-Arts de Lyon. », « À l'Institut d'art contemporain de
Villeurbanne - IAC »), et l'un de ses libellés maison mentionne « Musée
d'art contemporain » - un mot que porte aussi l'Institut d'art
contemporain. Le marqueur de la liste est plus sûr parce qu'il est
catégoriel et non descriptif.

LES PÉRIODES sont écrites en français, l'année du début étant sous-
entendue : « Du 11 septembre au 14 mars 2027 » commence en 2026. On la
déduit de l'année de fin, sauf si le mois de début lui est postérieur,
l'exposition franchissant alors le nouvel an.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from datetime import date as Date, timedelta
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from .base import Event, HORIZON_JOURS, get as base_get

VENUE = "Musée d'Art Contemporain"
SLUG = "mac-lyon"
BASE = "https://www.mac-lyon.com"
# L'agenda porte tout ; /fr/expositions n'en est qu'un sous-ensemble, lu
# par précaution au cas où une exposition n'y figurerait pas.
LISTES = ("/fr/agenda", "/fr/expositions")

HORIZON_DAYS = HORIZON_JOURS

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
                  "+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

MOIS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
        "juin": 6, "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10,
        "novembre": 11, "decembre": 12}

# Le marqueur qui écarte une entrée : voir l'en-tête.
HORS_LES_MURS = "hors les murs"

# Types de la liste → étiquettes que TYPE_BUCKETS (index.html) reconnaît.
TYPES = {
    "exposition": "exposition",
    "concert":    "concert",           # → musique
    "visite":     "visite",            # → expo
    # Une nocturne de musée est une ouverture du soir - exposition, DJ
    # sets, open air. Rangée en « expos » comme celles des Beaux-Arts,
    # faute de bucket plus juste pour du musée après la tombée du jour.
    "nocturne":   "exposition",
    # « Invitation » n'est pas un genre mais une formule du musée : un
    # artiste montre un travail dans la macROOM. C'est une exposition.
    "invitation": "exposition",
}

# « Du 11 septembre au 14 mars 2027 » - l'année du début est facultative.
_PERIODE = re.compile(r"du\s+(\d{1,2})\s+([a-z]+)(?:\s+(20\d{2}))?\s+"
                      r"au\s+(\d{1,2})\s+([a-z]+)\s+(20\d{2})")
# « Samedi 17 octobre 2026 » - le jour de la semaine est optionnel.
_JOUR = re.compile(r"(?:\w+\s+)?(\d{1,2})\s+([a-z]+)\s+(20\d{2})")


def _norm(s: str) -> str:
    s = (s or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _plat(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def _bornes(txt: str) -> Optional[Tuple[Date, Date]]:
    """(début, fin) d'après le libellé de la liste, ou None."""
    t = _norm(txt)
    m = _PERIODE.search(t)
    if m:
        d, md, ad, f, mf, af = m.groups()
        if md not in MOIS or mf not in MOIS:
            return None
        try:
            an_f = int(af)
            # Année de début sous-entendue : celle de la fin, sauf si le
            # mois de début lui est postérieur - la période franchit alors
            # le nouvel an.
            an_d = int(ad) if ad else (an_f - 1 if MOIS[md] > MOIS[mf] else an_f)
            return Date(an_d, MOIS[md], int(d)), Date(an_f, MOIS[mf], int(f))
        except ValueError:
            return None
    m = _JOUR.search(t)
    if m and m.group(2) in MOIS:
        try:
            jour = Date(int(m.group(3)), MOIS[m.group(2)], int(m.group(1)))
        except ValueError:
            return None
        return jour, jour
    return None


def _cartes(session: requests.Session) -> Dict[str, dict]:
    """lien -> {titre, sous_titre, date, type, image} pour chaque entrée."""
    out: Dict[str, dict] = {}
    for chemin in LISTES:
        try:
            r = base_get(BASE + chemin, session=session, headers=HEADERS,
                         timeout=30)
            r.raise_for_status()
        except requests.RequestException as exc:
            print(f"[macLYON] {chemin}: {exc}", file=sys.stderr)
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for info in soup.select(".link-bloc__info"):
            bloc = info.find_parent(class_="strate") or info.parent
            a = bloc.find("a", href=True) if bloc else None
            if not (a and "/programmation/" in a["href"]) or a["href"] in out:
                continue
            # L'affiche est un fichier téléversé ; les autres <img> de la
            # carte sont des habillages (logo, symbole de copyright).
            img = next((i.get("src") for i in bloc.find_all("img")
                        if "/sites/mac/files" in (i.get("src") or "")), None)
            out[a["href"]] = {
                "titre": _plat(info.select_one(".item-title")),
                "sous_titre": _plat(info.select_one(".item-subtitle")) or None,
                "date": _plat(info.select_one(".date")),
                "type": _plat(info.select_one(".bold")).lstrip("- ").strip(),
                "image": BASE + img if img and img.startswith("/") else img,
            }
    return out


def fetch() -> List[Event]:
    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    session = requests.Session()

    cartes = _cartes(session)
    if not cartes:
        print(f"[macLYON] aucune carte lue sur {BASE}{LISTES[0]} "
              "- structure modifiée ?", file=sys.stderr)
        return []

    events: List[Event] = []
    ailleurs: List[str] = []
    types_inconnus: Dict[str, int] = {}
    sans_date = 0

    for lien, c in cartes.items():
        type_norm = _norm(c["type"])
        if HORS_LES_MURS in type_norm:
            ailleurs.append(c["titre"])
            continue

        bornes = _bornes(c["date"])
        if bornes is None:
            sans_date += 1
            continue
        debut, fin = bornes
        if fin < today or debut > horizon:
            continue

        categorie = TYPES.get(type_norm)
        if categorie is None and type_norm:
            types_inconnus[c["type"]] = types_inconnus.get(c["type"], 0) + 1

        events.append(Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=c["titre"],
            subtitle=c["sous_titre"],
            category=categorie,
            # Une exposition déjà ouverte commence, pour nous, aujourd'hui.
            date_start=max(debut, today).isoformat(),
            date_end=fin.isoformat() if fin != debut else None,
            time=None,
            url=BASE + lien,
            image=c["image"],
        ))

    if ailleurs:
        print(f"[macLYON] hors les murs, écartés : {', '.join(ailleurs)}",
              file=sys.stderr)
    if types_inconnus:
        # Un type absent de TYPES laisse la carte sans catégorie. On le
        # nomme pour qu'il soit ajouté, plutôt que de le deviner.
        print(f"[macLYON] type(s) non traduit(s) : "
              f"{', '.join(sorted(types_inconnus))}", file=sys.stderr)
    if sans_date:
        print(f"[macLYON] {sans_date} carte(s) sans période lisible",
              file=sys.stderr)
    if not events:
        print(f"[macLYON] {len(cartes)} carte(s) lues, aucune retenue "
              "- structure modifiée ?", file=sys.stderr)
    return events
