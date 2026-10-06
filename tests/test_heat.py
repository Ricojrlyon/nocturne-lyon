"""Le collecteur de HEAT : l'heure de début d'un événement (BUG-13). Pages
synthétiques, qui reprennent la structure du vrai site - span.hour-start et
span.hour-end dans l'article de l'événement, et hors de lui le bandeau
« Happy Hour » commun à toutes les pages."""
import unittest
from unittest import mock

from scrapers import heat
from tests.outils import FauxSite

SOIREE = "https://h-eat.eu/events/soiree/"
BANDEAU = ('<div><div class="left"><span class="text">Afterwork : Happy Hour de '
           '17:30 à 20:00</span></div></div>')


def page(debut=None, fin=None, texte=""):
    heures = ('<div class="hours"><span class="hour-start">%s</span> — '
              '<span class="hour-end">%s</span></div>' % (debut, fin)) if debut else ""
    return ('<html><body>%s<article class="post-event"><div class="left">'
            '<div class="date">samedi 03 octobre</div>%s</div><div class="right">'
            '<div class="textarea">%s</div></div></article></body></html>'
            % (BANDEAU, heures, texte))


class HeureDeDebut(unittest.TestCase):

    def lire(self, html):
        site = FauxSite({SOIREE: html})
        with mock.patch("requests.request", site.request):
            return heat._fetch_detail_time(SOIREE)

    def test_l_heure_de_debut_a_toute_heure(self):
        # Un marché de journée, une journée qui finit à minuit, une nuit qui
        # finit au matin : c'est le début qui compte, ni la fin ni le bandeau.
        self.assertEqual(self.lire(page("11:00", "19:00")), "11:00")
        self.assertEqual(self.lire(page("11:00", "00:00")), "11:00")
        self.assertEqual(self.lire(page("18:00", "04:00")), "18:00")

    def test_sans_balise_d_heure_le_bandeau_ne_compte_pas(self):
        # Sans span.hour-start, l'heure se cherche encore - dans l'article
        # seulement : le « 17:30 » du bandeau n'est l'heure de personne.
        self.assertEqual(self.lire(page(texte="Ouverture des portes à 19h00")), "19:00")
        self.assertIsNone(self.lire(page(texte="Programme à venir")))


if __name__ == "__main__":
    unittest.main()
