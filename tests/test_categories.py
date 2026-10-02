"""Des concerts et des spectacles rangés dans la famille « autres » (BUG-23) :
une catégorie absente, ou que la page ne sait pas ranger. Une cause par
collecteur."""
import unittest
from datetime import date
from unittest import mock

from scrapers import comedie_odeon, croix_rousse, opera_lyon, radiant
from tests.outils import FauxSite, date_figee


class Radiant(unittest.TestCase):

    def test_le_genre_de_la_seconde_carte_d_un_spectacle(self):
        # La page d'accueil montre un spectacle deux fois : le bandeau « à
        # la une », sans genre, puis la liste, avec. Et « Cirque » manquait.
        page = ('<html><body>'
                '<div class="une"><a href="/spectacles/test-daho/"><h3>ETIENNE DAHO</h3>'
                ' lundi 04 janvier 2027</a></div>'
                '<div class="carte"><span>Chanson</span><a href="/spectacles/test-daho/">'
                '<h3>ETIENNE DAHO</h3></a> lundi 04 janvier 2027 Billetterie</div>'
                '<div class="carte"><span>Cirque</span><a href="/spectacles/test-genesis/">'
                '<h3>The Genesis</h3></a> vendredi 18 décembre 2026 Billetterie</div>'
                '</body></html>')
        site = FauxSite({radiant.URL: page})
        with mock.patch("requests.request", site.request), \
                mock.patch.object(radiant.detail_cache, "get_time", lambda url, f: None), \
                mock.patch.object(radiant, "Date", date_figee(date(2026, 10, 1))):
            lu = {(e.title, e.date_start, e.category) for e in radiant.fetch()}
        self.assertEqual(lu, {("ETIENNE DAHO", "2027-01-04", "chanson"),
                              ("The Genesis", "2026-12-18", "cirque")})


class ComedieOdeon(unittest.TestCase):

    def test_un_spectacle_seulement_jeune_public(self):
        self.assertEqual(comedie_odeon._genre(" coursVenir jeune-public 02"),
                         "spectacle jeune public")

    def test_le_genre_l_emporte_sur_le_public(self):
        self.assertEqual(comedie_odeon._genre(" coursVenir jeune-public comedie 10"), "théâtre")

    def test_sans_genre_ni_public(self):
        self.assertIsNone(comedie_odeon._genre(" coursVenir parcours 01"))


class Opera(unittest.TestCase):

    def test_opera_underground_est_une_serie_de_concerts(self):
        self.assertEqual(opera_lyon._category_from_url(
            opera_lyon.HOST + "/fr/programmation/saison-2026-2027/opera-underground/karma-bazar/"),
            "concert")


class CroixRousse(unittest.TestCase):
    NOMS = {1: "bruitages", 2: "pour les enfants", 3: "théâtre", 4: "heroic fantasy",
            5: "pour les ados", 6: "radio", 7: "cabaret", 8: "revue"}

    def test_le_genre_l_emporte_sur_la_precision(self):
        for ids in ([1, 2, 3], [4, 5, 3], [5, 6, 3]):
            self.assertEqual(croix_rousse._categorie(ids, self.NOMS), "théâtre", ids)
        self.assertEqual(croix_rousse._categorie([7, 8], self.NOMS), "cabaret")

    def test_une_precision_seule_reste_la_categorie(self):
        self.assertEqual(croix_rousse._categorie([6], self.NOMS), "radio")
        self.assertIsNone(croix_rousse._categorie([2], self.NOMS))


if __name__ == "__main__":
    unittest.main()
