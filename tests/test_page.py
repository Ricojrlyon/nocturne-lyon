"""La page, jouée dans un vrai navigateur sur des données et une date FIGÉES.

Le fil est celui de la collecte de référence (chaine_reference.json.gz),
la date le 1er octobre 2026 à 10 h. Neuf scénarios de visiteur - tout,
ce weekend, semaine pro, une recherche, une famille éteinte, un lieu, un
tag, un arrondissement, un groupe déplié - et après chacun, chaque
journée et chaque carte affichées, comparées à page_reference.json.gz.

C'est le filet de toute retouche d'index.html qui ne doit RIEN changer à
ce que voit le visiteur - l'affichage par morceaux de PERF-1 au premier
chef. Si un changement d'affichage est voulu : python -m tests.regenerer

Sans navigateur de la famille Chrome, le test est sauté, et le dit.
"""
import re
import unittest

from tests import navigateur
from tests.outils import RACINE, lire_gz

GENERE_LE = "2026-10-01T09:00:00+00:00"


def donnees_figees():
    ref = lire_gz("chaine_reference.json.gz")
    evenements = {"generated_at": GENERE_LE, "count": len(ref["publie"]),
                  "events": ref["publie"]}
    return evenements, lire_gz("page_lieux.json.gz")


def premier_ecart(attendu, obtenu):
    """Le premier écart, en clair : scénario, journée, carte."""
    for nom in attendu["scenarios"]:
        a, o = attendu["scenarios"][nom], obtenu["scenarios"].get(nom)
        if o is None:
            return "scénario « %s » absent" % nom
        if a["vide"] != o["vide"]:
            return "« %s » : état vide %r → %r" % (nom, a["vide"], o["vide"])
        if len(a["jours"]) != len(o["jours"]):
            return "« %s » : %d journées → %d" % (nom, len(a["jours"]), len(o["jours"]))
        for ja, jo in zip(a["jours"], o["jours"]):
            if ja[:2] != jo[:2]:
                return "« %s », %s : barre %r → %r" % (nom, ja[0], ja[1], jo[1])
            if ja[2] != jo[2]:
                for i, (ca, co) in enumerate(zip(ja[2], jo[2])):
                    if ca != co:
                        return "« %s », %s, carte %d :\n    %r\n →  %r" % (nom, ja[0], i + 1, ca, co)
                return "« %s », %s : %d cartes → %d" % (nom, ja[0], len(ja[2]), len(jo[2]))
    for cle in ("evenements", "pied", "index", "pastilles"):
        if attendu[cle] != obtenu[cle]:
            return "%s : %r → %r" % (cle, attendu[cle], obtenu[cle])
    return "écart non localisé"


class PageFigee(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        evenements, lieux = donnees_figees()
        cls.releve, cls.probleme = navigateur.releve_ou_probleme(
            evenements=evenements, lieux=lieux, date_figee=True)

    def setUp(self):
        navigateur.sauter_si_indisponible(self, self.probleme)

    def test_le_pilote_a_joue_jusqu_au_bout(self):
        self.assertNotIn("echec", self.releve, self.releve.get("echec"))

    def test_aucune_erreur_dans_la_page(self):
        self.assertEqual(self.releve.get("erreurs_fin", self.releve.get("erreurs")), [])

    def test_meme_affichage_qu_a_la_reference(self):
        attendu = lire_gz("page_reference.json.gz")
        obtenu = {k: v for k, v in self.releve.items() if k != "erreurs"}
        attendu = {k: v for k, v in attendu.items() if k != "erreurs"}
        if obtenu != attendu:
            self.fail("l'affichage a changé - " + premier_ecart(attendu, obtenu))


class PageAvecFondus(unittest.TestCase):
    """Le chemin de la plupart des visiteurs : la page avec ses fondus.

    Le test de référence tourne en « moins d'animations », pour que ses
    relevés ne dépendent pas du chronométrage d'un fondu. Celui-ci joue les
    fondus, et enchaîne les filtres plus vite qu'ils ne s'achèvent : la
    page doit rester sans erreur (BUG-6), et revenir exactement à son
    affichage de départ.
    """

    @classmethod
    def setUpClass(cls):
        evenements, lieux = donnees_figees()
        cls.releve, cls.probleme = navigateur.releve_ou_probleme(
            evenements=evenements, lieux=lieux, date_figee=True,
            scenarios="rafale", animations=True)

    def setUp(self):
        navigateur.sauter_si_indisponible(self, self.probleme)

    def test_filtres_en_rafale_sans_erreur(self):
        self.assertNotIn("echec", self.releve, self.releve.get("echec"))
        self.assertEqual(self.releve["erreurs_fin"], [])

    def test_retour_exact_a_l_affichage_de_depart(self):
        s = self.releve["scenarios"]
        self.assertEqual(s["apres_rafale"], s["tout"])
        self.assertGreater(len(s["tout"]["jours"]), 0)


class PageNavigation(unittest.TestCase):
    """Ce que l'affichage par morceaux (PERF-1) ne doit pas changer.

    La page ne construit d'abord que les premières journées. La barre des
    14 jours doit pourtant amener chacune en haut de l'écran, même pas
    encore construite ; et la recherche du navigateur, Ctrl+F, doit
    trouver tout le fil.
    """

    @classmethod
    def setUpClass(cls):
        evenements, lieux = donnees_figees()
        cls.releve, cls.probleme = navigateur.releve_ou_probleme(
            evenements=evenements, lieux=lieux, date_figee=True,
            scenarios="navigation")

    def setUp(self):
        navigateur.sauter_si_indisponible(self, self.probleme)
        self.assertNotIn("echec", self.releve, self.releve.get("echec"))

    def test_la_barre_des_jours_amene_au_bon_jour(self):
        # Le 2, le 7 et le 14 octobre ; 8 px : la marge de la journée.
        self.assertEqual(self.releve["pastilles_suivies"],
                         [["day-2026-10-02", 8], ["day-2026-10-07", 8],
                          ["day-2026-10-14", 8]])

    def test_ctrl_f_construit_tout_le_fil(self):
        total = len(self.releve["scenarios"]["tout"]["jours"])
        self.assertLess(self.releve["journees_avant_ctrl_f"], total)
        self.assertEqual(self.releve["journees_apres_ctrl_f"], total)
        self.assertEqual(self.releve["erreurs_fin"], [])

    def test_une_famille_eteinte_au_milieu_garde_sa_journee(self):
        verifier_famille_au_milieu(self, self.releve["familles_suivies"])

    def test_aujourd_hui_signale_echap_et_boutons_de_24_px(self):
        # Audit n° 18, à la date figée du 1er octobre 2026 : la case du
        # calendrier et la première pastille portent aria-current="date" ;
        # Échap ferme le calendrier et rend la main au bouton « date » ;
        # les boutons de famille font 24 px de haut.
        self.assertEqual(self.releve["aujourd_hui"], ["2026-10-01", "pastille 0"])
        self.assertEqual(self.releve["apres_echap"], [False, "dateBtn"])
        self.assertEqual(self.releve["boutons_de_famille"], [24])

    def test_le_bouton_date_dit_la_periode_et_le_filtre_vide_en_sort(self):
        # Audit n° 20 : la période choisie, en court à l'écran et en toutes
        # lettres pour un lecteur d'écran, puis « date → » une fois
        # effacée ; un filtre sans résultat le dit, et son bouton « tout
        # effacer » ramène tout le fil.
        self.assertEqual(self.releve["bouton_date"], [
            ["12 → 18 oct.", "dates choisies : du 12 octobre au 18 octobre"],
            ["28 oct. → 1 nov.", "dates choisies : du 28 octobre au 1er novembre"],
            ["15 oct.", "date choisie : le 15 octobre"],
            ["date →", None]])
        self.assertEqual(self.releve["filtre_vide"], ["aucun événement pour ces filtres", "tout effacer"])
        self.assertEqual(self.releve["apres_tout_effacer"], [True, ""])

    def test_chaque_police_prechargee_n_est_telechargee_qu_une_fois(self):
        # Audit n° 17 : une fois, et par le préchargement. Un « crossorigin »
        # oublié la ferait télécharger une seconde fois, par polices.css.
        page = (RACINE / "index.html").read_text(encoding="utf-8")
        prechargees = re.findall(r'<link rel="preload" href="fonts/([^"]+)"', page)
        self.assertGreater(len(prechargees), 0)
        for police in prechargees:
            self.assertEqual([qui for nom, qui in self.releve["polices_chargees"] if nom == police],
                             ["link"], police)


def verifier_famille_au_milieu(cas, suivi):
    """BUG-8 : une famille éteinte ou rallumée dans la barre d'une journée,
    au milieu de la page, laisse le visiteur sur CETTE journée. Le haut de
    la journée, avant → après, dans les trois positions du pilote."""
    # À un pixel près : une journée peut commencer entre deux pixels, et le
    # défilement, lui, avance de pixel en pixel.
    (a1, p1), (a2, p2), (a3, p3) = suivi
    cas.assertAlmostEqual(a1, 8, delta=1)   # barre à sa place…
    cas.assertAlmostEqual(p1, a1, delta=1)  # … rien ne bouge
    cas.assertLess(a2, 0)                   # barre collée, visiteur dans la journée :
    cas.assertAlmostEqual(p2, 8, delta=1)   # la journée revient à son début
    cas.assertGreater(a3, 8)                # la veille à l'écran au-dessus :
    cas.assertAlmostEqual(p3, a3, delta=1)  # la journée ne bouge pas


class PastillesAvecFondus(unittest.TestCase):
    """BUG-7 : avec les fondus, la pastille d'un jour proche fait défiler la
    page en douceur, celle d'un jour lointain la fait sauter, par-dessus des
    journées jamais affichées. Dans les deux cas, la page doit s'arrêter sur
    SA journée - le défilement en douceur s'arrêtait quelques jours plus
    tôt."""

    @classmethod
    def setUpClass(cls):
        evenements, lieux = donnees_figees()
        cls.releve, cls.probleme = navigateur.releve_ou_probleme(
            evenements=evenements, lieux=lieux, date_figee=True,
            scenarios="pastilles_fondues", animations=True)

    def setUp(self):
        navigateur.sauter_si_indisponible(self, self.probleme)
        self.assertNotIn("echec", self.releve, self.releve.get("echec"))

    def test_la_pastille_amene_au_bon_jour_meme_en_douceur(self):
        # Le 1er octobre, proche : en douceur ; le 7 et le 14, lointains :
        # d'un saut. 8 px : la marge de la journée.
        self.assertEqual(self.releve["pastilles_suivies"],
                         [["day-2026-10-01", 8], ["day-2026-10-07", 8],
                          ["day-2026-10-14", 8]])
        self.assertEqual(self.releve["erreurs_fin"], [])

    def test_une_famille_eteinte_au_milieu_garde_sa_journee(self):
        verifier_famille_au_milieu(self, self.releve["familles_suivies"])


if __name__ == "__main__":
    unittest.main()
