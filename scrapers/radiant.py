"""Scraper for Radiant-Bellevue (radiant-bellevue.fr).

Homepage lists upcoming events. Time is on each /spectacles/<slug>/ detail page.
Strategy: collect stubs from homepage, dedupe by URL, then fetch each detail page
once for time (in-request dedup avoids hitting the same URL twice for multi-date shows).

La fiche liste aussi chaque séance avec SON heure (div.spectacle-dates :
« dimanche 04 octobre 2026 », « 16h00 »). Une seule heure pour toutes les
dates d'un spectacle publiait les matinées du dimanche à l'heure du soir
(BUG-24) : chaque date prend désormais l'heure de ses séances.
"""
from collections import defaultdict
from typing import List, Optional
from datetime import date as Date, timedelta
import re
import sys
import requests
from bs4 import BeautifulSoup

from . import detail_cache
from .base import Event, img_src, FR_MONTHS, get as base_get

VENUE = "Radiant-Bellevue"
SLUG  = "radiant-bellevue"
URL   = "https://radiant-bellevue.fr/"
HOST  = "https://radiant-bellevue.fr"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
                  "+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

DATE_SINGLE = re.compile(
    r"(?:\w+\s+)?(\d{1,2})\s+(\w+)\s+(\d{4})",
    re.IGNORECASE,
)
DATE_AMP = re.compile(
    r"(\d{1,2})\s*&\s*(\d{1,2})\s+(\w+)\s+(\d{4})",
    re.IGNORECASE,
)
DATE_TRIPLE = re.compile(
    r"(\d{1,2}),\s*(\d{1,2})\s*&\s*(\d{1,2})\s+(\w+)\s+(\d{4})",
    re.IGNORECASE,
)

CATEGORIES = (
    "Musique", "Chanson", "Humour", "Magie", "Théâtre", "Danse",
    # « Cirque » manquait : Sans Regrets ?, The Genesis… restaient sans
    # genre, rangés dans la famille « autres » (BUG-23).
    "Cirque",
    "Famille", "Scolaires", "Club Bellevue", "Nouveauté",
)


def _categorie(text: str) -> Optional[str]:
    """Le premier genre du site que nomme la carte, ou None."""
    for kw in CATEGORIES:
        if kw in text:
            return kw.lower()
    return None


def _scolaire(text: str) -> bool:
    """Une carte « Scolaires » : un spectacle réservé aux classes (BUG-27).

    Ses séances se réservent au tarif « pour les écoles », comme les
    scolaires que l'Auditorium et le TNG écartent déjà. Une carte qui
    serait aussi « Famille » garderait ses dates, faute de savoir lesquelles
    sont publiques : aucune à l'écriture.
    """
    return "Scolaires" in text and "Famille" not in text


def _french_month_num(s: str) -> Optional[int]:
    return FR_MONTHS.get(s.lower())


def _find_card(link, max_levels: int = 6):
    el = link
    year_re = re.compile(r"\b20\d{2}\b")
    for _ in range(max_levels):
        parent = el.parent
        if parent is None or parent.name in ("html", "body"):
            return el
        el = parent
        if year_re.search(el.get_text(" ", strip=True)):
            return el
    return el


def _parse_time(text: str) -> Optional[str]:
    """Extract show time. Radiant shows are typically 20h00 or 20h30.
    Accept 14h-22h range (matinées included).
    """
    m = re.search(
        r"(?:à|heure|horaire|début|debut|ouverture|représentation|spectacle)"
        r"\s*[:\-]?\s*(\d{1,2})[h:](\d{0,2})",
        text, re.IGNORECASE,
    )
    if m:
        hh = int(m.group(1))
        mm_s = m.group(2)
        mm = int(mm_s) if mm_s else 0
        if 14 <= hh <= 22:
            return f"{hh:02d}:{mm:02d}"
    for m2 in re.finditer(r"\b(\d{1,2})[h:](\d{2})\b", text):
        hh, mm = int(m2.group(1)), int(m2.group(2))
        if 14 <= hh <= 22:
            return f"{hh:02d}:{mm:02d}"
    return None


def _heure_de_la_fiche(soup: BeautifulSoup) -> Optional[str]:
    """La première heure de la fiche : celle des dates sans séance lue."""
    for selector in (
        "[class*='horaire']", "[class*='time']", "[class*='heure']",
        "[class*='schedule']", "[class*='seance']", "[class*='date']", "time",
    ):
        for el in soup.select(selector)[:4]:
            t = _parse_time(el.get_text(" ", strip=True))
            if t:
                return t
    visible = soup.get_text(" ", strip=True)
    return _parse_time(visible[:800])


def _seances(soup: BeautifulSoup) -> List[List[str]]:
    """Les séances de la fiche, [date ISO, heure], toutes heures comprises."""
    seances: List[List[str]] = []
    for li in soup.select("div.spectacle-dates li"):
        texte = li.get_text(" ", strip=True)
        m = DATE_SINGLE.search(texte)
        month = _french_month_num(m.group(2)) if m else None
        if not month:
            continue
        try:
            jour = Date(int(m.group(3)), month, int(m.group(1))).isoformat()
        except ValueError:
            continue
        for h in re.finditer(r"\b(\d{1,2})\s*[h:]\s*(\d{2})?(?!\d)", texte[m.end():]):
            seances.append([jour, f"{int(h.group(1)):02d}:{h.group(2) or '00'}"])
    return seances


def _lire_fiche(url: str) -> Optional[dict]:
    """Fetch /spectacles/<slug>/ : son heure et ses séances datées."""
    try:
        r = base_get(url, timeout=10, headers=HEADERS)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        return {"time": _heure_de_la_fiche(soup), "seances": _seances(soup)}
    except requests.RequestException:
        return None


def fetch() -> List[Event]:
    resp = base_get(URL, timeout=20, headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Pass 1: collect stubs from listing
    raw_stubs: List[dict] = []
    seen_urls: set = set()
    stub_par_url: dict = {}
    scolaires: List[str] = []

    for a in soup.select('a[href*="/spectacles/"]'):
        href = a.get("href", "")
        if href.startswith("/"):
            href = HOST + href
        if not href.startswith("http"):
            continue
        if "/spectacles/" not in href or href.endswith("/spectacles/"):
            continue
        if href in seen_urls:
            # BUG-23 : la page montre un spectacle deux fois — le bandeau
            # « à la une », SANS genre, puis la liste, avec (« Chanson
            # ETIENNE DAHO … »). La première carte gardée, le genre se
            # perdait, et le concert tombait dans la famille « autres ».
            # C'est aussi la seconde carte qui dit « Scolaires » (BUG-27).
            stub = stub_par_url.get(href)
            texte = _find_card(a).get_text(" ", strip=True)
            if stub is not None and _scolaire(texte):
                raw_stubs.remove(stub)
                del stub_par_url[href]
                scolaires.append(stub["title"])
            elif stub is not None and stub["category"] is None:
                stub["category"] = _categorie(texte)
            continue

        card = _find_card(a)
        text = card.get_text(" ", strip=True)
        if _scolaire(text):
            seen_urls.add(href)
            titre = card.find(["h2", "h3"])
            scolaires.append(titre.get_text(strip=True) if titre else href)
            continue

        date_starts: List[str] = []
        m_triple = DATE_TRIPLE.search(text)
        m_amp    = DATE_AMP.search(text)
        m_single = DATE_SINGLE.search(text)

        if m_triple:
            d1, d2, d3, mo, yr = m_triple.groups()
            month = _french_month_num(mo)
            year  = int(yr)
            if month:
                for d_s in (d1, d2, d3):
                    try:
                        date_starts.append(Date(year, month, int(d_s)).isoformat())
                    except ValueError:
                        pass
        elif m_amp:
            d1, d2, mo, yr = m_amp.groups()
            month = _french_month_num(mo)
            year  = int(yr)
            if month:
                for d_s in (d1, d2):
                    try:
                        date_starts.append(Date(year, month, int(d_s)).isoformat())
                    except ValueError:
                        pass
        elif m_single:
            d_s, mo, yr = m_single.groups()
            month = _french_month_num(mo)
            year  = int(yr)
            if month:
                try:
                    date_starts.append(Date(year, month, int(d_s)).isoformat())
                except ValueError:
                    pass

        if not date_starts:
            continue

        title_el = card.find(["h2", "h3"])
        title = title_el.get_text(strip=True) if title_el else a.get_text(" ", strip=True)
        if not title or len(title) < 2:
            continue

        category = _categorie(text)

        image: Optional[str] = None
        for img in card.find_all("img"):
            image = img_src(img, host=HOST)
            if image:
                break

        seen_urls.add(href)
        raw_stubs.append({
            "date_starts": date_starts,
            "title": title, "category": category,
            "url": href, "image": image,
        })
        stub_par_url[href] = raw_stubs[-1]

    if scolaires:
        print(f"[Radiant] scolaires écartés : {', '.join(scolaires)}", file=sys.stderr)

    # Cap horizon: keep only dates within ~6 months and drop stubs with no
    # remaining date BEFORE the detail-page fetch phase — the homepage lists
    # the whole season (150+ events up to 2 years out), which made the daily
    # run slow and the JSON bloated.
    horizon_iso = (Date.today() + timedelta(days=180)).isoformat()
    for stub in raw_stubs:
        stub["date_starts"] = [ds for ds in stub["date_starts"] if ds <= horizon_iso]
    raw_stubs = [s for s in raw_stubs if s["date_starts"]]

    # Pass 2: fetch each unique URL once for time and sessions (cached
    # across runs, throttled — see scrapers/detail_cache.py)
    url_to_fiche: dict = {}
    for stub in raw_stubs:
        url_to_fiche[stub["url"]] = detail_cache.get_details(
            stub["url"], _lire_fiche, fields=("time", "seances"))

    # Build events (one per session, or per date when the page lists none)
    events: List[Event] = []
    seen_ids: set = set()
    for stub in raw_stubs:
        fiche = url_to_fiche.get(stub["url"]) or {}
        time_str = fiche.get("time")
        # Les heures de chaque jour, dans la fenêtre de _parse_time (14 h à
        # 22 h) : une séance du matin reste écartée comme avant. Un jour
        # sans séance lue garde l'heure de la fiche.
        heures_du_jour = defaultdict(set)
        for jour, heure in fiche.get("seances") or []:
            if 14 <= int(heure[:2]) <= 22:
                heures_du_jour[jour].add(heure)
        for ds in stub["date_starts"]:
            for heure in sorted(heures_du_jour.get(ds) or [time_str]):
                ev = Event(
                    venue=VENUE,
                    venue_slug=SLUG,
                    title=stub["title"],
                    subtitle=None,
                    category=stub["category"],
                    date_start=ds,
                    date_end=None,
                    time=heure,
                    url=stub["url"],
                    image=stub["image"],
                )
                if ev.id not in seen_ids:
                    seen_ids.add(ev.id)
                    events.append(ev)

    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time or "  -  ", "·", e.title, "·", e.url)
