"""Recalcule les références des tests, quand un changement est VOULU.

  python -m tests.regenerer          les deux
  python -m tests.regenerer chaine   le fil publié de la chaîne
  python -m tests.regenerer page     l'affichage de la page

La collecte figée elle-même n'est pas touchée : seuls les RÉSULTATS
attendus sont recalculés, avec le code d'aujourd'hui. Relire ensuite le
diff - git diff --stat tests/donnees - et vérifier que ce qui a changé est
bien ce que l'on voulait changer.
"""
import sys

from tests.outils import ecrire_gz, lire_gz


def chaine() -> None:
    from tests.test_chaine import rejouer
    ref = lire_gz("chaine_reference.json.gz")
    code, publie = rejouer(ref)
    if code != 0:
        raise SystemExit("aggregate.main() a rendu %d : référence non écrite" % code)
    avant = len(ref["publie"])
    ref["publie"] = publie
    ecrire_gz("chaine_reference.json.gz", ref)
    print("chaîne : %d événements publiés (avant : %d)" % (len(publie), avant))


def page() -> None:
    from tests import navigateur
    from tests.test_page import donnees_figees
    releve = navigateur.jouer(*donnees_figees(), date_figee=True)
    if "echec" in releve:
        raise SystemExit("le pilote a échoué : " + releve["echec"])
    if releve.get("erreurs_fin"):
        raise SystemExit("erreurs dans la page : %s" % releve["erreurs_fin"])
    ecrire_gz("page_reference.json.gz", releve)
    print("page : %s" % ", ".join("%s %d cartes" % (n, sum(len(j[2]) for j in s["jours"]))
                                  for n, s in releve["scenarios"].items()))


if __name__ == "__main__":
    quoi = sys.argv[1:] or ["chaine", "page"]
    for q in quoi:
        {"chaine": chaine, "page": page}[q]()
