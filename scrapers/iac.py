"""Scraper for the IAC — Institut d'art contemporain (Villeurbanne).

Site artisanal de 2013 — jQuery 1.8.2, scripts datés du 29 mai 2013 —
mais qui a une qualité inattendue : ses dates sont balisées en
microdonnées. C'est ce qui rend ce scraper possible, car la prose des
fiches, elle, est un piège : celle du vernissage annonce « jeudi 17
septembre 2024 » alors que la fiche est classée en 2026, et que le 17
septembre 2026 est bien un jeudi. Un millésime copié-collé.

ON PART DE L'EXPOSITION, ET C'EST LE CHOIX CENTRAL DE CE SCRAPER. La
fiche d'une exposition in situ porte à la fois sa propre période et la
LISTE DE SES RENDEZ-VOUS SATELLITES, chacun avec son ou ses <time
datetime>. Une requête donne donc l'exposition et toute sa
programmation, là où parcourir les fiches une à une obligerait à lire
des dates en français.

L'ATTRIBUT itemprop DIT LA FORME de la date, et il faut le lire :

  itemprop="startDate endDate"   sur un seul <time> → une date unique
  itemprop="startDate" + "endDate" sur deux <time> → un intervalle

Et cet intervalle n'est PAS une série continue : « Visites en famille »
va du 11 octobre au 22 novembre, mais ne se joue que ces deux
dimanches-là. Seul le texte le dit. C'est sans conséquence ici — toutes
les fiches à intervalle sont des visites, et la seule qu'on garde est
celle du week-end, dont le rythme est dans son nom.

CE QU'ON GARDE. Tout, sauf les visites ; et parmi les visites, celles du
week-end seulement. Le reste — visites sur le pouce, en famille, en LSF,
visites-ateliers, PASS'Région Senior — est de la médiation en journée.
Les expositions en cours et à venir sont publiées comme des plages.

LES RELÂCHES DE LA VISITE DU WEEK-END sont annoncées dans sa fiche, en
français : « Pas de visite les dimanches 4 et 11 octobre et les 21 et 22
novembre ». Elles sont lues, car publier une visite annulée est une
faute plus lourde que d'en manquer une.

TOUT EST IN SITU par construction : on ne lit que l'index in situ. Les
expositions ex situ et les galeries nomades de l'IAC se tiennent
ailleurs, et n'ont donc rien à faire sous ce nom de lieu.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from datetime import date as Date, timedelta
from typing import Dict, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup

from . import detail_cache
from .base import Event, get as base_get

VENUE = "IAC Villeurbanne"
SLUG = "iac-villeurbanne"
BASE = "https://i-ac.eu"
# L'index in situ, et la section « à venir » qui porte les expositions
# dont la période n'a pas encore commencé.
INDEX = ("/fr/expositions/24_in-situ", "/fr/expositions/24_in-situ/a-venir")

HORIZON_DAYS = 180

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
                  "+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

_FICHE_EXPO = re.compile(r"^/fr/expositions/\d+_[^/]+/\d{4}/\d+_")
_HEURE = re.compile(r"\|\s*(\d{1,2})\s*[:h]\s*(\d{2})?")
_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})")

# Une visite, sauf celle du week-end : c'est la règle de tri demandée.
_VISITE = re.compile(r"visite", re.I)
_WEEKEND = re.compile(r"week[\s-]?end", re.I)
# Jours de la semaine que « week-end » désigne : samedi et dimanche.
JOURS_WEEKEND = (5, 6)

MOIS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
        "juin": 6, "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10,
        "novembre": 11, "decembre": 12}
_MOIS_RE = re.compile("|".join(MOIS))
_RELACHE = re.compile(r"pas\s+de\s+visite")
# La suite de quantièmes collée au nom du mois — même règle que pour
# agend'Arts : « les 21 et 22 novembre » donne le 21 et le 22.
_SUITE = re.compile(r"((?:(?:lundis?|mardis?|mercredis?|jeudis?|vendredis?"
                    r"|samedis?|dimanches?)\s+)?\d{1,2}(?:er)?\s*"
                    r"(?:,|et|&)?\s*)+$")
_QUANTIEME = re.compile(r"\b(\d{1,2})(?:er)?\b")

# Étiquettes que TYPE_BUCKETS (index.html) reconnaît. Ce qui n'est pas
# ici reste vide : categorie.py range alors la carte d'après le lieu,
# l'IAC étant un centre d'art (LIEUX_MONOGENRE → expo).
GENRES = (
    (re.compile(r"projection", re.I),  "projection"),
    (re.compile(r"performance", re.I), "performance"),
    (re.compile(r"vernissage", re.I),  "vernissage"),
    (re.compile(r"visite", re.I),      "visite"),
)


def exclu(titre: str) -> bool:
    """Un titre que ce scraper écarte : une visite, hors celle du week-end.

    Exposé, et non gardé pour soi, parce qu'aggregate.py doit appliquer la
    MÊME règle aux événements d'agrégateur. Sans cela le filtre resterait
    sans effet : le Petit Bulletin couvre bien l'IAC et republiait
    exactement les neuf visites qu'on venait de retirer.
    """
    t = titre or ""
    return bool(_VISITE.search(t)) and not bool(_WEEKEND.search(t))


def _norm(s: str) -> str:
    s = (s or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _plat(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def _genre(titre: str) -> Optional[str]:
    return next((g for rx, g in GENRES if rx.search(titre or "")), None)


def _bornes(bloc) -> Tuple[Optional[str], Optional[str]]:
    """(début, fin) d'un bloc, d'après les microdonnées.

    Un seul <time> portant « startDate endDate » est une date unique ;
    deux <time> distincts sont un intervalle.
    """
    debut = fin = None
    for t in bloc.find_all("time"):
        prop = t.get("itemprop") or ""
        m = _ISO.match(t.get("datetime") or "")
        if not m:
            continue
        if "startDate" in prop and debut is None:
            debut = m.group(1)
        if "endDate" in prop:
            fin = m.group(1)
    return debut, fin


def _relaches(txt: str) -> Set[str]:
    """Jours sans visite, annoncés en français dans la fiche.

    « Pas de visite les dimanches 4 et 11 octobre et les 21 et 22
    novembre » : on ne lit que la suite de quantièmes collée à chaque nom
    de mois, et seulement après la mention d'annulation.
    """
    t = _norm(txt)
    m = _RELACHE.search(t)
    if not m:
        return set()
    t = t[m.end():m.end() + 300]
    out: Set[str] = set()
    debut = 0
    for mm in _MOIS_RE.finditer(t):
        seg = t[debut:mm.start()].rstrip()
        debut = mm.end()
        s = _SUITE.search(seg)
        if not s:
            continue
        for q in _QUANTIEME.findall(s.group(0)):
            if 1 <= int(q) <= 31:
                out.add(f"{MOIS[mm.group(0)]:02d}-{int(q):02d}")
    return out


def _lire_satellite(url: str) -> Optional[dict]:
    """Heure, affiche et relâches d'un rendez-vous satellite.

    Les dates viennent de la fiche d'exposition, mieux balisée ; on
    n'ouvre celle du satellite que pour ce qu'elle seule porte.
    """
    try:
        r = base_get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"[IAC] {url}: {exc}", file=sys.stderr)
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    bloc = soup.select_one("#view-rdvsats")
    if bloc is None:
        return {"heure": None, "image": None, "relaches": []}
    h = _HEURE.search(_plat(bloc.select_one(".time")))
    img = bloc.select_one("figure img")
    src = img.get("src") if img else None
    return {
        "heure": f"{int(h.group(1)):02d}:{h.group(2) or '00'}" if h else None,
        "image": (BASE + src if src and src.startswith("/") else src),
        "relaches": sorted(_relaches(_plat(bloc.select_one("article")))),
    }


def _fiches_expo(session: requests.Session) -> List[str]:
    """Liens des expositions in situ, en cours comme à venir."""
    out: List[str] = []
    for chemin in INDEX:
        try:
            r = session.get(BASE + chemin, headers=HEADERS, timeout=30)
            r.raise_for_status()
        except requests.RequestException as exc:
            print(f"[IAC] {chemin}: {exc}", file=sys.stderr)
            continue
        for a in BeautifulSoup(r.text, "html.parser").find_all("a", href=True):
            if _FICHE_EXPO.match(a["href"]) and a["href"] not in out:
                out.append(a["href"])
    return out


def _weekends(debut: str, fin: str, horizon: Date,
              relaches: Set[str]) -> List[str]:
    """Samedis et dimanches de l'intervalle, relâches déduites."""
    d, f = Date.fromisoformat(debut), min(Date.fromisoformat(fin), horizon)
    out = []
    while d <= f:
        if d.weekday() in JOURS_WEEKEND and d.strftime("%m-%d") not in relaches:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def fetch() -> List[Event]:
    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    session = requests.Session()

    liens = _fiches_expo(session)
    if not liens:
        print(f"[IAC] aucune exposition lue sur {BASE}{INDEX[0]} "
              "— structure modifiée ?", file=sys.stderr)
        return []

    events: List[Event] = []
    ecartes: Dict[str, int] = {}
    sans_date = 0

    for lien in liens:
        try:
            r = session.get(BASE + lien, headers=HEADERS, timeout=30)
            r.raise_for_status()
        except requests.RequestException as exc:
            print(f"[IAC] {lien}: {exc}", file=sys.stderr)
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        vue = soup.select_one("#view-expos")
        if vue is None:
            continue

        # ── l'exposition elle-même, publiée comme une plage ──
        entete = vue.find("header")
        debut, fin = _bornes(entete) if entete else (None, None)
        titre = _plat(vue.select_one("h1"))
        if debut and titre:
            fin = fin or debut
            if not (fin < today.isoformat() or debut > horizon.isoformat()):
                img = vue.select_one("figure img")
                src = img.get("src") if img else None
                events.append(Event(
                    venue=VENUE, venue_slug=SLUG, title=titre,
                    subtitle=_plat(vue.select_one("h2")) or None,
                    category="exposition",
                    date_start=max(debut, today.isoformat()),
                    date_end=fin, time=None, url=BASE + lien,
                    image=(BASE + src if src and src.startswith("/") else src),
                ))
        else:
            sans_date += 1

        # ── ses rendez-vous satellites ──
        for a in vue.select("#right aside a[href]"):
            href = a["href"]
            if "/RDV-satellites_" not in href:
                continue
            nom = _plat(a.select_one("h1"))
            if exclu(nom):
                ecartes[nom] = ecartes.get(nom, 0) + 1
                continue
            d0, d1 = _bornes(a)
            if not d0:
                continue

            detail = detail_cache.get_details(
                BASE + href, _lire_satellite,
                fields=("heure", "image", "relaches")) or {}

            if d1 and d1 != d0:
                # Seule la visite du week-end arrive ici : son intervalle
                # est un vrai rythme, samedi et dimanche.
                jours = _weekends(d0, d1, horizon,
                                  set(detail.get("relaches") or ()))
            else:
                jours = [d0]

            for jour in jours:
                if not (today.isoformat() <= jour <= horizon.isoformat()):
                    continue
                events.append(Event(
                    venue=VENUE, venue_slug=SLUG, title=nom,
                    subtitle=_plat(a.select_one("h2")) or None,
                    category=_genre(nom),
                    date_start=jour, date_end=None,
                    time=detail.get("heure"),
                    url=BASE + href, image=detail.get("image"),
                ))

    if ecartes:
        detail = ", ".join(f"{k} ({v})" for k, v in sorted(ecartes.items()))
        print(f"[IAC] visites écartées : {detail}", file=sys.stderr)
    if sans_date:
        print(f"[IAC] {sans_date} exposition(s) sans période lisible",
              file=sys.stderr)
    if not events:
        print(f"[IAC] {len(liens)} exposition(s) lues, aucune date retenue "
              "— structure modifiée ?", file=sys.stderr)
    return events
