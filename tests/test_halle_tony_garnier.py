"""Le collecteur de la Halle Tony Garnier : ses dates s'écrivent « 06.11 »,
sans année (audit A-22). Sa copie de la règle d'année n'avait pas de
grâce : un concert de la veille encore affiché partait un an plus tard, et
une plage en cours sortait à l'envers. Le site est passé sur www.htg.fr.
Page synthétique, qui reprend la carte du vrai site."""
import unittest
from datetime import date
from unittest import mock

from scrapers import halle_tony_garnier as halle
from tests.outils import FauxSite, date_figee


def carte(slug, titre, quand):
    return ('<a class="bloc-square types type_concert" href="%s/fr/programmation/%s">'
            '<div class="bloc-square__title">%s</div><div class="bloc-square__date">%s</div>'
            '<div class="bloc-square__heure">20h00</div></a>' % (halle.HOST, slug, titre, quand))


def lire(cartes, jour):
    """{slug: (début, fin)} de la liste, lue ce jour-là."""
    site = FauxSite({halle.URLS[0]: "<html><body>%s</body></html>" % "".join(cartes)})
    with mock.patch("requests.request", site.request), \
            mock.patch.object(halle, "Date", date_figee(jour)):
        return {e.url.rsplit("/", 1)[-1]: (e.date_start, e.date_end) for e in halle.fetch()}


class AnneesSansMillesime(unittest.TestCase):

    def test_le_site_est_lu_a_sa_nouvelle_adresse(self):
        self.assertEqual(halle.HOST, "https://www.htg.fr")
        self.assertEqual(halle.URLS[0], "https://www.htg.fr/fr/programmation")

    def test_une_plage_en_cours_reste_a_l_endroit(self):
        lu = lire([carte("holiday", "HOLIDAY ON ICE", "06.11 au 15.11")], date(2026, 11, 12))
        self.assertEqual(lu, {"holiday": ("2026-11-06", "2026-11-15")})

    def test_un_concert_de_la_veille_n_est_pas_republie_dans_un_an(self):
        lu = lire([carte("gojira", "GOJIRA", "11.11"), carte("demain", "DEMAIN", "13.11")],
                  date(2026, 11, 12))
        self.assertEqual(lu, {"demain": ("2026-11-13", None)})

    def test_une_plage_ouverte_depuis_longtemps(self):
        lu = lire([carte("salon", "SALON", "01.09 au 15.11")], date(2026, 11, 12))
        self.assertEqual(lu, {"salon": ("2026-09-01", "2026-11-15")})


if __name__ == "__main__":
    unittest.main()
