"""Les catégories du Petit Bulletin que le collecteur écarte (2026-10-14) :
toutes les expositions et visites, et les rendez-vous de parole -
rencontres et dédicaces, conférences, sciences et histoire, lecture et
conte. Une fausse page de l'agenda, un événement par catégorie, passe par
le vrai collecteur ; aucun réseau.
"""
import contextlib
import io
import unittest
from datetime import date
from unittest import mock

import requests

from scrapers.aggregators import petit_bulletin
from tests.outils import date_figee

J = date(2026, 10, 14)
GARDEES = ["Théâtre", "Impro", "Rock & Pop", "Humour & Café Théâtre", "Jeux"]
ECARTEES = ["Photographie", "Peinture & Dessin", "Art contemporain et numérique",
            "Design & Architecture", "Visites", "Street Art", "Art graphique", "Sculpture",
            "Rencontres et Dédicaces", "Conférences", "Sciences et Histoire",
            "Lecture et conte", "rencontres et dedicaces"]


def bloc(i, categorie):
    ligne = "<p>(%s)</p>" % categorie if categorie else "<p>Un mot de présentation.</p>"
    return ('<h3><a href="/agenda-%d-evenement.html">Événement %d</a></h3>%s'
            '<ul><li><a href="/lieu.html">Salle %d</a></li>'
            '<li><a href="/date.html">Mardi 20 octobre 2026 à 20h</a></li></ul>'
            % (1000 + i, i, ligne, i))


def page(categories):
    html = "<html><body><div>%s</div></body></html>" % "".join(
        bloc(i, c) for i, c in enumerate(categories))
    return html


def collecter(categories):
    """Les catégories que fetch() rend, et ce qu'il a dit."""
    def faux_get(url, **options):
        r = requests.Response()
        r.url, r.encoding = url, "utf-8"
        if url == petit_bulletin.URL:
            r.status_code, r._content = 200, page(categories).encode("utf-8")
        else:
            r.status_code, r._content = 404, b"fin"
        return r
    journal = io.StringIO()
    with mock.patch.object(petit_bulletin, "base_get", faux_get), \
            mock.patch.object(petit_bulletin, "date", date_figee(J)), \
            mock.patch.object(petit_bulletin.time, "sleep", lambda s: None), \
            contextlib.redirect_stdout(journal):
        evenements = petit_bulletin.fetch()
    return [e.category for e in evenements], journal.getvalue()


class CategoriesEcartees(unittest.TestCase):

    def test_les_expos_et_les_rendez_vous_de_parole_sont_ecartes(self):
        rendues, journal = collecter(GARDEES + ECARTEES + [None])
        self.assertEqual(rendues, GARDEES + [None])
        self.assertIn("[Petit Bulletin] %d événement(s) écarté(s) par catégorie" % len(ECARTEES),
                      journal)

    def test_chaque_categorie_ecartee_l_est_seule(self):
        for categorie in ECARTEES:
            self.assertEqual(collecter(["Théâtre", categorie])[0], ["Théâtre"], categorie)


if __name__ == "__main__":
    unittest.main()
