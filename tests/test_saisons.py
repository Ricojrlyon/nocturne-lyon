"""Les saisons des salles (audit A-24). Une saison court de septembre à
juin ; de mai à août, la suivante est déjà en vente. En juillet et en août,
la Croix-Rousse et la Maison de la Danse relisaient la saison finie, et les
spectacles de la rentrée manquaient jusqu'au 1er septembre. Aucun réseau."""
import contextlib
import io
import unittest
from datetime import date
from unittest import mock

import requests

from scrapers import base, croix_rousse, maison_de_la_danse as mdd
from tests.outils import date_figee


class RegleCommune(unittest.TestCase):

    def test_les_saisons_a_lire(self):
        cas = {date(2026, 11, 14): [2026], date(2027, 4, 30): [2026],
               date(2027, 5, 1): [2026, 2027], date(2027, 7, 1): [2026, 2027],
               date(2027, 8, 31): [2026, 2027], date(2027, 9, 1): [2027]}
        for jour, attendu in cas.items():
            with self.subTest(jour=jour):
                self.assertEqual(base.saisons_a_lire(jour), attendu)


class CroixRousse(unittest.TestCase):

    def test_en_juillet_la_saison_suivante_aussi(self):
        lues = []

        def faux_api(session, chemin, **params):
            if chemin == "saison":
                return [{"id": 7, "name": "Saison 26.27", "count": 40},
                        {"id": 8, "name": "Saison 27.28", "count": 12},
                        {"id": 6, "name": "Saison 25.26", "count": 38}]
            if chemin == "programmation":
                lues.append(params["saison"])
            return []
        with mock.patch.object(croix_rousse, "_api", faux_api), \
                mock.patch.object(croix_rousse, "Date", date_figee(date(2027, 7, 1))), \
                contextlib.redirect_stderr(io.StringIO()):
            croix_rousse.fetch()
        self.assertEqual(lues, [7, 8])

    def test_en_novembre_la_saison_en_cours_seule(self):
        lues = []

        def faux_api(session, chemin, **params):
            if chemin == "saison":
                return [{"id": 7, "name": "Saison 26.27", "count": 40},
                        {"id": 8, "name": "Saison 27.28", "count": 1}]
            if chemin == "programmation":
                lues.append(params["saison"])
            return []
        with mock.patch.object(croix_rousse, "_api", faux_api), \
                mock.patch.object(croix_rousse, "Date", date_figee(date(2026, 11, 14))), \
                contextlib.redirect_stderr(io.StringIO()):
            croix_rousse.fetch()
        self.assertEqual(lues, [7])


class MaisonDeLaDanse(unittest.TestCase):

    def lire(self, jour, codes):
        lues = []

        def faux_get(url, **options):
            lues.append(url)
            r = requests.Response()
            r.status_code, r._content, r.url = codes.get(url, 200), b"<html></html>", url
            return r
        with mock.patch.object(mdd, "base_get", faux_get), \
                mock.patch.object(mdd.time, "sleep", lambda s: None), \
                mock.patch.object(mdd, "Date", date_figee(jour)), \
                contextlib.redirect_stderr(io.StringIO()):
            mdd.fetch()
        return lues

    def test_en_juillet_la_saison_suivante_aussi(self):
        self.assertEqual(self.lire(date(2027, 7, 1), {}),
                         [mdd.BASE + "/programmation-2026-27", mdd.BASE + "/programmation-2027-28"])

    def test_une_saison_suivante_pas_encore_en_ligne_n_est_pas_une_panne(self):
        lues = self.lire(date(2027, 5, 15), {mdd.BASE + "/programmation-2027-28": 404})
        self.assertEqual(len(lues), 2)

    def test_la_saison_en_cours_introuvable_reste_une_panne(self):
        with self.assertRaises(requests.HTTPError):
            self.lire(date(2027, 5, 15), {mdd.BASE + "/programmation-2026-27": 404})


if __name__ == "__main__":
    unittest.main()
