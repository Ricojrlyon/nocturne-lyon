"""La chaîne de publication ENTIÈRE, rejouée sur une collecte réelle.

tests/donnees/chaine_reference.json.gz garde ce que les 35 collecteurs et
les 2 agrégateurs ont rendu le 1er octobre 2026 - 3 395 événements bruts -
et le fil qu'aggregate.main() en a publié : 2 985 événements. Le test
rejoue la collecte, à la même date figée, et exige le même fil, au
caractère près : ordre, fusions du dédoublonnage, catégories comblées,
graphies des lieux.

C'est le filet de toute retouche d'aggregate.py ou de scrapers/dedup.py
qui ne doit RIEN changer au résultat. Si un changement est voulu, la
référence se recalcule : python -m tests.regenerer
"""
import unittest
from datetime import date

from scrapers.base import Event
from tests.outils import atelier, lire_gz


def collecte_figee(reference):
    """Les collecteurs de la référence, rejoués à l'identique."""
    def faux(c):
        if "erreur" in c:
            def fetch():
                raise RuntimeError(c["erreur"])
        else:
            def fetch():
                return [Event(**d) for d in c["evenements"]]
        return fetch

    salles = [(c["nom"], faux(c)) for c in reference["collecteurs"]
              if c["genre"] == "salle"]
    agregateurs = [(c["nom"], faux(c), c["priorite"])
                   for c in reference["collecteurs"] if c["genre"] == "agregateur"]
    return salles, agregateurs


def rejouer(reference):
    """(code de sortie, fil publié) d'aggregate.main() sur la référence."""
    salles, agregateurs = collecte_figee(reference)
    with atelier(date.fromisoformat(reference["date"])) as a:
        code = a.lancer(salles, agregateurs)
        return code, a.publie()["events"]


def ecarts(attendu, obtenu, n=5):
    """Les premiers écarts, en clair, plutôt qu'un diff de trois mille lignes."""
    cle = lambda e: (e["date_start"], e.get("time") or "", e["venue"], e["title"])
    a, o = {cle(e): e for e in attendu}, {cle(e): e for e in obtenu}
    lignes = ["%d événements attendus, %d obtenus" % (len(attendu), len(obtenu))]
    for k in [k for k in a if k not in o][:n]:
        lignes.append("  disparu  : %s" % (k,))
    for k in [k for k in o if k not in a][:n]:
        lignes.append("  apparu   : %s" % (k,))
    for k in [k for k in a if k in o and a[k] != o[k]][:n]:
        champs = sorted(c for c in a[k] if a[k].get(c) != o[k].get(c))
        lignes.append("  modifié  : %s - %s" % (k, ", ".join(
            "%s %r → %r" % (c, a[k].get(c), o[k].get(c)) for c in champs)))
    if len(lignes) == 1:
        lignes.append("  mêmes événements, dans un ORDRE différent")
    return "\n".join(lignes)


class ChaineDePublication(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.reference = lire_gz("chaine_reference.json.gz")

    def test_meme_fil_qu_a_la_reference(self):
        code, publie = rejouer(self.reference)
        self.assertEqual(code, 0)
        if publie != self.reference["publie"]:
            self.fail("le fil publié a changé\n"
                      + ecarts(self.reference["publie"], publie))

    def test_la_reference_est_un_jour_ordinaire(self):
        # Garde-fou du test lui-même : une référence capturée un jour de
        # panne figerait la panne.
        c = self.reference["collecteurs"]
        self.assertEqual([x["nom"] for x in c if "erreur" in x], [])
        self.assertGreater(len(self.reference["publie"]), 2000)


if __name__ == "__main__":
    unittest.main()
