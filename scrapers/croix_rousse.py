"""Scraper for the Théâtre de la Croix-Rousse (4e, place Joannès Ambre).

WordPress dont l'API REST est OUVERTE — leur robots.txt n'interdit que
/wp-admin/, à la différence du TNP où le blocage Yoast de /wp-json/
imposait la lecture du HTML. On s'en sert donc pour la liste :

  /wp-json/wp/v2/programmation?saison=<id>   les spectacles de la saison,
                                             avec titre, lien et affiche
  /wp-json/wp/v2/event_type                  le genre, en clair
  /wp-json/wp/v2/saison                      pour choisir la saison

Les DATES, elles, ne sont pas dans l'API : le champ ACF est vide sur
tous les enregistrements. Elles vivent dans le HTML de chaque fiche, où
la structure est heureusement nette et sans ambiguïté.

  .seances-dates-horaires   un bloc par série, son <h4> portant le mois
                            et l'ANNÉE — pas d'inférence à faire
  .mobile-seances           une ligne par jour : « jeu 24 › … »
  .mobile-horaires          UN LIEN PAR SÉANCE, chacun vers sa billetterie

Ce dernier point est le seul vraiment piégeux. « 14h30 » et « 19h30 »
sur la même ligne sont deux séances, deux liens distincts ; « 17h > 17h50 »
est une séance unique affichée avec son heure de fin, dans un lien
unique. Compter les liens tranche, lire le texte non.

Le <h4> peut couvrir deux mois — « 30 septembre → 1 octobre 2026 ». On
essaie donc les combinaisons mois × année qu'il propose et l'on garde
celle dont le jour de la semaine annoncé tombe juste. Une combinaison
sans concordance fait écarter la séance plutôt que publier une date
fausse.
"""
from __future__ import annotations

import html as _html
import itertools
import re
import sys
import unicodedata
from datetime import date as Date, timedelta
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from . import detail_cache
from .base import Event, get as base_get

VENUE = "Théâtre de la Croix-Rousse"
SLUG = "croix-rousse"
BASE = "https://www.croix-rousse.com"
API = BASE + "/wp-json/wp/v2"

HORIZON_DAYS = 180
PER_PAGE = 100
MAX_PAGES = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
                  "+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# Deux termes d'event_type désignent un public et non un genre. Ils sont
# écartés du choix de catégorie, sans quoi « pour les enfants » aurait pu
# l'emporter sur « cirque ».
PUBLICS = ("pour les", "en famille", "jeune public", "tout public")

MOIS_CANON = ("janvier", "fevrier", "mars", "avril", "mai", "juin",
              "juillet", "aout", "septembre", "octobre", "novembre",
              "decembre")
JOURS_COURTS = ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")

_ANNEE = re.compile(r"\b(20\d{2})\b")
_MOT = re.compile(r"[a-z]+")
_JOUR_LIGNE = re.compile(r"\b(lun|mar|mer|jeu|ven|sam|dim)\w*\s*(\d{1,2})\b")
_HEURE = re.compile(r"\b(\d{1,2})\s*h\s*(\d{2})?\b")


def _texte(s: Optional[str]) -> str:
    """L'API WordPress rend du HTML, pas du texte : title.rendered vaut
    « On a failli t&rsquo;appeler Marthe ! », et les noms de taxonomie
    portent des &amp;. Sans décodage l'entité s'afficherait telle quelle
    sur la carte."""
    return _html.unescape(s or "").strip()


def _norm(s: Optional[str]) -> str:
    s = (s or "").lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)


def _mois(jeton: str) -> Optional[int]:
    """Numéro du mois, par préfixe unique — « sept » ne peut être que
    septembre. Un préfixe ambigu (« jui ») est refusé."""
    if len(jeton) < 3:
        return None
    t = [i for i, m in enumerate(MOIS_CANON, 1) if m.startswith(jeton)]
    return t[0] if len(t) == 1 else None


def _saison_attendue(today: Date) -> str:
    """La saison court de septembre à juin : « saison 26.27 »."""
    debut = today.year if today.month >= 9 else today.year - 1
    return f"saison {debut % 100:02d}.{(debut + 1) % 100:02d}"


def _api(session: requests.Session, chemin: str, **params) -> list:
    r = session.get(f"{API}/{chemin}", params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    d = r.json()
    return d if isinstance(d, list) else []


def _resoudre_seance(jour_court: str, jj: int, mois: List[int],
                     annees: List[int]) -> tuple:
    """(date, concordance) pour une ligne de séance.

    Le jour de la semaine sert à DÉPARTAGER, pas à opposer un veto. Quand
    le <h4> ne propose qu'une seule combinaison mois-année, la date est
    entièrement déterminée par la page et le nom du jour n'est qu'une
    étiquette redondante — qui peut porter une coquille : le site annonce
    « mar 26 » pour le 26 mai 2027, un mercredi. Rejeter la date sur cette
    foi perdrait une vraie représentation.

    Avec plusieurs combinaisons en revanche, le nom du jour est la seule
    chose qui tranche, et une absence de concordance rend None.
    """
    combos = list(itertools.product(mois, annees))
    valides = []
    for mo, an in combos:
        try:
            valides.append(Date(an, mo, jj))
        except ValueError:
            continue
    if not valides:
        return None, True
    concordantes = [d for d in valides if JOURS_COURTS[d.weekday()] == jour_court]
    if concordantes:
        return concordantes[0], True
    if len(valides) == 1:
        return valides[0], False      # date certaine, étiquette fautive
    return None, True


def _lire_fiche(url: str) -> Optional[dict]:
    """Représentations d'un spectacle. Rendu à detail_cache."""
    try:
        r = base_get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"[Croix-Rousse] {url}: {exc}", file=sys.stderr)
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    seances: List[List] = []
    discordantes: List[str] = []
    incoherentes = 0
    for bloc in soup.select(".seances-dates-horaires"):
        titre = bloc.select_one("h4")
        entete = _norm(titre.get_text(" ", strip=True)) if titre else ""
        annees = [int(a) for a in _ANNEE.findall(entete)]
        mois = [n for n in (_mois(m) for m in _MOT.findall(entete)) if n]
        if not annees or not mois:
            continue
        for ligne in bloc.select(".mobile-seances"):
            mj = _JOUR_LIGNE.search(_norm(ligne.get_text(" ", strip=True)))
            if not mj:
                continue
            d, concorde = _resoudre_seance(mj.group(1), int(mj.group(2)),
                                            mois, annees)
            if d is None:
                incoherentes += 1
                continue
            if not concorde:
                discordantes.append(f"{mj.group(1)} {mj.group(2)} -> {d}")
            # UN LIEN PAR SÉANCE. Lire le texte confondrait « 17h > 17h50 »
            # — une séance et son heure de fin — avec « 14h30 19h30 », qui
            # en est deux.
            liens = ligne.select(".mobile-horaires a")
            textes = [a.get_text(" ", strip=True) for a in liens] or \
                     [ligne.select_one(".mobile-horaires").get_text(" ", strip=True)
                      if ligne.select_one(".mobile-horaires") else ""]
            for t in textes:
                mh = _HEURE.search(t)          # la PREMIÈRE heure du lien
                heure = (f"{int(mh.group(1)):02d}:{mh.group(2) or '00'}"
                         if mh else None)
                seances.append([d.isoformat(), heure])
    if incoherentes:
        print(f"[Croix-Rousse] {incoherentes} date(s) indéterminable(s) — "
              f"{url}", file=sys.stderr)
    if discordantes:
        # Date retenue quand même : voir _resoudre_seance. Signalé pour
        # qu'une discordance SYSTÉMATIQUE — signe d'un parseur qui dérive —
        # se distingue d'une coquille isolée du site.
        print(f"[Croix-Rousse] jour de la semaine discordant, date retenue : "
              f"{', '.join(discordantes)} — {url}", file=sys.stderr)
    return {"seances": seances}


def _categorie(ids: List[int], noms: Dict[int, str]) -> Optional[str]:
    for i in ids or []:
        nom = _texte(noms.get(i))
        if nom and not any(p in _norm(nom) for p in PUBLICS):
            return nom
    return None


def fetch() -> List[Event]:
    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    session = requests.Session()

    genres = {t["id"]: t["name"] for t in
              _api(session, "event_type", per_page=PER_PAGE)}

    saisons = _api(session, "saison", per_page=PER_PAGE)
    attendue = _saison_attendue(today)
    saison = next((s for s in saisons if _norm(s["name"]) == attendue), None)
    if saison is None:
        # Le nom a changé de forme : on retombe sur la saison la mieux
        # fournie plutôt que de ne rien rendre.
        saison = max(saisons, key=lambda s: s.get("count", 0), default=None)
        if saison is None:
            print("[Croix-Rousse] aucune saison exposée par l'API",
                  file=sys.stderr)
            return []
        print(f"[Croix-Rousse] saison « {attendue} » introuvable, repli sur "
              f"« {saison['name']} »", file=sys.stderr)

    spectacles = []
    for page in range(1, MAX_PAGES + 1):
        lot = _api(session, "programmation", saison=saison["id"],
                   per_page=PER_PAGE, page=page)
        spectacles.extend(lot)
        if len(lot) < PER_PAGE:
            break

    if not spectacles:
        print(f"[Croix-Rousse] la saison « {saison['name']} » ne contient "
              f"aucun spectacle", file=sys.stderr)
        return []

    events: List[Event] = []
    for sp in spectacles:
        lien = sp.get("link")
        titre = _texte((sp.get("title") or {}).get("rendered"))
        if not lien or not titre:
            continue
        src = sp.get("uagb_featured_image_src") or {}
        pleine = src.get("full") if isinstance(src, dict) else None
        image = pleine[0] if isinstance(pleine, list) and pleine else None
        categorie = _categorie(sp.get("event_type") or [], genres)

        d = detail_cache.get_details(lien, _lire_fiche, fields=("seances",))
        for jour_iso, heure in (d.get("seances") or []):
            if not (today.isoformat() <= jour_iso <= horizon.isoformat()):
                continue
            events.append(Event(
                venue=VENUE,
                venue_slug=SLUG,
                title=titre,
                subtitle=None,
                category=categorie,
                date_start=jour_iso,
                date_end=None,
                time=heure,
                url=lien,
                image=image,
            ))
    return events
