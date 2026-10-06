"""Ce que la page et les collecteurs doivent avoir en commun (audit n° 10).

Trois valeurs vivent des deux côtés, en Python et dans index.html, sans
que rien ne les relie : le marqueur « ailleurs » d'un spectacle joué dans
plusieurs salles, l'horizon de 180 jours, et la table des lieux que
aggregate.py relit dans la page pour ne pas les géocoder. Modifiée d'un
seul côté, l'une d'elles décalerait le site en silence. Ici, elle bloque
la publication.
"""
import importlib
import pkgutil
import re
import unittest

import aggregate
import scrapers
from scrapers import base
from tests.outils import RACINE

PAGE = (RACINE / "index.html").read_text(encoding="utf-8")


def constante_de_la_page(nom: str) -> str:
    m = re.search(r"^const %s = (.+?);" % nom, PAGE, re.M)
    if not m:
        raise AssertionError("%s introuvable dans index.html" % nom)
    return m.group(1).strip()


class Coherence(unittest.TestCase):

    def test_le_marqueur_ailleurs_est_le_meme_dans_la_page(self):
        self.assertEqual(constante_de_la_page("OFFSITE_PLUSIEURS"),
                         "'%s'" % base.OFFSITE_PLUSIEURS)

    def test_l_horizon_de_la_page_est_celui_des_collecteurs(self):
        self.assertEqual(constante_de_la_page("HORIZON_JOURS"), str(base.HORIZON_JOURS))

    def test_chaque_collecteur_prend_l_horizon_commun(self):
        # La valeur se lit dans base.HORIZON_JOURS, jamais en dur : le jour
        # où l'horizon change, il change partout.
        dossier = RACINE / "scrapers"
        en_dur = [f.relative_to(RACINE).as_posix() for f in sorted(dossier.rglob("*.py"))
                  if re.search(r"days\s*=\s*180\b|HORIZON_DAYS\s*=\s*\d",
                               f.read_text(encoding="utf-8"))]
        self.assertEqual(en_dur, [])
        for info in pkgutil.walk_packages(scrapers.__path__, "scrapers."):
            module = importlib.import_module(info.name)
            if hasattr(module, "HORIZON_DAYS"):
                self.assertEqual(module.HORIZON_DAYS, base.HORIZON_JOURS, info.name)

    def test_le_robot_lit_toute_la_table_des_lieux_de_la_page(self):
        # Les autres tests remplacent cette lecture par un ensemble vide :
        # ici, elle lit le vrai index.html. Chaque ligne d'entrée de
        # VENUE_ARRONDISSEMENT doit être lue ; une entrée écrite autrement
        # serait sinon géocodée chaque nuit pour rien, sans un mot.
        # Les entrées sont comptées une à une, commentaires ôtés : deux
        # lieux écrits sur une même ligne n'en feraient pas un seul.
        bloc = PAGE.split("const VENUE_ARRONDISSEMENT = {", 1)[1].split("\n};", 1)[0]
        entrees = [cle for l in bloc.split("\n")
                   for _, cle in re.findall(r"""(['"])((?:(?!\1).)+)\1\s*:\s*['"]""",
                                            l.split("//", 1)[0])]
        lus = aggregate.frontend_hardcoded_venues()
        self.assertGreater(len(entrees), 100)
        self.assertEqual(sorted(lus), sorted(set(entrees)))


if __name__ == "__main__":
    unittest.main()
