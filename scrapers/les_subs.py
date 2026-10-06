"""Scraper for Les Subsistances (les-subs.com/agenda).

Structure re-verified July 2026 after a full site redesign (Tailwind).
Each event is a card <div class="js-events-month-item ..."> containing:

    <a href="https://www.les-subs.com/evenement/<slug>/" ...>
    <p class="uppercase text-11 ..."><span>Performance</span><span>Théâtre</span></p>
    <p class="... font-bold ...">La Parabole du seum</p>       (titre)
    <p class="font-medium uppercase text-14 ...">Rébecca Chaillon</p>  (artiste)
    <ul>
      <li ...> ven. 17 Juil <span>|</span> <span>20:00</span> </li>   (une par date)
    </ul>

Dates en mois ABRÉGÉS capitalisés (Juil, Sep, Déc, Jan, Fév…) sans
année — la saison court sur l'année suivante, d'où l'inférence d'année
avec grâce de 15 jours. L'ancienne version (regex sur HTML brut, mois
complets, titres reconstruits depuis les slugs) est remplacée par un
parcours BeautifulSoup des cartes : titres réels, artiste en subtitle,
catégories et image en prime.
"""
from typing import List, Optional
from datetime import date as Date
import re
import sys
import unicodedata
from bs4 import BeautifulSoup

from .base import Event, iso, img_src, get as base_get

VENUE = "Les Subsistances"
SLUG = "les-subs"
URL = "https://www.les-subs.com/agenda/"
HOST = "https://www.les-subs.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nocturne-lyon-events/1.0; "
                  "+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# "ven. 17 Juil | 20:00" — jour numérique puis token mois (abrégé ou complet)
DATE_LI_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-zÀ-ÿ]{3,10})")
TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")

# Formes complètes ET abrégées telles qu'affichées par le site
# (comparées après minuscules + accents strippés + point final retiré).
_MONTHS = {
    "jan": 1, "janv": 1, "janvier": 1,
    "fev": 2, "fevr": 2, "fevrier": 2,
    "mar": 3, "mars": 3,
    "avr": 4, "avril": 4,
    "mai": 5,
    "juin": 6,
    "juil": 7, "juillet": 7,
    "aou": 8, "aout": 8,
    "sep": 9, "sept": 9, "septembre": 9,
    "oct": 10, "octobre": 10,
    "nov": 11, "novembre": 11,
    "dec": 12, "decembre": 12,
}


def _month_num(token: str) -> Optional[int]:
    t = "".join(c for c in unicodedata.normalize("NFD", token.lower())
                if unicodedata.category(c) != "Mn").rstrip(".")
    return _MONTHS.get(t)


def _smart_year(month: int, day: int) -> int:
    today = Date.today()
    try:
        candidate = Date(today.year, month, day)
    except ValueError:
        return today.year
    # Grâce de 15 jours (comme tng.py) : une date passée de quelques jours
    # est un listing pas encore purgé de CETTE année, pas l'annonce de
    # l'année prochaine. La saison des Subs court jusqu'en avril suivant :
    # « 14 Jan » vu en juillet doit bien donner l'année +1.
    return today.year + 1 if (today - candidate).days > 15 else today.year


# Largeur visée pour une affiche : les cartes font 400 px de large au plus,
# 800 couvre donc les écrans denses.
LARGEUR_AFFICHE = 800


def _taille_proche(img) -> Optional[str]:
    """Quand la carte montre l'ORIGINAL de son affiche (son src est
    l'entrée la plus large du srcset), la taille du srcset la plus
    proche de LARGEUR_AFFICHE. L'original pesait jusqu'à 8,7 Mo pour une
    carte de 400 px. La plupart des cartes montrent déjà une vignette de
    200 px, absente du srcset : None, on n'y touche pas. None aussi sans
    srcset."""
    def absolue(u: str) -> str:
        return HOST + u if u.startswith("/") and not u.startswith("//") else u

    choix = []
    for morceau in re.split(r",\s+", (img.get("srcset") or "").strip()):
        m = re.fullmatch(r"(\S+)\s+(\d+)w", morceau.strip())
        if m and absolue(m.group(1)).startswith("http"):
            choix.append((int(m.group(2)), absolue(m.group(1))))
    if not choix or max(choix)[1] != absolue((img.get("src") or "").strip()):
        return None
    return min(choix, key=lambda c: abs(c[0] - LARGEUR_AFFICHE))[1]


def _slug_to_title(url: str) -> str:
    """Fallback si la carte n'a pas de titre : 'some-slug' -> 'Some Slug'."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    words = slug.replace("-", " ").split()
    return " ".join(w[:1].upper() + w[1:].lower() if w else w for w in words)


def fetch() -> List[Event]:
    resp = base_get(URL, timeout=25, headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    events: List[Event] = []
    seen: set = set()
    cards = soup.select("div.js-events-month-item")

    for card in cards:
        a = card.select_one('a[href*="/evenement/"]')
        if a is None:
            continue
        href = a.get("href", "")
        if href.startswith("/"):
            href = HOST + href
        if not href.startswith("http"):
            continue

        title_el = card.select_one("p.font-bold")
        title = (title_el.get_text(" ", strip=True) if title_el
                 else _slug_to_title(href))
        if not title or len(title) < 2 or len(title) > 250:
            continue

        subtitle: Optional[str] = None
        sub_el = card.select_one("p.font-medium")
        if sub_el:
            cand = sub_el.get_text(" ", strip=True)
            if cand and cand != title and len(cand) < 200:
                subtitle = cand

        category: Optional[str] = None
        cat_el = card.select_one("p.text-11")
        if cat_el:
            cats = [s.get_text(strip=True) for s in cat_el.find_all("span")]
            if not cats:
                cats = [cat_el.get_text(" ", strip=True)]
            category = " · ".join(c for c in cats if c).lower() or None

        img = card.find("img")
        image = (_taille_proche(img) if img else None) or img_src(img, host=HOST)

        # Une <li> par date : "ven. 17 Juil | 20:00"
        for li in card.select("ul li"):
            text = li.get_text(" ", strip=True)
            m = DATE_LI_RE.search(text)
            if not m:
                continue
            month = _month_num(m.group(2))
            if not month:
                continue
            day = int(m.group(1))
            try:
                d = Date(_smart_year(month, day), month, day)
            except ValueError:
                continue

            time_str: Optional[str] = None
            mt = TIME_RE.search(text)
            if mt:
                hh, mm = int(mt.group(1)), int(mt.group(2))
                if 0 <= hh <= 23 and 0 <= mm <= 59:
                    time_str = f"{hh:02d}:{mm:02d}"

            key = (href, d.isoformat(), time_str)
            if key in seen:
                continue
            seen.add(key)

            events.append(Event(
                venue=VENUE,
                venue_slug=SLUG,
                title=title,
                subtitle=subtitle,
                category=category,
                date_start=iso(d),
                date_end=None,
                time=time_str,
                url=href,
                image=image,
            ))

    if not events:
        print("=" * 60, file=sys.stderr)
        print("DIAGNOSTIC: Les Subs — 0 events", file=sys.stderr)
        print(f"  cartes js-events-month-item: {len(cards)}", file=sys.stderr)
        lis = soup.select("div.js-events-month-item ul li")
        print(f"  li de dates: {len(lis)}", file=sys.stderr)
        for li in lis[:4]:
            print(f"    - {li.get_text(' ', strip=True)[:60]!r}", file=sys.stderr)
        ev_links = soup.select('a[href*="/evenement/"]')
        print(f"  liens /evenement/: {len(ev_links)}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    events.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time or "  -  ", "·", e.title, "—",
              e.subtitle or "", "·", e.category or "", "·", e.url)
