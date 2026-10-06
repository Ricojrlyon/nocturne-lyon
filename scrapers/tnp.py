"""Scraper for the TNP - Théâtre National Populaire (Villeurbanne).

Le site est un WordPress, mais son robots.txt INTERDIT /wp-json/ : l'API
REST est donc hors limites, et on lit le HTML. C'est le seul scraper du
dépôt à s'être vu refuser une API qui existait - le blocage vient du
réglage Yoast par défaut, pas d'une hostilité aux robots, mais un
robots.txt se respecte tel qu'il est écrit.

Heureusement /agenda/ rend la SAISON ENTIÈRE en une page : 181
représentations de septembre à juin, avec des attributs `datetime`
lisibles à la machine plutôt que des dates en toutes lettres. Une seule
requête suffit donc pour tout le calendrier.

  .agenda__month-day            une journée, sa date dans un <time>
  .agenda-manifestation-item    une représentation : heure, titre, salle

Les affiches ne sont pas dans l'agenda : elles viennent de l'`og:image`
de chaque fiche spectacle. Dix-sept fiches à charger dans l'horizon, pas
cent vingt-trois - le même spectacle se joue dix à dix-sept fois.
C'était le manque à combler, le Petit Bulletin remontant cette salle
sans une seule image.

Les tournées vivent sur une page séparée (/productions-du-tnp/tournees-
saison/) que ce scraper ne lit pas. L'agenda connaît les deux salles de
la maison, Roger-Planchon et Jean-Bouise, et parfois « Hors les murs » :
« Avant que les mots ne retournent à l'air » est un parcours dans
l'espace public, dont le point de départ n'est donné qu'à l'inscription.
Publié tel quel, il envoyait au TNP (BUG-33). Il reste publié, mais
marqué hors les murs sans salle nommée (OFFSITE_PLUSIEURS) : la page
l'étiquette « ailleurs ». Une autre salle inconnue qui apparaîtrait est
signalée, sans être écartée : le TNP peut légitimement ouvrir un
troisième espace, et perdre ses représentations en silence serait pire
que de les publier.
"""
from __future__ import annotations

import re
import sys
import time
import unicodedata
from datetime import date as Date, timedelta
from typing import Dict, List, Optional
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from .base import Event, OFFSITE_PLUSIEURS, get as base_get

# Graphie du Petit Bulletin, qui remonte aussi cette salle : c'est ce qui
# permet à la dédup de regrouper les deux sources.
VENUE = "TNP - Théâtre National Populaire"
SLUG = "tnp"
BASE = "https://www.tnp-villeurbanne.com"
AGENDA = BASE + "/agenda/"

# Le site n'expose aucune taxonomie de genre - ni classe de body, ni
# libellé sur la fiche. « théâtre » est le défaut juste pour un théâtre
# national ; les rares pièces dansées y perdent leur nuance, mais elles
# restent dans la bonne famille d'affichage.
CATEGORY = "théâtre"

HORIZON_DAYS = 180
MIN_INTERVAL = 0.4
MAX_FICHES = 60          # garde-fou : 17 aujourd'hui, jamais 60

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
                  "+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# Salles de la maison. Sert uniquement à SIGNALER une salle inconnue, pas
# à filtrer : voir le docstring.
SALLES = ("roger-planchon", "jean-bouise")

_JOUR_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HEURE = re.compile(r"^\d{1,2}:\d{2}$")


def _norm(s: Optional[str]) -> str:
    s = (s or "").lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _jour_de(bloc) -> Optional[str]:
    """Date de la journée, prise sur le premier <time> qui EST une date.

    Le bloc contient aussi les <time> des représentations, en HH:MM :
    on ne peut donc pas se contenter du premier venu.
    """
    for t in bloc.select("time[datetime]"):
        v = (t.get("datetime") or "").strip()
        if _JOUR_ISO.match(v):
            return v
    return None


def _titre_et_sous_titre(a) -> tuple:
    """Le sous-titre vit dans un <span class="subtitle"> DANS le lien.

    Sans l'en extraire, get_text() colle les deux : « Une petite
    affaired'après Seul dans Berlin ». Outre la laideur, le titre collé
    ressemble moins à celui du Petit Bulletin et la déduplication entre
    les deux sources s'en trouverait affaiblie.
    """
    span = a.select_one(".subtitle")
    sous = span.get_text(" ", strip=True) if span else None
    if span:
        span.extract()
    titre = a.get_text(" ", strip=True)
    return titre, (sous or None)


# Largeur visée pour une affiche : les cartes font 400 px de large au plus,
# 800 couvre donc les écrans denses.
LARGEUR_AFFICHE = 800


def _taille_proche(img) -> Optional[str]:
    """Parmi les tailles que la page propose pour cette image (srcset :
    « url 465w, url 800w, … »), celle dont la largeur est la plus proche
    de LARGEUR_AFFICHE. None si la balise n'a pas de srcset."""
    choix = []
    for morceau in re.split(r",\s+", (img.get("srcset") or "").strip()):
        m = re.fullmatch(r"(\S+)\s+(\d+)w", morceau.strip())
        if not m:
            continue
        url = m.group(1)
        if url.startswith("/") and not url.startswith("//"):
            url = BASE + url
        if url.startswith("http"):
            choix.append((abs(int(m.group(2)) - LARGEUR_AFFICHE), url))
    return min(choix)[1] if choix else None


def _affiche(session: requests.Session, url: str) -> Optional[str]:
    """L'affiche de la fiche spectacle : son og:image, dans la taille la
    plus proche de 800 px que la page propose pour la même image.

    L'og:image est l'original, 1,3 à 1,6 Mo pour une carte de 400 px. La
    page montre la même image avec ses tailles réduites (srcset :
    « …-800x518.v1776848314.jpg 800w, … »), retrouvée par le chemin du
    fichier : 120 à 200 Ko en 800 px. Faute de la retrouver, l'og:image,
    comme avant.
    """
    r = base_get(url, session=session, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    og = soup.select_one('meta[property="og:image"]')
    src = (og.get("content") or "").strip() if og else ""
    if not src.startswith("http"):
        return None
    # « /app/uploads/2026/04/1_7minutescFelipe_Dupouy », suivi d'un tiret
    # (une taille) ou d'un point (l'original versionné) : pas le fichier
    # d'à côté au nom plus long.
    racine = re.compile(re.escape(re.sub(r"\.\w+$", "", urlsplit(src).path)) + r"[-.]")
    for img in soup.find_all("img"):
        if racine.search(img.get("srcset") or ""):
            reduite = _taille_proche(img)
            if reduite:
                return reduite
    return src


def fetch() -> List[Event]:
    today = Date.today()
    debut = today.isoformat()
    fin = (today + timedelta(days=HORIZON_DAYS)).isoformat()

    session = requests.Session()
    r = base_get(AGENDA, session=session, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    lignes = []
    salles_inconnues = set()
    hors_les_murs = set()
    for bloc in soup.select(".agenda__month-day"):
        jour = _jour_de(bloc)
        if not jour or not (debut <= jour <= fin):
            continue
        for item in bloc.select(".agenda-manifestation-item"):
            a = item.select_one(".agenda-manifestation-item__title")
            if not a or not a.get("href"):
                continue
            titre, sous = _titre_et_sous_titre(a)
            if not titre:
                continue
            t = item.select_one(".agenda-manifestation-item__time")
            heure = (t.get("datetime") or "").strip() if t else ""
            p = item.select_one(".manifestation-place")
            salle = p.get_text(" ", strip=True) if p else ""
            # « Hors les murs » n'est pas une troisième salle (BUG-33).
            dehors = "hors-les-murs" in _norm(salle)
            if dehors:
                hors_les_murs.add(titre)
            elif salle and not any(s in _norm(salle) for s in SALLES):
                salles_inconnues.add(salle)
            lignes.append({
                "jour": jour,
                "heure": heure if _HEURE.match(heure) else None,
                "titre": titre,
                "sous": sous,
                "url": a["href"],
                "dehors": dehors,
            })

    if not lignes:
        # Page lisible mais agenda vide : la structure a changé. On le
        # signale plutôt que de rendre une liste vide qu'aggregate.py ne
        # distinguerait pas d'une panne.
        print("[TNP] aucune représentation lue sur /agenda/ - structure "
              "du site modifiée ?", file=sys.stderr)
        return []

    # Une fiche par SPECTACLE, pas par représentation : dix-sept contre
    # cent vingt-trois.
    affiches: Dict[str, Optional[str]] = {}
    urls = list(dict.fromkeys(l["url"] for l in lignes))[:MAX_FICHES]
    illisibles = 0
    for i, u in enumerate(urls):
        if i:
            time.sleep(MIN_INTERVAL)
        try:
            affiches[u] = _affiche(session, u)
        except requests.RequestException as exc:
            # Une fiche qui tombe ne doit pas emporter les autres : la
            # représentation reste publiable sans son affiche.
            print(f"[TNP] {u}: {exc}", file=sys.stderr)
            illisibles += 1

    events = [
        Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=l["titre"],
            subtitle=l["sous"],
            category=CATEGORY,
            date_start=l["jour"],
            date_end=None,
            time=l["heure"],
            url=l["url"],
            image=affiches.get(l["url"]),
            offsite_venue=OFFSITE_PLUSIEURS if l["dehors"] else None,
        )
        for l in lignes
    ]

    if hors_les_murs:
        print(f"[TNP] hors les murs, marqué(s) « ailleurs » : "
              f"{', '.join(sorted(hors_les_murs))}", file=sys.stderr)
    if salles_inconnues:
        print(f"[TNP] salle(s) hors des deux habituelles, conservée(s) : "
              f"{', '.join(sorted(salles_inconnues))}", file=sys.stderr)
    if illisibles:
        print(f"[TNP] {illisibles} fiche(s) spectacle illisible(s)",
              file=sys.stderr)
    return events
