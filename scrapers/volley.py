"""Les matchs À DOMICILE du volley lyonnais de niveau national.

TROIS ÉQUIPES, DEUX GYMNASES. Aucun club de Lyon ne joue en Ligue A, en
Ligue B ni en Élite ; le plus haut niveau atteint dans la ville est la
Nationale 2, et trois équipes y tiennent le haut du pavé lyonnais :

  ASUL Lyon            Nationale 2 masculine   Gymnase Mado Bonnet (8e)
  Lyon Croix-Rousse    Nationale 2 masculine   Gymnase René Baillieu (4e)
  Lyon Croix-Rousse    Nationale 3 féminine    Gymnase René Baillieu (4e)

Onze réceptions chacune sur la saison, soit trente-trois matchs.

UNE SEULE SOURCE, ET C'EST LA FÉDÉRATION. Le site de l'ASUL ne publie pas
son calendrier — sa page « programme » n'offre qu'un fichier à
télécharger. La FFVB, elle, exporte le calendrier COMPLET d'un club en
CSV, saison entière, sur une seule requête :

  POST vbspo_calendrier_export_club.php
       cnclub=0699603&cal_saison=2026/2027&typ_edition=E&type=RES

  Entité;Jo;Match;Date;Heure;EQA_no;EQA_nom;EQB_no;EQB_nom;...;Salle;...
  ABCCS;01;2MA002;2026-09-27;15:00;0699603;ASUL LYON VOLLEY BALL;
        0696380;AS CALUIRE ET CUIRE;;;;GYMNASE MADO BONNET;...

Dates ISO, heures locales, salle par match, et le NUMÉRO du club qui
reçoit — EQA_no — qui dit le domicile sans qu'on ait à lire les noms.

PAR CLUB, PAS PAR POULE. On aurait pu lire les poules (2MA, 3FC) : elles
donnent les mêmes lignes. Mais un code de poule change chaque saison, et
une montée ou une descente le change du tout au tout ; le numéro de club,
lui, ne bouge jamais. Deux requêtes suffisent, une par club, et le
scraper survivra à une promotion en Élite comme à une descente.

L'ENTITÉ FAIT LE TRI. L'export donne TOUTES les équipes du club — 84
rencontres pour l'ASUL, dont les jeunes, la régionale et la
départementale. La colonne Entité les sépare : ABCCS est l'organisateur
des championnats de France, LIRA la ligue Auvergne-Rhône-Alpes, ADPVA le
département. On ne garde qu'ABCCS, c'est-à-dire le niveau national.

LES SALLES SONT UNE LISTE FERMÉE. Un match national délocalisé ailleurs
serait écarté avec un mot sur stderr plutôt que publié sous un nom qu'on
n'a pas vérifié — même règle que pour l'ASVEL. Les départementales de
l'ASUL tournent d'ailleurs dans huit gymnases de la ville, tous écartés
par la règle de l'entité bien avant d'arriver ici.
"""
from typing import List, Optional
from datetime import date as Date, timedelta
import csv
import io
import os
import re
import sys
import unicodedata

import requests

from .base import Event, iso

HORIZON_DAYS = 180

EXPORT = ("https://www.ffvbbeach.org/ffvbapp/resu/"
          "vbspo_calendrier_export_club.php")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# Le numéro de club à la FFVB, et le nom qu'on donne à son équipe. Ces
# numéros se lisent dans les pages de poule de la fédération ; ils ne
# changent pas d'une saison à l'autre.
CLUBS = (
    ("0699603", "ASUL Lyon", "https://www.asulvolley.com/"),
    ("0693493", "Lyon Croix-Rousse", "https://www.lyoncroixroussevolley.fr/"),
)

# L'organisateur des championnats de France. Tout le reste de l'export
# est régional, départemental ou jeune : hors sujet pour un agenda.
ENTITE_NATIONALE = "ABCCS"

# Les gymnases où ces trois équipes reçoivent. Liste FERMÉE, clés sans
# accents ni casse : la FFVB écrit tantôt « GYMNASE MADO BONNET »,
# tantôt « MADO BONNET ».
SALLES = {
    "gymnase mado bonnet": "Gymnase Mado Bonnet",
    "mado bonnet": "Gymnase Mado Bonnet",
    "gymnase rene baillieu": "Gymnase René Baillieu",
    "rene baillieu": "Gymnase René Baillieu",
}
SLUGS = {
    "Gymnase Mado Bonnet": "gymnase-mado-bonnet",
    "Gymnase René Baillieu": "gymnase-rene-baillieu",
}

# Le code du match porte sa division : 2MA = Nationale 2 Masculine poule
# A, 3FC = Nationale 3 Féminine poule C. Les deux premiers caractères
# suffisent ; la lettre de poule change tous les ans et ne nous dit rien.
DIVISIONS = {
    "2M": "Nationale 2 masculine",
    "2F": "Nationale 2 féminine",
    "3M": "Nationale 3 masculine",
    "3F": "Nationale 3 féminine",
}
DIVISION_INCONNUE = "Championnat de France"

# Le mois où la saison bascule : une saison de volley court de septembre
# à avril, et « 2026/2027 » la désigne dès l'été 2026.
MOIS_BASCULE = 8

# Les sigles que la fédération écrit en capitales et qu'un simple
# title() abîmerait : « AS » deviendrait « As ».
SIGLES = frozenset(
    "AS US USC UC VB VBC VC CA CS AC SC SA JS ES EC CFC MJC ASPTT PUC "
    "ACBB TOAC UGS ASUL SCO FC OL".split())
PARTICULES = frozenset("de du des la le les et en sur aux au d l".split())

# La fédération abrège « Association » en ASS, ce qu'aucune carte ne peut
# afficher tel quel. On développe plutôt que de laisser le sigle.
DEVELOPPEMENTS = {"ASS": "Association"}

# Ce qui, dans un mot, doit reprendre une capitale : Croix-Rousse,
# Saint-Étienne, (Le) Grès. capitalize() ne voit que le premier
# caractère, et « (LE) » lui rendrait « (le) ».
_LETTRES = re.compile(r"[A-Za-zÀ-ÿ]+")


def _norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower())
                   if unicodedata.category(c) != "Mn").strip()


def _joli(nom: str) -> str:
    """« AS CALUIRE ET CUIRE » → « AS Caluire et Cuire ».

    La fédération écrit tout en capitales. On rabaisse, en gardant les
    sigles debout et les particules en minuscules — sauf en tête, où
    « Le Mans » doit rester « Le Mans ».
    """
    mots = re.split(r"(\s+)", (nom or "").strip())
    out = []
    for i, mot in enumerate(mots):
        if not mot.strip():
            out.append(mot)
            continue
        nu = mot.strip(".").upper()
        if nu in DEVELOPPEMENTS:
            out.append(DEVELOPPEMENTS[nu])
        elif nu in SIGLES or "." in mot:   # AS, U.S., V.B.
            out.append(mot.upper())
        elif mot.lower() in PARTICULES and i > 0:
            out.append(mot.lower())
        elif mot.isupper():
            out.append(_LETTRES.sub(lambda m: m.group(0).capitalize(), mot))
        else:
            out.append(mot)
    return "".join(out)


def _saison(aujourdhui: Optional[Date] = None) -> str:
    j = aujourdhui or Date.today()
    debut = j.year if j.month >= MOIS_BASCULE else j.year - 1
    return "%d/%d" % (debut, debut + 1)


def _alerte(message: str) -> None:
    """Un avertissement qui se voit jusque dans l'en-tête du run."""
    print("[VOLLEY] " + message, file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("::warning title=VOLLEY::%s" % message.replace("\n", "%0A"))


def _lignes(numero: str, saison: str) -> List[dict]:
    """L'export CSV d'un club, en dictionnaires."""
    r = requests.post(EXPORT, headers=HEADERS, timeout=30, data={
        "cnclub": numero,
        "cal_saison": saison,
        "typ_edition": "E",
        "type": "RES",
    })
    r.raise_for_status()
    # La fédération sert du latin-1, et requests lit « ISO-8859-1 » de
    # travers quand le serveur ne le dit pas : on impose.
    texte = r.content.decode("cp1252", errors="replace")
    return list(csv.DictReader(io.StringIO(texte), delimiter=";"))


def fetch() -> List[Event]:
    saison = _saison()
    today = Date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    today_iso, horizon_iso = iso(today), iso(horizon)

    events: List[Event] = []
    vus = set()
    inconnues: List[str] = []
    total_national = 0

    for numero, club, lien in CLUBS:
        try:
            lignes = _lignes(numero, saison)
        except (requests.RequestException, ValueError, UnicodeError) as exc:
            print("[VOLLEY] %s : %s" % (club, exc), file=sys.stderr)
            continue

        domicile = [l for l in lignes
                    if (l.get("Entité") or l.get("Entit�") or "").strip()
                    == ENTITE_NATIONALE
                    and (l.get("EQA_no") or "").strip() == numero]
        total_national += len(domicile)
        if not domicile:
            _alerte("%s n'a aucun match national en %s : descendu en "
                    "régionale, ou numéro de club changé — vérifier sur "
                    "ffvbbeach.org" % (club, saison))
            continue

        for l in domicile:
            code = (l.get("Match") or "").strip()
            jour = (l.get("Date") or "").strip()
            heure = (l.get("Heure") or "").strip()[:5]
            if not (code and jour and heure) or code in vus:
                continue
            if not (today_iso <= jour <= horizon_iso):
                continue

            salle = SALLES.get(_norm(l.get("Salle")))
            if not salle:
                inconnues.append((l.get("Salle") or "(vide)").strip())
                continue

            division = DIVISIONS.get(code[:2].upper())
            if not division:
                _alerte("division inconnue pour le match %s de %s : la "
                        "carte dira « %s »" % (code, club, DIVISION_INCONNUE))
                division = DIVISION_INCONNUE

            # Le club a deux équipes nationales au même gymnase : le F du
            # code de match est ce qui les distingue sur la carte.
            equipe = club + (" féminines" if code[1:2].upper() == "F" else "")
            adversaire = _joli((l.get("EQB_nom") or "").strip())
            if not adversaire:
                continue

            vus.add(code)
            events.append(Event(
                venue=salle,
                venue_slug=SLUGS[salle],
                title="%s - %s" % (equipe, adversaire),
                subtitle=division,
                category="volley",
                date_start=jour,
                date_end=None,
                time=heure,
                url=lien,
                image=None,
            ))

    if inconnues:
        print("[VOLLEY] salle inconnue, match(s) écarté(s) : %s"
              % ", ".join(sorted(set(inconnues))), file=sys.stderr)
    events.sort(key=lambda e: (e.date_start, e.time))
    print("[VOLLEY] saison %s : %d match(s) national(aux) à domicile, %d "
          "retenu(s) sous %d jours"
          % (saison, total_national, len(events), HORIZON_DAYS),
          file=sys.stderr)
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time, "·", e.venue, "·", e.title,
              "·", e.subtitle)
