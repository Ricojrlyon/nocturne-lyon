"""Relevé MANUEL du calendrier Betclic ÉLITE, à lancer depuis Lyon.

CE MODULE N'EST PAS UN SCRAPER DU FIL. Il n'est jamais appelé par
aggregate.py : il sert à fabriquer asvel_betclic.json, le calendrier figé
que asvel.py publie ensuite tous les jours sans rien demander à personne.

POURQUOI UN RELEVÉ FIGÉ. ldlcasvel.com et lnb.fr rendent tous deux 403 au
runner GitHub — un blocage d'adresses de centre de données. L'EuroLeague,
elle, répond en clair : ses 19 matchs à domicile arrivent donc en direct,
et seuls les 15 de Betclic manquaient au fil. Une lecture faite à la main
depuis une adresse lyonnaise, écrite dans un fichier du dépôt, les rend.

CE QU'ON LIT, ET OÙ. La page range chaque rencontre dans un item de
grille JetEngine dont le texte, mis à plat, donne :

  dim. 11 Oct. 2026 | 19:00 | Astroballe | LDLC ASVEL | VS | Roanne

La compétition ne s'y lit pas : elle est dans le LOGO de l'item,
/uploads/2024/09/Betclic-Elite-White-logo-web.png. On la lit là, et on
écarte tout item dont le logo ne dit rien de connu — mieux vaut un match
manquant qu'un match rangé dans la mauvaise compétition.

LA SALLE, ENFIN DONNÉE. La LNB a un champ venue_name, vide sur les 242
rencontres de la saison ; asvel.py plaçait donc la Betclic à l'Astroballe
par soustraction. Le site du club, lui, nomme la salle de chaque match :
le relevé confirme les 15 à l'Astroballe, et le jour où l'un sera
délocalisé à la LDLC Arena, le relevé suivant le dira.

LE DÉSACCORD AVEC LA LNB, CONSIGNÉ. Le 2026-09-19, sur 15 matchs, six
tombent à des dates différentes selon la source — la LNB les place le
samedi à 20:00, le club le dimanche à 19:00 ou 16:30. On suit le CLUB :
c'est son propre calendrier à domicile, c'est lui qui loue la salle et
vend les billets, et c'est sa page que la carte du fil ouvre — un lecteur
qui clique doit y retrouver la date qu'on lui a annoncée. L'écart est
écrit dans le fichier, match par match, pour qu'on sache lequel des deux
a bougé la prochaine fois.

USAGE, une fois par saison ou quand des dates bougent :

    python -m scrapers.asvel_releve          # écrit asvel_betclic.json
    python -m scrapers.asvel_releve --voir   # montre tout, n'écrit rien
"""
from typing import List, Optional
from datetime import date as Date
from pathlib import Path
import json
import re
import sys

import requests
from bs4 import BeautifulSoup

from .asvel import CLUB, LIEN, HEADERS, SALLES, _saison, _lnb

FICHIER = Path(__file__).parent.parent / "asvel_betclic.json"

COMPETITION = "Betclic ÉLITE"

# Le nom du fichier du logo trahit la compétition. Liste FERMÉE : un logo
# inconnu écarte le match avec un mot, plutôt que de le ranger au hasard.
LOGOS = (
    ("betclic", "Betclic ÉLITE"),
    ("euroleague", "EuroLeague"),
    ("euroligue", "EuroLeague"),
    ("eurocup", "EuroCup"),
    ("leaders", "Leaders Cup"),
    ("coupe-de-france", "Coupe de France"),
)

# Les mois en toutes lettres et abrégés : la page écrit « 24 septembre
# 2026 » dans le bloc « prochain match » et « sam. 13 Mar. 2027 » dans la
# liste. Les abrégés de trois lettres comptent : sans « mar », cinq matchs
# de mars tombent en silence.
MOIS = {"janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
        "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
        "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
        "decembre": 12, "jan": 1, "fév": 2, "fev": 2, "mar": 3, "avr": 4,
        "juil": 7, "aoû": 8, "aou": 8, "sep": 9, "oct": 10, "nov": 11,
        "déc": 12, "dec": 12}

# « dim. 11 Oct. 2026 » — le jour de la semaine est facultatif, la page
# l'omet dans le bloc « prochain match » qui répète la rencontre à venir.
DATE = re.compile(r"^(?:[A-Za-zÀ-ÿ]{3,8}\.?\s+)?"
                  r"(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\.?\s+(\d{4})$")
HEURE = re.compile(r"^(\d{1,2}):(\d{2})$")

# Les liens de billetterie par match. Les pages de vente n'ouvrent qu'à
# l'approche de la rencontre : on prend celui qui existe le jour du
# relevé, les autres cartes renverront au calendrier du club.
BILLET = re.compile(r"billetterie|seetickets|digitick", re.I)
# ...mais pas les liens génériques de la boutique, qui ne mènent à aucun
# match en particulier et traînent dans le pied de page de chaque item.
BILLET_GENERIQUE = re.compile(r"\?site=|index-css5|/ext/billetterie5/\?")


def _mois(mot: str) -> Optional[int]:
    m = mot.lower().rstrip(".")
    return MOIS.get(m) or MOIS.get(m[:4]) or MOIS.get(m[:3])


def _rencontres(soup: BeautifulSoup) -> List[dict]:
    """Toutes les rencontres de la page, à domicile comme au dehors."""
    par_item: dict = {}
    for el in soup.select(".jet-listing-grid__item"):
        parts = [p.strip() for p in el.get_text("|", strip=True).split("|")]
        parts = [p for p in parts if p]
        if "VS" not in parts:
            continue
        md = next((DATE.match(p) for p in parts if DATE.match(p)), None)
        mh = next((HEURE.match(p) for p in parts if HEURE.match(p)), None)
        if not md or not mh:
            continue
        mois = _mois(md.group(2))
        if not mois:
            print("[relevé] mois illisible : %r" % md.group(2), file=sys.stderr)
            continue

        i = parts.index("VS")
        srcs = " ".join((img.get("src") or "").lower()
                        for img in el.find_all("img"))
        billets = [a["href"] for a in el.find_all("a", href=True)
                   if BILLET.search(a["href"])
                   and not BILLET_GENERIQUE.search(a["href"])]

        m = {
            "jour": "%04d-%02d-%02d" % (int(md.group(3)), mois,
                                        int(md.group(1))),
            "heure": "%02d:%s" % (int(mh.group(1)), mh.group(2)),
            "salle": next((s for s in SALLES.values() if s in parts), None),
            "recoit": parts[i - 1] if i else "",
            "visiteur": parts[i + 1] if i + 1 < len(parts) else "",
            "competition": next((nom for cle, nom in LOGOS if cle in srcs),
                                None),
            "billet": billets[0] if billets else None,
        }
        # Le bloc « prochain match » répète une rencontre déjà listée, sans
        # son logo : data-post-id les réunit, et on garde la plus complète.
        cle = el.get("data-post-id") or (m["jour"], m["heure"], m["visiteur"])
        garde = par_item.get(cle)
        if garde and not (m["competition"] and not garde["competition"]):
            continue
        par_item[cle] = m
    return sorted(par_item.values(), key=lambda x: (x["jour"], x["heure"]))


def releve(voir: bool = False) -> dict:
    r = requests.get(LIEN, headers=dict(HEADERS, Accept="text/html"),
                     timeout=30)
    r.raise_for_status()
    tout = _rencontres(BeautifulSoup(r.text, "html.parser"))
    if not tout:
        raise SystemExit("[relevé] aucune rencontre lue — la page a changé "
                         "de forme, il faut rouvrir %s" % LIEN)

    domicile = [m for m in tout
                if m["salle"] and m["recoit"].startswith(CLUB)]
    if voir:
        for m in domicile:
            print("  %s %s  %-11s %-14s vs %s%s"
                  % (m["jour"], m["heure"], m["salle"],
                     m["competition"] or "COMPÉTITION INCONNUE", m["visiteur"],
                     "  [billet]" if m["billet"] else ""))
    inconnues = [m for m in domicile if not m["competition"]]
    for m in inconnues:
        print("[relevé] compétition illisible, match écarté : %s %s vs %s"
              % (m["jour"], m["heure"], m["visiteur"]), file=sys.stderr)

    betclic = [m for m in domicile if m["competition"] == COMPETITION]

    # La LNB, quand on peut la joindre, pour CONSIGNER les écarts — pas
    # pour les corriger. Appariement par adversaire : chaque club ne vient
    # qu'une fois par saison à l'Astroballe.
    saison = _saison()
    ecarts = {}
    for v in _lnb(saison):
        ecarts[_souche(v["adversaire"])] = "%s %s" % (v["jour"], v["heure"])

    matchs = []
    for m in betclic:
        entree = {"jour": m["jour"], "heure": m["heure"], "salle": m["salle"],
                  "adversaire": m["visiteur"]}
        if m["billet"]:
            entree["billet"] = m["billet"]
        vu = ecarts.get(_souche(m["visiteur"]))
        if vu and vu != "%s %s" % (m["jour"], m["heure"]):
            entree["desaccord_lnb"] = vu
        matchs.append(entree)

    return {
        "_lisez_moi": "Calendrier Betclic ÉLITE à domicile, relevé à la main "
                      "sur %s : ldlcasvel.com rend 403 au runner GitHub. "
                      "Régénérer avec « python -m scrapers.asvel_releve » "
                      "depuis une connexion française, une fois par saison "
                      "ou quand des dates bougent." % LIEN,
        "saison": saison,
        "releve_le": Date.today().isoformat(),
        "source": LIEN,
        "matchs": matchs,
    }


def _souche(nom: str) -> str:
    """Le premier mot d'un nom de club, en minuscules sans ponctuation.

    La LNB écrit « Chalon/Saône » là où le club écrit « Chalon-sur-Saône »,
    et « Bourg-en-Bresse » des deux côtés. Le premier mot suffit à les
    apparier, et aucun des dix-sept adversaires ne partage le sien.
    """
    mots = re.split(r"[\s/\-.]+", (nom or "").lower())
    return mots[0] if mots else ""


if __name__ == "__main__":
    voir = "--voir" in sys.argv
    d = releve(voir=voir)
    print("[relevé] saison %d-%d : %d match(s) de %s à domicile, %d avec "
          "billetterie, %d en désaccord avec la LNB"
          % (d["saison"], d["saison"] + 1, len(d["matchs"]), COMPETITION,
             sum(1 for m in d["matchs"] if m.get("billet")),
             sum(1 for m in d["matchs"] if m.get("desaccord_lnb"))),
          file=sys.stderr)
    if voir:
        sys.exit(0)
    FICHIER.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
    print("écrit : %s" % FICHIER)
