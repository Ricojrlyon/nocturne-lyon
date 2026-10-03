"""Un spectacle à plusieurs dates : chaque date à l'heure de SA séance
(BUG-24). Une seule heure pour toutes publiait les matinées du dimanche à
l'heure du soir : « Festival Karavel 20 » le dimanche 4 octobre 2026 à 20h,
joué à 16h. Pages synthétiques, qui reprennent la structure des vrais
sites."""
import unittest
from datetime import date
from unittest import mock

from scrapers import comedie_odeon, radiant
from tests.outils import FauxSite, date_figee

FICHE = radiant.HOST + "/spectacles/%s/"


def carte(slug, titre, dates):
    return ('<div class="carte"><span>Danse</span><a href="/spectacles/%s/"><h3>%s</h3></a>'
            ' %s Billetterie</div>' % (slug, titre, dates))


def fiche(seances):
    """seances : [(« dimanche 04 octobre 2026 », « 16h00 »)]."""
    lignes = "".join("<li><strong>%s</strong><br/><span>%s</span></li>" % s for s in seances)
    return ('<html><body><div class="spectacle-main-right"><div class="spectacle-dates">'
            '<ul>%s</ul></div></div></body></html>' % lignes)


class Radiant(unittest.TestCase):

    def lire(self, cartes, fiches):
        """{(date, heure)} publiés pour ces cartes et ces fiches."""
        site = FauxSite({radiant.URL: "<html><body>%s</body></html>" % "".join(cartes),
                         **{FICHE % slug: page for slug, page in fiches.items()}})
        with mock.patch("requests.request", site.request), \
                mock.patch.object(radiant.detail_cache, "get_details",
                                  lambda url, f, fields: f(url) or {}), \
                mock.patch.object(radiant, "Date", date_figee(date(2026, 10, 1))):
            return {(e.date_start, e.time) for e in radiant.fetch()}

    def test_chaque_date_a_l_heure_de_sa_seance(self):
        lu = self.lire([carte("karavel", "Festival Karavel 20", "03 & 04 octobre 2026")],
                       {"karavel": fiche([("samedi 03 octobre 2026", "20h00"),
                                          ("dimanche 04 octobre 2026", "16h00")])})
        self.assertEqual(lu, {("2026-10-03", "20:00"), ("2026-10-04", "16:00")})

    def test_deux_seances_le_meme_jour(self):
        # Murmuration Level 2 : la séance du dimanche soir manquait.
        lu = self.lire([carte("murmuration", "MURMURATION LEVEL 2", "12 & 13 décembre 2026")],
                       {"murmuration": fiche([("samedi 12 décembre 2026", "20h30"),
                                              ("dimanche 13 décembre 2026", "16h00"),
                                              ("dimanche 13 décembre 2026", "20h00")])})
        self.assertEqual(lu, {("2026-12-12", "20:30"), ("2026-12-13", "16:00"),
                              ("2026-12-13", "20:00")})

    def test_une_seance_du_matin_reste_ecartee(self):
        # Comme avant : seules les heures de 14h à 22h sont lues.
        lu = self.lire([carte("cheminotes", "CHEMINOTES", "02 & 03 novembre 2026")],
                       {"cheminotes": fiche([("lundi 02 novembre 2026", "09h30"),
                                             ("lundi 02 novembre 2026", "14h30"),
                                             ("mardi 03 novembre 2026", "10h45"),
                                             ("mardi 03 novembre 2026", "14h30")])})
        self.assertEqual(lu, {("2026-11-02", "14:30"), ("2026-11-03", "14:30")})

    def test_sans_liste_de_seances_l_heure_de_la_fiche(self):
        page = '<html><body><p class="horaire">Représentation à 20h30</p></body></html>'
        lu = self.lire([carte("seul", "Seul en scène", "jeudi 05 novembre 2026")],
                       {"seul": page})
        self.assertEqual(lu, {("2026-11-05", "20:30")})


class ComedieOdeon(unittest.TestCase):

    def horaires(self, texte):
        url = comedie_odeon.BASE + "/spectacle/essai/"
        site = FauxSite({url: '<html><body><div class="date_spect">%s</div></body></html>'
                              % texte})
        with mock.patch("requests.request", site.request):
            return comedie_odeon._lire_fiche(url)

    def test_une_date_en_toutes_lettres_garde_son_heure(self):
        # « Jovany » : le jeudi 11 mars était publié à 20h, l'heure du 14 janvier.
        self.assertEqual(self.horaires("Jeudi 14 janvier 2027 à 20h Jeudi 11 mars 2027 à 21h"),
                         {"defaut": None, "exceptions": {"01-14": "20:00", "03-11": "21:00"}})

    def test_l_heure_par_defaut_et_ses_exceptions(self):
        self.assertEqual(self.horaires("Du 02 septembre au 30 octobre 2026 Du mercredi au "
                                       "samedi à 20h Le 09/10 à 19h Relâches : 15/10 + 16/10"),
                         {"defaut": "20:00", "exceptions": {"10-09": "19:00"}})

    def test_la_fin_d_une_plage_n_est_pas_une_exception(self):
        self.assertEqual(self.horaires("Du mercredi 4 au samedi 14 novembre 2026 à 21h"),
                         {"defaut": "21:00", "exceptions": {}})


if __name__ == "__main__":
    unittest.main()
