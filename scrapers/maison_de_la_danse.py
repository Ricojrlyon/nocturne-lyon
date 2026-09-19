"""Scraper for the Maison de la Danse (8e, avenue Jean Mermoz).

Site Drupal 7, sans API : /jsonapi et /api rendent 404. On lit donc deux
niveaux de HTML.

  /programmation-<saison>   les ~35 spectacles : titre, chorégraphe,
                            affiche, plage de dates, lien
  la fiche de chaque        le LIEU et les dates exactes avec horaires

CRAWL-DELAY. Leur robots.txt demande 10 secondes entre deux requêtes —
aucun autre site du dépôt ne le fait, les autres scrapers tournent à
0,4 s. C'est respecté ici, et c'est ce qui rend le cache indispensable :
sans lui, 24 fiches coûteraient quatre minutes À CHAQUE run horaire du
bot. Le délai est posé DANS le fetcher passé à detail_cache.get_details,
donc il ne s'applique qu'aux fiches réellement téléchargées ; une reprise
en cache ne dort pas du tout. En régime établi, seuls les spectacles
nouveaux coûtent dix secondes.

ATTENTION AU LIEU, comme aux Célestins. La Maison de la Danse programme
hors les murs : « SHOUT TWICE » se joue aux Subsistances, salle que
nocturne scrappe déjà. Publier ces représentations sous « Maison de la
Danse » créerait des doublons attribués au mauvais lieu, que la dédup ne
rattraperait pas puisqu'elle regroupe justement PAR lieu. La fiche donne
le lieu en clair — « Maison de la danse - Grande salle » ou « Les SUBS »
— et seul le premier est retenu.

L'ANNÉE ne figure pas dans la table des représentations : on y lit
« Mercredi 23 » sous un titre « SEPTEMBRE ». Plutôt que d'analyser la
plage affichée en tête, on essaie les années candidates et on garde
celle dont le jour de la semaine tombe juste — un 23 septembre n'est un
mercredi qu'une année sur cinq ou six, la réponse est donc unique sur
une fenêtre de trois ans. La date se valide ainsi elle-même, sans
dépendre d'un second format de date à parser.
"""
from __future__ import annotations

import re
import sys
import time
import unicodedata
from datetime import date as Date, timedelta
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from . import detail_cache
from .base import Event, get as base_get

VENUE = "Maison de la Danse"
SLUG = "maison-de-la-danse"
BASE = "https://maisondeladanse.com"

CATEGORY = "danse"
HORIZON_DAYS = 180

# Demandé par leur robots.txt. Voir le docstring : ce délai ne frappe que
# les fiches absentes du cache.
CRAWL_DELAY = 10.0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
                  "+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# La fiche écrit les mois en toutes lettres, la page de saison les
# abrège — et pas toujours de la même longueur : « sept. », « fév. »,
# « janv. ». Plutôt que d'énumérer les formes, on résout par PRÉFIXE
# UNIQUE : « fev » ne peut être que février, « sept » que septembre.
# Une seule paire est ambiguë, juin et juillet, d'où le refus explicite
# d'un préfixe qui désignerait plusieurs mois — « juil » et « juin »
# tranchent, « jui » non.
MOIS_CANON = ("janvier", "fevrier", "mars", "avril", "mai", "juin",
              "juillet", "aout", "septembre", "octobre", "novembre",
              "decembre")


def _mois(jeton: str) -> Optional[int]:
    """Numéro du mois désigné par un jeton, entier ou abrégé."""
    if len(jeton) < 3:
        return None
    trouves = [i for i, m in enumerate(MOIS_CANON, 1) if m.startswith(jeton)]
    return trouves[0] if len(trouves) == 1 else None
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")

_ANNEE = re.compile(r"\b(20\d{2})\b")
_JOURS_NUM = re.compile(r"\b(\d{1,2})\b")
_MOT = re.compile(r"[a-z]+")
_CELLULE_JOUR = re.compile(
    r"(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+(\d{1,2})")
_HEURE = re.compile(r"\b(\d{1,2})[:h](\d{2})\b")


def _norm(s: Optional[str]) -> str:
    s = (s or "").lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)


def _url_saison(today: Date) -> str:
    """La saison court de septembre à juin : /programmation-2026-27."""
    debut = today.year if today.month >= 9 else today.year - 1
    return f"{BASE}/programmation-{debut}-{(debut + 1) % 100:02d}"


def _debut_annonce(libelle: str) -> Optional[Date]:
    """Première date de la plage affichée sur la carte de saison.

    Sert UNIQUEMENT à décider si la fiche vaut le détour : à dix secondes
    la requête, on ne charge pas les spectacles hors horizon. La date
    exacte, elle, vient de la fiche.

    « 30 sept. au 08 oct. 2026 » → 2026-09-30, en prenant la PREMIÈRE
    année du libellé : une plage à cheval sur deux ans (« 28 déc. 2026 au
    05 janv. 2027 ») commence bien dans la première.
    """
    t = _norm(libelle)
    annees = _ANNEE.findall(t)
    if not annees:
        return None
    # Les années sont retirées AVANT de chercher le quantième, sinon
    # « 2026 » fournirait ses propres chiffres. Et on prend le PREMIER
    # nombre, non le premier suivi d'un mois : dans « 11-12 sept. » le
    # quantième de début est 11, que rien ne suit.
    reste = _ANNEE.sub(" ", t)
    jours = _JOURS_NUM.findall(reste)
    mois = [n for n in (_mois(m) for m in _MOT.findall(reste)) if n]
    if not jours or not mois:
        return None
    try:
        return Date(int(annees[0]), mois[0], int(jours[0]))
    except ValueError:
        return None


def _annee_par_jour_semaine(jour_nom: str, jj: int, mois: int,
                            candidates: List[int]) -> Optional[int]:
    """L'année dont le jour de la semaine concorde. Voir le docstring."""
    for an in candidates:
        try:
            d = Date(an, mois, jj)
        except ValueError:
            continue
        if JOURS[d.weekday()] == jour_nom:
            return an
    return None


def _lire_fiche(url: str) -> Optional[dict]:
    """Lieu et représentations d'un spectacle. Rendu à detail_cache."""
    # Le délai demandé par robots.txt, posé ici pour ne frapper que les
    # vraies requêtes : un spectacle déjà en cache ne passe pas par là.
    time.sleep(CRAWL_DELAY)
    try:
        r = base_get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"[Maison de la Danse] {url}: {exc}", file=sys.stderr)
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    lieu = ""
    seances: List[List] = []
    for bloc in soup.select(".spectacle-dates"):
        titre = bloc.select_one(".table-title")
        etiquette = _norm(titre.get_text(" ", strip=True)) if titre else ""
        if etiquette == "lieu":
            divs = [d.get_text(" ", strip=True) for d in bloc.select("div")
                    if d is not titre]
            lieu = next((d for d in divs if d), "")
            continue
        # Le titre porte UN mois pour une série courte, mais DEUX pour une
        # série à cheval — « NOVEMBRE - DÉCEMBRE ». On lit donc une liste,
        # et l'on avance au mois suivant dès que le quantième recule.
        # Sans ça, les longues séries ne rendaient aucune séance : trois
        # spectacles perdus en silence, dont Slava's Snowshow.
        mois_liste = [n for n in (_mois(m) for m in _MOT.findall(etiquette)) if n]
        if not mois_liste:
            continue
        rang, precedent, jour_precedent = 0, None, None
        for rangee in bloc.select(".table"):
            cellules = [c.get_text(" ", strip=True) for c in rangee.select(".cell")]
            if not cellules:
                continue
            mj = _CELLULE_JOUR.search(_norm(cellules[0]))
            if not mj:
                continue
            jj = int(mj.group(2))
            # Bascule au mois suivant quand le quantième RECULE — mais
            # aussi quand il se RÉPÈTE sous un autre jour de la semaine :
            # « SACRE » se joue le mercredi 28 octobre puis le samedi
            # 28 novembre, et une comparaison strictement décroissante
            # manquait le second. Le nom du jour distingue ce cas d'une
            # double séance, qui répète le quantième ET le jour.
            jour_nom = mj.group(1)
            if (precedent is not None and rang + 1 < len(mois_liste)
                    and (jj < precedent
                         or (jj == precedent and jour_nom != jour_precedent))):
                rang += 1
            precedent, jour_precedent = jj, jour_nom
            heure = None
            for c in cellules[1:]:
                mh = _HEURE.search(c)
                if mh:
                    heure = f"{int(mh.group(1)):02d}:{mh.group(2)}"
                    break
            seances.append([jour_nom, jj, mois_liste[rang], heure])
    return {"lieu": lieu, "seances": seances}


def fetch() -> List[Event]:
    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    candidates = [today.year, today.year + 1, today.year - 1]

    session = requests.Session()
    url_saison = _url_saison(today)
    r = session.get(url_saison, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    cartes = []
    for c in soup.select(".CrossContent--saison"):
        a = c.select_one("a[href]")
        titre = c.select_one(".s-hookCross")
        sous = c.select_one(".s-titleCross")
        libelle = c.select_one(".s-titleDate")
        img = c.select_one("img[src]")
        if not (a and titre and libelle):
            continue
        debut = _debut_annonce(libelle.get_text(" ", strip=True))
        # Hors horizon : on n'ouvre pas la fiche, à dix secondes l'unité.
        # Une plage déjà commencée est gardée, son début fût-il passé.
        if debut is None or debut > horizon:
            continue
        href = a["href"]
        cartes.append({
            "url": href if href.startswith("http") else BASE + href,
            "titre": titre.get_text(" ", strip=True),
            "sous": (sous.get_text(" ", strip=True) or None) if sous else None,
            "image": img["src"] if img else None,
        })

    if not cartes:
        # Page lisible mais aucune carte : structure changée, ou l'URL de
        # saison déduite ne pointe plus au bon endroit. On le signale.
        print(f"[Maison de la Danse] aucun spectacle lu sur {url_saison} — "
              f"structure du site ou schéma d'URL modifié ?", file=sys.stderr)
        return []

    events: List[Event] = []
    sans_lieu: List[str] = []
    ailleurs = ecartees = 0
    for c in cartes:
        d = detail_cache.get_details(c["url"], _lire_fiche,
                                     fields=("lieu", "seances"))
        lieu = _norm(d.get("lieu"))
        if not lieu:
            # Aucun bloc « Lieu » sur la fiche. Les spectacles hors les
            # murs, eux, le renseignent TOUJOURS — « Les SUBS ». Un bloc
            # absent veut donc dire « non précisé », et sur la page de
            # saison de la maison le défaut raisonnable est la maison.
            # Journalisé : si un jour ils cessent de renseigner le lieu
            # des accueils extérieurs, cette ligne le fera voir.
            sans_lieu.append(c["titre"])
        elif not lieu.startswith("maison de la danse"):
            ailleurs += 1
            continue
        for jour_nom, jj, mois, heure in (d.get("seances") or []):
            an = _annee_par_jour_semaine(jour_nom, jj, mois, candidates)
            if an is None:
                # Jour de la semaine incohérent avec toutes les années
                # candidates : la date serait fausse, on préfère la perdre.
                ecartees += 1
                continue
            jour = Date(an, mois, jj)
            if not (today <= jour <= horizon):
                continue
            events.append(Event(
                venue=VENUE,
                venue_slug=SLUG,
                title=c["titre"],
                subtitle=c["sous"],
                category=CATEGORY,
                date_start=jour.isoformat(),
                date_end=None,
                time=heure,
                url=c["url"],
                image=c["image"],
            ))

    if ailleurs or ecartees:
        print(f"[Maison de la Danse] écartés : {ailleurs} spectacle(s) hors "
              f"les murs, {ecartees} date(s) au jour de la semaine incohérent",
              file=sys.stderr)
    if sans_lieu:
        print(f"[Maison de la Danse] lieu non précisé, supposé chez eux : "
              f"{', '.join(sans_lieu)}", file=sys.stderr)
    return events
