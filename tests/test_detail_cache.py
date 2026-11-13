"""Le cache des fiches (scrapers/detail_cache.py, audit A-30) : une lecture
qui échoue n'écrit rien, et une liste de séances est relue après trois
jours. Une fiche nouvelle en panne passagère était notée « vide » pour
sept jours, et une date ajoutée restait invisible sept ou trente jours,
selon que la fiche donnait une heure. Aucun réseau."""
import contextlib
import io
import unittest
from datetime import date, timedelta
from unittest import mock

import requests

from scrapers import detail_cache, heat, le_sucre
from tests.outils import FauxSite

URL = "https://salle.exemple.org/fiche"


def il_y_a(jours: int) -> str:
    return (date.today() - timedelta(days=jours)).isoformat()


class Cache(unittest.TestCase):

    def setUp(self):
        self.cache = {}
        self.enterContext(mock.patch.object(detail_cache, "_cache", self.cache))
        self.enterContext(mock.patch.object(detail_cache, "_dirty", False))
        self.enterContext(mock.patch.object(detail_cache, "_throttle", lambda: None))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.lus = []

    def lecteur(self, rendu):
        def lire(url):
            self.lus.append(url)
            if isinstance(rendu, BaseException):
                raise rendu
            return rendu
        return lire

    def test_une_fiche_nouvelle_en_panne_n_est_pas_notee(self):
        self.assertEqual(detail_cache.get_details(URL, self.lecteur(None),
                                                  fields=("seances", "lieu")), {})
        self.assertNotIn(URL, self.cache)

    def test_une_fiche_connue_en_panne_garde_ce_qu_on_savait_sans_rajeunir(self):
        ancienne = {"fetched_at": il_y_a(10), "seances": [["2026-12-01", "20:00"]],
                    "lieu": "Grande salle"}
        self.cache[URL] = dict(ancienne)
        rendu = detail_cache.get_details(URL, self.lecteur(None), fields=("seances", "lieu"))
        self.assertEqual(rendu["seances"], ancienne["seances"])
        # Rien n'est écrit : la fiche sera relue au passage suivant.
        self.assertEqual(self.cache[URL], ancienne)

    def test_les_seances_sont_relues_apres_trois_jours(self):
        # Même quand la fiche donne une heure, qui la gardait trente jours.
        for age, relue in ((3, False), (4, True)):
            with self.subTest(age=age):
                self.lus.clear()
                self.cache[URL] = {"fetched_at": il_y_a(age), "time": "20:00",
                                   "seances": [["2026-12-01", "20:00"]]}
                detail_cache.get_details(URL, self.lecteur({"time": "20:00", "seances": []}),
                                         fields=("time", "seances"))
                self.assertEqual(bool(self.lus), relue)

    def test_sans_seances_les_durees_d_avant(self):
        self.cache[URL] = {"fetched_at": il_y_a(20), "time": "20:00", "image": None}
        detail_cache.get_details(URL, self.lecteur({"time": "20:00"}), fields=("time", "image"))
        self.assertEqual(self.lus, [])

    def test_l_heure_en_panne_reseau_n_est_pas_notee(self):
        self.assertIsNone(detail_cache.get_time(URL, self.lecteur(requests.ConnectionError("x"))))
        self.assertNotIn(URL, self.cache)
        self.cache[URL] = {"fetched_at": il_y_a(40), "time": "21:00"}
        self.assertEqual(detail_cache.get_time(URL, self.lecteur(requests.ConnectionError("x"))),
                         "21:00")
        self.assertEqual(self.cache[URL]["fetched_at"], il_y_a(40))

    def test_une_page_sans_heure_reste_notee(self):
        self.assertIsNone(detail_cache.get_time(URL, self.lecteur(None)))
        self.assertEqual(self.cache[URL], {"time": None, "fetched_at": date.today().isoformat()})


class LecteursDHeure(unittest.TestCase):
    """HEAT et Le Sucre passent par get_time : une panne doit y remonter
    comme une erreur, et non comme « pas d'heure sur la page »."""

    def test_une_fiche_en_panne_leve_une_fiche_introuvable_n_a_pas_d_heure(self):
        for module, fiche in ((heat, "https://h-eat.eu/events/soiree/"),
                              (le_sucre, "https://le-sucre.eu/events/soiree/")):
            with self.subTest(module.__name__):
                site = FauxSite({}, avant={fiche: [(500, b"", {})]})
                with mock.patch("requests.request", site.request), \
                        self.assertRaises(requests.HTTPError):
                    module._fetch_detail_time(fiche)
                with mock.patch("requests.request", FauxSite({}).request):
                    self.assertIsNone(module._fetch_detail_time(fiche))


if __name__ == "__main__":
    unittest.main()
