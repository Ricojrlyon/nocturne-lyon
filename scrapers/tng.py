"""Scraper for Théâtre Nouvelle Génération (tng-lyon.fr).

Listing page: <a href="/evenement/<slug>/"> wraps all card data.
Dates et heures : sur la fiche de chaque spectacle, qui liste ses séances
tout public (voir _seances_publiques).
Strategy: collect stubs from listing, then fetch each detail page for its
public sessions - one Event per session.
"""
from typing import List, Optional, Tuple
from datetime import date as Date, timedelta
import re
import sys
import time
import requests
from bs4 import BeautifulSoup

from .base import Event, img_src, iso, FR_MONTHS, get as base_get

VENUE = "TNG"
SLUG  = "tng"
HOST  = "https://www.tng-lyon.fr"
URL   = HOST + "/programme/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
                  "+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

SHORT_MONTHS = {
    "janv": 1, "fevr": 2, "févr": 2, "mars": 3, "avr": 4, "mai": 5,
    "juin": 6, "juil": 7, "juill": 7, "aout": 8, "août": 8, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12, "déc": 12,
}

# « 02 oct. > 06 oct. », mais aussi « 02 > 06 oct. », le mois écrit une
# seule fois : c'est ainsi que le programme écrit ses plages (BUG-16), et
# la date de fin passait seule pour la date du spectacle.
DATE_RANGE = re.compile(
    r"\b(\d{1,2})(?:\s+([\wéèêôû]+)\.?)?\s*[>→–\-]\s*(\d{1,2})\s+([\wéèêôû]+)",
    re.IGNORECASE | re.DOTALL,
)
DATE_SINGLE = re.compile(
    r"(\d{1,2})\s+([\wéèêôû]+)",
    re.IGNORECASE,
)


def _normalize_month(s: str) -> Optional[int]:
    s = s.lower().rstrip(".")
    s = (s.replace("é","e").replace("è","e").replace("ê","e")
           .replace("ô","o").replace("û","u"))
    if s in FR_MONTHS:
        return FR_MONTHS[s]
    return SHORT_MONTHS.get(s)


def _slug_from_href(href: str) -> str:
    return href.split("?")[0].split("#")[0].rstrip("/").lower() if href else ""


def _smart_year(month: int, day: int, ref: Date) -> int:
    grace = 15
    try:
        candidate = Date(ref.year, month, day)
    except ValueError:
        return ref.year
    return ref.year + 1 if (ref - candidate).days > grace else ref.year


def _extract_dates(text: str) -> Tuple[Optional[Date], Optional[Date]]:
    today = Date.today()
    m = DATE_RANGE.search(text)
    if m:
        d1, mo1, d2, mo2 = m.groups()
        month2 = _normalize_month(mo2)
        month1 = _normalize_month(mo1) if mo1 else month2
        if month1 and month2:
            try:
                day1, day2 = int(d1), int(d2)
                if 1 <= day1 <= 31 and 1 <= day2 <= 31:
                    if month1 == month2:
                        year = _smart_year(month1, day1, today)
                        return Date(year, month1, day1), Date(year, month2, day2)
                    else:
                        start_year = _smart_year(month1, day1, today)
                        end_year = start_year + 1 if month1 > month2 else start_year
                        return Date(start_year, month1, day1), Date(end_year, month2, day2)
            except ValueError:
                pass
    for m2 in DATE_SINGLE.finditer(text):
        d, mo = m2.group(1), m2.group(2)
        month = _normalize_month(mo)
        if month:
            try:
                day = int(d)
                if 1 <= day <= 31:
                    year = _smart_year(month, day, today)
                    return Date(year, month, day), None
            except ValueError:
                continue
    return None, None


JOURS = ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")

# Une séance de la fiche n'est retenue qu'à une semaine au plus des dates
# de la carte du programme. Au-delà, c'est une erreur de saisie : la fiche
# de l'atelier intergénérationnel du « Chat sur la photo » (carte : 30
# janvier) reprenait la séance du 16 janvier d'un autre atelier.
MARGE_SEANCES = timedelta(days=7)
MIN_INTERVAL = 0.4


def _seances_publiques(url: str) -> List[Tuple[Date, Optional[str]]]:
    """(jour, heure) des séances TOUT PUBLIC de la fiche d'un spectacle.

    BUG-16 : la carte du programme ne donne qu'une plage (« 02 > 06
    oct. »), qui couvre aussi les séances scolaires et les relâches. La
    fiche, elle, liste ses séances à part, div.event-sessions : un mois en
    p.month, puis par séance le jour (« ven 02 ») et une ou plusieurs
    heures. Les séances scolaires, div.event-school, réservées aux classes,
    sont écartées comme à l'Auditorium. L'année n'est pas écrite
    (_smart_year), et le nom du jour la contrôle. Liste vide si la fiche
    ne répond pas ou n'a pas de séances.
    """
    try:
        r = base_get(url, timeout=10, headers=HEADERS)
    except requests.RequestException:
        return []
    if r.status_code != 200:
        return []
    bloc = BeautifulSoup(r.text, "html.parser").select_one("div.event-sessions")
    if bloc is None:
        return []
    today = Date.today()
    out: List[Tuple[Date, Optional[str]]] = []
    mois = None
    for el in bloc.find_all(["p", "div"], recursive=False):
        classes = el.get("class") or []
        if "month" in classes:
            mois = _normalize_month(el.get_text(strip=True))
            continue
        if "session-line" not in classes or not mois:
            continue
        jour = el.select_one("p.date")
        m = (re.match(r"\s*([a-z]{3})[a-z]*\.?\s+(\d{1,2})\b",
                      jour.get_text(" ", strip=True).lower()) if jour else None)
        if not m:
            continue
        try:
            d = Date(_smart_year(mois, int(m.group(2)), today), mois, int(m.group(2)))
        except ValueError:
            continue
        if JOURS[d.weekday()] != m.group(1):
            continue                     # le jour annoncé contredit la date
        heures = [re.search(r"\b(\d{1,2})[h:](\d{2})\b", h.get_text(" ", strip=True))
                  for h in el.select("span.hour")]
        heures = [f"{int(h.group(1)):02d}:{h.group(2)}" for h in heures if h]
        for heure in heures or [None]:
            out.append((d, heure))
    return out


def fetch() -> List[Event]:
    try:
        resp = base_get(URL, timeout=20, headers=HEADERS)
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    stubs: List[dict] = []
    seen_slugs: set = set()
    today = Date.today()

    for a in soup.select('a[href*="/evenement/"]'):
        href = a.get("href", "")
        if href.startswith("/"):
            href = HOST + href
        slug = _slug_from_href(href)
        if not slug or slug in seen_slugs:
            continue
        if slug.endswith("/evenement"):
            continue

        h2 = a.find(["h2", "h3"])
        if not h2:
            continue
        title = h2.get_text(" ", strip=True)
        if not title or len(title) < 2 or len(title) > 250:
            continue

        text = a.get_text(" ", strip=True)
        d_start, d_end = _extract_dates(text)
        if not d_start:
            continue
        if d_start < today and (d_end is None or d_end < today):
            continue

        subtitle: Optional[str] = None
        for tn in a.stripped_strings:
            if tn == title:
                continue
            tn_lower = tn.lower()
            if (DATE_RANGE.fullmatch(tn) or DATE_SINGLE.fullmatch(tn) or
                    re.fullmatch(r"\d{1,2}", tn)):
                continue
            if tn_lower in (">", "→", "tng-vaise", "ateliers - presqu'île",
                            "en famille", "réserver", "plus d'infos",
                            "voir plus", "gratuit", "spectacle", "atelier"):
                continue
            if tn_lower.startswith("dès ") or _normalize_month(tn):
                continue
            if len(tn) < 3 or len(tn) > 250:
                continue
            # Le site coupe ses extraits à l'octet, parfois au milieu d'un
            # caractère : « ateliers créatifs, Pop » suivi du losange de
            # remplacement (U+FFFD), l'apostrophe de Pop’Corn tranchée en
            # deux. Le losange est déjà dans sa page ; on le retire plutôt
            # que de le publier (BUG-35).
            subtitle = tn.replace("\ufffd", "")
            break

        image = img_src(a.find("img"), host=HOST)

        seen_slugs.add(slug)
        stubs.append({
            "title": title, "subtitle": subtitle, "d_start": d_start,
            "d_end": d_end, "url": href.split("?")[0], "image": image,
        })

    # Cap horizon: drop events more than ~6 months out BEFORE the
    # detail-page fetch phase - keeps the daily run fast and the JSON lean.
    horizon = Date.today() + timedelta(days=180)
    stubs = [s for s in stubs if s["d_start"] <= horizon]

    # Une date par séance tout public de la fiche (BUG-16). La fiche est
    # relue à chaque passage, sans le cache des heures : une séance ajoutée
    # ou retirée doit se voir dès le lendemain. Sans séance utilisable, la
    # plage de la carte, comme avant.
    events: List[Event] = []
    for i, stub in enumerate(stubs):
        if i:
            time.sleep(MIN_INTERVAL)
        debut, fin = stub["d_start"], stub["d_end"] or stub["d_start"]
        toutes = _seances_publiques(stub["url"])
        proches = [(d, h) for d, h in toutes
                   if debut - MARGE_SEANCES <= d <= fin + MARGE_SEANCES]
        if proches:
            # Celles qui restent à venir ; aucune si le public a vu sa
            # dernière séance, même quand la carte court encore - ses
            # derniers jours ne sont plus que des séances scolaires.
            dates = [(d, None, h) for d, h in proches if today <= d <= horizon]
        else:
            # Faute de séance retenue, la carte ; l'heure de la fiche reste
            # bonne à prendre quand seule sa date est fausse.
            dates = [(stub["d_start"], stub["d_end"],
                      next((h for _, h in toutes if h), None))]
        for d_start, d_end, heure in dates:
            events.append(Event(
                venue=VENUE,
                venue_slug=SLUG,
                title=stub["title"],
                subtitle=stub["subtitle"],
                category="théâtre",
                date_start=iso(d_start),
                date_end=iso(d_end) if d_end else None,
                time=heure,
                url=stub["url"],
                image=stub["image"],
            ))

    if not events:
        print("=" * 60, file=sys.stderr)
        print("DIAGNOSTIC: TNG - 0 events", file=sys.stderr)
        try:
            resp2 = base_get(URL, timeout=15, headers=HEADERS)
            soup2 = BeautifulSoup(resp2.text, "html.parser")
            ev_links = soup2.select('a[href*="/evenement/"]')
            print(f"  /evenement/ <a> count: {len(ev_links)}", file=sys.stderr)
            for a in ev_links[:3]:
                print(f"  href={a.get('href','')!r} | text: {a.get_text(' ',strip=True)[:100]!r}",
                      file=sys.stderr)
        except requests.RequestException as e:
            print(f"  failed: {e}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    events.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, "→", e.date_end or "  -  ", e.time or "  -  ", "·", e.title)
