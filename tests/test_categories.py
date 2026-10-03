"""Des concerts et des spectacles rangés dans la famille « autres » (BUG-23) :
une catégorie absente, ou que la page ne sait pas ranger. Une cause par
collecteur."""
import unittest
from datetime import date
from unittest import mock

from scrapers import comedie_odeon, croix_rousse, halle_tony_garnier, opera_lyon, radiant
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
                mock.patch.object(radiant.detail_cache, "get_details", lambda url, f, fields: {}), \
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

    def test_un_spectacle_en_famille_rejoint_la_scene(self):
        # BUG-30 : « en-famille » dit le public ; brut, il tombait dans « autres ».
        self.assertEqual(opera_lyon._category_from_url(
            opera_lyon.HOST + "/fr/programmation/saison-2026-2027/en-famille/le-roi-des-ours-1/"),
            "spectacle jeune public")

    def test_un_atelier_n_est_pas_un_concert(self):
        # BUG-30 : rangé sous /opera-underground/, comme les concerts de la série.
        saison = opera_lyon._saisons(date(2026, 10, 1))[0]
        page = ('<html><body><div><a href="/fr/programmation/saison-2026-2027/opera-underground/'
                'marelle"><span class="title_a">Atelier “La Marelle” : découverte du gamelan</span>'
                '<span class="date_a">27 oct. 2026</span></a></div></body></html>')
        site = FauxSite({saison: page})
        with mock.patch.object(opera_lyon, "Date", date_figee(date(2026, 10, 1))), \
                mock.patch("requests.request", site.request):
            self.assertEqual([s["category"] for s in opera_lyon._scrape_url(saison)], ["atelier"])


class Halle(unittest.TestCase):

    def test_le_type_de_la_carte_fait_la_categorie(self):
        # BUG-29 : tout était « concert », humoristes et Salon des vignerons
        # compris. Le type est dans la classe de la carte.
        def carte(classes, slug, titre, jour):
            return ('<a class="%s" href="%s/fr/programmation/%s"><div class="bloc-square__title">'
                    '%s</div><div class="bloc-square__date">%s</div>'
                    '<div class="bloc-square__heure">20h00</div></a>'
                    % (classes, halle_tony_garnier.HOST, slug, titre, jour))
        page = "<html><body>%s</body></html>" % "".join([
            carte("bloc-square types type_concert", "bernard", "BERNARD LAVILLIERS", "17.03.27"),
            carte("bloc-square types type_spectacle", "malik", "MALIK BENTALHA", "11.11.26"),
            carte("bloc-square types type_salon", "vins", "SALON DES VIGNERONS", "30.10.26"),
            carte("bloc-square types type_cine-concert", "rocky", "ROCKY IN CONCERT", "02.05.27"),
            carte("bloc-square types type_evenement", "tigre", "TIGRE & DRAGON", "11.10.26"),
            carte("bloc-square", "sans-type", "SANS TYPE", "12.10.26")])
        site = FauxSite({halle_tony_garnier.URLS[0]: page})
        with mock.patch("requests.request", site.request), \
                mock.patch.object(halle_tony_garnier, "Date", date_figee(date(2026, 10, 3))):
            lu = {e.url.rsplit("/", 1)[-1]: e.category for e in halle_tony_garnier.fetch()}
        self.assertEqual(lu, {"bernard": "concert", "malik": "spectacle", "vins": "salon",
                              "rocky": "ciné-concert", "tigre": None, "sans-type": "concert"})


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
