"""Le collecteur du Musée des Confluences : l'affiche dans la taille
« carte » de l'agenda du musée, 800 × 500, pas l'original (audit n° 1).
L'API ne donne que l'original ; l'adresse de la carte s'en déduit
(/sites/default/files/styles/card_desktop_2x/public/…), mais le musée ne
la cite qu'avec un jeton : elle est vérifiée par une requête HEAD."""
import contextlib
import io
import unittest
from datetime import date
from unittest import mock

import requests

from scrapers import base, confluences
from tests.outils import FauxSite, date_figee, evenement

FICHIERS = confluences.BASE + "/sites/default/files/"
ORIGINAL = FICHIERS + "uploads/2026-06/lebalp-1.JPG"
CARTE = FICHIERS + "styles/card_desktop_2x/public/uploads/2026-06/lebalp-1.JPG"
EXPO = FICHIERS + "uploads/2024-09/une-tropforts-exposition.png"
EXPO_CARTE = FICHIERS + "styles/card_desktop_2x/public/uploads/2024-09/une-tropforts-exposition.png"
IMAGE = (200, b"", {"Content-Type": "image/jpeg"})


class TailleCarte(unittest.TestCase):

    def verifier(self, url, reponses):
        site = FauxSite({}, avant={CARTE: reponses} if reponses else None)
        return confluences._taille_carte(site, url), site

    def test_la_carte_quand_le_musee_la_sert(self):
        # 601 Ko au lieu de 9,5 Mo pour la vraie affiche du Bal poussière.
        self.assertEqual(self.verifier(ORIGINAL, [IMAGE])[0], CARTE)

    def test_refusee_sans_jeton_l_original(self):
        # Drupal peut refuser une taille qu'il n'a pas encore fabriquée.
        self.assertEqual(self.verifier(ORIGINAL, [(403, b"", {})])[0], ORIGINAL)

    def test_une_image_servie_en_404_est_refusee(self):
        self.assertEqual(self.verifier(ORIGINAL, [(404, b"", {"Content-Type": "image/jpeg"})])[0],
                         ORIGINAL)

    def test_une_page_qui_n_est_pas_une_image_est_refusee(self):
        page = (200, b"<html></html>", {"Content-Type": "text/html; charset=UTF-8"})
        self.assertEqual(self.verifier(ORIGINAL, [page])[0], ORIGINAL)

    def test_site_injoignable_l_original(self):
        with mock.patch.object(base.time, "sleep", lambda s: None), \
                mock.patch.object(base, "_INJOIGNABLES", set()), \
                contextlib.redirect_stderr(io.StringIO()):
            lu, _ = self.verifier(ORIGINAL, [requests.ConnectionError("coupée")] * 3)
        self.assertEqual(lu, ORIGINAL)

    def test_une_image_d_ailleurs_n_est_pas_touchee(self):
        ailleurs = "https://images.exemple.org/affiche.jpg"
        lu, site = self.verifier(ailleurs, None)
        self.assertEqual(lu, ailleurs)
        self.assertEqual(site.appels, [])


class Fetch(unittest.TestCase):

    def test_une_verification_par_affiche_distincte(self):
        j = date(2026, 10, 15)
        seances = [evenement(confluences.VENUE, "Le Bal poussière", j, image=ORIGINAL),
                   evenement(confluences.VENUE, "Le Bal poussière", date(2026, 10, 16),
                             image=ORIGINAL),
                   evenement(confluences.VENUE, "Conférence", j, image=None)]
        expos = [evenement(confluences.VENUE, "Trop forts !", j, heure=None, image=EXPO,
                           categorie="exposition")]
        site = FauxSite({}, avant={CARTE: [IMAGE], EXPO_CARTE: [(403, b"", {})]})
        with mock.patch.object(confluences, "Date", date_figee(date(2026, 10, 1))), \
                mock.patch.object(confluences.requests, "Session", lambda: site), \
                mock.patch.object(confluences, "_evenements", lambda s, t, h: (seances, {}, 0)), \
                mock.patch.object(confluences, "_expositions", lambda s, t, h: (expos, 0)):
            lu = [(e.title, e.date_start, e.image) for e in confluences.fetch()]
        self.assertEqual(lu, [("Le Bal poussière", "2026-10-15", CARTE),
                              ("Le Bal poussière", "2026-10-16", CARTE),
                              ("Conférence", "2026-10-15", None),
                              ("Trop forts !", "2026-10-15", EXPO)])
        self.assertEqual(site.appels, [CARTE, EXPO_CARTE])


if __name__ == "__main__":
    unittest.main()
