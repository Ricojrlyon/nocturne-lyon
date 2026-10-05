"""Le collecteur du Transbordeur : l'affiche dans sa taille réduite, pas
l'original (audit n° 1). Pages synthétiques, qui reprennent la structure
des vraies : l'API WordPress donne date, titre et lien de chaque concert ;
la fiche porte l'affiche dans son bandeau (.Single__hero-cover__image),
le src à l'original, les tailles réduites dans le srcset."""
import contextlib
import io
import json
import unittest
from datetime import date
from unittest import mock

from scrapers import detail_cache, transbordeur
from tests.outils import FauxSite, date_figee

API = transbordeur.SITE + "/wp-json/wp/v2/evenement?per_page=100&_embed=1&page=1"
FICHE = transbordeur.SITE + "/evenement/beast-in-black-15112026/"
UP = transbordeur.SITE + "/wp-content/uploads/2025/10/"
ORIGINAL = UP + "BIB_hellraiser.jpg"
REDUITE = UP + "BIB_hellraiser-768x1085.jpg"
SRCSET = ", ".join([REDUITE + " 768w", UP + "BIB_hellraiser-1024x1447.jpg 1024w",
                    UP + "BIB_hellraiser-1440x2035.jpg 1440w", UP + "BIB_hellraiser-200x283.jpg 200w",
                    ORIGINAL + " 1772w"])
CONCERT = [{"acf": {"date_event": "20261115"}, "title": {"rendered": "Beast in Black"},
            "link": FICHE}]


def fiche(srcset=SRCSET):
    attr = ' srcset="%s" sizes="768px"' % srcset if srcset else ""
    return ('<html><body><div class="Single__hero-cover"><div class="Single__hero-cover__image">'
            '<img src="%s"%s alt=""></div><div class="Single__hero-cover__contain">'
            '<div class="ts-label">dim. 15 nov.</div><div class="ts-label">19:00</div></div>'
            '</div></body></html>' % (ORIGINAL, attr))


class Affiche(unittest.TestCase):

    def lire(self, pages, cache):
        site = FauxSite(pages)
        with mock.patch.object(transbordeur, "Date", date_figee(date(2026, 10, 1))), \
                mock.patch("requests.request", site.request), \
                mock.patch.object(detail_cache, "_cache", cache), \
                mock.patch.object(detail_cache, "_dirty", False), \
                contextlib.redirect_stderr(io.StringIO()):
            evs = transbordeur.fetch()
        return [(e.title, e.date_start, e.time, e.image) for e in evs], site

    def test_la_taille_la_plus_proche_de_800_px(self):
        # 768 px, et non l'original de 1 772 px : 211 Ko au lieu de 2,2 Mo
        # pour la vraie affiche de Beast in Black.
        lu, _ = self.lire({API: json.dumps(CONCERT), FICHE: fiche()}, {})
        self.assertEqual(lu, [("Beast in Black", "2026-11-15", "19:00", REDUITE)])

    def test_sans_bandeau_la_premiere_image_du_site_reduite_aussi(self):
        sans_bandeau = ('<html><body><img src="https://cdn.exemple.org/logo.png">'
                        '<img src="%s" srcset="%s"></body></html>' % (ORIGINAL, SRCSET))
        lu, _ = self.lire({API: json.dumps(CONCERT), FICHE: sans_bandeau}, {})
        self.assertEqual([i for *_, i in lu], [REDUITE])

    def test_sans_srcset_l_original_comme_avant(self):
        lu, _ = self.lire({API: json.dumps(CONCERT), FICHE: fiche(srcset=None)}, {})
        self.assertEqual(lu, [("Beast in Black", "2026-11-15", "19:00", ORIGINAL)])

    def test_une_fiche_en_cache_avec_l_original_est_relue(self):
        # La clé « affiche » remplace « image » : la fiche gardée en cache
        # avec l'original est relue dès le premier passage.
        cache = {FICHE: {"fetched_at": date.today().isoformat(), "time": "19:00",
                         "image": ORIGINAL}}
        lu, site = self.lire({API: json.dumps(CONCERT), FICHE: fiche()}, cache)
        self.assertEqual(lu, [("Beast in Black", "2026-11-15", "19:00", REDUITE)])
        self.assertIn(FICHE, site.appels)

    def test_relecture_echouee_l_ancienne_affiche_reste(self):
        # La fiche ne répond pas (404) : la carte garde l'affiche du cache,
        # au lieu de n'en avoir plus aucune jusqu'à la relecture suivante.
        cache = {FICHE: {"fetched_at": date.today().isoformat(), "time": "19:00",
                         "image": ORIGINAL}}
        lu, _ = self.lire({API: json.dumps(CONCERT)}, cache)
        self.assertEqual(lu, [("Beast in Black", "2026-11-15", "19:00", ORIGINAL)])


if __name__ == "__main__":
    unittest.main()
