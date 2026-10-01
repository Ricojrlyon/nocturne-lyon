"""Les nouveaux essais réseau de base.get (BUG-4) : quand on réessaie,
combien de fois, et quand on cesse — y compris pour un hôte tombé."""
import contextlib
import io
import unittest
from unittest import mock

import requests

from scrapers import base
from tests.outils import FausseSession

A = "https://salle-a.exemple.org/agenda"
B = "https://salle-b.exemple.org/agenda"


class NouveauxEssais(unittest.TestCase):

    def setUp(self):
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        base._INJOIGNABLES.clear()
        self.attentes = []
        p = mock.patch.object(base.time, "sleep", self.attentes.append)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(base._INJOIGNABLES.clear)

    def test_succes_du_premier_coup(self):
        s = FausseSession("ok")
        self.assertEqual(base.get(A, session=s).status_code, 200)
        self.assertEqual((len(s.appels), self.attentes), (1, []))

    def test_coupure_puis_succes(self):
        s = FausseSession(requests.ConnectionError("coupée"), "ok")
        self.assertEqual(base.get(A, session=s).status_code, 200)
        self.assertEqual((len(s.appels), self.attentes), (2, [2.0]))

    def test_deux_delais_depasses_puis_succes(self):
        s = FausseSession(requests.ReadTimeout("lent"), requests.ReadTimeout("lent"), "ok")
        self.assertEqual(base.get(A, session=s).status_code, 200)
        self.assertEqual(self.attentes, [2.0, 4.0])

    def test_502_puis_succes(self):
        s = FausseSession(502, "ok")
        self.assertEqual(base.get(A, session=s).status_code, 200)

    def test_503_durable_rend_la_reponse(self):
        s = FausseSession(503, 503, 503)
        self.assertEqual(base.get(A, session=s).status_code, 503)
        self.assertEqual(len(s.appels), 3)

    def test_404_n_est_jamais_reessaye(self):
        s = FausseSession(404, "ok")
        self.assertEqual(base.get(A, session=s).status_code, 404)
        self.assertEqual(len(s.appels), 1)

    def test_coupure_durable_leve_l_erreur(self):
        s = FausseSession(*[requests.ConnectionError("coupée")] * 3)
        with self.assertRaises(requests.ConnectionError):
            base.get(A, session=s)
        self.assertEqual(len(s.appels), 3)

    def test_hote_tombe_un_seul_essai_jusqu_a_sa_reponse(self):
        coupe = requests.ConnectionError("coupée")
        s = FausseSession(coupe, coupe, coupe,      # 1 : trois essais, échec
                          coupe,                    # 2 : un seul essai
                          "ok",                     # 3 : il répond
                          coupe, "ok")              # 4 : de nouveau trois essais
        with self.assertRaises(requests.ConnectionError):
            base.get(A, session=s)
        with self.assertRaises(requests.ConnectionError):
            base.get(A, session=s)
        self.assertEqual(len(s.appels), 4)
        self.assertEqual(base.get(A, session=s).status_code, 200)
        self.assertEqual(base.get(A, session=s).status_code, 200)
        self.assertEqual(len(s.appels), 7)

    def test_un_hote_tombe_n_affecte_pas_les_autres(self):
        coupe = requests.ConnectionError("coupée")
        with self.assertRaises(requests.ConnectionError):
            base.get(A, session=FausseSession(coupe, coupe, coupe))
        s = FausseSession(coupe, "ok")
        self.assertEqual(base.get(B, session=s).status_code, 200)
        self.assertEqual(len(s.appels), 2)

    def test_session_et_parametres_transmis(self):
        s = FausseSession("ok")
        base.get(A, session=s, params={"page": 2}, headers={"X": "1"}, timeout=12)
        methode, url, options = s.appels[0]
        self.assertEqual((methode, url), ("GET", A))
        self.assertEqual(options["params"], {"page": 2})
        self.assertEqual(options["headers"], {"X": "1"})
        self.assertEqual(options["timeout"], 12)

    def test_post_transmet_le_formulaire(self):
        s = FausseSession("ok")
        base.post(A, session=s, data={"club": "123"})
        methode, _, options = s.appels[0]
        self.assertEqual(methode, "POST")
        self.assertEqual(options["data"], {"club": "123"})

    def test_sans_session_passe_par_requests(self):
        r = requests.Response()
        r.status_code = 200
        with mock.patch.object(base.requests, "request", return_value=r) as appel:
            base.get(A, headers={"X": "1"}, timeout=7)
        appel.assert_called_once_with("GET", A, params=None, data=None,
                                      headers={"X": "1"}, timeout=7)


if __name__ == "__main__":
    unittest.main()
