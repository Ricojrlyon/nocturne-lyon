"""Scraper for Le Complexe café-théâtre (Lyon 1er, 7 rue des Capucins).

ATTENTION AU USER-AGENT. Le pare-feu du site renvoie 403 à toute chaîne
contenant « Mozilla/5.0 (compatible » - une règle classique contre les
robots qui se déguisent en navigateur. L'UA franc utilisé ici passe en
200, et `curl/8.0` aussi : le site ne refuse pas les robots, il refuse le
déguisement. Son robots.txt autorise d'ailleurs tout. Ne pas remplacer
cet UA par celui des autres scrapers, qui casserait la salle.

Le site est un WordPress. The Events Calendar y est installé mais
INUTILISÉ - tous ses endpoints REST rendent 0 (événements comme lieux) -
et il n'existe pas d'API publique côté billetterie (Slidebooker). On lit
donc le HTML, qui a l'avantage de porter des classes sémantiques stables
préfixées « tly_ », et non des hachages regénérés à chaque déploiement.

Deux étapes :
  1. /actuellement/ porte DEUX choses. Un accordéon des sept prochains
     jours, et - hors accordéon - le catalogue complet des spectacles.
     C'est le catalogue qu'on lit : chaque entrée donne l'URL, le titre,
     l'affiche et la plage de dates AVEC les années.
  2. chaque page spectacle porte la table de ses représentations :
     .tly_day (date française SANS année), .tly_hour, et la salle.

L'année est déduite de la plage du catalogue, puis roulée dès que le mois
recule d'une séance à la suivante - ou d'entrée, quand la première séance
est déjà passée de loin (voir _passee_de_loin). Le nom du JOUR DE LA
SEMAINE sert de contrôle : si la date calculée ne tombe pas ce jour-là, la
déduction est fausse et la séance est écartée plutôt que publiée de
travers. Mesuré à l'écriture : 214 séances sur 214 validées.
"""
from __future__ import annotations

import re
import sys
import time
import unicodedata
from datetime import date as Date, timedelta
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from .base import Event, get as base_get

# Même graphie que le Petit Bulletin, qui remonte aussi cette salle :
# c'est ce qui permet à la dédup de regrouper les deux sources.
VENUE = "Le Complexe café-théâtre"
SLUG = "le-complexe"
BASE = "https://www.lecomplexelyon.com"
LISTING = BASE + "/actuellement/"

# Catégorie fixe : le site n'expose pas de genre exploitable, et
# « café-théâtre » tombe dans le bucket humour du frontend, ce qui est
# juste pour cette salle.
CATEGORY = "café-théâtre"

HORIZON_DAYS = 180
MIN_INTERVAL = 0.4

# PAS de « Mozilla/5.0 (compatible » ici : voir le docstring, le pare-feu
# du site le renvoie en 403.
HEADERS = {
    "User-Agent": "nocturne-lyon-events/1.0 "
                  "(+https://github.com/Ricojrlyon/nocturne-lyon)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# LA VÉRIFICATION ANTI-ROBOT DE L'HÉBERGEUR. Le site est chez SiteGround
# - l'en-tête Host-Header de ses serveurs le signe -, dont le bouclier
# sert parfois, à la place de la page, une vérification destinée aux
# navigateurs : un code 202 et quelques lignes qui renvoient vers
# /.well-known/sgcaptcha/. Ce n'est pas une erreur HTTP -
# raise_for_status() la laisse passer -, et le collecteur la lisait comme
# un catalogue vide, en accusant à tort la structure du site. Sa forme
# exacte n'a pas pu être observée d'ici, le site ne la sert jamais en
# local : d'où la parade de _page sur le contenu attendu.
#
# Relevé sur les 43 passages du workflow du 7 au 30 septembre 2026 : 13
# échecs, jamais en local. 11 dès /actuellement/. Les 2 autres ont lu le
# catalogue, puis plus une seule séance sur la minute de pages spectacle
# qui suivait : un refus installé tient donc au moins une minute, d'où la
# première attente. Aucun lien avec la région du runner, et les refus
# viennent par vagues : 4 passages sur 4 du 8 au 10 septembre, puis 0 sur
# 15.
#
# On ne cherche PAS à franchir la vérification : c'est une protection que
# le site a voulue, et l'UA reste franc. On renonce en le disant, et le
# garde-fou d'aggregate.py reprend le programme de la veille.
#
# PLUS D'ATTENTE (SUIVI-1, 2 octobre 2026). On redemandait après 60 puis
# 120 s, sur un budget commun à tout le passage. Mesuré sur les passages
# des 1er et 2 octobre : 4 refus, et chaque fois les deux nouveaux essais
# refusés aussi - aucun sauvetage, trois minutes de passage perdues par
# refus. Un refus tient donc au moins trois minutes sur la machine qui le
# reçoit ; ce sont les passages suivants, sur d'autres machines, qui sont
# servis. Le budget est vide : _page renonce au premier refus. La
# mécanique reste, pour le jour où une attente servirait.
ATTENTES_VERIFICATION = ()

MOIS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
        "juin": 6, "juillet": 7, "aout": 8, "septembre": 9,
        "octobre": 10, "novembre": 11, "decembre": 12}
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi",
         "dimanche")

_SEANCE_RE = re.compile(
    r"(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)"
    r"\s+(\d{1,2})\s+([a-z]+)")
_PLAGE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_HEURE_RE = re.compile(r"\b(\d{1,2})[:h](\d{2})\b")
_URL_CSS_RE = re.compile(r"url\((.*?)\)")


def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)


def _image(item) -> Optional[str]:
    """L'affiche est une image de fond CSS, pas une balise <img>."""
    div = item.select_one(".tly_featuredImg")
    if div is None:
        return None
    m = _URL_CSS_RE.search(div.get("style") or "")
    if not m:
        return None
    url = m.group(1).strip("'\" ")
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return BASE + url
    return url if url.startswith("http") else None


class VerificationAntiRobot(RuntimeError):
    """L'hébergeur a servi sa vérification anti-robot au lieu de la page."""


def _verification(r: requests.Response) -> bool:
    """La réponse est-elle la vérification de SiteGround, et non la page ?

    Trois signes, dont aucun ne paraît sur les 75 vraies pages relevées à
    l'écriture - toutes en 200, de 240 à 580 Ko : le code 202, l'en-tête
    sg-captcha, le chemin sgcaptcha dans le corps.
    """
    return (r.status_code == 202 or "sg-captcha" in r.headers
            or b"sgcaptcha" in r.content)


def _page(session: requests.Session, url: str, attentes: list,
          attendu: Optional[str] = None) -> requests.Response:
    """GET qui reconnaît la vérification anti-robot et, si le budget le
    permet, redemande plus tard.

    Chaque nouvel essai consomme une attente du budget `attentes`, partagé
    par tout le passage - vide depuis SUIVI-1 (voir ATTENTES_VERIFICATION) :
    pas de nouvel essai. Une page privée de `attendu`, quand on le donne,
    est redemandée de même : c'est la parade si la vérification changeait
    de forme. Budget épuisé, la vérification lève VerificationAntiRobot ;
    une page seulement privée de l'attendu est rendue, et l'appelant juge.
    """
    essai = 0
    while True:
        essai += 1
        r = base_get(url, session=session, headers=HEADERS, timeout=30)
        r.raise_for_status()
        refus = _verification(r)
        if not refus and (attendu is None or attendu.encode() in r.content):
            return r
        if not attentes:
            if refus:
                raise VerificationAntiRobot(
                    f"vérification anti-robot de l'hébergeur (HTTP "
                    f"{r.status_code}) au lieu de {url}, {essai} essai(s)")
            return r
        pause = attentes.pop(0)
        quoi = ("vérification anti-robot de l'hébergeur" if refus
                else "page sans le contenu attendu")
        print(f"[Le Complexe] {url} : {quoi} (HTTP {r.status_code}, "
              f"{len(r.content)} octets), nouvel essai dans {pause} s",
              file=sys.stderr)
        time.sleep(pause)


def _catalogue(session: requests.Session, attentes: list) -> List[dict]:
    """Spectacles du catalogue, avec leur plage de dates annotée d'années.

    On écarte les entrées de l'accordéon : ce sont les séances des sept
    prochains jours, et leur date ne porte pas d'année.
    """
    r = _page(session, LISTING, attentes, attendu="tly_productItem")
    soup = BeautifulSoup(r.text, "html.parser")

    out = []
    for item in soup.select(".tly_productItem"):
        if item.find_parent(class_="tly_accordionContent"):
            continue
        a = item.select_one("a.tly_moreLink")
        titre = item.select_one(".tly_productTitle")
        plage = item.select_one(".tly_productDate")
        if not (a and a.get("href") and titre and plage):
            continue
        m = _PLAGE_RE.search(plage.get_text(" ", strip=True))
        if not m:
            continue                     # sans année, l'inférence est aveugle
        out.append({
            "url": a["href"],
            "titre": titre.get_text(strip=True),
            "annee": int(m.group(3)),
            "image": _image(item),
        })

    # Un même spectacle figure PLUSIEURS fois au catalogue, une entrée par
    # saison - « IMPRO'MINOTS » y apparaît trois fois - mais toutes
    # pointent vers la MÊME page, dont la table contient déjà l'intégralité
    # des séances. Sans cette déduplication on récupérait la page autant de
    # fois qu'elle a d'entrées, et la passe partie de l'année la plus
    # tardive échouait en bloc : le contrôle du jour de la semaine
    # rejetait ses 20 à 23 séances, déjà captées par la bonne passe.
    # On garde donc l'année la plus ANCIENNE, celle où commence la table,
    # le roulement d'année faisant le reste.
    par_url: dict = {}
    for c in out:
        vu = par_url.get(c["url"])
        if vu is None or c["annee"] < vu["annee"]:
            par_url[c["url"]] = c
    return list(par_url.values())


def _passee_de_loin(annee: int, mois: int, jj: int) -> bool:
    """La date tombe-t-elle plus de quinze jours avant aujourd'hui ?

    La page d'un spectacle ne garde que les séances à venir. Quand il n'en
    reste qu'après le Nouvel An, l'année de début de la plage ne vaut plus
    - « du 23/09/2026 au 20/01/2027 » donnait 2026 - et aucun mois ne
    recule pour la faire rouler : le 20 janvier de Jules Robin tombait en
    2026, et le contrôle du jour l'écartait à chaque passage. Une PREMIÈRE
    séance déjà passée de loin est donc celle de l'année suivante. Les
    quinze jours de grâce (comme tng.py) gardent à son année une séance
    tout juste passée que la page n'a pas encore retirée.
    """
    try:
        return (Date.today() - Date(annee, mois, jj)).days > 15
    except ValueError:
        return False


def _seances(session: requests.Session, url: str,
             annee: int, tag: str, attentes: list) -> List[tuple]:
    """(date ISO, heure) de chaque représentation d'un spectacle.

    La sélection est cadrée sur la table des représentations : la classe
    .tly_productDate sert aussi aux plages du catalogue et pourrait
    apparaître dans d'éventuels blocs de spectacles liés.
    """
    r = _page(session, url, attentes)
    soup = BeautifulSoup(r.text, "html.parser")

    blocs = soup.select(".tly_productDatesTable .tly_productDate")
    out, precedent, ecartees = [], None, 0
    for bloc in blocs:
        jour = bloc.select_one(".tly_day")
        if jour is None:
            continue
        m = _SEANCE_RE.search(_norm(jour.get_text(" ", strip=True)))
        if not m:
            continue
        dow, jj, mois = m.group(1), int(m.group(2)), MOIS.get(m.group(3))
        if not mois:
            continue
        if precedent and (mois, jj) < precedent:
            annee += 1                   # le mois recule : année suivante
        elif precedent is None and _passee_de_loin(annee, mois, jj):
            annee += 1                   # déjà passée : année suivante
        precedent = (mois, jj)
        try:
            d = Date(annee, mois, jj)
        except ValueError:
            continue
        # Contrôle : la date calculée doit tomber le jour annoncé. Sinon
        # l'année déduite est fausse et on préfère perdre la séance que
        # publier une date erronée.
        if JOURS[d.weekday()] != dow:
            ecartees += 1
            continue
        h = bloc.select_one(".tly_hour")
        mh = _HEURE_RE.search(h.get_text(strip=True)) if h else None
        heure = f"{int(mh.group(1)):02d}:{mh.group(2)}" if mh else None
        out.append((d.isoformat(), heure))

    if ecartees:
        print(f"[{tag}] {ecartees} séance(s) écartée(s), jour de la semaine "
              f"incohérent - {url}", file=sys.stderr)
    return out


def fetch() -> List[Event]:
    today = Date.today()
    horizon = (today + timedelta(days=HORIZON_DAYS)).isoformat()
    today_iso = today.isoformat()

    session = requests.Session()
    attentes = list(ATTENTES_VERIFICATION)
    shows = _catalogue(session, attentes)
    if not shows:
        # Page lisible mais catalogue vide : la structure a changé. On le
        # signale, plutôt que de rendre une liste vide silencieuse
        # qu'aggregate.py ne distinguerait pas d'une panne.
        print("[Le Complexe] catalogue vide sur /actuellement/ - structure "
              "du site modifiée ?", file=sys.stderr)
        return []

    events: List[Event] = []
    illisibles = 0
    for i, show in enumerate(shows):
        if i:
            time.sleep(MIN_INTERVAL)
        try:
            seances = _seances(session, show["url"], show["annee"],
                               "Le Complexe", attentes)
        except requests.RequestException as exc:
            # Une page qui tombe ne doit pas emporter les autres. La
            # vérification anti-robot, elle, n'est pas attrapée ici :
            # installée, elle refuserait aussi toutes les suivantes. Elle
            # remonte, et le programme de la veille est repris EN ENTIER
            # plutôt qu'un programme troué publié comme complet.
            print(f"[Le Complexe] {show['url']}: {exc}", file=sys.stderr)
            illisibles += 1
            continue

        for jour, heure in seances:
            if jour < today_iso or jour > horizon:
                continue
            events.append(Event(
                venue=VENUE,
                venue_slug=SLUG,
                title=show["titre"],
                subtitle=None,
                category=CATEGORY,
                date_start=jour,
                date_end=None,
                time=heure,
                url=show["url"],
                image=show["image"],
            ))

    if illisibles:
        print(f"[Le Complexe] {illisibles} page(s) spectacle illisible(s)",
              file=sys.stderr)
    return events
