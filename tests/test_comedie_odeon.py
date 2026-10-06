"""Le collecteur de la Comédie Odéon : l'affiche dans sa taille réduite,
pas l'original (audit n° 1). Page synthétique, qui reprend la structure
de la vraie : une carte par spectacle (article[data-category]), son
affiche en img.wp-post-image (le src à l'original, les tailles réduites
dans le srcset) et le calendrier, un onglet par mois."""
import calendar
import contextlib
import html
import io
import unittest
from datetime import date
from unittest import mock

from scrapers import comedie_odeon
from tests.outils import FauxSite, date_figee

LIEN = comedie_odeon.BASE + "/spectacle/barbara/"
UP = comedie_odeon.BASE + "/wp-content/uploads/2026/07/"
ORIGINAL = UP + "Affiche-Barbara.png"
REDUITE = UP + "Affiche-Barbara-768x1086.png"
SRCSET = ", ".join([ORIGINAL + " 1414w", UP + "Affiche-Barbara-212x300.png 212w",
                    UP + "Affiche-Barbara-724x1024.png 724w", REDUITE + " 768w",
                    UP + "Affiche-Barbara-1086x1536.png 1086w"])
OCTOBRE = calendar.timegm((2026, 10, 1, 0, 0, 0))


def programme(srcset=SRCSET):
    attr = ' srcset="%s"' % srcset if srcset else ""
    bulle = html.escape('<span class="titlePop">Barbara</span><span class="datesIndication">'
                        'Du 15 oct. au 15 oct. 2026<a href="%s" class="plus">Voir plus</a>'
                        '</span>' % LIEN)
    return ('<html><body><article data-category="theatre"><a href="%s">'
            '<img class="wp-post-image" src="%s"%s></a><h2>Barbara</h2>'
            '<div class="dateCaption">Le 15 octobre à 20h</div></article>'
            '<div class="tab-pane" id="%d"><a class="TMdate" data-content="%s">15</a></div>'
            '</body></html>' % (LIEN, ORIGINAL, attr, OCTOBRE, bulle))


class Affiche(unittest.TestCase):

    def lire(self, page):
        site = FauxSite({comedie_odeon.LISTING: page})
        with mock.patch.object(comedie_odeon, "Date", date_figee(date(2026, 10, 1))), \
                mock.patch.object(comedie_odeon.requests, "Session", lambda: site), \
                contextlib.redirect_stderr(io.StringIO()):
            return [(e.date_start, e.time, e.image) for e in comedie_odeon.fetch()]

    def test_la_taille_la_plus_proche_de_800_px(self):
        # 768 px, et non l'original de 1 414 px : 973 Ko au lieu de 3,1 Mo
        # pour la vraie affiche de Barbara.
        self.assertEqual(self.lire(programme()), [("2026-10-15", "20:00", REDUITE)])

    def test_sans_srcset_l_original_comme_avant(self):
        self.assertEqual(self.lire(programme(srcset=None)), [("2026-10-15", "20:00", ORIGINAL)])

    def test_un_srcset_aux_adresses_relatives(self):
        relatif = SRCSET.replace(comedie_odeon.BASE, "")
        self.assertEqual(self.lire(programme(srcset=relatif)), [("2026-10-15", "20:00", REDUITE)])


if __name__ == "__main__":
    unittest.main()
