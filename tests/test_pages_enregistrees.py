"""Chaque source relit ses pages enregistrées au caractère près (audit A-28).

Les pages réelles d'une salle, enregistrées une fois (tests/enregistrer.py,
dossier tests/pages/), sont rejouées sans réseau, à la date de
l'enregistrement : le collecteur doit en tirer exactement les mêmes
événements. Une retouche du code qui change la lecture d'une salle se voit
ici, et non plus en production.

Un écart VOULU (une lecture corrigée) : réenregistrer la source,
python -m tests.enregistrer <source>, et relire le diff des événements.
"""
import json
import unittest
from unittest import mock

from requests.adapters import HTTPAdapter

from tests import enregistrer

ENREGISTREES = sorted(p.name.removesuffix(".json.xz")
                      for p in enregistrer.PAGES.glob("*.json.xz"))


def premier_ecart(attendus: list, obtenus: list) -> str:
    """Le premier écart, en clair."""
    cle = lambda e: (e["date_start"], e["time"] or "", e["title"], e["url"])  # noqa: E731
    a = {json.dumps(e, ensure_ascii=False, sort_keys=True) for e in attendus}
    o = {json.dumps(e, ensure_ascii=False, sort_keys=True) for e in obtenus}
    manque = sorted((json.loads(x) for x in a - o), key=cle)
    surplus = sorted((json.loads(x) for x in o - a), key=cle)
    lignes = ["%d événements attendus, %d obtenus" % (len(attendus), len(obtenus))]
    if manque:
        lignes.append("attendu, absent : %s" % json.dumps(manque[0], ensure_ascii=False))
    if surplus:
        lignes.append("obtenu en plus  : %s" % json.dumps(surplus[0], ensure_ascii=False))
    return "\n".join(lignes)


class PagesEnregistrees(unittest.TestCase):

    def verifier(self, cle: str):
        e = enregistrer.lire(cle)
        self.assertTrue(e["attendu"], "%s : un enregistrement sans événement n'essaie rien" % cle)
        obtenus, manquantes = enregistrer.rejouer(cle, e)
        self.assertEqual(manquantes, [],
                         "%s demande des pages qui n'ont pas été enregistrées : la lecture "
                         "a changé (python -m tests.enregistrer %s, si c'est voulu)" % (cle, cle))
        if obtenus != e["attendu"]:
            self.fail("%s, enregistrée le %s : %s" % (cle, e["jour"], premier_ecart(e["attendu"], obtenus)))

    def test_les_sources_sans_autre_test_sont_enregistrees(self):
        # Les 16 sources qui n'avaient aucun test de lecture (audit A-28) ;
        # le LOU et le Sonic ne le seront jamais (voir enregistrer.EXCLUES).
        for cle in ("celestins", "improvidence", "espace_gerson", "auditorium", "agendarts",
                    "chapelle_trinite", "bourse_du_travail", "mac_lyon", "le_sucre",
                    "periscope", "la_commune", "asvel", "asvel_feminin", "volley",
                    "handball", "villemorte"):
            self.assertIn(cle, ENREGISTREES)
        for cle in enregistrer.EXCLUES:
            self.assertNotIn(cle, ENREGISTREES)


class OutilDEnregistrement(unittest.TestCase):
    """L'outil lui-même : ce qu'il refuse, et le rejeu qu'il prépare."""

    def test_une_source_exclue_n_est_jamais_sollicitee(self):
        with mock.patch.object(enregistrer, "_ENVOI", side_effect=AssertionError("réseau")):
            for cle in ("rugby", "sonic", "complexe"):
                with self.assertRaises(SystemExit) as e:
                    enregistrer.enregistrer(cle)
                self.assertIn("jamais enregistrée", str(e.exception))

    def test_le_robots_txt_est_respecte(self):
        tout_interdit = enregistrer.regles(200, "User-agent: *\nDisallow: /\n")
        self.assertFalse(tout_interdit.can_fetch(enregistrer.UA, "https://exemple.org/agenda"))
        api_interdite = enregistrer.regles(200, "User-agent: *\nDisallow: /api/\nCrawl-delay: 10\n")
        self.assertTrue(api_interdite.can_fetch(enregistrer.UA, "https://exemple.org/agenda"))
        self.assertFalse(api_interdite.can_fetch(enregistrer.UA, "https://exemple.org/api/x"))
        self.assertEqual(api_interdite.crawl_delay(enregistrer.UA), 10)
        self.assertFalse(enregistrer.regles(403, "").can_fetch(enregistrer.UA, "https://exemple.org/"))
        self.assertTrue(enregistrer.regles(404, "").can_fetch(enregistrer.UA, "https://exemple.org/"))

    def test_une_adresse_interdite_n_est_pas_demandee(self):
        outil = enregistrer.Enregistreur()
        outil.robots["https://exemple.org"] = enregistrer.regles(200, "User-agent: *\nDisallow: /\n")
        demande = mock.Mock(url="https://exemple.org/agenda", method="GET", body=None)
        with mock.patch.object(enregistrer, "_ENVOI", side_effect=AssertionError("réseau")):
            with self.assertRaises(enregistrer.Interdit):
                outil.envoyer(HTTPAdapter(), demande)
        self.assertEqual(outil.echanges, [])

    def test_le_rejeu_sert_dans_l_ordre_et_note_ce_qui_manque(self):
        echanges = [{"methode": "GET", "url": "https://exemple.org/p", "corps": "",
                     "statut": s, "raison": "",
                     "entetes": {"Content-Type": "text/html; charset=utf-8"},
                     "texte": t} for s, t in ((503, "panne"), (200, "<p>été</p>"))]
        rejeu = enregistrer.Rejeu(echanges)
        demande = mock.Mock(url="https://exemple.org/p", method="GET", body=None)
        premiere, seconde, encore = (rejeu.envoyer(HTTPAdapter(), demande) for _ in range(3))
        self.assertEqual((premiere.status_code, seconde.status_code, encore.status_code),
                         (503, 200, 200))
        self.assertEqual(seconde.text, "<p>été</p>")
        autre = mock.Mock(url="https://exemple.org/autre", method="GET", body=None)
        with self.assertRaises(Exception):
            rejeu.envoyer(HTTPAdapter(), autre)
        self.assertEqual(rejeu.manquantes, ["GET https://exemple.org/autre"])


def _test_de(cle):
    def test(self):
        self.verifier(cle)
    test.__doc__ = "La source %s relit ses pages enregistrées au caractère près." % cle
    return test


for _cle in ENREGISTREES:
    setattr(PagesEnregistrees, "test_%s" % _cle, _test_de(_cle))


if __name__ == "__main__":
    unittest.main()
