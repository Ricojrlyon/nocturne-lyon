"""Scraper for agend'Arts (4e), petite salle associative de la Croix-Rousse.

Le lieu n'a pas de domaine propre : il publie sur un blog WordPress.com,
un billet par spectacle. L'API publique de WordPress.com le sert sans
clé, cent billets par page, deux cent vingt en tout - quatre requêtes
pour toute la programmation.

LA DATE DE PUBLICATION N'EST PAS LA DATE DE L'ÉVÉNEMENT : un billet écrit
en juin annonce un concert de décembre. La date se lit dans la première
phrase du billet, en français - « Samedi 28 novembre à 20h ».

Cette phrase est lue par DEUX SOURCES INDÉPENDANTES, et c'est le choix
central de ce scraper :

  la prose             « samedi 5 septembre à 20h et dimanche 6 à 18h »
  les liens HelloAsso  .../lucas-rocher-samedi-5-septembre-2026-20h

La billetterie porte la date complète - millésime compris - dans son
slug, mais pas toujours : certains liens omettent l'année, abrègent le
jour de la semaine, ou sont tronqués par la longueur du slug. Elle ne
peut donc pas remplacer la prose, seulement la corroborer. Sur les 325
couples jour+mois qu'elle nomme, 324 sont confirmés par la prose ; le
325e - « samedi 30 à 20h et dimanche 31 mai » - est un cas que la prose
seule ne peut pas voir, le 30 n'ayant pas de mois collé. On garde donc
l'union des deux lectures.

LA SUITE COLLÉE AU MOIS. Dans la prose, seule la suite de quantièmes qui
précède immédiatement un nom de mois compte. Sans cette règle « Les 3
becs - samedi 19 septembre » produirait un faux 3 septembre : les titres
sont pleins de nombres qui ne sont pas des dates.

L'ANNÉE n'est presque jamais écrite. Quatre sources, dans l'ordre, et
jamais de supposition : le slug HelloAsso, la catégorie du billet (elles
sont de la forme « novembre 2026 »), un millésime écrit dans le texte,
enfin la concordance du jour de la semaine - « samedi 28 novembre » ne
tombe un samedi qu'une année sur sept. Faute de quoi la date est
abandonnée plutôt que projetée sur l'année en cours.

Cette concordance se cherche à partir de l'année de PUBLICATION du
billet, non de l'année courante : une salle annonce avant de jouer. Les
billets de 2024 se résolvent ainsi dans le passé et sortent de l'horizon,
au lieu d'échouer faute de candidat plausible. Et le jour de la semaine
doit être celui du quantième visé, pas le premier de la phrase : dans
« samedi 11 et dimanche 12 janvier », c'est dimanche qui date le 12.

L'HEURE est propre à chaque séance : la salle joue le samedi à 20h et le
dimanche à 18h, et les deux figurent dans la même phrase. On lit donc
l'heure qui suit CHAQUE mention de mois, pas la première du billet.

LES ANNULATIONS sont annoncées dans la phrase des dates - « la carte
blanche est annulée les 27, 28 et 29 mars mais… ». Une suite introduite
par une annulation est écartée : publier une séance annulée est une
faute plus lourde que d'en manquer une.

LE GENRE est en gras en tête de description - « Rap », « Chanson folk,
humour », « Quartet jazz ». C'est du texte libre, ramené à une étiquette
courte par la table de scrapers.categorie, qui sert déjà à cela ; ce
qu'elle ne reconnaît pas est laissé vide plutôt que deviné. On examine
les deux premiers <strong>, pas seulement le premier, qui est parfois
« Evénement ! » ou une grille tarifaire - et pas davantage, les suivants
étant des bios d'artistes.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from datetime import date as Date, timedelta
from typing import Dict, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup

from . import categorie
from .base import Event, get as base_get

VENUE = "Agend'arts"
SLUG = "agend-arts"
SITE = "agendarts.wordpress.com"
API = f"https://public-api.wordpress.com/wp/v2/sites/{SITE}"

HORIZON_DAYS = 180
PAR_PAGE = 100
PAGES_MAX = 5          # 3 pages à l'écriture ; la marge couvre la croissance

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
                  "+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

MOIS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
        "juin": 6, "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10,
        "novembre": 11, "decembre": 12}
JOURS_SEM = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi",
             "dimanche")

_MOIS_RE = re.compile("|".join(MOIS))
# La suite de quantièmes collée au nom du mois, et elle seule.
_SUITE = re.compile(r"((?:(?:" + "|".join(JOURS_SEM) + r")\s+)?"
                    r"\d{1,2}(?:er)?\s*(?:,|et|&)?\s*)+$")
# Chaque quantième de la suite avec le jour de semaine qui le précède :
# dans « jeudi 13, vendredi 14 et samedi 15 février », c'est le seul moyen
# de savoir que le 15 est un samedi. Le millésime en dépend.
_UNITE = re.compile(r"(?:(" + "|".join(JOURS_SEM) + r")\s+)?(\d{1,2})(?:er)?\b")
_HEURE = re.compile(r"\b(\d{1,2})\s?h\s?(\d{2})?\b")
_ANNEE = re.compile(r"\b(20\d{2})\b")
_ANNULE = re.compile(r"annul")
_LIEN_HA = re.compile(r"https://www\.helloasso\.com/[^\"'\s]+")
_DATE_HA = re.compile(r"-(\d{1,2})(?:er)?-(" + "|".join(MOIS) + r")"
                      r"(?:-(20\d{2}))?(?:-(\d{1,2})h(\d{2})?)?(?:-|$)")
# Fenêtre où l'on cherche une annulation devant une suite de quantièmes.
AVANT = 80
# Repli quand le billet n'a pas de <p> : on s'en tient à son ouverture.
OUVERTURE = 260
# Millésimes essayés à partir de l'année de publication du billet.
ANNEES_CANDIDATES = 3
# Nombre de <strong> examinés pour y trouver le genre. Deux, pas plus :
# le premier est parfois « Evénement ! » ou une grille tarifaire, d'où le
# second ; à partir du troisième on tombe dans les bios d'artistes, et les
# dix soirées « Scène découverte 4 artistes / 4 styles » hériteraient du
# genre d'un seul des quatre.
STRONGS_GENRE = 2


def _norm(s: str) -> str:
    s = (s or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _plat(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def _heure(txt: str) -> Optional[str]:
    m = _HEURE.search(txt or "")
    return f"{int(m.group(1)):02d}:{m.group(2) or '00'}" if m else None


def _seances(txt: str) -> List[Tuple[int, int, Optional[str], Optional[str]]]:
    """(jour, mois, heure, jour de semaine) pour chaque date de la phrase.

    L'heure est celle qui suit le nom du mois, avant la mention suivante.
    Les suites introduites par une annulation sont écartées.
    """
    out: List[Tuple[int, int, Optional[str], Optional[str]]] = []
    bornes = list(_MOIS_RE.finditer(txt))
    debut = 0
    for i, m in enumerate(bornes):
        seg = txt[debut:m.start()].rstrip()
        debut = m.end()
        s = _SUITE.search(seg)
        if not s:
            continue
        if _ANNULE.search(seg[max(0, s.start() - AVANT):s.start()]):
            continue
        fin = bornes[i + 1].start() if i + 1 < len(bornes) else len(txt)
        heure = _heure(txt[m.end():fin])
        for u in _UNITE.finditer(s.group(0)):
            jj = int(u.group(2))
            if 1 <= jj <= 31:
                out.append((jj, MOIS[m.group(0)], heure, u.group(1)))
    return out


def _dates_helloasso(brut: str) -> Dict[Tuple[int, int],
                                        Tuple[Optional[int], Optional[str]]]:
    """(jour, mois) -> (année, heure) d'après les liens de billetterie.

    La date est un suffixe du slug : on retient la dernière occurrence,
    un titre pouvant lui aussi contenir un couple quantième + mois.
    """
    out: Dict[Tuple[int, int], Tuple[Optional[int], Optional[str]]] = {}
    for lien in set(_LIEN_HA.findall(brut)):
        trouves = list(_DATE_HA.finditer(_norm(lien)))
        if not trouves:
            continue
        g = trouves[-1]
        heure = (f"{int(g.group(4)):02d}:{g.group(5) or '00'}"
                 if g.group(4) else None)
        out[(int(g.group(1)), MOIS[g.group(2)])] = (
            int(g.group(3)) if g.group(3) else None, heure)
    return out


def _annee(jour: int, mois: int, txt: str, annees_cat: Set[int],
           an_ha: Optional[int], jsem: Optional[str],
           an_pub: int) -> Optional[int]:
    """Millésime de la date, ou None si aucune source ne le donne."""
    if an_ha:
        return an_ha
    for an in sorted(annees_cat):
        try:
            Date(an, mois, jour)
        except ValueError:
            continue
        return an
    m = _ANNEE.search(txt)
    if m:
        return int(m.group(1))
    # Concordance du jour de la semaine : « samedi 28 novembre » ne tombe
    # un samedi qu'une année sur sept. Il faut le jour de semaine PROPRE à
    # ce quantième - celui du 12 dans « samedi 11 et dimanche 12 janvier ».
    #
    # Les millésimes candidats partent de l'année de PUBLICATION, non de
    # l'année courante : une salle annonce avant de jouer. Un billet de
    # 2024 se résout ainsi dans le passé, et sort de l'horizon, au lieu
    # d'échouer faute de candidat plausible.
    if jsem:
        for an in range(an_pub, an_pub + ANNEES_CANDIDATES):
            try:
                d = Date(an, mois, jour)
            except ValueError:
                continue
            if JOURS_SEM[d.weekday()] == jsem:
                return an
    return None


def _get(session: requests.Session, chemin: str, **params) -> list:
    r = base_get(f"{API}/{chemin}", session=session, params=params,
                 headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch() -> List[Event]:
    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    session = requests.Session()

    try:
        cats = _get(session, "categories", per_page=100, _fields="id,name")
    except requests.RequestException as exc:
        print(f"[agend'Arts] catégories : {exc}", file=sys.stderr)
        cats = []
    # Les catégories sont des mois : « novembre 2026 » donne le millésime.
    mois_cat: Dict[int, Tuple[int, int]] = {}
    for c in cats:
        m = re.match(r"([a-z]+)\s+(20\d{2})", _norm(c.get("name")))
        if m and m.group(1) in MOIS:
            mois_cat[c["id"]] = (MOIS[m.group(1)], int(m.group(2)))

    billets: list = []
    for page in range(1, PAGES_MAX + 1):
        try:
            lot = _get(session, "posts", per_page=PAR_PAGE, page=page,
                       orderby="date", order="desc",
                       _fields="id,date,link,title,content,categories")
        except requests.RequestException as exc:
            print(f"[agend'Arts] page {page} : {exc}", file=sys.stderr)
            break
        billets.extend(lot)
        if len(lot) < PAR_PAGE:
            break

    if not billets:
        print(f"[agend'Arts] aucun billet lu sur {SITE}", file=sys.stderr)
        return []

    events: List[Event] = []
    sans_annee = 0
    for b in billets:
        brut = (b.get("content") or {}).get("rendered", "")
        soup = BeautifulSoup(brut, "html.parser")

        # La phrase de dates est le premier paragraphe ; s'en tenir à lui
        # évite de lire les dates citées plus bas dans la description.
        p = soup.find("p")
        phrase = _norm(_plat(p) if p else _plat(soup)[:OUVERTURE])

        ha = _dates_helloasso(brut)
        an_pub = int((b.get("date") or "")[:4] or Date.today().year)
        annees_cat = {mois_cat[c][1] for c in (b.get("categories") or [])
                      if c in mois_cat}

        # Union des deux lectures : la prose donne le jour et l'heure, la
        # billetterie complète ce que la phrase ne peut pas exprimer.
        vues: Dict[Tuple[int, int], Tuple[Optional[str], Optional[str]]] = {}
        for jj, mois, heure, jsem in _seances(phrase):
            vues.setdefault((jj, mois), (heure, jsem))
        for cle, (_an, heure) in ha.items():
            vues.setdefault(cle, (heure, None))
        if not vues:
            continue

        titre = _plat(BeautifulSoup((b.get("title") or {}).get("rendered", ""),
                                    "html.parser"))
        img = soup.find("img")
        genre = next((g for st in soup.find_all("strong")[:STRONGS_GENRE]
                      if (g := categorie.deduire(_plat(st), None))), None)

        for (jj, mois), (heure, jsem) in sorted(vues.items()):
            an_ha, heure_ha = ha.get((jj, mois), (None, None))
            an = _annee(jj, mois, phrase, annees_cat, an_ha, jsem, an_pub)
            if an is None:
                sans_annee += 1
                continue
            try:
                jour = Date(an, mois, jj)
            except ValueError:
                continue
            if not (today <= jour <= horizon):
                continue
            events.append(Event(
                venue=VENUE,
                venue_slug=SLUG,
                title=titre,
                subtitle=None,
                category=genre,
                date_start=jour.isoformat(),
                date_end=None,
                time=heure or heure_ha,
                url=b.get("link"),
                image=img.get("src") if img else None,
            ))

    if sans_annee:
        print(f"[agend'Arts] {sans_annee} date(s) sans millésime déterminable",
              file=sys.stderr)
    if not events:
        print(f"[agend'Arts] {len(billets)} billet(s) lus, aucune date à venir "
              f"- structure modifiée ?", file=sys.stderr)
    return events
