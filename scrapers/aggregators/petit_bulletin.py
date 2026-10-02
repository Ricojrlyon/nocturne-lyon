"""Petit Bulletin aggregator scraper.

Fetches https://www.petit-bulletin.fr/agenda-recherche.html and parses the
list of upcoming events. The page structure is regular: each event has a
title in an h-tag with a stable URL (/agenda-NNNNNN-slug.html), a category
in parens on the next sibling line, then a list with venue and date.

UN SEUL filtre éditorial : quatre catégories d'arts plastiques (voir
CATEGORIES_ECARTEES). Pour le reste tout l'agenda remonte, et c'est la
déduplication en trois passes (scrapers/dedup.py) qui écarte les doublons
quand un événement est aussi publié par la salle elle-même.

Historique, parce que la décision a changé deux fois. Deux filtres
existaient ici — musées et galeries d'un côté, une liste de catégories de
l'autre — parce que leurs accrochages, courant sur des mois, saturaient le
feed. Ils ont été retirés en août 2026, au motif que le frontend regroupe
chaque journée en quatre familles qu'on éteint d'un bouton, et que c'est
au lecteur de dire qu'il ne veut pas d'expositions ce soir.

Le filtre de catégories revient en septembre 2026, resserré à quatre
libellés, et pour une raison différente de la première : les événements
longs sont désormais affichés sur CHACUN de leurs jours, seuil supprimé.
Un accrochage de trois mois pesait une carte, il en pèse quatre-vingt-dix.

L'agenda est paginé (`?p=N`) et fetch() suit toutes les pages.

Dates :
  * jour unique          → un Event
  * plage ≤ 7 jours      → un Event par jour (petits festivals) ; seulement
                           les jours de jeu quand le texte les nomme
  * plage > 7 jours      → UN Event à plage (date_start..date_end)
  * « Jusqu'au X »       → UN Event à plage, du jour courant à X
Rien n'est jeté : le frontend déploie les plages sur chacun de leurs
jours, avec un badge de progression.
"""
from __future__ import annotations
import re
import sys
import time
import unicodedata
from datetime import date, timedelta
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from ..base import Event, get as base_get

URL = "https://www.petit-bulletin.fr/agenda-recherche.html"
BASE = "https://www.petit-bulletin.fr"

USER_AGENT = (
    "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
    "+https://github.com/Ricojrlyon/nocturne-lyon)"
)

# Garde-fou anti-boucle, PAS une limite de lecture : fetch() s'arrête de
# lui-même dès qu'une page n'apporte plus aucune URL nouvelle.
# Il doit rester largement au-dessus de la taille réelle de l'agenda,
# sinon il tronque en silence. Fixé à 15 quand l'agenda tenait en 9 pages,
# il en comptait 21 six mois plus tard : les pages 16 à 21 étaient perdues,
# soit près d'un tiers du contenu.
MAX_PAGES = 40

# Au-delà de ce nombre de jours, une plage devient UN événement à plage
# plutôt qu'un événement par jour.
LONG_RUN_DAYS = 7

MONTHS_FR = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "aout": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "decembre": 12,
}


def _normalize(s: str) -> str:
    """Lowercase, strip accents, punctuation → space, collapse whitespace."""
    s = (s or "").lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Catégories écartées : les arts plastiques du Petit Bulletin. Ce sont des
# ACCROCHAGES, ouverts tous les jours pendant des semaines, et depuis que
# le frontend déploie les événements longs sur chacun de leurs jours, ils
# pèsent leur durée entière. Mesuré à l'introduction de la règle : 99
# événements, dont 68 en galerie et 9 en musée non scrappé, et 1 443 jours
# cumulés d'accrochage sur l'horizon.
#
# Les libellés sont comparés normalisés (casse, accents et ponctuation
# écrasés) : « Design & Architecture » et « design et architecture »
# tombent donc pareil.
CATEGORIES_ECARTEES = frozenset(_normalize(c) for c in (
    "Peinture & Dessin",
    "Art contemporain et numérique",
    "Photographie",
    "Design & Architecture",
))


# Le Petit Bulletin sert son HTML avec les apostrophes ÉCHAPPÉES — la
# signature d'un addslashes() PHP appliqué à la sortie plutôt qu'à l'entrée
# d'une requête. Le texte contient donc littéralement « Théâtre de l\'Élysée »,
# antislash compris, et get_text() le rend tel quel. Vu le 2026-09-14 sur
# quatre lieux et neuf événements ; aucun titre touché ce jour-là, mais rien
# ne l'en protège, d'où l'application aux deux champs.
#
# Conséquence si on ne corrige pas : l'antislash s'affiche sur la carte, et
# surtout le lieu devient une salle À PART — le frontend indexe l'arrondis-
# sement et regroupe les cartes sur la chaîne EXACTE. Le Théâtre de l'Élysée
# comptait ainsi trois entrées pour une salle.
#
# On ne retire que l'antislash qui précède une apostrophe ou un guillemet,
# seul cas produit par addslashes : un antislash isolé dans un titre — rare
# mais légitime — survit.
_ANTISLASH_APOSTROPHE = re.compile(r"\\(['’\"])")


def _desechappe(s: str) -> str:
    return _ANTISLASH_APOSTROPHE.sub(r"\1", s or "")


def _slugify(s: str) -> str:
    s = (s or "").lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


_JOURS_FR = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def _jour_de(annee: int, mois: int, jour: int) -> Optional[str]:
    """Le jour de la semaine d'une date, en français ; None si elle n'existe pas."""
    try:
        return _JOURS_FR[date(annee, mois, jour).weekday()]
    except ValueError:
        return None


# Les horaires d'une plage courte, lus dans le texte normalisé (sans accents
# ni ponctuation) : « du mardi au vendredi a 19h30 samedi a 19h ».
_NOM = r"(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)s?"
_JOUR = r"(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)s?"
_GROUPE = r"(?:du\s+{j}\s+au\s+{j}|{j})(?:\s+(?:et\s+)?(?:du\s+{j}\s+au\s+{j}|{j}))*".format(j=_JOUR)
# « mardi et jeudi a 19h30 », « du mardi au vendredi a 19h30 », « samedi de 11h a 18h »
_CRENEAU = re.compile(r"\b(" + _GROUPE + r")\s+(?:a|de)\s+(\d{1,2})h(\d{0,2})\b")
# « relache le jeudi », « sauf le lundi » ; mais pas « sauf samedi a 21h15 »,
# qui change l'heure du samedi sans l'ôter.
_RELACHE = re.compile(r"\b(?:relache|sauf)\s+(?:les?\s+)?(" + _GROUPE + r")\b"
                      r"(?!\s+(?:(?:et\s+)?(?:du\s+)?" + _JOUR + r"|(?:a|de)\s+\d))")
_MOIS_NOMME = re.compile(r"\b(?:janvier|fevrier|mars|avril|mai|juin|juillet|aout|"
                         r"septembre|octobre|novembre|decembre)\b")


def _hhmm(hh: str, mm: str) -> Optional[str]:
    """« 19 », « 30 » -> « 19:30 » ; None pour une heure qui n'existe pas."""
    h, m = int(hh), int(mm or 0)
    return f"{h:02d}:{m:02d}" if h < 24 and m < 60 else None


def _jours_du_groupe(groupe: str) -> set:
    """Les jours (0 = lundi) de « mardi et jeudi », « du mardi au vendredi »."""
    jours = set()
    for debut, fin in re.findall(r"\bdu\s+" + _NOM + r"\s+au\s+" + _NOM, groupe):
        i, j = _JOURS_FR.index(debut), _JOURS_FR.index(fin)
        jours.add(i)
        while i != j:
            i = (i + 1) % 7
            jours.add(i)
    reste = re.sub(r"\bdu\s+" + _JOUR + r"\s+au\s+" + _JOUR, " ", groupe)
    jours.update(_JOURS_FR.index(n) for n in re.findall(r"\b" + _NOM + r"\b", reste))
    return jours


def _jours_de_jeu(horaires: str) -> Tuple[Optional[set], dict, Optional[str]]:
    """Les jours où se joue une plage courte, et l'heure de chacun (BUG-18).

    `horaires` est le texte normalisé qui suit la plage. Rend (jours, heures,
    heure_commune) : les jours de la semaine joués (0 = lundi), ou None quand
    le texte ne les restreint pas ; l'heure de chaque jour nommé ; l'heure
    des autres jours.

      « mercredi et samedi a 15h »         mercredi et samedi seulement
      « du mardi au vendredi a 19h30
        samedi a 19h dimanche a 16h »      pas le lundi (relâche)
      « a 20h30 sauf samedi a 21h15 »      tous les jours, samedi à 21h15
      « a 20h45 relache le jeudi »         tous les jours sauf le jeudi

    Devant une tournure inconnue — un jour nommé hors d'un horaire
    (« rencontre jeudi », « samedi à midi »), une date (« 7 novembre à
    15h »), un « sauf le lundi à 20h » sans heure commune —, on n'ôte rien :
    mieux vaut une séance de trop qu'une vraie séance perdue.
    """
    rien = (None, {}, None)
    if _MOIS_NOMME.search(horaires):
        return rien
    creneaux = list(_CRENEAU.finditer(horaires))
    relaches = list(_RELACHE.finditer(horaires))
    zones = [m.span() for m in creneaux + relaches]
    if any(not any(a <= n.start() < b for a, b in zones)
           for n in re.finditer(r"\b" + _JOUR + r"\b", horaires)):
        return rien
    heure = re.search(r"\b(\d{1,2})h(\d{0,2})\b", horaires)
    commune = None
    if heure and (not creneaux or heure.start() < creneaux[0].start()):
        commune = _hhmm(heure.group(1), heure.group(2))
    if commune is None and any(re.search(r"\bsauf(?:\s+les?)?\s*$", horaires[:m.start()])
                               for m in creneaux):
        return rien
    nommes, heures = set(), {}
    for m in creneaux:
        h = _hhmm(m.group(2), m.group(3))
        for j in _jours_du_groupe(m.group(1)):
            nommes.add(j)
            if h:
                heures.setdefault(j, h)
    exclus = set()
    for m in relaches:
        exclus |= _jours_du_groupe(m.group(1))
    if commune or not creneaux:
        jours = (set(range(7)) - exclus) if exclus else None
    else:
        jours = nommes - exclus
    return jours, heures, commune


def _parse_date_str(s: str) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """Parse une chaîne de date du Petit Bulletin.

    Renvoie une LISTE de triplets (date_start, heure, date_end) :
      "Mardi 26 mai 2026 à 20h"        -> [("2026-05-26", "20:00", None)]
      "Du 26 au 28 mai 2026, à 19h"    -> 3 entrées, date_end None
      "Du 26 au 30 mai 2026, mardi et jeudi à 19h30, samedi à 19h"
                                       -> le 26 et le 28 à 19h30, le 30 à 19h
      "Du 1 au 30 juin 2026"           -> [("2026-06-01", None, "2026-06-30")]
      "Jusqu'au 16 août 2026, ..."     -> [(aujourd'hui, None, "2026-08-16")]
      "Samedi 30 mai et Dimanche 31 mai à 20h"
                                       -> [("2026-05-30", "20:00", None),
                                           ("2026-05-31", "20:00", None)]
      "... samedi à 20h, dimanche à 16h" : une heure par jour

    Liste vide seulement si aucune date n'est lisible ou si l'événement est
    entièrement passé.
    """
    norm = _normalize(s)
    today = date.today()
    today_iso = today.isoformat()

    MOIS = (r"(janvier|fevrier|mars|avril|mai|juin|juillet|aout|"
            r"septembre|octobre|novembre|decembre)")
    JOURS = "|".join(_JOURS_FR)

    # Heure : "à HHh", "à HHhMM", "de HHh à HHh"
    time_str: Optional[str] = None
    time_m = re.search(r"\b(\d{1,2})h(\d{0,2})\b", norm)
    if time_m:
        hh = int(time_m.group(1))
        mm = int(time_m.group(2)) if time_m.group(2) else 0
        if 0 <= hh < 24 and 0 <= mm < 60:
            time_str = f"{hh:02d}:{mm:02d}"

    def _year_for(month: int, day: int, explicit: Optional[str],
                  jour_semaine: Optional[str] = None) -> int:
        """Année explicite si présente ; sinon celle que le jour de la
        semaine désigne ; sinon la prochaine occurrence.

        BUG-17 : « Jeudi 1 octobre », lu le 2, partait au 1er octobre 2027
        — une soirée fantôme un an plus tard —, toute date passée d'un seul
        jour passant à l'année suivante. Le jour de la semaine tranche : le
        1er octobre 2027 est un vendredi, c'est donc 2026, et la date passée
        est écartée. Sans lui, quinze jours de grâce, comme tng.py.
        """
        if explicit:
            return int(explicit)
        if jour_semaine:
            candidates = [y for y in (today.year, today.year + 1)
                          if _jour_de(y, month, day) == jour_semaine]
            if len(candidates) == 1:
                return candidates[0]
        y = today.year
        try:
            if (today - date(y, month, day)).days > 15:
                y += 1
        except ValueError:
            pass
        return y

    def _expand(start: date, end: date,
                horaires: str = "") -> List[Tuple[str, Optional[str], Optional[str]]]:
        """Plage courte -> un événement par jour ; longue -> un seul à plage."""
        if end < start:
            return []
        if (end - start).days > LONG_RUN_DAYS:
            # Événement long (expo, festival au long cours) : un seul Event
            # à plage. Il n'est plus jeté comme avant, et le frontend sait
            # l'afficher — avec un badge « en cours » au-delà de 30 jours.
            eff = max(start, today)
            return [(eff.isoformat(), time_str, end.isoformat())]
        # BUG-18 : chaque jour de la plage devenait une séance, relâche
        # comprise. « Pétrole », du 26 novembre au 2 décembre « du mardi au
        # vendredi à 19h30, samedi à 19h, dimanche à 16h », était aussi
        # publié le lundi 30, et à 19h30 le dimanche. On ne garde que les
        # jours de jeu que le texte nomme, chacun à son heure.
        jours, heures, commune = _jours_de_jeu(horaires)
        plage = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        if jours is not None and not any(d.weekday() in jours for d in plage):
            jours = None        # aucun jour de la plage n'est nommé : on n'ôte rien
        return [(d.isoformat(), heures.get(d.weekday(), commune or time_str), None)
                for d in plage
                if d.isoformat() >= today_iso and (jours is None or d.weekday() in jours)]

    has_du_range = bool(re.search(
        r"\bdu\s+\d+(?:er)?\s+(?:\w+\s+)?au\s+\d+(?:er)?\s", norm))

    # 1) "Jusqu'au X" sans "Du" : événement en cours, sans début connu.
    if "jusqu" in norm and not has_du_range:
        m = re.search(r"jusqu.{0,4}au\s+(\d{1,2})(?:er)?\s+" + MOIS
                      + r"(?:\s+(\d{4}))?\b", norm)
        if not m:
            return []
        day, month = int(m.group(1)), MONTHS_FR[m.group(2)]
        try:
            end = date(_year_for(month, day, m.group(3)), month, day)
        except ValueError:
            return []
        if end < today:
            return []
        return [(today_iso, time_str, end.isoformat())]

    # 2) Plage dans le même mois : "Du X au Y mois YYYY"
    m = re.search(r"\bdu\s+(\d{1,2})(?:er)?\s+au\s+(\d{1,2})(?:er)?\s+"
                  + MOIS + r"(?:\s+(\d{4}))?", norm)
    if m:
        d1, d2, month = int(m.group(1)), int(m.group(2)), MONTHS_FR[m.group(3)]
        year = _year_for(month, d1, m.group(4))
        try:
            return _expand(date(year, month, d1), date(year, month, d2), norm[m.end():])
        except ValueError:
            return []

    # 3) Plage à cheval sur deux mois : "Du 28 mai au 3 juin 2026", ou avec
    #    l'année du début : "Du 16 octobre 2026 au 15 août 2027". BUG-20 :
    #    cette année-là n'était pas attendue, la plage échappait à la
    #    lecture et devenait son seul premier jour — une exposition de dix
    #    mois annoncée un seul jour.
    m = re.search(r"\bdu\s+(\d{1,2})(?:er)?\s+" + MOIS + r"(?:\s+(\d{4}))?"
                  + r"\s+au\s+(\d{1,2})(?:er)?\s+" + MOIS
                  + r"(?:\s+(\d{4}))?", norm)
    if m:
        d1, m1 = int(m.group(1)), MONTHS_FR[m.group(2)]
        d2, m2 = int(m.group(4)), MONTHS_FR[m.group(5)]
        year_end = _year_for(m2, d2, m.group(6))
        # "du 30 decembre au 2 janvier 2027" : l'année écrite est celle de la fin.
        year_start = (int(m.group(3)) if m.group(3)
                      else year_end - 1 if m1 > m2 else year_end)
        try:
            return _expand(date(year_start, m1, d1), date(year_end, m2, d2), norm[m.end():])
        except ValueError:
            return []

    # 4) Date(s) isolée(s) : le premier « [jour] DD mois [YYYY] », et ceux
    #    qui le suivent aussitôt, joints par « et » ou une virgule (BUG-17) :
    #    « Jeudi 1 octobre et Vendredi 2 octobre à 19h ». Seule la première
    #    date était lue, la seconde perdue. Chaque date prend l'heure de SON
    #    jour quand le texte en donne une (« jeudi à 20h, vendredi à 18h »),
    #    l'heure commune sinon.
    une_date = (r"(?:\b(" + JOURS + r")\s+)?\b(\d{1,2})(?:er)?\s+" + MOIS
                + r"(?:\s+(\d{4}))?")
    m = re.search(une_date, norm)
    if not m:
        return []
    trouvees = [m]
    suite = re.compile(r"\s+(?:et\s+)?" + une_date)
    while True:
        m = suite.match(norm, trouvees[-1].end())
        if not m:
            break
        trouvees.append(m)
    par_jour = {}
    for j, hh, mm in re.findall(r"\b(" + JOURS + r")s?\s+(?:a|de)\s+(\d{1,2})h(\d{0,2})\b",
                                norm):
        if int(hh) < 24 and int(mm or 0) < 60:
            par_jour.setdefault(j, f"{int(hh):02d}:{int(mm or 0):02d}")
    dates = []
    for m in trouvees:
        jour_semaine, day, month = m.group(1), int(m.group(2)), MONTHS_FR[m.group(3)]
        try:
            dates.append(date(_year_for(month, day, m.group(4), jour_semaine), month, day))
        except ValueError:
            continue
    # Deux jours qui se suivent et un horaire qui passe minuit : « Samedi 24
    # octobre et Dimanche 25 octobre de 22h à 4h30 » est UNE nuit, celle du
    # samedi — la Halle Tony Garnier l'annonce « le 24 octobre, fin 04h30 » —,
    # et non deux soirées.
    nuit = re.search(r"\bde\s+(\d{1,2})h(\d{0,2})\s+a\s+(\d{1,2})h(\d{0,2})\b", norm)
    if (nuit and not par_jour and len(dates) == 2
            and dates[1] - dates[0] == timedelta(days=1)
            and (int(nuit.group(3)), int(nuit.group(4) or 0))
            < (int(nuit.group(1)), int(nuit.group(2) or 0))):
        dates = dates[:1]
    return [(d.isoformat(), par_jour.get(_JOURS_FR[d.weekday()], time_str), None)
            for d in dates if d.isoformat() >= today_iso]


def _extract_events_from_soup(soup: BeautifulSoup) -> List[Event]:
    """Find every event in the parsed page and return Event objects."""
    today_iso = date.today().isoformat()
    events: List[Event] = []

    # Find every "title link" — an <a> inside an h-tag that points to an
    # /agenda-NNNNNN-slug.html URL. The same URL may appear several times
    # on the page (title, venue, date all link to it); we only want the
    # title occurrence.
    seen_urls: set[str] = set()
    title_links = soup.find_all(
        "a",
        href=re.compile(r"/agenda-\d+-[^.]+\.html")
    )

    for a in title_links:
        h_parent = a.find_parent(["h1", "h2", "h3", "h4"])
        if h_parent is None:
            continue
        # We only treat the FIRST link in the h-tag as the title link
        first_a = h_parent.find("a")
        if first_a is not a:
            continue

        href = a.get("href", "")
        if not href or href in seen_urls:
            continue
        seen_urls.add(href)

        title = _desechappe(a.get_text(strip=True))
        if not title:
            continue

        # Walk forward through siblings to find category, venue, date.
        category: Optional[str] = None
        venue: Optional[str] = None
        date_str: Optional[str] = None

        cur = h_parent
        depth = 0
        while True:
            cur = cur.find_next_sibling()
            depth += 1
            if cur is None or depth > 30:
                break
            if cur.name in ("h1", "h2", "h3", "h4"):
                break  # next event starts

            text = cur.get_text(strip=True)

            # Category line: "(Foo)" alone
            if category is None and text:
                cm = re.match(r"^\(([^)]+)\)\s*$", text)
                if cm:
                    category = cm.group(1).strip()
                    continue

            # Venue + date in a <ul>
            if venue is None and cur.name == "ul":
                lis = cur.find_all("li", recursive=False)
                if len(lis) >= 1:
                    va = lis[0].find("a")
                    venue = _desechappe((va or lis[0]).get_text(strip=True))
                if len(lis) >= 2:
                    da = lis[1].find("a")
                    date_str = (da or lis[1]).get_text(strip=True)
                # don't break — there might be more useful sibs, but
                # typically nothing else relevant follows the ul
                break

        # La catégorie est OPTIONNELLE : certains blocs ont un paragraphe de
        # description là où se trouve d'habitude la ligne « (Catégorie) ».
        # Seuls le lieu et la date sont exigés — ils suffisent à identifier un
        # vrai bloc d'événement. Sans cet assouplissement, une quinzaine
        # d'événements par passage restaient invisibles.
        if not venue or not date_str:
            continue

        date_times = _parse_date_str(date_str)
        if not date_times:
            continue

        url = href if href.startswith("http") else BASE + href

        for date_iso, time_str, date_end in date_times:
            # Un événement à plage est conservé tant qu'il n'est pas terminé.
            if (date_end or date_iso) < today_iso:
                continue
            events.append(Event(
                venue=venue,
                venue_slug=_slugify(venue),
                title=title,
                subtitle=None,
                category=category,
                date_start=date_iso,
                date_end=date_end,
                time=time_str,
                url=url,
                image=None,
            ))

    return events


def fetch() -> List[Event]:
    """Parcourt toutes les pages de l'agenda Petit Bulletin.

    L'agenda est paginé (`?p=N`, 164 événements sur 9 pages aujourd'hui) et
    seule la première page était lue : les 8 autres — soit ~85 % du contenu —
    n'arrivaient jamais dans nocturne. La boucle s'arrête dès qu'une page
    n'apporte plus aucune URL nouvelle, ou au cap de MAX_PAGES.
    """
    headers = {"User-Agent": USER_AGENT}
    events: List[Event] = []
    seen_urls: set[str] = set()
    ecartes = 0

    for page in range(1, MAX_PAGES + 1):
        url = URL if page == 1 else f"{URL}?p={page}"
        try:
            r = base_get(url, headers=headers, timeout=30)
        except requests.RequestException as exc:
            if page == 1:
                raise
            print(f"[Petit Bulletin] page {page} injoignable ({exc}) — arrêt",
                  file=sys.stderr)
            break
        if r.status_code != 200:
            if page == 1:
                r.raise_for_status()
            break

        page_events = _extract_events_from_soup(BeautifulSoup(r.text, "html.parser"))
        fresh = [e for e in page_events if e.url not in seen_urls]
        if not fresh:
            break                      # page vide ou déjà vue : fin de l'agenda
        for e in fresh:
            seen_urls.add(e.url)
        # Le filtre s'applique APRÈS le test de fraîcheur : une page qui ne
        # contiendrait que des arts plastiques serait sinon prise pour la fin
        # de l'agenda, et la pagination s'arrêterait là.
        gardes = [e for e in page_events
                  if _normalize(e.category) not in CATEGORIES_ECARTEES]
        ecartes += len(page_events) - len(gardes)
        events.extend(gardes)

        if page < MAX_PAGES:
            time.sleep(0.4)            # on ne martèle pas le serveur

    if ecartes:
        print(f"[Petit Bulletin] {ecartes} événement(s) d'arts plastiques "
              f"écarté(s) ({', '.join(sorted(CATEGORIES_ECARTEES))})")
    return events
