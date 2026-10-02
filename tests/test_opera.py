"""Le collecteur de l'Opéra : l'affiche des cartes de la liste (BUG-14).
Page synthétique, qui reprend les deux gabarits de carte du vrai site : le
titre DANS le lien qui porte l'image, ou le titre lui-même lien, l'image à
côté dans un autre lien vers la même fiche (« Opéra Underground »)."""
import unittest
from datetime import date
from unittest import mock

from scrapers import opera_lyon
from tests.outils import FauxSite, date_figee

SAISON = "https://www.opera-lyon.com/programmation-reservations/saison-2026-2027"
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


if __name__ == "__main__":
    unittest.main()
