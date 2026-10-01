"""Le dédoublonnage, règle par règle : ce qui doit fusionner, et surtout ce
qui ne le doit pas. Priorités : salle 100, Petit Bulletin 60, Ville Morte 50."""
import unittest
from datetime import date, timedelta

from scrapers.dedup import canonical_venue_name, deduplicate
from tests.outils import evenement

J = date(2026, 10, 15)
PB = "https://www.petit-bulletin.fr/agenda/%d"
VM = "https://agenda.villemorte.fr/event/%d"


def titres(evs):
    return sorted((e.venue, e.date_start, e.time, e.title) for e in evs)


class PremierePasse(unittest.TestCase):
    """Même lieu, même jour, titres proches (≥ 0,7)."""

    def test_salle_et_agregateur_fusionnent_la_salle_gagne(self):
        salle = evenement("Le Périscope", "Soirée Funk", J, heure=None)
        pb = evenement("Le Périscope", "Soirée Funk au Périscope", J,
                       heure="21:00", url=PB % 1)
        out = deduplicate([(salle, 100), (pb, 60)])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].title, "Soirée Funk")        # l'identité de la salle
        self.assertEqual(out[0].time, "21:00")               # l'heure de l'agrégateur

    def test_deux_horaires_d_une_meme_source_font_deux_seances(self):
        a = evenement("Célestins", "Atelier théâtre", J, heure="14:00")
        b = evenement("Célestins", "Atelier théâtre", J, heure="15:00")
        self.assertEqual(len(deduplicate([(a, 100), (b, 100)])), 2)

    def test_deux_horaires_de_deux_sources_fusionnent(self):
        salle = evenement("Le Sucre", "Nuit Kompakt", J, heure="23:00")
        pb = evenement("Le Sucre", "Nuit Kompakt", J, heure="23:30", url=PB % 2)
        out = deduplicate([(salle, 100), (pb, 60)])
        self.assertEqual([(e.time, e.url) for e in out], [("23:00", salle.url)])

    def test_l_exposition_n_absorbe_pas_sa_conference(self):
        expo = evenement("Musée des Beaux-Arts", "Musée sentimental", J,
                         heure=None, fin=J + timedelta(days=180), categorie="expo")
        conf = evenement("Musée des Beaux-Arts", "Conférence : Musée sentimental",
                         J, heure="15:00", categorie="conference")
        self.assertEqual(len(deduplicate([(expo, 100), (conf, 100)])), 2)

    def test_une_plage_croise_les_dates_de_chacun_de_ses_jours(self):
        plage = evenement("La Commune", "Festival Jazz à la Commune", J,
                          heure=None, fin=J + timedelta(days=4))
        jour3 = evenement("La Commune", "Festival Jazz à la Commune",
                          J + timedelta(days=2), heure=None, url=PB % 3)
        out = deduplicate([(plage, 100), (jour3, 60)])
        self.assertEqual([e.url for e in out], [plage.url])


class DeuxiemePasse(unittest.TestCase):
    """Même jour, lieux différents, titres très proches (≥ 0,85)."""

    def test_meme_soiree_sous_deux_graphies_de_lieu(self):
        pb = evenement("Toï Toï le Zinc", "FeFan release party", J, url=PB % 4)
        vm = evenement("Dans toute la ville", "FeFan release party", J, url=VM % 4)
        out = deduplicate([(pb, 60), (vm, 50)])
        self.assertEqual([e.url for e in out], [pb.url])

    def test_titres_generiques_jamais_fusionnes_entre_lieux(self):
        a = evenement("Salle A", "Concert", J)
        b = evenement("Salle B", "Concert", J, url=PB % 5)
        self.assertEqual(len(deduplicate([(a, 100), (b, 60)])), 2)


class TroisiemePasse(unittest.TestCase):
    """Même lieu, même jour, titres trop éloignés pour le flou."""

    def test_appariement_a_la_minute_pres(self):
        # Le cas mesuré le 2026-09-20 à la Chapelle de la Trinité.
        lieu = "Chapelle de la Trinité"
        salle = [evenement(lieu, "Ovni baroque", J, heure="17:00"),
                 evenement(lieu, "Gaspard", J, heure="19:00")]
        pb = [evenement(lieu, "Ovni Sonore", J, heure="17:00", url=PB % 6),
              evenement(lieu, "Fanny Meteier", J, heure="19:00", url=PB % 7),
              evenement(lieu, "Atelier yoga", J, heure="10:00", url=PB % 8)]
        out = deduplicate([(e, 100) for e in salle] + [(e, 60) for e in pb])
        self.assertEqual(titres(out), titres(salle + pb[2:]))

    def test_effectifs_egaux_apparies_dans_l_ordre(self):
        # Le Transbordeur : deux affiches sans heure, deux noms de soirée.
        # Des affiches réelles : deux titres inventés trop semblables
        # (« ARTIST1 + ARTIST2 », « ARTIST3 + ARTIST4 ») fusionneraient dès
        # la première passe, et à raison.
        lieu = "Le Transbordeur"
        salle = [evenement(lieu, "Durden + Slmvx", J, heure=None),
                 evenement(lieu, "Meryl + Lawskie", J, heure=None)]
        pb = [evenement(lieu, "Transcendia x Transbo open-air", J, heure="18:00",
                        url=PB % 9),
              evenement(lieu, "23:59 X Organik", J, heure="23:59", url=PB % 10)]
        out = deduplicate([(e, 100) for e in salle] + [(e, 60) for e in pb])
        self.assertEqual(sorted((e.title, e.time) for e in out),
                         [("Durden + Slmvx", "18:00"), ("Meryl + Lawskie", "23:59")])

    def test_plus_de_quatre_heures_d_ecart_on_ne_touche_a_rien(self):
        lieu = "Radiant-Bellevue"
        salle = evenement(lieu, "Spectacle jeune public", J, heure="15:00")
        pb = evenement(lieu, "Rock en scène", J, heure="21:00", url=PB % 11)
        self.assertEqual(len(deduplicate([(salle, 100), (pb, 60)])), 2)

    def test_deux_agregateurs_sur_un_lieu_non_scrappe(self):
        lieu = "Théâtre de l'Élysée"
        pb = evenement(lieu, "Grand-merde", J, heure="20:00", url=PB % 12)
        vm = evenement(lieu, "GRAND-ME(R)DE / CIE Bleuir Le Cœur", J,
                       heure="20:00", url=VM % 12)
        out = deduplicate([(pb, 60), (vm, 50)])
        self.assertEqual([e.url for e in out], [pb.url])

    def test_un_reste_inegal_n_est_pas_duplique(self):
        # Le défaut corrigé le 2026-09-20 : après les paires à la minute,
        # le repli rendait le groupe ENTIER, paires comprises — doublons.
        lieu = "HEAT"
        salle = [evenement(lieu, "Afterwork", J, heure="17:00"),
                 evenement(lieu, "Live set", J, heure="19:00"),
                 evenement(lieu, "Brunch", J, heure=None)]
        pb = [evenement(lieu, "After work !", J, heure="17:00", url=PB % 13),
              evenement(lieu, "Clôture", J, heure="22:00", url=PB % 14)]
        out = deduplicate([(e, 100) for e in salle] + [(e, 60) for e in pb])
        urls = [e.url for e in out]
        self.assertEqual(len(urls), len(set(urls)))         # aucun doublon
        self.assertEqual(sorted(urls), sorted(e.url for e in salle + pb[1:]))


class GraphiesDesLieux(unittest.TestCase):

    def test_noms_canoniques(self):
        self.assertEqual(canonical_venue_name("sonic"), "Le Sonic")
        self.assertEqual(canonical_venue_name("LE PERISCOPE"), "Le Périscope")
        self.assertEqual(canonical_venue_name("Inconnu Random"), "Inconnu Random")

    def test_une_seule_graphie_elue_la_plus_accentuee(self):
        evs = [evenement("Théâtre de la Mouche", "Pièce %d" % i, J + timedelta(days=i),
                         url=VM % (20 + i)) for i in range(3)]
        evs.append(evenement("Théâtre de la Mouché", "Autre pièce", J, url=PB % 30))
        out = deduplicate([(e, 50) for e in evs[:3]] + [(evs[3], 60)])
        self.assertEqual({e.venue for e in out}, {"Théâtre de la Mouché"})


if __name__ == "__main__":
    unittest.main()
