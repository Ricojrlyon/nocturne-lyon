"""Le collecteur des Subsistances : une carte qui montre l'ORIGINAL de son
affiche prend une taille réduite (audit n° 1) ; les vignettes ne bougent
pas. Page synthétique, qui reprend la structure de la vraie : une carte
par spectacle (div.js-events-month-item), son affiche avec un src et un
srcset aux adresses relatives, une <li> par date."""
import contextlib
import io
import unittest
from datetime import date
from unittest import mock

from scrapers import les_subs
from tests.outils import FauxSite, date_figee

UP = "/wp-content/uploads/2026/07/"
ORIGINAL = UP + "img-8247-2x.jpg"


def agenda(src, srcset):
    attr = ' srcset="%s"' % srcset if srcset else ""
    return ('<html><body><div class="js-events-month-item"><a href="%s/evenement/attablements/">'
            '<img src="%s"%s><p class="uppercase text-11"><span>Théâtre</span></p>'
            '<p class="font-bold">ATTABLEMENTS</p></a><ul><li>jeu. 11 Mar <span>|</span> '
            '<span>20:00</span></li></ul></div></body></html>' % (les_subs.HOST, src, attr))


class Affiche(unittest.TestCase):

    def lire(self, src, srcset):
        site = FauxSite({les_subs.URL: agenda(src, srcset)})
        with mock.patch.object(les_subs, "Date", date_figee(date(2026, 10, 1))), \
                mock.patch("requests.request", site.request), \
                contextlib.redirect_stderr(io.StringIO()):
            return [(e.date_start, e.time, e.image) for e in les_subs.fetch()]

    def test_l_original_cede_la_place_a_la_taille_la_plus_proche_de_800_px(self):
        srcset = ", ".join([ORIGINAL + " 2400w", UP + "img-8247-2x-338x450.jpg 338w",
                            UP + "img-8247-2x-768x1024.jpg 768w", UP + "img-8247-2x-1536x2048.jpg 1536w"])
        self.assertEqual(self.lire(ORIGINAL, srcset),
                         [("2027-03-11", "20:00", les_subs.HOST + UP + "img-8247-2x-768x1024.jpg")])

    def test_la_vraie_carte_d_attablements(self):
        # 699 Ko au lieu de 8,7 Mo : la seule taille réduite que le site a faite.
        srcset = ORIGINAL + " 2400w, " + UP + "img-8247-2x-338x450.jpg 338w"
        self.assertEqual(self.lire(ORIGINAL, srcset),
                         [("2027-03-11", "20:00", les_subs.HOST + UP + "img-8247-2x-338x450.jpg")])

    def test_une_vignette_de_200_px_ne_bouge_pas(self):
        vignette = UP + "loic-nys-200x200.jpg"
        srcset = ", ".join([UP + "loic-nys-2560x3838.jpg 2560w", UP + "loic-nys-300x450.jpg 300w",
                            UP + "loic-nys-683x1024.jpg 683w"])
        self.assertEqual(self.lire(vignette, srcset),
                         [("2027-03-11", "20:00", les_subs.HOST + vignette)])

    def test_sans_srcset_l_original_comme_avant(self):
        # DEBOUT//PUBLIC : 8,2 Mo, et le site n'en a fait aucune taille réduite.
        original = UP + "visuel-debout-public-2.jpg"
        self.assertEqual(self.lire(original, None),
                         [("2027-03-11", "20:00", les_subs.HOST + original)])


if __name__ == "__main__":
    unittest.main()
