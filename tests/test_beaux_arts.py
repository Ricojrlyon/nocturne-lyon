"""Le collecteur du Musée des Beaux-Arts : l'affiche de chaque rendez-vous
(BUG-32). Pages synthétiques, qui reprennent la structure des vraies : la
fiche porte son image dans un BANDEAU, et en bas de page des cartes de
suggestions, chacune avec l'image d'un AUTRE rendez-vous."""
import contextlib
import io
import unittest
from datetime import date
from unittest import mock

from scrapers import beaux_arts, detail_cache
from tests.outils import FauxSite, date_figee

FICHE = beaux_arts.BASE + "/fr/fiche-programmation/ingres-par-lui-meme"
BANDEAU = "/sites/default/files/styles/banner/public/2020-02/ingres_aretin_sans_crop.jpg?itok=a"
VOISIN = "/sites/default/files/styles/card/public/2026-07/du-regard-au-crayon.jpg?itok=b"
LISTE = ('<html><body><div class="carte"><div class="field--name-field-pc-appointment-type">'
         '#Conférence</div><a href="/fr/fiche-programmation/ingres-par-lui-meme">'
         '<h3>Ingres par lui-même</h3></a></div></body></html>')


def fiche(bandeau=True):
    haut = ('<div class="LAME-banner"><div class="LAME-banner-img masque-banner-img">'
            '<div class="media-image-container"><img src="%s" alt="Ingres"></div></div></div>'
            % BANDEAU) if bandeau else ""
    suggestion = ('<div class="card scroll-effect-bloc"><div class="card-img-top">'
                  '<div class="field field--name-field-pc-main-image"><div class="field__item">'
                  '<div class="media-image-container"><img src="%s"></div></div></div></div>'
                  '<h3>Du regard au crayon</h3></div>' % VOISIN)
    return ('<html><body><article class="node node--type-fiche-programmation">'
            '<div class="node__content">%s<div class="field--name-title">Ingres par lui-même</div>'
            '<div class="modal-date-programmation">jeudi 15 octobre 2026 - 18h30</div>'
            '</div></article><section>%s</section></body></html>' % (haut, suggestion))


class Affiche(unittest.TestCase):

    def lire(self, page):
        site = FauxSite({FICHE: page})
        with mock.patch("requests.request", site.request):
            return beaux_arts._lire_fiche(FICHE)

    def test_l_affiche_est_celle_du_bandeau(self):
        # Et non la vignette d'une suggestion, qui vient avant dans le
        # champ « main image » : 34 dates sur 36 en portaient une.
        self.assertEqual(self.lire(fiche())["affiche"], beaux_arts.BASE + BANDEAU)

    def test_sans_bandeau_jamais_l_affiche_d_un_voisin(self):
        self.assertIsNone(self.lire(fiche(bandeau=False))["affiche"])

    def test_une_fiche_en_cache_avec_l_image_d_un_voisin_est_relue(self):
        # La clé « affiche » remplace « image » : la fiche gardée en cache
        # avec l'image d'un voisin est relue dès le premier passage.
        cache = {FICHE: {"fetched_at": date.today().isoformat(),
                         "seances": [["2026-10-15", "18:30"]],
                         "titre": "Ingres par lui-même", "image": beaux_arts.BASE + VOISIN}}
        site = FauxSite({beaux_arts.LISTING: LISTE, FICHE: fiche()})
        with mock.patch.object(beaux_arts, "Date", date_figee(date(2026, 10, 1))), \
                mock.patch.object(beaux_arts.requests, "Session", lambda: site), \
                mock.patch("requests.request", site.request), \
                mock.patch.object(detail_cache, "_cache", cache), \
                mock.patch.object(detail_cache, "_dirty", False), \
                contextlib.redirect_stderr(io.StringIO()):
            evs = beaux_arts.fetch()
        self.assertEqual([(e.title, e.date_start, e.time, e.image) for e in evs],
                         [("Ingres par lui-même", "2026-10-15", "18:30",
                           beaux_arts.BASE + BANDEAU)])
        self.assertIn(FICHE, site.appels)


if __name__ == "__main__":
    unittest.main()
