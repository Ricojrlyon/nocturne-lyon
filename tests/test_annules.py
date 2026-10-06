"""Les événements que leur source dit annulés ne sont pas publiés (BUG-19).
Les titres sont ceux relevés dans les fils publiés de juillet à octobre
2026 ; les pièges aussi."""
import copy
import unittest
from datetime import date, timedelta

import aggregate
from tests.outils import atelier, evenement

J = date(2026, 10, 1)


def annule(titre, sous_titre=None):
    return aggregate._est_annule(evenement("Une salle", titre, J, sous_titre=sous_titre))


class ReperageDesAnnules(unittest.TestCase):

    def test_les_formes_relevees(self):
        for titre in ("(Annulé) Silence Collapse",
                      "ANNULE // [Open Air] Bubble Boom Party avec Reperkusound",
                      "Annulé Formation FORMATION PRO 'MAO & Production Musicale'",
                      "[annulé] AG de la RiV",
                      "Concert de Lune - ANNULÉ",
                      "Concert de Lune (concert annulé)"):
            self.assertTrue(annule(titre), titre)
        self.assertTrue(annule("Morenica + Diámelo", "Concert annulé"))
        # Un titre réduit à l'avis.
        self.assertTrue(annule("Soirée annulée"))

    def test_ce_qui_n_est_pas_une_annulation(self):
        # Une soirée qui a bien lieu, avec une autre artiste - même quand
        # l'annonce du remplacement emprunte la forme d'une annulation.
        self.assertFalse(annule("Almond Butyl - annulé / remplacé par Viviane Cavale"))
        self.assertFalse(annule("Almond Butyl (annulé) - remplacé par Viviane Cavale"))
        for titre in ("Grand reporterre #12 : Rebelles en exil",
                      "Annulation de la dette : conférence",
                      "Les Annulés",
                      "Concert de Lune (complet)",
                      "Concert de Lune (remplace la date annulée du 3 mars)",
                      "Concert de Lune (nouvelle date, après celle annulée du 3 mars)"):
            self.assertFalse(annule(titre), titre)
        self.assertFalse(annule("Tournée d'adieu", "Après une tournée annulée en 2024, "
                                                   "le groupe revient enfin"))


class AnnulesDansLeFil(unittest.TestCase):
    """aggregate.main() en entier, sur de fausses collectes."""

    SALLE = [evenement("Le Sucre", "Soirée n°%d" % i, J + timedelta(days=1 + i),
                       url="https://le-sucre.exemple.org/e/%d" % i) for i in range(30)]
    PB = [evenement("Salle PB", "Spectacle PB %d" % i, J + timedelta(days=1 + i),
                    url="https://www.petit-bulletin.fr/agenda-%d" % i) for i in range(30)]
    VM = [evenement("Lieu VM", "Concert VM %d" % i, J + timedelta(days=1 + i),
                    url="https://agenda.villemorte.fr/event/%d" % i) for i in range(30)]

    @staticmethod
    def copies(evs):
        return lambda: [copy.copy(e) for e in evs]

    def test_annule_par_la_salle_et_republie_tel_quel_par_l_agregateur(self):
        # La salle dit « Annulé », le Petit Bulletin ne le dit pas : la dédup
        # les fond sous le titre de la salle, et le spectacle disparaît.
        jour = J + timedelta(days=3)
        salle = self.SALLE + [evenement("Le Sucre", "Annulé - Nuit Electro Massive", jour,
                                        url="https://le-sucre.exemple.org/annule")]
        pb = self.PB + [evenement("Le Sucre", "Nuit Electro Massive", jour,
                                  url="https://www.petit-bulletin.fr/agenda-nuit")]
        with atelier(J) as a:
            a.veille(self.SALLE + self.PB + self.VM)
            self.assertEqual(a.lancer([("Le Sucre", self.copies(salle))],
                                      [("Petit Bulletin", self.copies(pb), 60),
                                       ("Ville Morte", self.copies(self.VM), 50)]), 0)
            fil = a.publie()
            self.assertIn("[annulés] écarté : Le Sucre", a.journal.getvalue())
        self.assertEqual([e for e in fil["events"] if "Electro Massive" in e["title"]], [])
        self.assertEqual(len(fil["events"]), 90)

    def test_annule_repris_de_la_veille_par_un_garde_fou(self):
        # Ville Morte tombe : ses événements de la veille sont repris, et
        # l'annulé parmi eux ne doit pas revenir.
        vm = self.VM[:-1] + [evenement("Lieu VM", "(annulé) Concert VM 29", J + timedelta(days=30),
                                       url="https://agenda.villemorte.fr/event/29")]
        with atelier(J) as a:
            a.veille(self.SALLE + self.PB + vm)
            self.assertEqual(a.lancer([("Le Sucre", self.copies(self.SALLE))],
                                      [("Petit Bulletin", self.copies(self.PB), 60),
                                       ("Ville Morte", lambda: [], 50)]), 0)
            fil = a.publie()
        titres = [e["title"] for e in fil["events"] if "villemorte" in e["url"]]
        self.assertEqual(len(titres), 29)
        self.assertNotIn("(annulé) Concert VM 29", titres)


if __name__ == "__main__":
    unittest.main()
