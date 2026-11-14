"""Les pannes partielles (audit A-09) : quand une page suivante ou une
sous-source tombe, le collecteur LÈVE, et le garde-fou reprend la veille
entière. Il rendait jusque-là ce qu'il avait déjà lu, comme si c'était
tout le programme : le Petit Bulletin coupé à la page 3 publiait 160
cartes sur 277, sur un passage vert. Une page introuvable (404), elle,
reste la fin d'un agenda ou un spectacle retiré. Aucun réseau : chaque
site est simulé."""
import contextlib
import io
import json
import unittest
from datetime import date
from unittest import mock

import requests
from requests.structures import CaseInsensitiveDict

from scrapers import (agendarts, asvel, auditorium, base, beaux_arts, complexe,
                      confluences, handball, la_commune, mapado, periscope, rugby,
                      transbordeur, volley)
from scrapers.aggregators import petit_bulletin
from tests.outils import FauxSite, date_figee, evenement
from tests.test_petit_bulletin import J as JOUR_PB, page as page_pb

J = date(2026, 11, 13)


def reponse(code, corps=b"", entetes=None, url="https://exemple.org/"):
    r = requests.Response()
    r.status_code, r._content, r.url, r.encoding = code, corps, url, "utf-8"
    r.headers = CaseInsensitiveDict(entetes or {})
    return r


def erreur(code):
    return requests.HTTPError("%d" % code, response=reponse(code))


def leve(exc):
    def f(*a, **k):
        raise exc
    return f


class Silencieux(unittest.TestCase):
    """Sans attente ni bruit : base.get ne dort pas entre ses essais."""

    def setUp(self):
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(mock.patch.object(base.time, "sleep", lambda s: None))
        self.enterContext(mock.patch.object(base, "_INJOIGNABLES", set()))


class PetitBulletin(Silencieux):

    def collecter(self, page2):
        def faux_get(url, **options):
            if url == petit_bulletin.URL:
                return reponse(200, page_pb(["Théâtre", "Jeux"]).encode("utf-8"), url=url)
            if url.endswith("?p=2"):
                if isinstance(page2, BaseException):
                    raise page2
                return reponse(page2, url=url)
            return reponse(404, url=url)
        with mock.patch.object(petit_bulletin, "base_get", faux_get), \
                mock.patch.object(petit_bulletin, "date", date_figee(JOUR_PB)), \
                mock.patch.object(petit_bulletin.time, "sleep", lambda s: None):
            return petit_bulletin.fetch()

    def test_une_page_suivante_en_panne_fait_lever(self):
        with self.assertRaises(requests.HTTPError):
            self.collecter(500)
        with self.assertRaises(requests.HTTPError):
            self.collecter(429)
        with self.assertRaises(requests.ConnectionError):
            self.collecter(requests.ConnectionError("coupée"))

    def test_une_page_introuvable_est_la_fin_de_l_agenda(self):
        self.assertEqual(len(self.collecter(404)), 2)


class Periscope(Silencieux):

    def collecter(self, page2):
        lus = []

        def faux_lire(soup, vus):
            lus.append(1)
            return ([evenement("Le Périscope", "Concert", J, url=periscope.URL + "un/")]
                    if len(lus) == 1 else [])

        def faux_get(url, **options):
            if url == periscope.URL:
                return reponse(200, b"<html></html>", url=url)
            return reponse(page2, url=url)
        with mock.patch.object(periscope, "base_get", faux_get), \
                mock.patch.object(periscope, "_scrape_page", faux_lire):
            return periscope.fetch()

    def test_une_page_suivante_en_panne_fait_lever(self):
        with self.assertRaises(requests.HTTPError):
            self.collecter(500)

    def test_une_page_introuvable_est_la_fin_de_l_agenda(self):
        self.assertEqual(len(self.collecter(404)), 1)


class Transbordeur(Silencieux):

    API = transbordeur.SITE + "/wp-json/wp/v2/evenement?per_page=100&_embed=1&page=%d"

    def collecter(self, pages):
        site = FauxSite({}, avant={self.API % (i + 1): [p] for i, p in enumerate(pages)})
        with mock.patch("requests.request", site.request), \
                mock.patch.object(transbordeur, "Date", date_figee(J)):
            return transbordeur.fetch()

    def test_la_premiere_page_en_panne_fait_lever(self):
        # Elle rendait une liste vide, que rien ne distinguait d'un
        # programme fini.
        with self.assertRaises(requests.HTTPError):
            self.collecter([(500, b"", {})])

    def test_une_page_annoncee_en_panne_fait_lever(self):
        with self.assertRaises(requests.HTTPError):
            self.collecter([(200, b"[]", {"X-WP-TotalPages": "2"}), (500, b"", {})])

    def test_une_reponse_qui_n_est_pas_une_liste_fait_lever(self):
        with self.assertRaises(ValueError):
            self.collecter([(200, b'{"code": "rest_no_route"}', {})])


class LaCommune(Silencieux):

    def collecter(self, hub, programme):
        def faux_get(url, **options):
            code = hub if url == la_commune.URLS[0] else programme
            return reponse(code, b"<html></html>", url=url)
        with mock.patch.object(la_commune, "base_get", faux_get):
            return la_commune.fetch()

    def test_le_hub_en_panne_fait_lever(self):
        # Le 500 du hub laissait les 8 cartes de /programme/ seules, sur 28.
        with self.assertRaises(requests.HTTPError):
            self.collecter(500, 200)

    def test_l_extrait_en_panne_ne_coute_rien_quand_le_hub_repond(self):
        self.assertEqual(self.collecter(200, 500), [])


class Auditorium(Silencieux):

    def collecter(self, avant):
        site = FauxSite({auditorium.BASE + "/fr/agenda/202611": "<html></html>"},
                        avant=avant)
        with mock.patch.object(auditorium, "Date", date_figee(J)), \
                mock.patch.object(auditorium.requests, "Session", lambda: site):
            return auditorium.fetch()

    def test_un_mois_en_panne_fait_lever(self):
        with self.assertRaises(requests.HTTPError):
            self.collecter({auditorium.BASE + "/fr/agenda/202612": [(500, b"", {})]})

    def test_un_mois_sans_page_n_est_pas_une_panne(self):
        # Les mois suivants répondent 404 : pas de page, pas de panne.
        self.assertEqual(self.collecter(None), [])


class Confluences(Silencieux):

    def test_une_page_suivante_en_panne_fait_lever(self):
        suite = confluences.API + "/node/event?page%5Boffset%5D=50"
        page1 = json.dumps({"data": [{"id": "a"}], "links": {"next": {"href": suite}}})
        site = FauxSite({confluences.API + "/node/event": page1},
                        avant={suite: [(500, b"", {})]})
        with self.assertRaises(requests.HTTPError):
            confluences._tout(site, "event", "field_place")

    def test_sans_page_suivante_tout_est_rendu(self):
        page1 = json.dumps({"data": [{"id": "a"}], "links": {}})
        site = FauxSite({confluences.API + "/node/event": page1})
        noeuds, _ = confluences._tout(site, "event", "field_place")
        self.assertEqual(noeuds, [{"id": "a"}])


class BeauxArts(Silencieux):

    LISTE = ('<html><body><div class="carte"><div class="field--name-field-pc-appointment-type">'
             '#Conférence</div><a href="/fr/fiche-programmation/une"><h3>Une</h3></a></div>'
             '</body></html>')

    def test_une_page_suivante_en_panne_fait_lever(self):
        site = FauxSite({beaux_arts.LISTING: self.LISTE},
                        avant={beaux_arts.LISTING + "?page=1": [(500, b"", {})]})
        with self.assertRaises(requests.HTTPError):
            beaux_arts._cartes(site)

    def test_une_page_introuvable_est_la_fin_de_la_liste(self):
        site = FauxSite({beaux_arts.LISTING: self.LISTE})
        self.assertEqual(list(beaux_arts._cartes(site)), ["/fr/fiche-programmation/une"])

    def test_l_article_des_expositions_en_panne_fait_lever(self):
        site = FauxSite({}, avant={beaux_arts.BASE + beaux_arts.EXPOSITIONS: [(500, b"", {})]})
        with self.assertRaises(requests.HTTPError):
            beaux_arts._expositions(site, J, J)


class Agendarts(Silencieux):
    """Lu dans le flux RSS du blog depuis le lot 5 (audit A-10)."""

    PAGE1 = ('<?xml version="1.0"?><rss xmlns:content="http://purl.org/rss/1.0/modules/content/">'
             '<channel><item><title>Concert</title><link>https://exemple.org/b</link>'
             '<pubDate>Thu, 12 Nov 2026 10:00:00 +0000</pubDate>'
             '<content:encoded><![CDATA[<p>Rien</p>]]></content:encoded></item>'
             '</channel></rss>').encode("utf-8")

    def collecter(self, page2):
        def faux_get(url, params=None, **options):
            return reponse(200, self.PAGE1) if not params else reponse(page2)
        with mock.patch.object(agendarts, "base_get", faux_get), \
                mock.patch.object(agendarts.time, "sleep", lambda s: None), \
                mock.patch.object(agendarts, "Date", date_figee(J)):
            return agendarts.fetch()

    def test_une_page_suivante_en_panne_fait_lever(self):
        with self.assertRaises(requests.HTTPError):
            self.collecter(500)

    def test_une_page_introuvable_est_la_fin_du_flux(self):
        self.assertEqual(self.collecter(404), [])


class Sports(Silencieux):

    def test_volley_l_export_d_un_club_en_panne_fait_lever(self):
        # Les matchs du gymnase Mado Bonnet quittaient le site pour la nuit.
        with mock.patch.object(volley, "_lignes", leve(erreur(500))), \
                self.assertRaises(requests.HTTPError):
            volley.fetch()

    def test_handball_un_calendrier_en_panne_fait_lever(self):
        with mock.patch.object(handball, "_calendrier", leve(requests.ConnectionError("coupée"))), \
                self.assertRaises(requests.ConnectionError):
            handball.fetch()

    def test_rugby_la_ffr_en_panne_fait_lever(self):
        # 7 matchs sur 10 quittaient le site. La billetterie du LOU n'est
        # jamais appelée : _hommes est remplacé.
        with mock.patch.object(rugby, "_hommes", lambda t, h: []), \
                mock.patch.object(rugby, "base_get", leve(requests.ConnectionError("coupée"))), \
                self.assertRaises(requests.ConnectionError):
            rugby.fetch()

    def test_asvel_l_euroleague_en_panne_fait_lever(self):
        with mock.patch.object(asvel, "_betclic", lambda s: []), \
                mock.patch.object(asvel, "base_get", leve(requests.ConnectionError("coupée"))), \
                self.assertRaises(requests.ConnectionError):
            asvel.fetch()


class PagesDeSpectacle(Silencieux):
    """Le Complexe et les boutiques Mapado : les dates d'un spectacle ne
    sont que sur sa page. La perdre, c'était perdre le spectacle."""

    SPECTACLE = {"url": "https://www.lecomplexelyon.com/un", "titre": "Un",
                 "annee": 2026, "image": None}

    def complexe(self, exc):
        with mock.patch.object(complexe, "_catalogue", lambda s, a: [dict(self.SPECTACLE)]), \
                mock.patch.object(complexe, "_seances", leve(exc)), \
                mock.patch.object(complexe, "Date", date_figee(J)):
            return complexe.fetch()

    def mapado(self, exc):
        with mock.patch.object(mapado, "shows", lambda s, shop, g: [{"slug": "un", "title": "Un"}]), \
                mock.patch.object(mapado, "sessions", leve(exc)), \
                mock.patch.object(mapado, "Date", date_figee(J)):
            return mapado.fetch_venue("https://salle.mapado.com", "Salle", "salle", None,
                                      lambda v: True)

    def test_une_page_de_spectacle_en_panne_fait_lever(self):
        for lire in (self.complexe, self.mapado):
            with self.subTest(lire.__name__), self.assertRaises(requests.HTTPError):
                lire(erreur(500))
            with self.subTest(lire.__name__), self.assertRaises(requests.ConnectionError):
                lire(requests.ConnectionError("coupée"))

    def test_un_spectacle_retire_ne_coute_que_lui(self):
        for lire in (self.complexe, self.mapado):
            with self.subTest(lire.__name__):
                self.assertEqual(lire(erreur(404)), [])


class Introuvable(unittest.TestCase):

    def test_la_reponse_ou_l_erreur(self):
        self.assertTrue(base.introuvable(reponse(404)))
        self.assertTrue(base.introuvable(erreur(410)))
        self.assertFalse(base.introuvable(reponse(500)))
        self.assertFalse(base.introuvable(erreur(500)))
        self.assertFalse(base.introuvable(requests.ConnectionError("coupée")))
        self.assertFalse(base.introuvable(reponse(200)))


if __name__ == "__main__":
    unittest.main()
