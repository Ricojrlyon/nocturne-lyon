"""Scraper for L'Opéra national de Lyon (opera-lyon.com).

Each production has a detail page listing individual performance times.
Strategy:
1. Scrape listing page → get productions (title, date range, URL)
2. For each production, fetch the detail page to get the first/main
   performance time (Opera shows "20h00" or "15h00" for matinées).

Le listing se lit par CARTE, pas par lien : il a deux gabarits, et l'un
d'eux sort le titre et la date du <a>. Voir _scrape_url.
"""
from typing import List, Optional, Tuple
from datetime import date as Date, timedelta
import json
import re
import sys
import unicodedata
import requests
from bs4 import BeautifulSoup, Tag

from . import detail_cache
from .base import Event, iso, FR_MONTHS, OFFSITE_PLUSIEURS

VENUE = "Opéra national de Lyon"
SLUG  = "opera-lyon"
HOST  = "https://www.opera-lyon.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

URLS = [
    HOST + "/programmation-reservations/saison-2025-2026",
    HOST + "/programmation-reservations/saison-2026-2027",
]

URL_CATEGORY_MAP = {
    "opera": "opéra",
    "danse": "danse",
    "concert": "concert",
    "evenement": "événement",
    "opera-underground": "underground",
    "conference": "conférence",
    "visites": "visite",
    "festival": "festival",
}

SHORT_MONTHS = {
    "janv": 1, "fevr": 2, "févr": 2, "mars": 3, "avr": 4, "mai": 5,
    "juin": 6, "juil": 7, "aout": 8, "août": 8, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12, "déc": 12,
}

DATE_SINGLE = re.compile(
    r"\b(\d{1,2})\s+([\wéèêôû]+\.?)\s+(\d{4})\b",
    re.IGNORECASE,
)
DATE_RANGE_SAME = re.compile(
    r"\b(\d{1,2})\s+([\wéèêôû]+\.?)\s*[-–]\s*(\d{1,2})\s+([\wéèêôû]+\.?)\s+(\d{4})\b",
    re.IGNORECASE,
)


def _normalize_month(s: str) -> Optional[int]:
    s = s.lower().rstrip(".")
    s = (s.replace("é","e").replace("è","e").replace("ê","e")
           .replace("ô","o").replace("û","u"))
    if s in FR_MONTHS:
        return FR_MONTHS[s]
    return SHORT_MONTHS.get(s)


def _extract_dates(text: str) -> Tuple[Optional[Date], Optional[Date]]:
    m = DATE_RANGE_SAME.search(text)
    if m:
        d1, mo1, d2, mo2, yr = m.groups()
        month1, month2 = _normalize_month(mo1), _normalize_month(mo2)
        year = int(yr)
        if month1 and month2:
            try:
                start_year = year - 1 if month1 > month2 else year
                return Date(start_year, month1, int(d1)), Date(year, month2, int(d2))
            except ValueError:
                pass
    m = DATE_SINGLE.search(text)
    if m:
        d, mo, yr = m.groups()
        month = _normalize_month(mo)
        if month:
            try:
                return Date(int(yr), month, int(d)), None
            except ValueError:
                pass
    return None, None


def _par_prefixe(racine, prefixe: str) -> List[Tag]:
    """Éléments dont une classe COMMENCE par le préfixe.

    Le site est un Nuxt : ses classes portent un hash de build —
    title_VhuBc, date_frXXU, subtitle_ZAgnv — qui change à chaque
    déploiement. Le préfixe, lui, vient du nom de classe source et tient.

    Le début compte : « title_ » cherché en sous-chaîne attrape aussi
    « subtitle_ », et toute carte paraît alors en porter deux.
    """
    return [el for el in racine.find_all(attrs={"class": True})
            if any(c.startswith(prefixe) for c in el["class"])]


def _premier(racine, prefixe: str) -> Optional[Tag]:
    els = _par_prefixe(racine, prefixe)
    return els[0] if els else None


# Les visites guidées de la maison, écartées comme partout ailleurs dans
# le fil — les Beaux-Arts en écartent 53, l'Auditorium 56 ateliers, l'IAC
# les siennes, le macLYON aussi. Ce ne sont pas des spectacles, elles
# reviennent plusieurs fois par semaine toute la saison, et elles
# noieraient la programmation : « Visites découverte commentées » annonce
# à elle seule 108 séances jusqu'en juillet 2027, et les Journées du
# Patrimoine 14 créneaux d'une demi-heure sur la même journée.
#
# La règle porte sur le titre ET le sous-titre : « Journées du
# Patrimoine » ne se trahit que par son sous-titre « Visite découverte de
# l'Opéra de Lyon ». Le Petit Bulletin continue d'en publier quelques-unes
# de son côté, à une cadence de lecteur plutôt que de billetterie.
_VISITE = re.compile(r"\bvisite", re.IGNORECASE)


def _est_visite(titre: str, sous_titre: Optional[str]) -> bool:
    return bool(_VISITE.search(titre or "")
                or _VISITE.search(sous_titre or ""))


def _category_from_url(href: str) -> Optional[str]:
    m = re.search(r"/programmation/saison-\d{4}-\d{4}/([^/]+)/", href)
    if m:
        return URL_CATEGORY_MAP.get(m.group(1).lower(), m.group(1))
    return None


def _sans_accents(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower())
                   if unicodedata.category(c) != "Mn")


def _champ_fiche(soup: BeautifulSoup, nom: str) -> Optional[str]:
    """Valeur d'un champ de la colonne d'informations de la fiche.

    La fiche range ses métadonnées en paires étiquette/valeur — Dates,
    Tarifs, Lieu, Durée, Âge, Début. On lit l'étiquette et on prend son
    frère suivant, ce qui vaut mieux que de chercher l'information dans
    le texte de la page : « 13h30 » et « 16h30 » y traînent en toutes
    lettres, ce sont les horaires de la BILLETTERIE.
    """
    cible = _sans_accents(nom)
    for lab in _par_prefixe(soup, "info-list__item__label"):
        if _sans_accents(lab.get_text(strip=True)) == cible:
            val = lab.find_next_sibling()
            if val is not None:
                return val.get_text(" ", strip=True)
    return None


# « 19h », « 20h30 ». Le champ Début ne contient rien d'autre.
_HEURE_CHAMP = re.compile(r"^(\d{1,2})\s*h\s*(\d{2})?$")


def _heure_fiche(soup: BeautifulSoup) -> Optional[str]:
    champ = _champ_fiche(soup, "Début")
    m = _HEURE_CHAMP.match((champ or "").strip())
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2) or 0)
    return "%02d:%02d" % (hh, mm) if 0 <= hh <= 23 and 0 <= mm <= 59 else None


# « 2026-11-28T20:00:00+01:00 ». On lit la date et l'heure TELLES QU'ÉCRITES,
# sans toucher au décalage : il vaut déjà l'heure de Paris, et convertir
# reviendrait à décaler une représentation de 20h00 à 19h00 la moitié de
# l'année.
_DEBUT_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})")


def _seances_jsonld(soup: BeautifulSoup) -> List[list]:
    """[jour_iso, heure] de chaque représentation annoncée.

    La fiche embarque un tableau schema.org/Event, UN PAR REPRÉSENTATION,
    avec l'heure exacte. C'est la seule source juste : le listing ne donne
    qu'une plage, et le frontend étale une plage sur chacun de ses jours.
    « L'opéra par l'Orchestre » annonçait ainsi 72 jours de concert pour
    deux dates, le 18 septembre et le 28 novembre. Mesuré sur les cinq
    productions que le site publiait au 2026-09-17 : 103 jours peints pour
    29 représentations réelles. L'erreur va d'ailleurs dans les deux sens
    — « La Fille de Madame Angot » ne peignait qu'UN jour pour huit
    représentations, sa plage à cheval sur deux années n'ayant pas été
    reconnue.

    Le champ `location` du JSON-LD, lui, est inutilisable : il répond
    « Opéra de Lyon » même pour les représentations données ailleurs.
    C'est le champ Lieu de la fiche qui dit la vérité.
    """
    seances = set()
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(sc.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for ev in (data if isinstance(data, list) else [data]):
            if not isinstance(ev, dict):
                continue
            if "Event" not in str(ev.get("@type", "")):
                continue
            m = _DEBUT_ISO.match(str(ev.get("startDate") or ""))
            if m:
                seances.add((m.group(1), m.group(2)))
    return [list(x) for x in sorted(seances)]


# Un mot qui désigne un lieu de spectacle. Sert à découper le champ Lieu
# en SALLES : « Salle Molière, Lyon 5e, Théâtre Théo Argence - Saint-Priest »
# en nomme deux, la mention « Lyon 5e » n'étant que l'adresse de la
# première. Sans ce tri, une virgule de plus ferait une salle de plus.
_MOT_DE_SALLE = re.compile(
    r"\b(salle|th[eé][aâ]tre|amphi|op[eé]ra|auditorium|studio|chapelle|"
    r"espace|maison|cin[eé]ma|halle|conservatoire)\b", re.IGNORECASE)


def _hors_les_murs(lieu: Optional[str]) -> Optional[str]:
    """Salle réelle, quand la production ne se joue pas dans les murs.

    Rend None pour une production jouée à l'Opéra — l'Amphi compris, qui
    est une salle de la maison —, le nom de la salle quand la fiche n'en
    nomme qu'une, et OFFSITE_PLUSIEURS quand elle en nomme plusieurs.

    Le suffixe de commune est retiré : « Théâtre Théo Argence -
    Saint-Priest » devient « Théâtre Théo Argence », qui est déjà la clé
    sous laquelle venue_arrondissements.json le connaît. C'est le
    géocodeur qui dira Saint-Priest.
    """
    if not lieu:
        return None
    salles = [f.strip() for f in lieu.split(",") if _MOT_DE_SALLE.search(f)]
    if not salles:
        salles = [lieu.strip()]
    dehors = [s for s in salles
              if "opera de lyon" not in _sans_accents(s)]
    if not dehors:
        return None
    if len(dehors) > 1:
        return OFFSITE_PLUSIEURS
    return re.split(r"\s[-–—]\s", dehors[0])[0].strip() or None


def _lire_fiche(url: str) -> Optional[dict]:
    """Représentations, lieu et heure de secours. Rendu à detail_cache."""
    try:
        r = requests.get(url, timeout=20, headers=HEADERS)
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"[Opéra] {url}: {exc}", file=sys.stderr)
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    return {
        "seances": _seances_jsonld(soup),
        "lieu": _champ_fiche(soup, "Lieu"),
        "time": _heure_fiche(soup),
    }


def _scrape_url(url: str) -> List[dict]:
    try:
        resp = requests.get(url, timeout=20, headers=HEADERS)
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    stubs: List[dict] = []
    visites: List[str] = []
    seen_urls: set = set()
    today = Date.today()

    # On part du TITRE et non du lien. Le listing a deux gabarits de
    # carte : le premier met titre, date et sous-titre DANS le <a>, le
    # second les met à côté, le <a> ne portant plus que l'image et une
    # pastille de genre. Lire le lien ne voyait donc que le premier
    # gabarit — 5 productions sur 14 au 2026-09-17, les 9 autres, dont
    # Quatuor Béla et les Concerts du CNSMD, n'ayant jamais existé pour
    # le site.
    #
    # Partir du titre attrape les deux, et en prime les champs sont lus
    # à leur classe au lieu d'être devinés dans la suite des nœuds de
    # texte. C'est ce devinage qui publiait « 14 déc. 2026 - 3 janv.
    # 2027 » comme titre de La Fille de Madame Angot : le nœud de date
    # était écarté par comparaison à deux motifs, dont aucun ne couvrait
    # une plage à cheval sur deux années, et il passait donc en tête.
    for titre_el in _par_prefixe(soup, "title_"):
        carte, a = titre_el, None
        for _ in range(6):
            carte = carte.parent
            if carte is None:
                break
            candidat = carte.select_one('a[href*="/programmation/saison-"]')
            if candidat is None:
                continue
            # Une carte ne porte qu'un titre. Au-delà, on a débordé sur le
            # carrousel entier et le lien trouvé n'est plus celui du titre.
            if len(_par_prefixe(carte, "title_")) != 1:
                break
            a = candidat
            break
        if a is None:
            continue

        href = a.get("href", "")
        if href.startswith("/"):
            href = HOST + href
        if "/programmation/saison-" not in href:
            continue
        if href in (url, url + "/") or href in seen_urls:
            continue

        d_el = _premier(carte, "date_")
        d_start, d_end = _extract_dates(d_el.get_text(" ", strip=True)
                                        if d_el else "")
        if not d_start:
            continue
        if d_start < today and (d_end is None or d_end < today):
            continue

        title = titre_el.get_text(" ", strip=True)
        if not title:
            continue
        s_el = _premier(carte, "subtitle_")
        subtitle = s_el.get_text(" ", strip=True) if s_el else None
        if subtitle and subtitle.lower().startswith("dès "):
            subtitle = None

        category = _category_from_url(href) or "spectacle"

        image: Optional[str] = None
        img = carte.find("img")
        if img:
            src = img.get("src", "") or ""
            if src.startswith("http"):
                image = src

        seen_urls.add(href)
        if _est_visite(title, subtitle):
            visites.append(title)
            continue
        stubs.append({
            "title": title, "subtitle": subtitle, "category": category,
            "d_start": d_start, "d_end": d_end, "url": href, "image": image,
        })

    if visites:
        print("[Opéra] visites écartées : %s" % ", ".join(sorted(set(visites))),
              file=sys.stderr)
    return stubs


def fetch() -> List[Event]:
    all_stubs: List[dict] = []
    seen_urls: set = set()
    for url in URLS:
        for stub in _scrape_url(url):
            if stub["url"] not in seen_urls:
                seen_urls.add(stub["url"])
                all_stubs.append(stub)

    # Cap horizon: drop events more than ~6 months out BEFORE the
    # detail-page fetch phase — keeps the daily run fast and the JSON lean.
    horizon = Date.today() + timedelta(days=180)
    all_stubs = [s for s in all_stubs if s["d_start"] <= horizon]

    # Une carte par REPRÉSENTATION, pas une plage. Le listing ne donne
    # qu'un intervalle, et le frontend l'étale sur chacun de ses jours :
    # une plage annonce donc des soirs où rien ne se joue. Les fiches sont
    # lues une fois puis mises en cache (scrapers/detail_cache.py).
    events: List[Event] = []
    today_iso, horizon_iso = Date.today().isoformat(), horizon.isoformat()
    sans_seance: List[str] = []
    dehors: List[str] = []
    for stub in all_stubs:
        fiche = detail_cache.get_details(stub["url"], _lire_fiche,
                                         fields=("seances", "lieu", "time"))
        commun = dict(
            venue=VENUE,
            venue_slug=SLUG,
            title=stub["title"],
            subtitle=stub["subtitle"],
            category=stub["category"],
            url=stub["url"],
            image=stub["image"],
            offsite_venue=_hors_les_murs(fiche.get("lieu")),
        )
        if commun["offsite_venue"]:
            dehors.append("%s → %s" % (stub["title"], commun["offsite_venue"]))
        seances = fiche.get("seances") or []
        if seances:
            for jour_iso, heure in seances:
                if not (today_iso <= jour_iso <= horizon_iso):
                    continue
                events.append(Event(date_start=jour_iso, date_end=None,
                                    time=heure, **commun))
            continue
        # Pas de représentation annoncée — cela arrive sur les fiches
        # gratuites et sur certains concerts : on retombe sur la plage du
        # listing, et sur l'heure du champ « Début » quand il existe.
        sans_seance.append(stub["title"])
        events.append(Event(
            date_start=iso(stub["d_start"]),
            date_end=iso(stub["d_end"]) if stub["d_end"] else None,
            time=fiche.get("time"),
            **commun,
        ))

    if sans_seance:
        print("[Opéra] sans représentation annoncée, repli sur la plage du "
              "listing : %s" % ", ".join(sans_seance), file=sys.stderr)
    if dehors:
        print("[Opéra] hors les murs, CONSERVÉS et marqués : %s"
              % " | ".join(dehors), file=sys.stderr)

    if not events:
        print("=" * 60, file=sys.stderr)
        print("DIAGNOSTIC: Opéra de Lyon — 0 events", file=sys.stderr)
        for url in URLS:
            try:
                resp = requests.get(url, timeout=15, headers=HEADERS)
                print(f"  {url} -> {resp.status_code} ({len(resp.text)} bytes)",
                      file=sys.stderr)
            except requests.RequestException as e:
                print(f"  {url} -> failed: {e}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    events.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, "→", e.date_end or "  -  ", e.time or "  -  ",
              "·", e.category, "·", e.title)
