"""La page, jouée dans un vrai navigateur sur des données et une date FIGÉES.

Le fil est celui de la collecte de référence (chaine_reference.json.gz),
la date le 1er octobre 2026 à 10 h. Neuf scénarios de visiteur — tout,
ce weekend, semaine pro, une recherche, une famille éteinte, un lieu, un
tag, un arrondissement, un groupe déplié — et après chacun, chaque
journée et chaque carte affichées, comparées à page_reference.json.gz.

C'est le filet de toute retouche d'index.html qui ne doit RIEN changer à
ce que voit le visiteur — l'affichage par morceaux de PERF-1 au premier
chef. Si un changement d'affichage est voulu : python -m tests.regenerer

Sans navigateur de la famille Chrome, le test est sauté, et le dit.
"""
import unittest

from tests import navigateur
from tests.outils import lire_gz

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
            self.fail("l'affichage a changé — " + premier_ecart(attendu, obtenu))


if __name__ == "__main__":
    unittest.main()
