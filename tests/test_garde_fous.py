"""Les garde-fous d'aggregate.main(), rejoués en entier sur de fausses
collectes : reprise d'une salle ou d'un agrégateur tombé (BUG-1), contrôle
du volume total (BUG-2), échec complet. Tout s'écrit dans un dossier
temporaire, à une date figée."""
import copy
import unittest
from datetime import date, timedelta

from tests.outils import atelier, evenement

J = date(2026, 10, 1)
SALLES = ["Le Sucre", "HEAT", "La Commune", "TNG", "Célestins"]


def programme(salle, n=30, debut=1):
    return [evenement(salle, "Soirée %s n°%d" % (salle, i), J + timedelta(days=debut + i % 50),
                      url="https://%s.exemple.org/e/%d" % (salle.lower().replace(" ", ""), i))
            for i in range(n)]


DIRECT = {s: programme(s) for s in SALLES}
PB = [evenement("Salle PB %d" % (i % 12), "Spectacle PB %d" % i, J + timedelta(days=1 + i % 50),
                url="https://www.petit-bulletin.fr/agenda-%d" % i) for i in range(60)]
VM = [evenement("Lieu VM %d" % (i % 6), "Concert VM %d" % i, J + timedelta(days=1 + i % 50),
                url="https://agenda.villemorte.fr/event/%d" % i) for i in range(30)]
VEILLE = [e for s in SALLES for e in DIRECT[s]] + PB + VM


def copies(evs):
    return lambda: [copy.copy(e) for e in evs]


def panne():
    raise RuntimeError("502 Proxy Error")


def normales(**remplace):
    return [(s, remplace.get(s, copies(DIRECT[s]))) for s in SALLES]


def agregateurs(pb=None, vm=None):
    return [("Petit Bulletin", pb or copies(PB), 60), ("Ville Morte", vm or copies(VM), 50)]


def compte(fil, hote):
    return sum(1 for e in fil["events"] if hote in e["url"])


class GardeFous(unittest.TestCase):

    def test_jour_normal(self):
        with atelier(J) as a:
            a.veille(VEILLE)
            self.assertEqual(a.lancer(normales(), agregateurs()), 0)
            fil = a.publie()
        self.assertEqual(len(fil["events"]), len(VEILLE))
        self.assertNotIn("reprises", fil)

    def test_le_fil_dit_combien_de_sources_le_site_lit(self):
        # BUG-9 : le pied de page affiche ce nombre. Il suit les listes de
        # collecteurs — 5 salles et 2 agendas ici —, et compte aussi une
        # salle tombée ce jour-là : le site la lit toujours.
        for salles in (normales(), normales(HEAT=panne)):
            with atelier(J) as a:
                a.veille(VEILLE)
                self.assertEqual(a.lancer(salles, agregateurs()), 0)
                self.assertEqual(a.publie()["sources"], len(SALLES) + 2)

    def test_salle_tombee_reprise_de_la_veille(self):
        with atelier(J) as a:
            a.veille(VEILLE)
            self.assertEqual(a.lancer(normales(HEAT=lambda: []), agregateurs()), 0)
            fil = a.publie()
        self.assertEqual(sum(1 for e in fil["events"] if e["venue"] == "HEAT"), 30)
        self.assertEqual(fil["reprises"], {"HEAT": J.isoformat()})

    def test_salle_tombee_depuis_sept_jours_n_est_plus_reprise(self):
        with atelier(J) as a:
            a.veille(VEILLE, reprises={"HEAT": (J - timedelta(days=7)).isoformat()})
            a.lancer(normales(HEAT=lambda: []), agregateurs())
            fil = a.publie()
            journal = a.journal.getvalue()
        self.assertEqual(sum(1 for e in fil["events"] if e["venue"] == "HEAT"), 0)
        self.assertIn("PLUS reprise", journal)

    def test_agregateur_tombe_repris_de_la_veille(self):
        with atelier(J) as a:
            a.veille(VEILLE)
            self.assertEqual(a.lancer(normales(), agregateurs(vm=panne)), 0)
            fil = a.publie()
        self.assertEqual(compte(fil, "villemorte"), 30)
        self.assertEqual(fil["reprises"], {"agrégateur:Ville Morte": J.isoformat()})

    def test_repris_de_l_agregateur_et_publie_par_la_salle_une_seule_carte(self):
        soir = J + timedelta(days=7)
        vu_par_pb = evenement("Le Sucre", "Nuit Kompakt avec Superpitcher", soir,
                              heure="23:00", url="https://www.petit-bulletin.fr/agenda-9999")
        vu_par_la_salle = evenement("Le Sucre", "Nuit Kompakt avec Superpitcher", soir,
                                    heure="23:00", url="https://lesucre.exemple.org/kompakt")
        with atelier(J) as a:
            a.veille(VEILLE + [vu_par_pb])
            a.lancer(normales(**{"Le Sucre": copies(DIRECT["Le Sucre"] + [vu_par_la_salle])}),
                     agregateurs(pb=lambda: []))
            fil = a.publie()
        ce_soir = [e for e in fil["events"] if e["venue"] == "Le Sucre"
                   and e["date_start"] == soir.isoformat() and e["time"] == "23:00"]
        self.assertEqual([e["url"] for e in ce_soir], [vu_par_la_salle.url])

    def test_baisse_generale_non_publiee(self):
        moitie = {s: copies(DIRECT[s][:18]) for s in SALLES}       # -40 % partout
        with atelier(J) as a:
            a.veille(VEILLE)
            avant = a.fichier.read_bytes()
            code = a.lancer(normales(**moitie), agregateurs(pb=copies(PB[:36]),
                                                             vm=copies(VM[:18])))
            self.assertEqual(code, 1)
            self.assertEqual(a.fichier.read_bytes(), avant)         # fichier intact

    def test_baisse_generale_publiee_quand_le_site_est_fige(self):
        moitie = {s: copies(DIRECT[s][:18]) for s in SALLES}
        with atelier(J) as a:
            a.veille(VEILLE, age_jours=5)
            code = a.lancer(normales(**moitie), agregateurs(pb=copies(PB[:36]),
                                                             vm=copies(VM[:18])))
            self.assertEqual(code, 0)
            self.assertEqual(len(a.publie()["events"]), 18 * 5 + 36 + 18)

    def test_forcage_publie_ce_qui_a_ete_scrappe(self):
        with atelier(J) as a:
            a.veille(VEILLE)
            code = a.lancer(normales(HEAT=lambda: []), agregateurs(vm=panne), forcer=True)
            fil = a.publie()
        self.assertEqual(code, 0)
        self.assertEqual(sum(1 for e in fil["events"] if e["venue"] == "HEAT"), 0)
        self.assertNotIn("reprises", fil)

    def test_toutes_les_sources_en_echec(self):
        with atelier(J) as a:
            a.veille(VEILLE)
            avant = a.fichier.read_bytes()
            code = a.lancer([(s, panne) for s in SALLES], agregateurs(pb=panne, vm=panne))
            self.assertEqual(code, 1)
            self.assertEqual(a.fichier.read_bytes(), avant)


if __name__ == "__main__":
    unittest.main()
