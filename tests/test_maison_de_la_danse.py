"""La lecture des séances d'une fiche de la Maison de la Danse (BUG-22).
Fiches synthétiques, qui reprennent la structure du vrai site : un bloc
« Lieu », puis un bloc par période, titré d'un ou de deux mois
(« SEPTEMBRE - NOVEMBRE »), une ligne par séance (« Lundi 5 », « 18:30 »)."""
import unittest
from unittest import mock

from scrapers import maison_de_la_danse as mdd
from tests.outils import FauxSite

FICHE = mdd.BASE + "/programmation/saison2026-2027/%s"


def page(titre, seances):
    """seances : [(« Lundi 5 », « 18:30 »)]."""
    lignes = "".join('<div class="table"><div class="cell">%s</div><div class="cell">%s</div></div>'
                     % s for s in seances)
    return ('<html><body><div class="spectacle-dates"><div class="table-title">Lieu</div>'
            '<div>Maison de la Danse - Grande salle</div></div>'
            '<div class="spectacle-dates"><div class="table-title">%s</div>%s</div></body></html>'
            % (titre, lignes))


class SeancesDeLaFiche(unittest.TestCase):

    def lire(self, titre, seances, url=FICHE % "spectacle"):
        """[(jour, quantième, mois)] lus par _lire_fiche."""
        site = FauxSite({url: page(titre, seances)})
        with mock.patch.object(mdd.time, "sleep", lambda s: None), \
                mock.patch("requests.request", site.request):
            return [(j, q, m) for j, q, m, _ in mdd._lire_fiche(url)["seances"]]

    def test_une_periode_de_trois_mois(self):
        # Les Ateliers singuliers : les lundis 5 et 12 octobre étaient lus
        # en novembre, puis écartés faute d'être des lundis.
        lu = self.lire("SEPTEMBRE - NOVEMBRE", [("Lundi 28", "18:30"), ("Lundi 5", "18:30"),
                                                 ("Lundi 12", "18:30"), ("Lundi 2", "18:30"),
                                                 ("Lundi 9", "18:30")])
        self.assertEqual(lu, [("lundi", 28, 9), ("lundi", 5, 10), ("lundi", 12, 10),
                              ("lundi", 2, 11), ("lundi", 9, 11)])

    def test_un_mois_saute(self):
        # Aucune séance en novembre : le samedi 5 est en décembre.
        lu = self.lire("OCTOBRE - DÉCEMBRE", [("Vendredi 30", "20:00"), ("Samedi 5", "20:00")])
        self.assertEqual(lu, [("vendredi", 30, 10), ("samedi", 5, 12)])

    def test_le_meme_quantieme_sous_un_autre_jour(self):
        # « SACRE », première version de sa fiche : mercredi 28 octobre puis
        # samedi 28 novembre.
        lu = self.lire("OCTOBRE - NOVEMBRE", [("Mercredi 28", "20:30"), ("Samedi 28", "20:30")])
        self.assertEqual(lu, [("mercredi", 28, 10), ("samedi", 28, 11)])

    def test_deux_seances_le_meme_jour(self):
        lu = self.lire("NOVEMBRE", [("Samedi 7", "15:00"), ("Samedi 7", "20:00")])
        self.assertEqual(lu, [("samedi", 7, 11), ("samedi", 7, 11)])

    def test_sans_saison_dans_l_adresse_la_periode_se_deplie_quand_meme(self):
        lu = self.lire("SEPTEMBRE - NOVEMBRE", [("Lundi 28", "18:30"), ("Lundi 5", "18:30"),
                                                 ("Lundi 2", "18:30")],
                       url=mdd.BASE + "/programmation/ateliers")
        self.assertEqual(lu, [("lundi", 28, 9), ("lundi", 5, 10), ("lundi", 2, 11)])


if __name__ == "__main__":
    unittest.main()
