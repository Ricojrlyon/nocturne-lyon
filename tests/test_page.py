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
import math
import re
import unittest
from datetime import datetime, timedelta

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

    def test_le_service_hors_ligne_s_installe(self):
        # L'application installable : sw.js s'active, et couvre tout le site.
        self.assertEqual(self.releve["service_hors_ligne"], ["activated", "/"])

    def test_l_adresse_suit_les_filtres_et_partager_la_copie(self):
        self.assertEqual(self.releve["adresses"],
                         ["", "?quand=weekend", "?quand=weekend&arr=7e", ""])
        self.assertEqual(self.releve["partage"], [True, True, True])

    def test_plus_agenda_donne_le_fichier_de_la_soiree(self):
        # Nouveauté n° 2, à la date figée du 1er octobre 2026.
        a = self.releve["agenda"]
        self.assertRegex(a["nom"], r"^nocturne-[a-z0-9-]+[.]ics$")
        self.assertEqual(a["type"], "text/calendar;charset=utf-8")
        self.assertEqual((a["premiere"], a["derniere"]), ("BEGIN:VCALENDAR", "END:VCALENDAR"))
        self.assertRegex(a["debut"], r"^DTSTART(;TZID=Europe/Paris:20261001T[0-9]{6}|;VALUE=DATE:20261001)$")
        self.assertEqual([a["titre_juste"], a["lien_juste"], a["lignes_courtes"], a["echappement"]],
                         [True, True, True, True])
        self.assertIs(a["lien_ouvert"], False)

    def test_le_menu_de_l_agenda(self):
        # Trois choix ; le premier prend la main ; « .ics » ferme le menu.
        # Échap le ferme et rend la main à l'icône ; un clic ailleurs aussi.
        self.assertEqual(self.releve["menu_agenda"],
                         [True, "true", ["google", "outlook", "ics"], True, True, "false",
                          True, True, True])

    def test_google_agenda_et_outlook_s_ouvrent_la_soiree_remplie(self):
        a = self.releve["agenda_en_ligne"]
        jour = a["date"].replace("-", "")
        if a["heure"]:
            debut = datetime.strptime(a["date"] + " " + a["heure"], "%Y-%m-%d %H:%M")
            fin = debut + timedelta(hours=2)
            dates = debut.strftime("%Y%m%dT%H%M%S") + "/" + fin.strftime("%Y%m%dT%H%M%S")
            ctz = "Europe/Paris"
            debut_o = debut.strftime("%Y-%m-%dT%H:%M:%S") + "+02:00"
            fin_o = fin.strftime("%Y-%m-%dT%H:%M:%S") + "+02:00"
        else:
            dates, ctz = jour + "/" + jour[:6] + "02", None
            debut_o, fin_o = a["date"], "2026-10-02"
        self.assertEqual(a["google"], ["https://calendar.google.com/calendar/render", "TEMPLATE", True,
                                       dates, ctz, True, True, "_blank"])
        self.assertEqual(a["outlook"], ["https://outlook.live.com/calendar/0/action/compose", True,
                                        debut_o, fin_o, True, True, "_blank"])

    def test_l_heure_d_ete_de_paris(self):
        # Dernier dimanche de mars (29) et d'octobre (25), en 2026.
        self.assertEqual(self.releve["decalages"],
                         ["+01:00", "+02:00", "+02:00", "+02:00", "+01:00", "+01:00"])

    def test_ecouter_ouvre_youtube_pour_la_musique(self):
        # Nouveauté n° 5.
        self.assertEqual(self.releve["ecouter"],
                         {"juste": True, "carte_ouverte": False, "seulement_musique": True})

    def test_le_bouton_des_filtres_d_une_journee(self):
        self.assertEqual(self.releve["filtres_epingles"],
                         [True, "true", 0, True, True, 8, "false", False, "0px", True, "static"])

    def test_les_boutons_de_carte_sont_des_icones_a_droite_de_l_heure(self):
        self.assertEqual(self.releve["boutons_de_carte"],
                         ["", "", True, True, "ajouter à mon agenda", "écouter sur YouTube",
                          True, True, True])

    def test_favoris_et_mes_salles(self):
        # Nouveauté n° 3 : l'étoile garde l'événement sans ouvrir la carte ;
        # « ★ favoris » ne montre plus que lui, puis aussi la salle suivie,
        # remontée en tête de liste ; « tout effacer » éteint le bouton.
        f = self.releve["favoris"]
        self.assertEqual((f["presse"], f["lien_ouvert"], f["memoire"]), ("true", False, 1))
        self.assertTrue(f["seul"])
        self.assertEqual(f["apres_une_periode"], "true")
        self.assertEqual(f["salles"], ["Le Sucre"])
        self.assertIs(f["case_cochee"], False)
        self.assertEqual(f["en_tete"], "Le Sucre")
        self.assertEqual(f["ligne"], "★ mes salles : 1 · gardées sur ce téléphone")
        self.assertTrue(f["avec_la_salle"])
        self.assertEqual(f["apres_effacer"], ["false", True])

    def test_l_impro_est_du_theatre(self):
        # « Impro », catégorie du Petit Bulletin pour le théâtre
        # d'improvisation, était rangée en jazz, donc en musique.
        self.assertEqual(self.releve["rangement"],
                         [["Impro", "theatre", "scene"], ["Jazz", "jazz", "musique"],
                          ["Théâtre", "theatre", "scene"]])

    def test_chaque_police_prechargee_n_est_telechargee_qu_une_fois(self):
        # Audit n° 17 : une fois, et par le préchargement. Un « crossorigin »
        # oublié la ferait télécharger une seconde fois, par polices.css.
        page = (RACINE / "index.html").read_text(encoding="utf-8")
        prechargees = re.findall(r'<link rel="preload" href="fonts/([^"]+)"', page)
        self.assertGreater(len(prechargees), 0)
        for police in prechargees:
            self.assertEqual([qui for nom, qui in self.releve["polices_chargees"] if nom == police],
                             ["link"], police)


class PageDepuisUnLien(unittest.TestCase):
    """Un lien partagé rouvre la même vue : la page ouverte avec
    « ?quand=weekend » montre exactement les cartes qu'on obtient en
    touchant « ce weekend », et ainsi de suite."""
    CAS = {"ce_weekend": "?quand=weekend", "recherche_comedy": "?q=comedy",
           "lieu_le_sucre": "?lieu=Le+Sucre", "tag_theatre": "?tag=theatre"}

    @classmethod
    def setUpClass(cls):
        evenements, lieux = donnees_figees()
        cls.releves = {nom: navigateur.releve_ou_probleme(
            evenements=evenements, lieux=lieux, date_figee=True, scenarios="tout", requete=requete)
            for nom, requete in cls.CAS.items()}

    def test_chaque_lien_rouvre_la_meme_vue(self):
        attendu = lire_gz("page_reference.json.gz")
        for nom, (releve, probleme) in self.releves.items():
            navigateur.sauter_si_indisponible(self, probleme)
            self.assertNotIn("echec", releve, nom)
            self.assertEqual(releve["erreurs_fin"], [], nom)
            self.assertEqual(releve["scenarios"]["tout"], attendu["scenarios"][nom], nom)


class PageNouveau(unittest.TestCase):
    """« nouveau » depuis ta dernière visite (nouveauté n° 4) : aucun
    repère à la première visite ; à la suivante, exactement les
    événements qui n'avaient pas été vus."""

    @classmethod
    def setUpClass(cls):
        evenements, lieux = donnees_figees()
        cls.releve, cls.probleme = navigateur.releve_ou_probleme(
            evenements=evenements, lieux=lieux, date_figee=True, scenarios="nouveau")

    def setUp(self):
        navigateur.sauter_si_indisponible(self, self.probleme)
        self.assertNotIn("echec", self.releve, self.releve.get("echec"))

    def test_seul_ce_qui_n_avait_pas_ete_vu_est_nouveau(self):
        n = self.releve["nouveau"]
        self.assertEqual(n["premiere_visite"], 0)
        self.assertGreater(n["total_vus"], 2000)
        self.assertGreaterEqual(len(n["attendues"]), 1)
        self.assertEqual(n["marquees"], n["attendues"])
        self.assertEqual(n["apres_rechargement"], n["attendues"])
        self.assertEqual(self.releve["erreurs_fin"], [])


class PageCarte(unittest.TestCase):
    """La vue carte (nouveauté n° 6) : les lieux d'une journée sur un plan,
    chacun avec son nombre d'événements, et exactement ceux de la liste ;
    sa fiche, ses gestes, la barre des jours, et le lien qui la rouvre."""

    @classmethod
    def setUpClass(cls):
        evenements, lieux = donnees_figees()
        cls.releve, cls.probleme = navigateur.releve_ou_probleme(
            evenements=evenements, lieux=lieux, date_figee=True, scenarios="carte")

    def setUp(self):
        navigateur.sauter_si_indisponible(self, self.probleme)
        self.assertNotIn("echec", self.releve, self.releve.get("echec"))
        self.c = self.releve["carte"]

    def test_sans_erreur(self):
        self.assertEqual(self.releve["erreurs_premier_passage"], [])
        self.assertEqual(self.releve["erreurs_fin"], [])

    def test_l_interrupteur_et_l_adresse(self):
        self.assertEqual(self.c["adresse"], "?vue=carte")
        self.assertEqual(self.c["interrupteur"], ["false", "true"])
        self.assertEqual(self.c["journees"], 0)
        self.assertTrue(self.c["apres_effacer"])
        self.assertEqual(self.c["retour"], ["", True, 0, False])

    def test_les_evenements_du_soir_sont_ceux_de_la_liste(self):
        # Pastilles et « pas sur la carte » comptent, à eux tous, les
        # événements de la journée affichée par la liste.
        n = self.c["compte_de_la_liste"]
        self.assertGreater(n, 10)
        self.assertEqual(self.c["total"], n)
        self.assertEqual(self.c["titre"], "ce soir · jeudi 1 octobre")
        self.assertRegex(self.c["compte"], r"^%d événements · [0-9]+ lieux$" % n)
        self.assertGreater(self.c["pastilles"], 5)

    def test_le_plan_cadre_les_lieux_et_les_noms_ne_se_chevauchent_pas(self):
        self.assertTrue(self.c["dans_le_cadre"])
        self.assertGreater(self.c["noms"], 3)
        self.assertTrue(self.c["sans_chevauchement"])

    def test_les_tuiles_d_openstreetmap_couvrent_le_cadre(self):
        self.assertEqual(self.c["tuiles"], [True, True, True])
        # La projection de Mercator, refaite ici : la place Bellecour au
        # zoom 12 tombe dans la tuile 2102 / 1461.
        lat, lon, n = 45.7578, 4.832, 256 * 2 ** 12
        s = math.sin(math.radians(lat))
        attendu = [math.floor((lon + 180) / 360 * n),
                   math.floor((0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n)]
        self.assertEqual(self.c["projection"], attendu)
        self.assertEqual([attendu[0] // 256, attendu[1] // 256], [2102, 1461])

    def test_la_fiche_d_un_lieu(self):
        self.assertEqual(self.c["fiche"], [True] * 8)
        self.assertEqual(self.c["apres_echap"], [True, True, False])

    def test_les_gestes(self):
        self.assertEqual(self.c["boutons_de_zoom"], [1, 0])
        sans_ctrl, avec_ctrl, ecart = self.c["molette"]
        self.assertEqual((sans_ctrl, avec_ctrl), (0, 1))
        self.assertLessEqual(ecart, 1)
        self.assertEqual(self.c["glisse"], [[0, 0], [60, 30]])

    def test_le_zoom_est_fluide(self):
        # Ctrl + molette et deux doigts : le plan suit le geste, agrandi tel
        # quel, puis est reconstruit ; le fondu d'un bouton aussi.
        self.assertEqual(self.c["molette_en_continu"], [[True, True], False])
        pendant, apres = self.c["pince"]
        self.assertEqual(pendant, [True] * 5)
        self.assertEqual(apres, [1, 400, 200, False])
        self.assertEqual(self.c["fondu"], [[True, True], 1, False])
        self.assertEqual(self.c["ancien_fond"], [2, 1])
        self.assertTrue(self.c["clavier_apres_glisse"])
        self.assertEqual(self.c["fleche"], 80)
        self.assertIsNotNone(self.c["groupe"])
        self.assertEqual(self.c["groupe"], [2, True])

    def test_une_famille_eteinte_sur_le_plan(self):
        self.assertEqual(self.c["famille"], ["false", True, True])

    def test_la_barre_des_jours_et_les_fleches(self):
        self.assertEqual(self.c["pastille"], ["samedi 3 octobre", "?vue=carte&jour=2026-10-03",
                                              ["2026-10-03"]])
        self.assertEqual(self.c["suivant"], "dimanche 4 octobre")
        self.assertEqual(self.c["couleurs_des_jours"], ["rgb(231, 223, 190)", "rgba(212, 165, 116, 0.09)"])
        self.assertEqual(self.c["precedent"], "samedi 3 octobre")

    def test_un_lien_rouvre_la_carte_du_jour(self):
        self.assertEqual(self.releve["carte_par_lien"],
                         {"adresse": "?vue=carte&jour=2026-10-03", "titre": "samedi 3 octobre",
                          "journees": 0, "interrupteur": ["false", "true"]})


class PageHorsLigne(unittest.TestCase):
    """L'application installée s'ouvre sans réseau : la page, ses polices
    et le dernier programme viennent du téléphone, et l'affichage est le
    même qu'en ligne, carte pour carte."""

    @classmethod
    def setUpClass(cls):
        evenements, lieux = donnees_figees()
        cls.releve, cls.probleme = navigateur.releve_ou_probleme(
            evenements=evenements, lieux=lieux, date_figee=True, scenarios="hors_ligne",
            delai=120)

    def setUp(self):
        navigateur.sauter_si_indisponible(self, self.probleme)
        self.assertNotIn("echec", self.releve, self.releve.get("echec"))

    def test_le_programme_s_affiche_sans_reseau(self):
        self.assertEqual(self.releve["hors_ligne"], {"servie_par_le_telephone": True})
        self.assertEqual(self.releve["erreurs_fin"], [])
        attendu = lire_gz("page_reference.json.gz")
        self.assertEqual(self.releve["evenements"], attendu["evenements"])
        self.assertEqual(self.releve["scenarios"]["tout"], attendu["scenarios"]["tout"])


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
