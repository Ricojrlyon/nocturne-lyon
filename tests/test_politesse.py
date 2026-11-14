"""La politesse envers les sites lus (audit A-37) : le délai qu'un site
demande vaut aussi pour un nouvel essai ; ce qui ne change pas d'une nuit à
l'autre (affiches, archives) n'est plus relu chaque nuit ; et le robot
s'annonce sous son vrai nom partout où rien ne l'en empêche. Aucun
réseau."""
import contextlib
import io
import json
import unittest
from datetime import date
from unittest import mock

import requests

from scrapers import base, confluences, croix_rousse, detail_cache, rugby, tnp
from scrapers.aggregators import villemorte
from tests.outils import FausseSession, FauxSite, date_figee
from tests.test_tnp import FICHE as FICHE_TNP, fiche as fiche_tnp, ligne as ligne_tnp

NOM = "nocturne-lyon-events/1.0"
IMAGE = (200, b"", {"Content-Type": "image/webp"})


def reponse(code=200, corps=b""):
    r = requests.Response()
    r.status_code, r._content = code, corps
    return r


class CacheVide(unittest.TestCase):

    def setUp(self):
        self.enterContext(mock.patch.object(detail_cache, "_cache", {}))
        self.enterContext(mock.patch.object(detail_cache, "_dirty", False))
        self.enterContext(mock.patch.object(detail_cache, "_throttle", lambda: None))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        self.enterContext(mock.patch.object(base.time, "sleep", lambda s: None))
        self.enterContext(mock.patch.object(base, "_INJOIGNABLES", set()))


class DelaiDesSites(unittest.TestCase):

    def essayer(self, url):
        attentes = []
        with mock.patch.object(base.time, "sleep", attentes.append), \
                mock.patch.object(base, "_INJOIGNABLES", set()), \
                contextlib.redirect_stderr(io.StringIO()):
            base.get(url, session=FausseSession(requests.ConnectionError("coupée"), "ok"))
        return attentes

    def test_un_nouvel_essai_attend_le_delai_du_site(self):
        # Crawl-delay de dix secondes : on redemandait au bout de deux.
        self.assertEqual(self.essayer("https://maisondeladanse.com/programmation-2026-27"), [10.0])
        self.assertEqual(self.essayer("https://larayonne.org/agenda/"), [10.0])
        self.assertEqual(self.essayer("https://www.maisondeladanse.com/x"), [10.0])
        self.assertEqual(self.essayer("https://salle.exemple.org/agenda"), [2.0])


class VraiNom(unittest.TestCase):

    def test_la_ffr_recoit_le_nom_du_projet(self):
        # L'identité de navigateur reste pour la seule billetterie du LOU,
        # jamais appelée ici.
        entetes = []

        def faux_get(url, headers=None, **options):
            entetes.append((url, headers["User-Agent"]))
            return reponse(200, b"... qualification-50150 ..." if url == rugby.FFR_STABLE else b"")
        with mock.patch.object(rugby, "base_get", faux_get), \
                contextlib.redirect_stderr(io.StringIO()):
            rugby._femmes(date(2026, 11, 14), date(2027, 5, 13))
        self.assertEqual([u for u, _ in entetes], [rugby.FFR_STABLE, rugby.FFR_CALENDRIER % "50150"])
        self.assertTrue(all(ua.startswith(NOM) for _, ua in entetes), entetes)

    def test_ville_morte_recoit_le_nom_du_projet(self):
        entetes = []

        def faux_get(url, headers=None, **options):
            entetes.append(headers["User-Agent"])
            return reponse(200, b"[]")
        with mock.patch.object(villemorte, "base_get", faux_get):
            villemorte.fetch()
        self.assertTrue(entetes[0].startswith(NOM), entetes)


class AffichesEnCache(CacheVide):

    def test_tnp_une_fiche_lue_une_fois_par_semaine(self):
        jour = ('<div class="agenda__month-day"><time datetime="2026-11-20">20 nov.</time>%s</div>'
                % ligne_tnp("affaire", "Une petite affaire", "20:00", "salle Roger-Planchon"))
        site = FauxSite({tnp.AGENDA: "<html><body>%s</body></html>" % jour,
                         FICHE_TNP % "affaire": fiche_tnp("affaire")})
        with mock.patch.object(tnp, "Date", date_figee(date(2026, 11, 14))), \
                mock.patch.object(tnp.requests, "Session", lambda: site):
            premier = [e.image for e in tnp.fetch()]
            second = [e.image for e in tnp.fetch()]
        self.assertEqual(premier, second)
        self.assertEqual(site.appels.count(FICHE_TNP % "affaire"), 1)

    def test_croix_rousse_une_verification_par_semaine(self):
        moyenne = croix_rousse.BASE + "/wp-content/uploads/a-768x768.png"
        tailles = {"full": [croix_rousse.BASE + "/wp-content/uploads/a.png"],
                   "medium_large": [moyenne]}
        site = FauxSite({}, avant={moyenne + ".webp": [IMAGE]})
        self.assertEqual(croix_rousse._affiche(site, tailles), moyenne + ".webp")
        self.assertEqual(croix_rousse._affiche(site, tailles), moyenne + ".webp")
        self.assertEqual(site.appels, [moyenne + ".webp"])

    def test_croix_rousse_une_verification_coupee_n_est_pas_notee(self):
        pleine = croix_rousse.BASE + "/wp-content/uploads/b.png"
        coupe = [requests.ConnectionError("coupée")] * 3
        site = FauxSite({}, avant={pleine + ".webp": coupe + [IMAGE]})
        self.assertEqual(croix_rousse._affiche(site, {"full": [pleine]}), pleine)
        self.assertEqual(croix_rousse._affiche(site, {"full": [pleine]}), pleine + ".webp")

    def test_confluences_une_verification_par_semaine(self):
        original = confluences.BASE + "/sites/default/files/2026-09/bal.jpg"
        carte = confluences.BASE + "/sites/default/files/styles/card_desktop_2x/public/2026-09/bal.jpg"
        site = FauxSite({}, avant={carte: [(200, b"", {"Content-Type": "image/jpeg"})]})
        self.assertEqual(confluences._carte_en_cache(site, original), carte)
        self.assertEqual(confluences._carte_en_cache(site, original), carte)
        self.assertEqual(site.appels, [carte])


class ArchivesDesConfluences(unittest.TestCase):

    def test_seuls_les_evenements_a_venir_ou_recurrents_sont_demandes(self):
        # 15 pages pour 670 événements, dont 67 à venir ; 5 avec le filtre.
        demandes = []

        def faux_tout(session, type_noeud, include, filtre=None):
            demandes.append((type_noeud, filtre))
            return [], {}
        with mock.patch.object(confluences, "_tout", faux_tout):
            confluences._evenements(None, date(2026, 11, 14), date(2027, 5, 13))
        (type_noeud, filtre), = demandes
        self.assertEqual(type_noeud, "event")
        self.assertEqual(filtre["filter[ou][group][conjunction]"], "OR")
        self.assertEqual(filtre["filter[av][condition][value]"], "2026-11-14")
        self.assertEqual(filtre["filter[rr][condition][operator]"], "IS NOT NULL")

    def test_le_filtre_part_avec_la_premiere_page(self):
        page = json.dumps({"data": [], "links": {}})
        appels = []

        class Session:
            def request(self, methode, url, params=None, **options):
                appels.append(params)
                r = reponse(200, page.encode())
                r.url = url
                return r
        confluences._tout(Session(), "event", "x", {"filter[a]": "b"})
        self.assertEqual(appels[0]["filter[a]"], "b")


if __name__ == "__main__":
    unittest.main()
