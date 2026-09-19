"""Scraper for the Musée des Beaux-Arts de Lyon (1er, place des Terreaux).

Drupal sans JSON:API — à la différence du Confluences — mais très
régulier : une liste paginée, et une fiche par rendez-vous où chaque
séance occupe sa propre ligne, datée et horodatée.

ON ÉCARTE LES VISITES, ET C'EST LE CHOIX CENTRAL DE CE SCRAPER. Le musée
programme 321 séances sur six mois, dont 283 sont des visites guidées :
dans les collections, dans les expositions, d'histoire de l'art, en
famille, en LSF, du bout des doigts. Trois séances seulement commencent
à 18h ou plus tard — c'est un programme de journée, de médiation
scolaire et familiale. Restent une trentaine de rendez-vous qui sont de
vraies sorties : les nocturnes, les conférences et colloques, « Le musée
fait son cinéma », les cartes blanches de midi à des chorégraphes et des
autrices, et les week-ends thématiques.

LE FILTRE PORTE SUR LE TYPE ET SUR LE TITRE, pas sur le seul type. La
liste range en effet trois séries de visites sous un type qui nomme le
PUBLIC et non l'activité — « LSF Sourds malentendants », « DBDD Aveugles
malvoyants », « Activités dans l'exposition » — et neuf visites
passaient. Leur titre, lui, dit « Visite LSF », « Visite du bout des
doigts », « Visite commentée ». Aucun des rendez-vous gardés ne porte ce
mot.

LE TYPE VIENT DE LA LISTE, qui le donne sur chaque carte. C'est ce qui
permet de n'ouvrir que les fiches retenues — une trentaine au lieu de
quatre-vingt-treize. La liste ne donne en revanche qu'UNE date par
rendez-vous, la prochaine ; les séances suivantes ne sont que sur la
fiche, d'où sa lecture.

LES EXPOSITIONS VIENNENT D'AILLEURS. Le musée les tient hors de sa liste
de rendez-vous, sur un article. Leur fiche de programmation existe, et
figure même dans la liste — mais elle ne porte AUCUNE date de séance :
son champ horaire dit « ouverte du mercredi au lundi de 10h à 18h », ce
qui est un horaire, pas une période. C'est pourquoi la première version
de ce scraper publiait trente-sept rendez-vous et pas une exposition.
La période n'est écrite que sur l'article, en toutes lettres, dans un
bloc régulier : un <h1> par exposition, le <p> suivant pour les
artistes, un <h3> pour les dates. Une seule page couvre l'en-cours et
l'à-venir.

Il n'y a pas de champ de lieu sur les fiches : le musée ne programme que
chez lui. Les expositions hors les murs sont sur un autre article encore,
et ne remontent pas ici.
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

VENUE = "Musée des Beaux-Arts"
SLUG = "musee-des-beaux-arts"
BASE = "https://www.mba-lyon.fr"
LISTING = BASE + "/fr/home_programmation"

HORIZON_DAYS = 180
PAGES_MAX = 30          # 19 pages à l'écriture ; la marge couvre la croissance

# Les EXPOSITIONS ne sont pas dans la liste des rendez-vous : le musée les
# tient à part, sur un article. Leur fiche de programmation existe bien,
# mais sans aucune date de séance — son champ horaire dit seulement
# « ouverte du mercredi au lundi de 10h à 18h ». La période, elle, n'est
# écrite que là. Une seule page couvre l'en-cours et l'à-venir.
EXPOSITIONS = "/fr/article/exposition-venir"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
                  "+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

MOIS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
        "juin": 6, "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10,
        "novembre": 11, "decembre": 12}

# Types de la liste → étiquettes que TYPE_BUCKETS (index.html) reconnaît.
# Un type absent d'ici laisse la fiche sans catégorie : categorie.py
# tentera le titre, plutôt que de la ranger au hasard.
TYPES = {
    "rendez-vous de midi":     "rencontre",     # → conférence
    "conference":              "conférence",
    "dialogues art et science": "conférence",
    # Une nocturne est une visite du musée après la tombée du jour : le
    # bal du musée, la lampe de poche, la nocturne étudiante. C'est du
    # musée, donc la famille « expos », faute de bucket plus juste.
    "nocturnes":               "exposition",
    # « Événement » ne dit rien du genre — Festin, Journée des
    # collectionneurs, Week-end chanté n'ont rien en commun. Laissé vide.
}
# « Le musée fait son cinéma » est rangé sous « Événement » ; le titre,
# lui, est explicite, et le bucket « ciné » du site est très maigre.
_CINEMA = re.compile(r"cin[ée]ma", re.I)
# Tout ce qui est une visite, que le mot soit dans le type ou le titre.
ECARTE = "visite"

# « vendredi 8 janvier 2027 - 18h45 »
_SEANCE = re.compile(r"(\d{1,2})\s+([a-z]+)\s+(20\d{2})"
                     r"(?:\s*-\s*(\d{1,2})\s*h\s*(\d{2})?)?")
# « 11 septembre 2026 - 14 mars 2027 ». L'année du début est facultative :
# une exposition qui ouvre et ferme la même année ne l'écrit qu'une fois.
_PERIODE = re.compile(r"(\d{1,2})\s+([a-z]+)(?:\s+(20\d{2}))?\s*"
                      r"[-–—]\s*(\d{1,2})\s+([a-z]+)\s+(20\d{2})")


def _norm(s: str) -> str:
    s = (s or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _plat(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def _sans_etiquette(el, etiquette: str) -> str:
    """Texte d'un champ Drupal, privé de son libellé.

    Le gabarit rend « Type de rendez-vous Nocturnes » d'un seul bloc :
    le libellé est là, simplement masqué en CSS.
    """
    txt = _plat(el)
    return re.sub(r"^" + re.escape(etiquette) + r"\s*", "", txt).strip()


def _seances(soup: BeautifulSoup) -> List[Tuple[str, Optional[str]]]:
    """(date ISO, heure) pour chaque séance de la fiche."""
    out: List[Tuple[str, Optional[str]]] = []
    for el in soup.select(".modal-date-programmation"):
        m = _SEANCE.search(_norm(_plat(el)))
        if not m or m.group(2) not in MOIS:
            continue
        try:
            jour = Date(int(m.group(3)), MOIS[m.group(2)], int(m.group(1)))
        except ValueError:
            continue
        heure = (f"{int(m.group(4)):02d}:{m.group(5) or '00'}"
                 if m.group(4) else None)
        cle = (jour.isoformat(), heure)
        if cle not in out:
            out.append(cle)
    return out


def _lire_fiche(url: str) -> Optional[dict]:
    """Séances, titre et affiche d'un rendez-vous."""
    try:
        r = base_get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"[Beaux-Arts] {url}: {exc}", file=sys.stderr)
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    img = soup.select_one(".field--name-field-pc-main-image img")
    src = img.get("src") if img else None
    return {
        "seances": _seances(soup),
        "titre": _plat(soup.select_one(".field--name-title")) or None,
        "image": (BASE + src if src and src.startswith("/") else src),
    }


def _expositions(session: requests.Session, today: Date,
                 horizon: Date) -> Tuple[List[Event], int]:
    """Les expositions de l'article, publiées comme des plages.

    Un bloc par exposition, et il est régulier : un <h1> pour le titre,
    le <p> qui suit pour les artistes, un <h3> pour la période, un lien
    vers la fiche, une image. On lit donc de <h1> en <h1>.
    """
    try:
        r = session.get(BASE + EXPOSITIONS, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"[Beaux-Arts] {EXPOSITIONS}: {exc}", file=sys.stderr)
        return [], 0
    bloc = BeautifulSoup(r.text, "html.parser").select_one(
        "[class*=field--name-field-sp-content] .field__item")
    if bloc is None:
        print(f"[Beaux-Arts] {EXPOSITIONS} : bloc de contenu introuvable "
              "— structure modifiée ?", file=sys.stderr)
        return [], 0

    events: List[Event] = []
    illisibles = 0
    for h1 in bloc.find_all("h1"):
        titre = _plat(h1)
        sous = periode = lien = image = None
        for el in h1.find_next_siblings():
            if el.name == "h1":
                break                       # exposition suivante
            if el.name == "h3" and periode is None:
                periode = _PERIODE.search(_norm(_plat(el)))
            elif el.name == "p" and sous is None and not el.find("a"):
                sous = _plat(el)
            if lien is None:
                a = el.find("a", href=True) if el.name != "a" else el
                if a and "/fiche-programmation/" in a["href"]:
                    lien = a["href"]
            if image is None:
                img = el.find("img")
                if img and img.get("src"):
                    image = img["src"]
        if not (titre and periode):
            illisibles += 1
            continue
        d, md, ad, f, mf, af = periode.groups()
        if md not in MOIS or mf not in MOIS:
            illisibles += 1
            continue
        try:
            # L'année du début, quand elle manque, est celle de la fin —
            # sauf si le mois de début est postérieur, l'exposition
            # franchissant alors le nouvel an.
            an_f = int(af)
            an_d = int(ad) if ad else (an_f - 1 if MOIS[md] > MOIS[mf] else an_f)
            debut = Date(an_d, MOIS[md], int(d))
            fin = Date(an_f, MOIS[mf], int(f))
        except ValueError:
            illisibles += 1
            continue
        if fin < today or debut > horizon:
            continue
        events.append(Event(
            venue=VENUE, venue_slug=SLUG, title=titre,
            subtitle=sous or None, category="exposition",
            # Une exposition déjà ouverte commence, pour nous, aujourd'hui.
            date_start=max(debut, today).isoformat(),
            date_end=fin.isoformat(), time=None,
            url=BASE + (lien or EXPOSITIONS),
            image=(BASE + image if image and image.startswith("/") else image),
        ))
    return events, illisibles


def _cartes(session: requests.Session) -> Dict[str, Tuple[str, str]]:
    """lien -> (type, titre) pour chaque carte de la liste paginée."""
    out: Dict[str, Tuple[str, str]] = {}
    for page in range(PAGES_MAX):
        url = LISTING + (f"?page={page}" if page else "")
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
        except requests.RequestException as exc:
            print(f"[Beaux-Arts] {url}: {exc}", file=sys.stderr)
            break
        avant = len(out)
        for t in BeautifulSoup(r.text, "html.parser").select(
                ".field--name-field-pc-appointment-type"):
            carte = t.parent
            a = carte.find("a", href=True)
            h3 = carte.find("h3")
            if not (a and "/fiche-programmation/" in a["href"]):
                continue
            out.setdefault(a["href"],
                           (_plat(t).lstrip("#").strip(), _plat(h3)))
        # La pagination ne dit pas où elle s'arrête : on s'arrête quand
        # une page n'apporte plus rien de neuf.
        if len(out) == avant and page > 2:
            break
    return out


def fetch() -> List[Event]:
    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    session = requests.Session()

    expos, expos_illisibles = _expositions(session, today, horizon)

    cartes = _cartes(session)
    if not cartes:
        print(f"[Beaux-Arts] aucune carte lue sur {LISTING} "
              "— structure modifiée ?", file=sys.stderr)
        return []

    events: List[Event] = []
    ecartees = 0
    types_inconnus: Dict[str, int] = {}
    sans_date = 0

    for lien, (type_brut, titre_liste) in cartes.items():
        type_norm = _norm(type_brut)
        if ECARTE in type_norm or ECARTE in _norm(titre_liste):
            ecartees += 1
            continue

        detail = detail_cache.get_details(
            BASE + lien, _lire_fiche,
            fields=("seances", "titre", "image")) or {}
        seances = detail.get("seances") or []
        if not seances:
            sans_date += 1
            continue

        titre = detail.get("titre") or titre_liste
        categorie = TYPES.get(type_norm)
        if categorie is None:
            if _CINEMA.search(titre):
                categorie = "cinéma"
            elif type_norm not in TYPES:
                types_inconnus[type_brut] = types_inconnus.get(type_brut, 0) + 1

        for jour, heure in seances:
            if not (today.isoformat() <= jour <= horizon.isoformat()):
                continue
            events.append(Event(
                venue=VENUE,
                venue_slug=SLUG,
                title=titre,
                subtitle=None,
                category=categorie,
                date_start=jour,
                date_end=None,
                time=heure,
                url=BASE + lien,
                image=detail.get("image"),
            ))

    if ecartees:
        print(f"[Beaux-Arts] {ecartees} visite(s) écartée(s)", file=sys.stderr)
    if expos_illisibles:
        print(f"[Beaux-Arts] {expos_illisibles} exposition(s) sans période "
              "lisible", file=sys.stderr)
    if not expos:
        # Le musée en a toujours au moins une : zéro signale un article
        # renommé ou restructuré, pas une saison creuse.
        print(f"[Beaux-Arts] aucune exposition lue sur {EXPOSITIONS}",
              file=sys.stderr)
    if types_inconnus:
        # Un type absent de TYPES laisse la fiche sans catégorie. On le
        # nomme pour qu'il soit ajouté, plutôt que de le deviner.
        print(f"[Beaux-Arts] type(s) non traduit(s) : "
              f"{', '.join(sorted(types_inconnus))}", file=sys.stderr)
    if sans_date:
        print(f"[Beaux-Arts] {sans_date} fiche(s) sans date lisible",
              file=sys.stderr)
    if not events:
        print(f"[Beaux-Arts] {len(cartes)} carte(s) lues, aucune séance "
              "retenue — structure modifiée ?", file=sys.stderr)
    return events + expos
