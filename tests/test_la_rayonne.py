"""Le collecteur de La Rayonne : ses dates n'ont pas d'année. Le 25
octobre 2026, une plage « sam. 24 oct. au dim. 25 oct. », lue le
lendemain de son premier jour, a donné un début en 2027 et une fin en
2026 ; le contrôle du fil a refusé de publier, et le site est resté à la
veille. Page synthétique, qui reprend la carte du vrai site."""
import unittest
from datetime import date
from unittest import mock

from scrapers import base, la_rayonne
from tests.outils import FauxSite, date_figee

PAGE = ('<html><body><div class="cco-card">'
        '<a href="/evenement/physical-tool/">PHYSICAL TOOL x LA RAYONNE : BASS MUSIC DAY CLUB</a>'
        '<p>%s</p><p>Musique</p></div></body></html>')


class DatesDeLaRayonne(unittest.TestCase):

    def lire(self, dates, jour):
        site = FauxSite({la_rayonne.URL: PAGE % dates})
        with mock.patch("requests.request", site.request), \
                mock.patch.object(base, "date", date_figee(jour)):
            return [(e.date_start, e.date_end) for e in la_rayonne.fetch()]

    def test_plage_lue_le_lendemain_de_son_premier_jour(self):
        self.assertEqual(self.lire("sam. 24 oct. au dim. 25 oct. 14h00", date(2026, 10, 25)),
                         [("2026-10-24", "2026-10-25")])

    def test_plage_a_venir(self):
        self.assertEqual(self.lire("sam. 24 oct. au dim. 25 oct. 14h00", date(2026, 10, 20)),
                         [("2026-10-24", "2026-10-25")])

    def test_plage_a_cheval_sur_l_annee(self):
        self.assertEqual(self.lire("mer. 30 déc. au ven. 1 janv. 20h00", date(2026, 12, 1)),
                         [("2026-12-30", "2027-01-01")])

    def test_date_unique(self):
        self.assertEqual(self.lire("sam. 24 oct. 20h00", date(2026, 10, 20)),
                         [("2026-10-24", None)])


if __name__ == "__main__":
    unittest.main()
