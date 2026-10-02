"""La lecture des dates en français : la source la plus fréquente de
séances mal datées. La date du jour est FIGÉE au 1er octobre 2026, pour
que ces tests rendent le même verdict dans un an."""
import unittest
from datetime import date
from unittest import mock

from scrapers import base
from scrapers.aggregators import petit_bulletin
from tests.outils import date_figee

AUJOURDHUI = date(2026, 10, 1)


class DateFrancaise(unittest.TestCase):
    """base.parse_french_date, partagée par plusieurs collecteurs."""

    def lire(self, texte, annee=None):
        with mock.patch.object(base, "date", date_figee(AUJOURDHUI)):
            return base.parse_french_date(texte, default_year=annee)

    def test_jour_abrege_avec_annee_par_defaut(self):
        self.assertEqual(self.lire("jeu. 30 avr.", 2026), date(2026, 4, 30))

    def test_premier_du_mois(self):
        # « 1er » échappait à la lecture : les séances du 1er disparaissaient.
        self.assertEqual(self.lire("1er mai 2026"), date(2026, 5, 1))

    def test_juillet_abrege_a_deux_l(self):
        self.assertEqual(self.lire("sam. 12 juill.", 2026), date(2026, 7, 12))

    def test_mois_accentue(self):
        self.assertEqual(self.lire("15 déc. 2026"), date(2026, 12, 15))

    def test_date_impossible(self):
        self.assertIsNone(self.lire("31 février 2026"))

    def test_sans_date(self):
        self.assertIsNone(self.lire("date à venir"))

    def test_annee_deduite_mois_passe_annee_suivante(self):
        self.assertEqual(self.lire("15 sept."), date(2027, 9, 15))

    def test_annee_deduite_mois_a_venir_annee_en_cours(self):
        self.assertEqual(self.lire("15 oct."), date(2026, 10, 15))

    def test_aujourdhui_reste_dans_l_annee(self):
        self.assertEqual(self.lire("1er oct."), date(2026, 10, 1))


class DatesDuPetitBulletin(unittest.TestCase):
    """petit_bulletin._parse_date_str : séances, plages, « jusqu'au »."""

    def lire(self, texte):
        with mock.patch.object(petit_bulletin, "date", date_figee(AUJOURDHUI)):
            return petit_bulletin._parse_date_str(texte)

    def test_date_unique_avec_heure(self):
        self.assertEqual(self.lire("Mardi 13 octobre 2026 à 20h"),
                         [("2026-10-13", "20:00", None)])

    def test_heure_avec_minutes(self):
        self.assertEqual(self.lire("Samedi 3 octobre à 20h30"),
                         [("2026-10-03", "20:30", None)])

    def test_plage_courte_une_seance_par_jour(self):
        self.assertEqual(self.lire("Du 14 au 16 octobre 2026, à 19h"),
                         [("2026-10-14", "19:00", None),
                          ("2026-10-15", "19:00", None),
                          ("2026-10-16", "19:00", None)])

    def test_plage_longue_un_seul_evenement(self):
        self.assertEqual(self.lire("Du 1 au 30 novembre 2026"),
                         [("2026-11-01", None, "2026-11-30")])

    def test_plage_a_cheval_sur_l_annee(self):
        # L'année écrite est celle de la FIN : le 30 décembre est en 2026.
        self.assertEqual(self.lire("Du 30 décembre au 2 janvier 2027 à 21h"),
                         [("2026-12-30", "21:00", None),
                          ("2026-12-31", "21:00", None),
                          ("2027-01-01", "21:00", None),
                          ("2027-01-02", "21:00", None)])

    def test_jusqu_au_commence_aujourd_hui(self):
        self.assertEqual(self.lire("Jusqu'au 16 novembre 2026"),
                         [("2026-10-01", None, "2026-11-16")])

    def test_sans_annee_mois_passe_annee_suivante(self):
        # Le 15 septembre 2027 est un mercredi : c'est bien l'an prochain.
        self.assertEqual(self.lire("Mercredi 15 septembre à 20h"),
                         [("2027-09-15", "20:00", None)])

    def test_le_jour_de_la_semaine_designe_l_annee(self):
        # BUG-17. Un mardi 15 septembre n'existe pas en 2027 : c'est celui
        # de 2026, déjà passé — et non une soirée fantôme l'an prochain.
        self.assertEqual(self.lire("Mardi 15 septembre à 20h"), [])

    def test_sans_jour_de_la_semaine_quinze_jours_de_grace(self):
        # Passée d'un jour seulement, une date reste de cette année — et
        # elle est écartée —, au lieu de partir à l'an prochain.
        self.assertEqual(self.lire("30 septembre à 19h"), [])

    def test_deux_dates_dont_la_premiere_est_passee(self):
        # « Jeudi 1 octobre et Vendredi 2 octobre », lu le 2 : la première
        # date partait en 2027, la seconde était perdue.
        self.assertEqual(self.lire("Mercredi 30 septembre et Jeudi 1 octobre à 19h"),
                         [("2026-10-01", "19:00", None)])

    def test_deux_dates_une_heure_chacune(self):
        self.assertEqual(self.lire("Samedi 3 octobre et Dimanche 4 octobre samedi à 20h, "
                                   "dimanche à 16h"),
                         [("2026-10-03", "20:00", None), ("2026-10-04", "16:00", None)])

    def test_deux_dates_une_heure_commune(self):
        self.assertEqual(self.lire("Vendredi 16 octobre et Samedi 17 octobre à 20h45"),
                         [("2026-10-16", "20:45", None), ("2026-10-17", "20:45", None)])

    def test_une_nuit_a_cheval_sur_minuit_est_une_seule_soiree(self):
        # « Samedi 24 octobre et Dimanche 25 octobre de 22h à 4h30 » : la
        # Halle Tony Garnier annonce UNE nuit, le 24, jusqu'à 4h30.
        self.assertEqual(self.lire("Samedi 3 octobre et Dimanche 4 octobre de 22h à 4h30"),
                         [("2026-10-03", "22:00", None)])

    def test_deux_journees_de_10h_a_19h_restent_deux(self):
        self.assertEqual(self.lire("Samedi 3 octobre et Dimanche 4 octobre de 10h à 19h"),
                         [("2026-10-03", "10:00", None), ("2026-10-04", "10:00", None)])

    def test_deux_nuits_a_une_semaine_d_ecart_restent_deux(self):
        self.assertEqual(self.lire("Vendredi 2 octobre et Vendredi 9 octobre de 23h à 5h"),
                         [("2026-10-02", "23:00", None), ("2026-10-09", "23:00", None)])

    def test_une_date_citee_plus_loin_n_est_pas_une_seance(self):
        # Seules les dates jointes par « et » ou une virgule font une liste.
        self.assertEqual(self.lire("Mardi 13 octobre 2026 à 20h, report du 6 octobre"),
                         [("2026-10-13", "20:00", None)])

    def test_evenement_passe_ecarte(self):
        self.assertEqual(self.lire("Mardi 1er septembre 2026"), [])

    def test_plage_dont_une_partie_est_passee(self):
        self.assertEqual(self.lire("Du 29 septembre au 2 octobre 2026"),
                         [("2026-10-01", None, None),
                          ("2026-10-02", None, None)])

    def test_illisible(self):
        self.assertEqual(self.lire("Date à préciser"), [])


if __name__ == "__main__":
    unittest.main()
