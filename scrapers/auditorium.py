"""Scraper for the Auditorium-Orchestre national de Lyon (3e).

Drupal, lu en deux temps, et c'est le choix central de ce scraper :

  /fr/agenda/AAAAMM   une page par mois, une carte par spectacle :
                      genre, titre, sous-titre, affiche, lien
  la fiche            l'encadré « Dates » et « Lieu »

LA CARTE NE SUFFIT PAS. Elle date le spectacle « jeu. 1 oct » — sans
année, et sans heure. La fiche, elle, écrit « Jeu. 1 oct 2026 à 20h Ven.
2 oct 2026 à 18h » : le millésime y est, et surtout une heure PAR
SÉANCE. Les deux dates d'un même concert n'ont pas le même horaire — 20h
le jeudi, 18h le vendredi — et publier la première pour les deux serait
faux un soir sur deux. On ouvre donc chaque fiche, ce que detail_cache
rend supportable : cent cinquante requêtes au premier passage, aucune
ensuite tant que le cache est frais.

LES CARTES SONT EN DOUBLE dans le HTML — 199 balises <article> pour 148
spectacles réels sur la saison. Le dédoublonnage se fait sur le lien.
Sans lui on annoncerait un tiers d'événements de trop.

HORS LES MURS. L'orchestre joue à la Salle Molière, à Tassin, à Grenoble,
à Évian, jusqu'à Bruxelles. La Salle Molière est déjà dans nocturne : la
publier sous « Auditorium de Lyon » créerait des doublons que le dedup ne
peut pas voir, puisqu'il groupe PAR lieu. Le champ « Lieu » de la fiche
tranche, et l'on retient une liste blanche de salles maison plutôt qu'une
liste noire : les lieux extérieurs sont un ensemble ouvert — n'importe
quelle ville — là où les salles du bâtiment sont une liste courte et
fermée. Toute mention inconnue est signalée, pour qu'une nouvelle salle
maison se voie au lieu de disparaître en silence.

DEUX GENRES SONT ÉCARTÉS, pour des raisons différentes.

Les SÉANCES SCOLAIRES ne sont pas des sorties : « 8 € par élève, gratuit
pour l'enseignant et deux accompagnateurs par classe » — elles sont
réservées aux groupes scolaires. L'URL les nomme (/scolaires/) et le
genre aussi.

Les ATELIERS, eux, sont bien publics — éveil musical, atelier en
famille, sur inscription. Ils sont écartés pour une raison de mesure :
ils pesaient 104 des 186 événements du lieu, davantage que toute sa
programmation de concerts, et une même séance se répète à 9h, 10h et 11h
le même matin. L'Auditorium serait devenu la première source d'ateliers
de nocturne, ce qu'il n'est pas.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from datetime import date as Date, timedelta
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from . import detail_cache
from .base import Event, get as base_get

VENUE = "Auditorium de Lyon"
SLUG = "auditorium-lyon"
BASE = "https://www.auditorium-lyon.com"

HORIZON_DAYS = 180

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
                  "+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

MOIS_CANON = ("janvier", "fevrier", "mars", "avril", "mai", "juin",
              "juillet", "aout", "septembre", "octobre", "novembre",
              "decembre")

# Salles du bâtiment. Tout ce qui n'est pas là est tenu pour hors les
# murs — et signalé, afin qu'une salle maison nouvelle ne soit pas
# silencieusement perdue.
SALLES_MAISON = ("grande salle", "espace decouverte", "auditorium de lyon",
                 "salle proton-de-la-chapelle", "studio")

# Genres du site → étiquettes que TYPE_BUCKETS (index.html) reconnaît.
# Le site mêle aux genres des publics (« En famille ») et des formules
# tarifaires (« Individuels », « Gratuit ») : ce qui n'est pas ici reste
# sans catégorie plutôt que d'être classé faux.
GENRES = {
    "symphonique":           "symphonique",          # → classique
    "musique de chambre":    "musique de chambre",   # → classique
    "recital":               "classique",
    "recital amateur":       "classique",
    "orgue":                 "classique",
    "ensemble invite":       "classique",
    "jeunes talents":        "classique",
    "jazz":                  "jazz",
    "chanson":               "chanson",
    "rap":                   "rap",
    # Le pluriel de « Musiques du monde » ne correspond à aucun motif :
    # le bucket « monde » attend « musique du monde » au singulier.
    "musiques du monde":     "musique du monde",
    "cine-concert":          "ciné-concert",         # → musique
    "conference":            "conférence",
    "conference-concert":    "conférence",
    "afterwork":             "concert",
    "pause-dejeuner":        "concert",
    "en famille":            "concert",
}

# Genres écartés — voir l'en-tête. Le test porte sur le genre normalisé,
# ce qui couvre « Atelier enfants », « Atelier sonore », « Concert
# scolaire » et « Ciné-concert scolaire » sans les énumérer.
GENRES_ECARTES = ("atelier", "scolaire")

_JOUR = re.compile(r"(\d{1,2})\s+([A-Za-zÀ-ÿ]{3,9})\.?\s+(20\d{2})")
_HEURE = re.compile(r"(?:à|a|de)\s*(\d{1,2})\s*h\s*(\d{2})?")


def _norm(s: str) -> str:
    s = (s or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _plat(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def _mois(jeton: str) -> Optional[int]:
    """Numéro du mois d'après une abréviation, si elle est sans ambiguïté.

    Le site abrège librement — sep, fév, déc, juil. On résout par préfixe
    unique, ce qui accepte toutes ces formes sans les énumérer.
    """
    j = _norm(jeton).rstrip(".")
    cands = [i for i, m in enumerate(MOIS_CANON, 1) if m.startswith(j)]
    return cands[0] if len(cands) == 1 else None


def _seances(txt: str) -> List[Tuple[str, Optional[str]]]:
    """(date ISO, heure) pour chaque séance de l'encadré « Dates ».

    Trois formes coexistent : « Jeu. 1 oct 2026 à 20h », « Jeu. 18 fév
    2027 de 9h30 à 12h », et « Ven. 4 déc 2026 Horaire communiqué
    ultérieurement » — sans heure. L'heure est cherchée entre une date et
    la suivante, jamais au-delà : sinon la seconde séance hériterait de
    l'horaire de la première.
    """
    out: List[Tuple[str, Optional[str]]] = []
    trouves = list(_JOUR.finditer(txt))
    for i, m in enumerate(trouves):
        mois = _mois(m.group(2))
        if mois is None:
            continue
        try:
            jour = Date(int(m.group(3)), mois, int(m.group(1)))
        except ValueError:
            continue
        fin = trouves[i + 1].start() if i + 1 < len(trouves) else len(txt)
        h = _HEURE.search(txt[m.end():fin])
        heure = f"{int(h.group(1)):02d}:{h.group(2) or '00'}" if h else None
        out.append((jour.isoformat(), heure))
    return out


def _lire_fiche(url: str) -> Optional[dict]:
    """Séances et lieu réel d'un spectacle, lus dans l'encadré de la fiche."""
    try:
        r = base_get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"[Auditorium] {url}: {exc}", file=sys.stderr)
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    bloc: Dict[str, str] = {}
    for dt in soup.select("aside dl dt"):
        label = _norm(_plat(dt)).strip()
        vals = []
        for sib in dt.find_next_siblings():
            if sib.name == "dt":
                break
            if sib.name == "dd":
                vals.append(_plat(sib))
        if vals:
            bloc[label] = " ".join(vals)
    return {"seances": _seances(bloc.get("dates") or bloc.get("date") or ""),
            "lieu": bloc.get("lieu")}


def _chez_eux(lieu: Optional[str]) -> bool:
    return any(s in _norm(lieu) for s in SALLES_MAISON)


def _mois_horizon(today: Date, horizon: Date) -> List[str]:
    out, an, mo = [], today.year, today.month
    while (an, mo) <= (horizon.year, horizon.month):
        out.append(f"{an}{mo:02d}")
        an, mo = (an + 1, 1) if mo == 12 else (an, mo + 1)
    return out


def fetch() -> List[Event]:
    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    session = requests.Session()

    # Une carte par spectacle, dédoublonnée sur le lien : le HTML en
    # sert deux exemplaires, et un spectacle figure sur la page de
    # chacun des mois où il joue.
    cartes: Dict[str, dict] = {}
    for ym in _mois_horizon(today, horizon):
        url = f"{BASE}/fr/agenda/{ym}"
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
        except requests.RequestException as exc:
            print(f"[Auditorium] {url}: {exc}", file=sys.stderr)
            continue
        for art in BeautifulSoup(r.text, "html.parser").select("article"):
            a = art.find("a", href=True)
            titre = art.select_one(".event-title")
            if not (a and titre) or a["href"] in cartes:
                continue
            cat = art.select_one(".event-categories h3")
            img = art.find("img")
            sous = art.select_one(".s-labeur4")
            cartes[a["href"]] = {
                "titre": _plat(titre),
                "sous_titre": _plat(sous) or None,
                # Le genre précède la barre ; ce qui suit est un lieu.
                "genre": (_plat(cat).split("|")[0].strip() if cat else ""),
                "image": BASE + img["src"] if img and img.get("src", "")
                         .startswith("/") else (img.get("src") if img else None),
            }

    if not cartes:
        print(f"[Auditorium] aucune carte lue sur {BASE}/fr/agenda "
              "— structure modifiée ?", file=sys.stderr)
        return []

    events: List[Event] = []
    ailleurs: Dict[str, int] = {}
    genres_inconnus: Dict[str, int] = {}
    ecartes: Dict[str, int] = {}
    sans_date = 0

    for lien, c in cartes.items():
        genre_norm = _norm(c["genre"])
        # Le genre suffit presque toujours ; l'URL rattrape la carte dont
        # le genre est vide, ce qui arrive sur les pages de saison.
        motif = next((m for m in GENRES_ECARTES if m in genre_norm), None)
        if motif is None and "/scolaires/" in lien:
            motif = "scolaire"
        if motif:
            ecartes[motif] = ecartes.get(motif, 0) + 1
            continue

        detail = detail_cache.get_details(BASE + lien, _lire_fiche,
                                          fields=("seances", "lieu")) or {}
        seances = detail.get("seances") or []
        if not seances:
            sans_date += 1
            continue
        lieu = detail.get("lieu")
        if not _chez_eux(lieu):
            ailleurs[lieu or "(non précisé)"] = ailleurs.get(
                lieu or "(non précisé)", 0) + 1
            continue

        categorie = GENRES.get(genre_norm)
        if categorie is None and genre_norm:
            genres_inconnus[c["genre"]] = genres_inconnus.get(c["genre"], 0) + 1

        for jour, heure in seances:
            if not (today.isoformat() <= jour <= horizon.isoformat()):
                continue
            events.append(Event(
                venue=VENUE,
                venue_slug=SLUG,
                title=c["titre"],
                subtitle=c["sous_titre"],
                category=categorie,
                date_start=jour,
                date_end=None,
                time=heure,
                url=BASE + lien,
                image=c["image"],
            ))

    if ecartes:
        detail = ", ".join(f"{v} {k}" for k, v in sorted(ecartes.items()))
        print(f"[Auditorium] écartés : {detail}", file=sys.stderr)
    if ailleurs:
        detail = ", ".join(f"{k} ({v})" for k, v in sorted(ailleurs.items()))
        print(f"[Auditorium] hors les murs, écartés : {detail}",
              file=sys.stderr)
    if genres_inconnus:
        # Un genre absent de GENRES laisse la carte sans catégorie. On le
        # nomme pour qu'il soit ajouté, plutôt que de le classer au hasard.
        print(f"[Auditorium] genre(s) non traduit(s) : "
              f"{', '.join(sorted(genres_inconnus))}", file=sys.stderr)
    if sans_date:
        print(f"[Auditorium] {sans_date} fiche(s) sans date lisible",
              file=sys.stderr)
    if not events:
        print(f"[Auditorium] {len(cartes)} carte(s) lues, aucune séance "
              "retenue — structure modifiée ?", file=sys.stderr)
    return events
