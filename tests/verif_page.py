"""La page avec le fil FRAIS, après la collecte et avant la publication.

Pas de référence ici — les données changent chaque jour — mais ce qui doit
tenir quelles qu'elles soient : la page se charge sans erreur, affiche des
cartes, et ses compteurs disent le nombre d'événements du fil.

  python -m unittest tests.verif_page -v
"""
import json
import unittest

from tests import navigateur
from tests.outils import RACINE


class PageDuJour(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fil = json.loads((RACINE / "events.json").read_text(encoding="utf-8"))
        lieux = RACINE / "venue_arrondissements.json"
        cls.releve, cls.probleme = navigateur.releve_ou_probleme(
            evenements=cls.fil,
            lieux=json.loads(lieux.read_text(encoding="utf-8")) if lieux.exists() else None,
            date_figee=False, scenarios="tout")

    def setUp(self):
        navigateur.sauter_si_indisponible(self, self.probleme)

    def test_la_page_se_charge_sans_erreur(self):
        self.assertNotIn("echec", self.releve, self.releve.get("echec"))
        self.assertEqual(self.releve["erreurs_fin"], [])

    def test_des_cartes_s_affichent(self):
        jours = self.releve["scenarios"]["tout"]["jours"]
        self.assertIsNone(self.releve["scenarios"]["tout"]["vide"])
        self.assertGreater(len(jours), 0)
        self.assertGreater(sum(len(j[2]) for j in jours), 0)

    def test_les_compteurs_disent_le_fil(self):
        n = len(self.fil["events"])
        self.assertEqual(self.releve["evenements"], n)
        self.assertEqual(self.releve["index"], str(n))
        self.assertEqual(len(self.releve["pastilles"]), 14)


if __name__ == "__main__":
    unittest.main()
