"""Scraper des matchs À DOMICILE du LDLC ASVEL Féminin.

Le calendrier du club tient toute la saison sur une page, rendue côté
serveur, et chaque rencontre y tient sur une ligne de la même forme :

  LDLC ASVEL Féminin VS Bourges  11 octobre 2026 -  Palais des Sports
  15:15  Lyon / Bourges – 4ème journée La Boulangère Wonderligue

  Angers VS LDLC ASVEL Féminin  17 octobre 2026  20:00
  Angers / Lyon – 5ème journée La Boulangère Wonderligue

DEUX MARQUEURS DU DOMICILE, ET ON EXIGE LES DEUX. Le club reçoit quand il
est écrit à GAUCHE du « VS », et la page ne nomme alors la SALLE que dans
ce cas — un déplacement n'en porte aucune. On vérifie donc les deux :
faute de quoi une refonte qui inverserait l'ordre des équipes publierait
onze déplacements comme des matchs lyonnais.

UNE SEULE SALLE, mais pas celle des hommes : le Palais des Sports de
Gerland, 350 avenue Jean Jaurès, Lyon 7e — et non l'Astroballe. La page
l'abrège en « Palais des Sports », qui ne désigne rien tout seul : chaque
ville en a un. On le réécrit donc en toutes lettres.

PAS DE COUPE D'EUROPE cette saison dans cette page : les 22 rencontres
annoncées sont toutes de La Boulangère Wonderligue, 11 à domicile et 11
au dehors. Si une coupe s'y ajoutait, elle passerait par le même chemin —
le sous-titre porte le nom de la compétition, lu sur la ligne.

Le site est derrière Cloudflare, comme celui des hommes qui, lui, rend
403 au runner GitHub. Rien ne dit que la configuration soit la même ici,
et seul un run le dira.
"""
from typing import List, Optional
from datetime import date as Date, timedelta
import re
import sys
import unicodedata

import requests
from bs4 import BeautifulSoup

from .base import Event, iso, FR_MONTHS, get as base_get

CLUB = "LDLC ASVEL Féminin"
URL = "https://ldlcasvelfeminin.com/calendrier/"
HORIZON_DAYS = 180

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# La salle, telle que la page l'abrège et telle qu'on la publie. Liste
# FERMÉE : un match délocalisé ailleurs est écarté avec un mot sur stderr
# plutôt que publié sous un nom qu'on n'a pas vérifié.
SALLES = {"palais des sports": "Palais des Sports de Gerland"}
SLUGS = {"Palais des Sports de Gerland": "palais-des-sports-gerland"}

# Les compétitions qu'on sait nommer. Le reste de la queue de ligne est du
# bruit — numéro de journée, et sur le bloc « prochain match » un compte à
# rebours en toutes lettres.
COMPETITIONS = (
    ("wonderligue", "La Boulangère Wonderligue"),
    ("euroleague", "EuroLeague Women"),
    ("eurocup", "EuroCup Women"),
    ("coupe de france", "Coupe de France"),
    ("open lfb", "Open LFB"),
)

# « X VS Y  11 octobre 2026 [-  Salle] 15:15  <queue> »
LIGNE = re.compile(
    r"^(.+?)\s+VS\s+(.+?)\s+"
    r"(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})\s*"
    r"(?:-\s*(.*?))?\s*"
    r"(\d{1,2}):(\d{2})\s*"
    r"(.*)$")


def _norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower())
                   if unicodedata.category(c) != "Mn").strip()


def _competition(queue: str) -> Optional[str]:
    n = _norm(queue)
    for marqueur, nom in COMPETITIONS:
        if marqueur in n:
            return nom
    return None


def _lignes(soup: BeautifulSoup) -> List[str]:
    """Le texte de chaque ligne de match, sans doublon de balisage.

    On retient le plus PETIT élément dont le texte a la forme voulue : la
    page imbrique ses conteneurs, et un ancêtre colle aussi bien que son
    enfant. C'est la donnée qui sert de repère, pas les classes — elles
    sont celles d'un thème WordPress et changeront sans prévenir.
    """
    out, vus = [], []
    for el in soup.find_all(["div", "section", "article", "li"]):
        t = el.get_text(" ", strip=True)
        if len(t) > 260 or " VS " not in t:
            continue
        if not LIGNE.match(t):
            continue
        if any(LIGNE.match(d.get_text(" ", strip=True))
               for d in el.find_all(["div", "section", "article", "li"])):
            continue
        if t in vus:
            continue
        vus.append(t)
        out.append(t)
    return out


def fetch() -> List[Event]:
    try:
        r = base_get(URL, timeout=30, headers=HEADERS, etiquette="[ASVEL F]")
        r.raise_for_status()
    except requests.RequestException as exc:
        print("[ASVEL F] %s : %s" % (URL, exc), file=sys.stderr)
        return []

    lignes = _lignes(BeautifulSoup(r.text, "html.parser"))
    if not lignes:
        print("[ASVEL F] aucune ligne de match reconnue sur %s — la page a "
              "probablement changé de forme" % URL, file=sys.stderr)
        return []

    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    exterieur = 0
    inconnues: List[str] = []
    retenus: dict = {}

    for texte in lignes:
        m = LIGNE.match(texte)
        gauche, droite, jour, mot_mois, annee, salle, hh, mm, queue = m.groups()

        # Le club reçoit : il est à gauche ET la ligne nomme une salle.
        if not _norm(gauche).startswith(_norm(CLUB)) or not (salle or "").strip():
            exterieur += 1
            continue

        nom_salle = SALLES.get(_norm(salle))
        if not nom_salle:
            inconnues.append(salle.strip())
            continue

        mois = FR_MONTHS.get(_norm(mot_mois))
        if not mois:
            continue
        try:
            d = Date(int(annee), mois, int(jour))
        except ValueError:
            continue
        if not (today <= d <= horizon):
            continue

        adversaire = droite.strip()
        if not adversaire:
            continue

        # Le bloc « prochain match » répète la rencontre à venir, avec un
        # compte à rebours en guise de queue de ligne. On garde donc
        # l'entrée qui NOMME sa compétition, quel que soit l'ordre.
        cle = (iso(d), adversaire)
        competition = _competition(queue)
        if cle in retenus and not competition:
            continue
        retenus[cle] = Event(
            venue=nom_salle,
            venue_slug=SLUGS[nom_salle],
            title="%s - %s" % (CLUB, adversaire),
            subtitle=competition,
            category="basket",
            date_start=iso(d),
            date_end=None,
            time="%02d:%02d" % (int(hh), int(mm)),
            url=URL,
            image=None,
        )

    events = sorted(retenus.values(), key=lambda e: (e.date_start, e.time))
    if inconnues:
        print("[ASVEL F] salle inconnue, match(s) écarté(s) : %s"
              % ", ".join(sorted(set(inconnues))), file=sys.stderr)
    print("[ASVEL F] %d ligne(s) lue(s), %d hors domicile, %d match(s) "
          "retenu(s) sous %d jours"
          % (len(lignes), exterieur, len(events), HORIZON_DAYS),
          file=sys.stderr)
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time, "·", e.venue, "·", e.title,
              "·", e.subtitle or "-")
