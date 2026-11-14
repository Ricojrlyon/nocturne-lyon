"""La Bourse du Travail : plus de catégorie « humour » d'office (audit
A-20). Elle rangeait en humour 98 événements sur 105, Gauvain Sers et
Medine compris. Le collecteur ne dit plus rien quand le titre ne dit rien ;
la fusion avec le Petit Bulletin, puis la déduction par le titre,
décident, et « humour » ne vient qu'en dernier recours, comme défaut du
lieu. Page synthétique, qui reprend la carte du vrai site."""
import unittest
from datetime import date
from unittest import mock

from scrapers import bourse_du_travail as bourse
from scrapers.categorie import deduire
from tests.outils import FauxSite, date_figee


def carte(i, titre):
    return ('<article class="event-card"><div class="event-card__body"><time>12/12/2026</time>'
            '<h3><a href="%s/spectacle-%d.html">%s</a></h3></div></article>'
            % (bourse.HOST, 1000 + i, titre))


class Categorie(unittest.TestCase):

    def lire(self, titres):
        page = "<html><body>%s</body></html>" % "".join(carte(i, t) for i, t in enumerate(titres))
        site = FauxSite({bourse.URL: page})
        with mock.patch("requests.request", site.request), \
                mock.patch.object(bourse, "Date", date_figee(date(2026, 11, 14))):
            return {e.title: e.category for e in bourse.fetch()}

    def test_le_collecteur_ne_met_plus_humour_d_office(self):
        self.assertEqual(self.lire(["Gauvain Sers", "Le Lac des cygnes - Ballet"]),
                         {"Gauvain Sers": None, "Le Lac des cygnes - Ballet": "danse"})

    def test_humour_reste_le_dernier_recours_du_lieu(self):
        self.assertEqual(deduire("Fabrice Eboué", "Bourse du Travail"), "humour")
        # Le titre passe avant le lieu ; un indice contraire, aussi.
        self.assertEqual(deduire("Concert de Noël", "Bourse du Travail"), "musique")
        self.assertIsNone(deduire("Yoga du rire", "Bourse du Travail"))
        # Ailleurs, rien.
        self.assertIsNone(deduire("Fabrice Eboué", "Radiant-Bellevue"))


if __name__ == "__main__":
    unittest.main()
