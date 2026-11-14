"""Le Sucre : un titre générique ne dit pas qui joue (audit A-41). 17
cartes sur 31 s'intitulaient « Concert », « club » ou « S.society » (le nom
d'une série), quand les artistes sont dans le sous-titre. Page
synthétique, qui reprend la carte du vrai site."""
import unittest
from datetime import date
from unittest import mock

from scrapers import detail_cache, le_sucre
from tests.outils import FauxSite, date_figee


def carte(slug, genre, titre, artistes):
    return ('<a href="%s/events/%s/"><span>sam. 12 décembre 2026 %s</span><h2>%s</h2>%s</a>'
            % (le_sucre.HOST, slug, genre, titre,
               "".join("<h3>%s</h3>" % a for a in artistes)))


class TitresGeneriques(unittest.TestCase):

    def lire(self, cartes):
        site = FauxSite({le_sucre.URL: "<html><body>%s</body></html>" % "".join(cartes)})
        with mock.patch("requests.request", site.request), \
                mock.patch.object(le_sucre, "date", date_figee(date(2026, 11, 14))), \
                mock.patch.object(detail_cache, "get_time", lambda url, f: "23:00"):
            return [(e.title, e.subtitle) for e in le_sucre.fetch()]

    def test_les_artistes_passent_en_titre(self):
        self.assertEqual(self.lire([
            carte("a", "Concert", "Concert", ["Ajna"]),
            carte("b", "Club", "club", ["Laurent Garnier", "Novopol"]),
            carte("c", "Club", "S.society", ["Mac Declos (all day long)"])]),
            [("Ajna", "Concert"), ("Laurent Garnier · Novopol", "club"),
             ("Mac Declos (all day long)", "S.society")])

    def test_un_vrai_titre_reste(self):
        self.assertEqual(self.lire([carte("d", "Club", "A week-end with Anetha", ["Anetha"])]),
                         [("A week-end with Anetha", "Anetha")])

    def test_personne_encore_annonce(self):
        self.assertEqual(self.lire([carte("e", "Club", "club", ["Line-up soon !"])]),
                         [("club", "Line-up soon !")])


if __name__ == "__main__":
    unittest.main()
