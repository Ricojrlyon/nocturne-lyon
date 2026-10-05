"""Le collecteur de la Croix-Rousse : l'affiche dans sa copie WebP de
768 px, pas l'original en PNG (audit n° 1). Pages synthétiques, qui
reprennent la structure des vraies : l'API WordPress donne la saison, le
genre et chaque spectacle avec ses tailles d'affiche ; la fiche porte les
séances. La copie WebP n'est citée nulle part : elle est vérifiée par une
requête HEAD avant d'être prise."""
import contextlib
import io
import json
import unittest
from datetime import date
from unittest import mock

import requests

from scrapers import base, croix_rousse, detail_cache
from tests.outils import FauxSite, date_figee

API = croix_rousse.API + "/"
FICHE = croix_rousse.BASE + "/au-programme/nelvar/"
UP = croix_rousse.BASE + "/wp-content/uploads/2026/05/"
PLEINE = UP + "Nelvar-1080x1080-1.png"
MOYENNE = UP + "Nelvar-1080x1080-1-768x768.png"
IMAGE_WEBP = (200, b"", {"Content-Type": "image/webp"})


def spectacle(tailles):
    return [{"link": FICHE, "title": {"rendered": "Nelvar, le royaume sans peuple"},
             "event_type": [], "uagb_featured_image_src": tailles}]


def fiche(seances=True):
    ligne = ('<div class="mobile-seances">jeu 15 › <div class="mobile-horaires">'
             '<a href="#">20h00</a></div></div>') if seances else ""
    return ('<html><body><div class="seances-dates-horaires"><h4>Octobre 2026</h4>%s'
            '</div></body></html>' % ligne)


TAILLES = {"full": [PLEINE, 1080, 1080, False], "medium_large": [MOYENNE, 768, 768, True],
           "thumbnail": [UP + "Nelvar-1080x1080-1-150x150.png", 150, 150, True]}


class Affiche(unittest.TestCase):

    def lire(self, tailles=TAILLES, seances=True, avant=None):
        pages = {API + "event_type": "[]",
                 API + "saison": json.dumps([{"id": 7, "name": "Saison 26.27", "count": 1}]),
                 API + "programmation": json.dumps(spectacle(tailles)),
                 FICHE: fiche(seances)}
        site = FauxSite(pages, avant=avant)
        with mock.patch.object(croix_rousse, "Date", date_figee(date(2026, 10, 1))), \
                mock.patch.object(croix_rousse.requests, "Session", lambda: site), \
                mock.patch("requests.request", site.request), \
                mock.patch.object(detail_cache, "_cache", {}), \
                mock.patch.object(detail_cache, "_dirty", False), \
                contextlib.redirect_stderr(io.StringIO()):
            evs = croix_rousse.fetch()
        return [(e.date_start, e.time, e.image) for e in evs], site

    def test_la_copie_webp_de_768_px(self):
        # 103 Ko au lieu de 2,5 Mo pour la vraie affiche de Nelvar.
        lu, _ = self.lire(avant={MOYENNE + ".webp": [IMAGE_WEBP]})
        self.assertEqual(lu, [("2026-10-15", "20:00", MOYENNE + ".webp")])

    def test_sans_copie_en_768_px_celle_de_l_original(self):
        lu, _ = self.lire(avant={PLEINE + ".webp": [IMAGE_WEBP]})
        self.assertEqual(lu, [("2026-10-15", "20:00", PLEINE + ".webp")])

    def test_sans_copie_webp_l_original_comme_avant(self):
        lu, site = self.lire()
        self.assertEqual(lu, [("2026-10-15", "20:00", PLEINE)])
        self.assertIn(MOYENNE + ".webp", site.appels)
        self.assertIn(PLEINE + ".webp", site.appels)

    def test_une_page_qui_n_est_pas_une_image_est_refusee(self):
        # Un « 200 » qui rend une page HTML (page d'erreur déguisée) ne
        # remplace pas l'affiche.
        page = (200, b"<html></html>", {"Content-Type": "text/html; charset=UTF-8"})
        lu, _ = self.lire(avant={MOYENNE + ".webp": [page], PLEINE + ".webp": [page]})
        self.assertEqual(lu, [("2026-10-15", "20:00", PLEINE)])

    def test_une_image_servie_en_404_est_refusee(self):
        perdue = (404, b"", {"Content-Type": "image/webp"})
        lu, _ = self.lire(avant={MOYENNE + ".webp": [perdue], PLEINE + ".webp": [perdue]})
        self.assertEqual(lu, [("2026-10-15", "20:00", PLEINE)])

    def test_site_injoignable_pour_la_copie_l_original(self):
        # La vérification échoue (connexion coupée, trois essais) : le
        # spectacle est publié quand même, avec l'original.
        coupe = [requests.ConnectionError("coupée")] * 3
        with mock.patch.object(base.time, "sleep", lambda s: None), \
                mock.patch.object(base, "_INJOIGNABLES", set()):
            lu, _ = self.lire(avant={MOYENNE + ".webp": list(coupe),
                                     PLEINE + ".webp": list(coupe)})
        self.assertEqual(lu, [("2026-10-15", "20:00", PLEINE)])

    def test_une_petite_affiche_n_est_verifiee_qu_une_fois(self):
        # Plus petite que 768 px : WordPress donne le même fichier pour
        # toutes les tailles.
        petite = {"full": [PLEINE, 500, 500, False], "medium_large": [PLEINE, 500, 500, False]}
        lu, site = self.lire(tailles=petite)
        self.assertEqual(lu, [("2026-10-15", "20:00", PLEINE)])
        self.assertEqual(site.appels.count(PLEINE + ".webp"), 1)

    def test_un_spectacle_sans_date_n_est_pas_verifie(self):
        lu, site = self.lire(seances=False)
        self.assertEqual(lu, [])
        self.assertFalse([u for u in site.appels if u.endswith(".webp")])

    def test_sans_affiche_aucune(self):
        lu, _ = self.lire(tailles=False)
        self.assertEqual(lu, [("2026-10-15", "20:00", None)])


if __name__ == "__main__":
    unittest.main()
