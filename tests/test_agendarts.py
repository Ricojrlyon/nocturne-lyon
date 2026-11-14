"""Le collecteur d'Agend'arts : l'année d'une séance (audit A-38). Les
catégories du blog sont des mois (« décembre 2026 ») ; seule leur année
était gardée, si bien qu'un billet classé en décembre 2026 et en janvier
2027 datait sa séance de janvier en 2026, dans le passé."""
import contextlib
import io
import unittest
from datetime import date
from unittest import mock

import requests

from scrapers import agendarts
from tests.outils import date_figee


class AnneeDeLaSeance(unittest.TestCase):

    CATS = {(12, 2026), (1, 2027)}

    def test_la_categorie_du_mois_de_la_seance_donne_l_annee(self):
        self.assertEqual(agendarts._annee(9, 1, "", self.CATS, None, None, 2026), 2027)
        self.assertEqual(agendarts._annee(9, 12, "", self.CATS, None, None, 2026), 2026)

    def test_sans_categorie_du_meme_mois_l_annee_des_categories(self):
        self.assertEqual(agendarts._annee(9, 3, "", {(12, 2026)}, None, None, 2026), 2026)

    def test_la_billetterie_reste_prioritaire(self):
        self.assertEqual(agendarts._annee(9, 1, "", self.CATS, 2028, None, 2026), 2028)


PIXEL = '<img alt="" border="0" src="https://pixel.wp.com/b.gif?host=agendarts.wordpress.com" />'


def billet(lien, titre, publie, categories, phrase, image=None):
    contenu = "<p>%s</p>%s%s" % (phrase, '<img src="%s" />' % image if image else "", PIXEL)
    return ("<item><title>%s</title><link>%s</link><pubDate>%s</pubDate>%s"
            "<content:encoded><![CDATA[%s]]></content:encoded></item>"
            % (titre, lien, publie, "".join("<category><![CDATA[%s]]></category>" % c
                                            for c in categories), contenu))


def flux(*billets):
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" '
            'xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>'
            "<title>agend'Arts</title>%s</channel></rss>" % "".join(billets)).encode("utf-8")


def reponse(code, corps=b""):
    r = requests.Response()
    r.status_code, r._content, r.url = code, corps, agendarts.FLUX
    return r


class FluxRss(unittest.TestCase):
    """Audit A-10 : l'API de WordPress.com ne se sert que par une passerelle
    que son robots.txt interdit ; le flux RSS du blog, lui, est permis."""

    def lire(self, pages):
        appels = []

        def faux_get(url, params=None, **options):
            page = (params or {}).get("paged", 1)
            appels.append(page)
            quoi = pages.get(page, reponse(404))
            if isinstance(quoi, BaseException):
                raise quoi
            return quoi
        with mock.patch.object(agendarts, "base_get", faux_get), \
                mock.patch.object(agendarts.time, "sleep", lambda s: None), \
                mock.patch.object(agendarts, "Date", date_figee(date(2026, 11, 14))), \
                contextlib.redirect_stderr(io.StringIO()):
            evs = agendarts.fetch()
        return [(e.date_start, e.time, e.title, e.image) for e in evs], appels

    def test_le_flux_est_lu_jusqu_a_sa_derniere_page(self):
        p1 = flux(billet("https://agendarts.wordpress.com/2026/10/16/a/", "Les mardis du 4",
                         "Fri, 16 Oct 2026 12:49:56 +0000", ["décembre 2026"],
                         "Mardi 8 décembre à 20h", "https://agendarts.wordpress.com/a.jpg"))
        p2 = flux(billet("https://agendarts.wordpress.com/2026/06/20/b/", "Une fille comme les autres",
                         "Sat, 20 Jun 2026 09:00:00 +0000", ["décembre 2026", "janvier 2027"],
                         "Vendredi 15 janvier à 19h"))
        lu, appels = self.lire({1: reponse(200, p1), 2: reponse(200, p2)})
        self.assertEqual(lu, [
            ("2026-12-08", "20:00", "Les mardis du 4", "https://agendarts.wordpress.com/a.jpg"),
            # L'année vient de la catégorie du mois de la séance (A-38), et
            # le pixel de statistiques de WordPress.com n'est pas une affiche.
            ("2027-01-15", "19:00", "Une fille comme les autres", None)])
        self.assertEqual(appels, [1, 2, 3])

    def test_la_lecture_s_arrete_aux_billets_trop_anciens(self):
        vieux = flux(billet("https://agendarts.wordpress.com/2024/06/20/c/", "Ancien",
                            "Thu, 20 Jun 2024 09:00:00 +0000", ["juin 2024"], "Samedi 29 juin à 20h"))
        _, appels = self.lire({1: reponse(200, vieux), 2: reponse(200, vieux)})
        self.assertEqual(appels, [1])

    def test_une_page_en_panne_fait_lever(self):
        p1 = flux(billet("https://agendarts.wordpress.com/2026/10/16/a/", "A",
                         "Fri, 16 Oct 2026 12:49:56 +0000", [], "Mardi 8 décembre 2026 à 20h"))
        with self.assertRaises(requests.HTTPError):
            self.lire({1: reponse(200, p1), 2: reponse(500)})
        with self.assertRaises(requests.HTTPError):
            self.lire({1: reponse(503)})

    def test_l_adresse_lue_est_permise(self):
        self.assertEqual(agendarts.FLUX, "https://agendarts.wordpress.com/feed/")
        self.assertNotIn("public-api.wordpress.com", agendarts.FLUX)


if __name__ == "__main__":
    unittest.main()
