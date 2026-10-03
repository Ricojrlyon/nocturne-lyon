"""Le collecteur de l'Opéra : l'affiche des cartes de la liste (BUG-14),
toutes les pages du listing et la salle réelle (BUG-30).
Page synthétique, qui reprend les deux gabarits de carte du vrai site : le
titre DANS le lien qui porte l'image, ou le titre lui-même lien, l'image à
côté dans un autre lien vers la même fiche (« Opéra Underground »)."""
import contextlib
import io
import unittest
from datetime import date
from unittest import mock

import requests

from scrapers import opera_lyon
from scrapers.base import OFFSITE_PLUSIEURS
from tests.outils import FauxSite, date_figee

SAISON = "https://www.opera-lyon.com/programmation-reservations/saison-2026-2027"
SUIVANTE = "https://www.opera-lyon.com/programmation-reservations/saison-2027-2028"
FICHE = "/fr/programmation/saison-2026-2027/%s"
AFFICHE = "https://api.opera-lyon.com/assets/%s.jpg"


def carte_lien(slug, titre, quand):
    """Premier gabarit : l'image, le titre et la date dans le lien."""
    return ('<div class="root_a"><a href="%s"><img src="%s"><span class="title_a">%s</span>'
            '<span class="date_a">%s</span></a></div>' % (FICHE % slug, AFFICHE % slug, titre, quand))


def carte_underground(slug, titre, quand, image=True):
    """Second gabarit : l'image dans un lien ; à côté, la date et le titre,
    lui-même lien vers la même fiche."""
    img = '<img src="%s">' % (AFFICHE % slug) if image else ""
    return ('<div class="root--underground_u"><a class="blockimage_u" href="%s">%s'
            '<span>Concert</span></a><div><span class="date_u">%s</span>'
            '<a class="title_u" href="%s">%s</a><button>+</button></div></div>'
            % (FICHE % slug, img, quand, FICHE % slug, titre))


class AfficheDesCartes(unittest.TestCase):

    def lire(self, *cartes):
        """{titre: affiche} de la liste, lue au 1er octobre 2026."""
        site = FauxSite({SAISON: '<html><body><div class="carousel">%s</div></body></html>'
                                 % "".join(cartes)})
        with mock.patch.object(opera_lyon, "Date", date_figee(date(2026, 10, 1))), \
                mock.patch("requests.request", site.request):
            return {s["title"]: s["image"] for s in opera_lyon._scrape_url(SAISON)}

    def test_les_deux_gabarits_ont_leur_affiche(self):
        lu = self.lire(carte_lien("opera/la-fille", "La Fille de Madame Angot", "4 oct. - 20 oct. 2026"),
                       carte_underground("opera-underground/karma", "Karma Bazar", "9 oct. 2026"))
        self.assertEqual(lu, {"La Fille de Madame Angot": AFFICHE % "opera/la-fille",
                              "Karma Bazar": AFFICHE % "opera-underground/karma"})

    def test_jamais_l_affiche_d_un_voisin(self):
        # Une carte sans image reste sans affiche, même à côté d'une carte
        # qui en porte une.
        lu = self.lire(carte_underground("opera-underground/sans", "Sans visuel", "9 oct. 2026",
                                         image=False),
                       carte_underground("opera-underground/karma", "Karma Bazar", "10 oct. 2026"))
        self.assertEqual(lu, {"Sans visuel": None, "Karma Bazar": AFFICHE % "opera-underground/karma"})


class Pages(unittest.TestCase):
    """Le listing s'étale sur plusieurs pages, chacune répétant en tête les
    productions mises en avant ; au-delà de la dernière, il ne reste
    qu'elles (BUG-30)."""

    UNE = carte_lien("opera/oranges", "L'Amour des trois oranges", "4 oct. - 20 oct. 2026")
    BECK = carte_lien("concert/beck", "Arielle Beck", "15 oct. 2026")

    def lire(self, pages, avant=None):
        """Les titres publiés au 1er octobre 2026, et les adresses lues."""
        site = FauxSite({url: "<html><body>%s</body></html>" % corps
                         for url, corps in pages.items()}, avant)
        with mock.patch.object(opera_lyon, "Date", date_figee(date(2026, 10, 1))), \
                mock.patch("requests.request", site.request), \
                mock.patch.object(opera_lyon.detail_cache, "get_details",
                                  lambda url, lire, fields=(): {}), \
                contextlib.redirect_stderr(io.StringIO()):
            titres = sorted(e.title for e in opera_lyon.fetch())
        return titres, site.appels

    def test_toutes_les_pages_sont_lues(self):
        titres, appels = self.lire({
            SAISON: self.UNE + self.BECK,
            SAISON + "?page=2": self.UNE + carte_underground("opera-underground/crimi", "Crimi",
                                                             "21 oct. 2026"),
            SAISON + "?page=3": self.UNE})
        self.assertEqual(titres, ["Arielle Beck", "Crimi", "L'Amour des trois oranges"])
        # La troisième n'apporte rien de neuf : on s'y arrête. La saison
        # suivante, pas encore publiée, répond 404 dès sa première page.
        self.assertEqual(appels, [SAISON, SAISON + "?page=2", SAISON + "?page=3", SUIVANTE])

    def test_un_404_en_suite_finit_la_liste(self):
        titres, appels = self.lire({SAISON: self.UNE + self.BECK})
        self.assertEqual((titres, appels), (["Arielle Beck", "L'Amour des trois oranges"],
                                            [SAISON, SAISON + "?page=2", SUIVANTE]))

    def test_une_page_de_suite_en_panne_fait_echouer_la_collecte(self):
        # Mieux vaut la veille entière, reprise par le garde-fou, que le
        # début de la saison seul, publié sans un mot.
        with self.assertRaises(requests.HTTPError):
            self.lire({SAISON: self.UNE + self.BECK},
                      avant={SAISON + "?page=2": [(500, b"panne", {})]})


class HorsLesMurs(unittest.TestCase):
    """La salle réelle, lue dans le champ Lieu de la fiche."""

    def test_les_salles_de_la_maison(self):
        # « Grande salle de l'Opéra », sans « de Lyon », passait pour une
        # salle du dehors (BUG-30).
        for lieu in ("Opéra de Lyon", "Amphi de l'Opéra de Lyon", "Grande salle de l'Opéra"):
            self.assertIsNone(opera_lyon._hors_les_murs(lieu), lieu)

    def test_une_salle_du_dehors_sans_son_adresse(self):
        self.assertEqual(opera_lyon._hors_les_murs("Salle Molière, Lyon 5e"), "Salle Molière")
        self.assertEqual(opera_lyon._hors_les_murs("Théâtre Théo Argence - Saint-Priest"),
                         "Théâtre Théo Argence")

    def test_la_virgule_du_nom_des_celestins(self):
        # Coupé à sa virgule, le nom ne laissait que « Théâtre de Lyon » (BUG-30).
        self.assertEqual(opera_lyon._hors_les_murs("Les Célestins, Théâtre de Lyon, Lyon 2e"),
                         "Les Célestins, Théâtre de Lyon")
        self.assertEqual(opera_lyon._hors_les_murs(
            "Théâtre du Point du Jour, Lyon 5e, Opéra de Lyon, Théâtre National Populaire, "
            "Villeurbanne, Les Célestins, Théâtre de Lyon, Lyon 2e"), OFFSITE_PLUSIEURS)


if __name__ == "__main__":
    unittest.main()
