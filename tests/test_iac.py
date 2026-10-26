"""Le collecteur de l'IAC : un site injoignable doit se dire. Fin octobre
2026, i-ac.eu ne répondait plus ; le collecteur rendait une liste vide,
et le passage ne savait dire que « 16→0 », sans la raison."""
import unittest
from unittest import mock

import requests

from scrapers import iac


def page_vide():
    r = requests.Response()
    r.status_code, r._content, r.encoding = 200, b"<html><body></body></html>", "utf-8"
    return r


class IacInjoignable(unittest.TestCase):

    def test_le_site_injoignable_leve_au_lieu_de_rendre_une_liste_vide(self):
        def panne(url, **options):
            raise requests.ConnectTimeout("Connection to i-ac.eu timed out.")
        with mock.patch.object(iac, "base_get", panne):
            with self.assertRaises(requests.ConnectTimeout):
                iac.fetch()

    def test_une_seule_page_en_panne_ne_leve_pas(self):
        def moitie(url, **options):
            if url.endswith("/a-venir"):
                raise requests.ConnectTimeout("Connection to i-ac.eu timed out.")
            return page_vide()
        with mock.patch.object(iac, "base_get", moitie):
            self.assertEqual(iac.fetch(), [])


if __name__ == "__main__":
    unittest.main()
