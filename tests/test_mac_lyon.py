"""Le collecteur du macLYON : les périodes de la liste, en prose (audit
A-21). « Du 1er octobre au 14 mars 2027 » n'était pas reconnu, et la 18e
Biennale de Lyon était publiée sur son seul jour de fermeture. Page
synthétique, qui reprend la carte du vrai site."""
import contextlib
import io
import unittest
from datetime import date
from unittest import mock

from scrapers import mac_lyon
from tests.outils import FauxSite, date_figee


class Periodes(unittest.TestCase):

    def test_le_premier_du_mois_et_le_jour_de_la_semaine(self):
        attendu = (date(2026, 10, 1), date(2027, 3, 14))
        self.assertEqual(mac_lyon._bornes("Du 1er octobre au 14 mars 2027"), attendu)
        self.assertEqual(mac_lyon._bornes("Du mercredi 1er octobre au dimanche 14 mars 2027"),
                         attendu)

    def test_les_formes_deja_lues(self):
        self.assertEqual(mac_lyon._bornes("Du 11 septembre au 14 mars 2027"),
                         (date(2026, 9, 11), date(2027, 3, 14)))
        self.assertEqual(mac_lyon._bornes("Samedi 17 octobre 2026"),
                         (date(2026, 10, 17), date(2026, 10, 17)))

    def test_jusqu_au(self):
        self.assertEqual(mac_lyon._bornes("Jusqu’au 14 mars 2027"), (date.min, date(2027, 3, 14)))

    def test_une_periode_illisible_n_est_pas_un_jour(self):
        self.assertIsNone(mac_lyon._bornes("Du printemps au 14 mars 2027"))


def carte(lien, titre, quand, type_="Exposition"):
    return ('<div class="strate"><a href="%s"><img src="/sites/mac/files/a.jpg"></a>'
            '<div class="link-bloc__info"><span class="item-title">%s</span>'
            '<span class="date">%s</span><span class="bold">- %s</span></div></div>'
            % (lien, titre, quand, type_))


class Liste(unittest.TestCase):

    def test_la_biennale_est_une_exposition_en_cours(self):
        page = "<html><body>%s</body></html>" % carte(
            "/fr/programmation/biennale", "18e Biennale de Lyon", "Du 1er octobre au 14 mars 2027")
        site = FauxSite({mac_lyon.BASE + mac_lyon.LISTES[0]: page})
        with mock.patch("requests.request", site.request), \
                mock.patch("requests.Session.request",
                           lambda self, m, u, **o: site.request(m, u, **o)), \
                mock.patch.object(mac_lyon, "Date", date_figee(date(2026, 11, 12))), \
                contextlib.redirect_stderr(io.StringIO()):
            lu = [(e.title, e.date_start, e.date_end) for e in mac_lyon.fetch()]
        self.assertEqual(lu, [("18e Biennale de Lyon", "2026-11-12", "2027-03-14")])


if __name__ == "__main__":
    unittest.main()
