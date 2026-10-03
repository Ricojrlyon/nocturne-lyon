"""Scraper for Marché Gare (marchegare.fr/agenda).

The site is built on Drupal. Each event card on the agenda page is wrapped
in a link to /agenda/<slug>, and contains the date and a category pill.

La carte range à part ce que le robot collait en un seul titre (BUG-26) :
« Épuisé sur ce point de vente Post-Metal HYPNO5E + HIPPOTRAKTOR ». Les
mentions sont des span.badge (« Gratuit », « Hors les murs », « Annulé »…),
les genres les span du bloc « --taxo-container » (« Indie Rock »,
« Formation »…), puis viennent le titre (« --title ») et le sous-titre
(« --subtitle »). Le titre est celui de la carte, les genres font la
catégorie, les mentions utiles passent au sous-titre. Une soirée « Hors les
murs » a lieu ailleurs (« > à Bizarre! Vénissieux ») et une « Formation »
est un stage pour musiciens, pas une sortie : les deux sont écartées, comme
ailleurs dans le fil.
"""
from typing import List
import re
import sys
import unicodedata
from bs4 import BeautifulSoup

from .base import Event, img_src, parse_french_date, iso, get as base_get

VENUE = "Marché Gare"
SLUG = "marche-gare"
URL = "https://marchegare.fr/agenda"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# Les mentions qui ne passent pas au sous-titre : l'une écarte la soirée,
# l'autre reste en tête du titre, où le filtre des annulés la lit après la
# déduplication (BUG-19).
_MENTIONS_HORS_SOUS_TITRE = ("Hors les murs", "Annulé")

# Les titres que le dernier fetch() a écartés. Le Petit Bulletin annonce
# Ivanoé « au Marché Gare », quand la salle le donne à la MJC du
# Vieux-Lyon : sans cette liste, la carte écartée revenait par l'agrégateur.
_ECARTES: set = set()


def _cle(titre: str) -> str:
    t = unicodedata.normalize("NFD", (titre or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def exclu(titre: str) -> bool:
    """Un titre que ce scraper vient d'écarter (hors les murs, formation).

    Exposé pour qu'aggregate.py applique la MÊME décision aux événements
    d'agrégateur sur ce lieu (FILTRES_DE_SALLE, étape 2.4b).
    """
    return _cle(titre) in _ECARTES


def fetch() -> List[Event]:
    _ECARTES.clear()
    resp = base_get(URL, timeout=20, headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    events: List[Event] = []
    seen = set()
    hors_les_murs: List[str] = []
    formations = 0

    for a in soup.select('a[href*="/agenda/"]'):
        href = a.get("href", "")
        if not href or href.endswith("/agenda") or href.endswith("/agenda/"):
            continue
        if href.startswith("/"):
            href = "https://marchegare.fr" + href
        if href in seen:
            continue
        seen.add(href)

        text = a.get_text(" ", strip=True)
        # Card text starts with day-of-week marker, e.g. "jeudiJeu. 30. avril04 20:30"
        # Followed by category and title.
        # Find date "30. avril" pattern.
        m = re.search(r"(\d{1,2})\.?\s+(\w+)", text)
        if not m:
            continue
        d = parse_french_date(f"{m.group(1)} {m.group(2)}")
        if not d:
            continue

        # Time: "20:30" or "20h30"
        m_time = re.search(r"(\d{1,2})[h:](\d{2})", text)
        time_str = f"{m_time.group(1):0>2}:{m_time.group(2)}" if m_time else None

        mentions = [b.get_text(" ", strip=True) for b in a.select("span.badge")]
        genres = [g.get_text(" ", strip=True)
                  for g in a.select('[class*="--taxo-container"] span[data-term-name]')]
        titre_el = a.select_one('[class*="--title"]')
        sous_titre_el = a.select_one('[class*="--subtitle"]')
        sous_titre_carte = sous_titre_el.get_text(" ", strip=True) if sous_titre_el else ""
        titre_carte = titre_el.get_text(" ", strip=True) if titre_el else ""
        if "Hors les murs" in mentions or "Formation" in genres:
            if "Hors les murs" in mentions:
                hors_les_murs.append(" ".join(t for t in (titre_carte or href,
                                                          sous_titre_carte) if t))
            else:
                formations += 1
            if _cle(titre_carte):
                _ECARTES.add(_cle(titre_carte))
            continue

        subtitle = category = None
        if titre_el is not None:
            title = titre_carte
            if "Annulé" in mentions:
                title = "Annulé – " + title
            subtitle = " · ".join(
                [m for m in mentions if m not in _MENTIONS_HORS_SOUS_TITRE]
                + ([sous_titre_carte] if sous_titre_carte else [])) or None
            # Tous les genres ensemble : « Groove » seul ne se range nulle
            # part, « Groove / Jazz » dans le jazz.
            category = " / ".join(genres) or None
        else:
            # Carte sans bloc de titre (le site a changé ?) : la lecture
            # d'avant, sur tout le texte de la carte.
            # Strip the leading date/time block to recover title + category.
            title_text = text
            # Cut off everything before the time
            if m_time:
                title_text = text[m_time.end():].strip()
            # Some cards have "Complet" as a status word at the start
            title_text = re.sub(r"^(Complet|Sold out)\s*", "", title_text, flags=re.I)
            # Title is usually all caps or has the category before it
            title = title_text.strip(" -·|")

        if not title:
            continue

        # Image (lazy-load aware; skips base64 spacers)
        image = img_src(a.find("img"), host="https://marchegare.fr")
        # Le listing sert une miniature Drupal de 32px (style auto_32) ;
        # le style "large" (~480px) est accessible sans token itok —
        # vérifié en direct. Sans cette réécriture, les cartes du
        # frontend afficheraient un timbre-poste flou.
        if image and "/styles/auto_32/" in image:
            image = image.replace("/styles/auto_32/",
                                  "/styles/large/").split("?")[0]

        events.append(Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=title[:140],
            subtitle=subtitle,
            category=category,
            date_start=iso(d),
            date_end=None,
            time=time_str,
            url=href,
            image=image,
        ))

    if hors_les_murs or formations:
        print(f"[Marché Gare] écartés : {len(hors_les_murs)} hors les murs "
              f"({', '.join(hors_les_murs)}), {formations} formation(s)",
              file=sys.stderr)
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time or "  -  ", "·", e.title, "·", e.url)
