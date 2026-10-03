"""Le Radiant (BUG-27) : un spectacle « Scolaires » est réservé aux classes,
et n'est pas publié. Page synthétique, qui reprend la structure de la vraie
page d'accueil : une ligne de genres (« Théâtre - Scolaires »), le titre, les
dates."""
import contextlib
import io
import unittest
from datetime import date
from unittest import mock

from scrapers import radiant
from tests.outils import FauxSite, date_figee


def carte(slug, genres, titre, dates):
    return ('<div class="spectacle-vignette"><a href="/spectacles/%s/"><div><p>%s</p></div>'
            '<div><h2>%s</h2><time>%s</time></div></a></div>' % (slug, genres, titre, dates))


class Scolaires(unittest.TestCase):

    def lire(self, cartes):
        site = FauxSite({radiant.URL: "<html><body>%s</body></html>" % "".join(cartes)})
        with mock.patch("requests.request", site.request), \
                mock.patch.object(radiant.detail_cache, "get_details",
                                  lambda url, f, fields: {}), \
                mock.patch.object(radiant, "Date", date_figee(date(2026, 10, 1))), \
                contextlib.redirect_stderr(io.StringIO()) as journal:
            lu = {(e.title, e.date_start) for e in radiant.fetch()}
        return lu, journal.getvalue()

    def test_un_spectacle_scolaire_n_est_pas_publie(self):
        lu, journal = self.lire([
            carte("camille", "Théâtre - Scolaires", "CAMILLE, TU DORS ?", "7 & 8 décembre 2026"),
            carte("pere", "Théâtre", "LE PÈRE", "mercredi 20 janvier 2027")])
        self.assertEqual(lu, {("LE PÈRE", "2027-01-20")})
        self.assertIn("[Radiant] scolaires écartés : CAMILLE, TU DORS ?", journal)

    def test_le_bandeau_sans_genre_puis_la_carte_scolaire(self):
        # Le bandeau « à la une » ne dit pas le genre : la seconde carte le dit.
        lu, _ = self.lire([
            '<div class="une"><a href="/spectacles/lumieres/"><h3>LUMIÈRES !</h3>'
            ' lundi 25 janvier 2027</a></div>',
            carte("lumieres", "Théâtre - Scolaires", "LUMIÈRES !", "lundi 25 janvier 2027")])
        self.assertEqual(lu, set())

    def test_famille_et_scolaires_garde_ses_dates(self):
        lu, _ = self.lire([carte("conte", "Famille - Scolaires", "Le Conte",
                                 "samedi 05 décembre 2026")])
        self.assertEqual(lu, {("Le Conte", "2026-12-05")})


if __name__ == "__main__":
    unittest.main()
