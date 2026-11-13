"""Le collecteur d'Agend'arts : l'année d'une séance (audit A-38). Les
catégories du blog sont des mois (« décembre 2026 ») ; seule leur année
était gardée, si bien qu'un billet classé en décembre 2026 et en janvier
2027 datait sa séance de janvier en 2026, dans le passé."""
import unittest

from scrapers import agendarts


class AnneeDeLaSeance(unittest.TestCase):

    CATS = {(12, 2026), (1, 2027)}

    def test_la_categorie_du_mois_de_la_seance_donne_l_annee(self):
        self.assertEqual(agendarts._annee(9, 1, "", self.CATS, None, None, 2026), 2027)
        self.assertEqual(agendarts._annee(9, 12, "", self.CATS, None, None, 2026), 2026)

    def test_sans_categorie_du_meme_mois_l_annee_des_categories(self):
        self.assertEqual(agendarts._annee(9, 3, "", {(12, 2026)}, None, None, 2026), 2026)

    def test_la_billetterie_reste_prioritaire(self):
        self.assertEqual(agendarts._annee(9, 1, "", self.CATS, 2028, None, 2026), 2028)


if __name__ == "__main__":
    unittest.main()
