"""L'alerte des salles en panne (audit A-02) : alertes.py, sans réseau.

Ce qu'elle décide (ouvrir, récrire, commenter, fermer une issue) selon
l'état des sources du passage, et ce qu'elle envoie à l'API GitHub, par un
faux client. Elle ne doit jamais faire échouer le passage."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import alertes

J = "2026-10-25"


def etat(reprises=None, abandons=None, en_echec=None, rapport=None):
    return {"date": J, "reprises": reprises or {}, "abandons": abandons or [],
            "en_echec": en_echec or {}, "rapport": rapport or []}


class Decisions(unittest.TestCase):

    def test_une_nuit_de_panne_ne_derange_personne(self):
        self.assertEqual(alertes.decider(etat(reprises={"HEAT": J}), {}, set()), [])

    def test_au_deuxieme_jour_une_issue_est_ouverte(self):
        actions = alertes.decider(
            etat(reprises={"IAC Villeurbanne": "2026-10-23"},
                 en_echec={"IAC Villeurbanne": {"raison": "ConnectTimeout: i-ac.eu",
                                                "lieux": ["IAC Villeurbanne"]}}),
            {}, set(), "Ricojrlyon")
        self.assertEqual([(a[0], a[1]) for a in actions], [("ouvrir", "IAC Villeurbanne")])
        corps = actions[0][2]
        self.assertTrue(corps.startswith("@Ricojrlyon : **IAC Villeurbanne**"))
        for morceau in ("depuis le 23 octobre 2026", "jour 3 sur 7", "ConnectTimeout: i-ac.eu",
                        "se ferme seule"):
            self.assertIn(morceau, corps)

    def test_le_jour_de_l_abandon_ouvre_ou_previent(self):
        e = etat(abandons=[["IAC Villeurbanne", "2026-10-18", 7]])
        self.assertEqual([a[0] for a in alertes.decider(e, {}, set())], ["ouvrir"])
        ouvertes = {"IAC Villeurbanne": {"numero": 12, "depuis": "2026-10-19"}}
        actions = alertes.decider(e, ouvertes, set(), "Ricojrlyon")
        self.assertEqual([(a[0], a[1]) for a in actions], [("modifier", 12), ("commenter", 12)])
        self.assertIn("**abandonnée**", actions[0][2])
        self.assertIn("quittent le site", actions[1][2])

    def test_tant_que_la_panne_dure_le_texte_est_recrit_sans_commentaire(self):
        ouvertes = {"HEAT": {"numero": 3, "depuis": "2026-10-24"}}
        actions = alertes.decider(etat(reprises={"HEAT": "2026-10-23"}), ouvertes, set())
        self.assertEqual([(a[0], a[1]) for a in actions], [("modifier", 3)])

    def test_la_salle_revenue_ferme_son_issue(self):
        ouvertes = {"HEAT": {"numero": 3, "depuis": "2026-10-24"}}
        actions = alertes.decider(etat(), ouvertes, {"HEAT", "Le Sucre"})
        self.assertEqual([(a[0], a[1]) for a in actions], [("fermer", 3)])
        self.assertIn("est revenue", actions[0][2])

    def test_abandonnee_et_toujours_absente_l_issue_reste_ouverte(self):
        # Après l'abandon, la salle n'est plus reprise ; tant qu'elle n'est
        # pas de retour dans le fil, l'issue reste ouverte.
        ouvertes = {"IAC Villeurbanne": {"numero": 12, "depuis": "2026-10-19"}}
        actions = alertes.decider(etat(), ouvertes, {"Le Sucre"})
        self.assertEqual([(a[0], a[1]) for a in actions], [("modifier", 12)])
        self.assertIn("toujours absente du site", actions[0][2])

    def test_un_agregateur_revenu_est_reconnu_a_son_rapport(self):
        fil = {"events": [{"venue": "Le Sucre"}]}
        e = etat(rapport=[["Petit Bulletin", 600, None], ["HEAT", 0, "Erreur"]])
        self.assertEqual(alertes.presentes(fil, e), {"Le Sucre", "Petit Bulletin"})


class FausseSession:
    def __init__(self, issues=()):
        self.issues, self.appels, self.headers = list(issues), [], {}

    def request(self, methode, url, json=None, timeout=None):
        self.appels.append((methode, url.split("/nocturne-lyon", 1)[1], json))
        r = mock.Mock(content=b"[]")
        r.raise_for_status = lambda: None
        r.json = lambda: self.issues if methode == "GET" else {}
        return r


class Client(unittest.TestCase):

    def test_seules_les_issues_de_l_alerte_comptent(self):
        s = FausseSession([
            {"number": 3, "title": "[salle en panne] HEAT", "created_at": "2026-10-24T07:00:00Z"},
            {"number": 4, "title": "Une autre question", "created_at": "2026-10-24T07:00:00Z"},
            {"number": 5, "title": "[salle en panne] X", "pull_request": {},
             "created_at": "2026-10-24T07:00:00Z"}])
        gh = alertes.GitHub("Ricojrlyon/nocturne-lyon", "jeton", session=s)
        self.assertEqual(gh.ouvertes(), {"HEAT": {"numero": 3, "depuis": "2026-10-24"}})

    def test_les_actions_deviennent_des_appels(self):
        s = FausseSession()
        gh = alertes.GitHub("Ricojrlyon/nocturne-lyon", "jeton", session=s)
        gh.appliquer([("ouvrir", "HEAT", "texte"), ("modifier", 3, "texte 2"),
                      ("commenter", 3, "mot"), ("fermer", 4, "revenue")])
        self.assertEqual(s.appels, [
            ("POST", "/issues", {"title": "[salle en panne] HEAT", "body": "texte"}),
            ("PATCH", "/issues/3", {"body": "texte 2"}),
            ("POST", "/issues/3/comments", {"body": "mot"}),
            ("POST", "/issues/4/comments", {"body": "revenue"}),
            ("PATCH", "/issues/4", {"state": "closed"})])
        self.assertEqual(s.headers["Authorization"], "Bearer jeton")


class JamaisBloquant(unittest.TestCase):

    def lancer(self, env):
        sortie = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(sortie):
            code = alertes.main()
        return code, sortie.getvalue()

    def test_sans_etat_rien_a_faire(self):
        self.assertEqual(self.lancer({})[0], 0)

    def test_hors_de_github_le_resume_seulement(self):
        with tempfile.TemporaryDirectory() as tmp:
            chemin, sommaire = Path(tmp) / "etat.json", Path(tmp) / "resume.md"
            chemin.write_text(json.dumps(etat(rapport=[["HEAT", 0, "RuntimeError: 502"],
                                                       ["Le Sucre", 30, None]])), encoding="utf-8")
            code, sortie = self.lancer({"NOCTURNE_ETAT": str(chemin),
                                        "GITHUB_STEP_SUMMARY": str(sommaire)})
            self.assertEqual(code, 0)
            self.assertIn("hors de GitHub", sortie)
            texte = sommaire.read_text(encoding="utf-8")
        self.assertIn("| HEAT | 0 | en échec : RuntimeError: 502 |", texte)
        self.assertIn("| Le Sucre | 30 | ok |", texte)

    def test_une_erreur_de_l_api_ne_fait_pas_echouer_le_passage(self):
        with tempfile.TemporaryDirectory() as tmp:
            chemin = Path(tmp) / "etat.json"
            chemin.write_text(json.dumps(etat()), encoding="utf-8")
            with mock.patch.object(alertes.GitHub, "ouvertes", side_effect=RuntimeError("401")):
                code, sortie = self.lancer({"NOCTURNE_ETAT": str(chemin), "GITHUB_TOKEN": "x",
                                            "GITHUB_REPOSITORY": "Ricojrlyon/nocturne-lyon"})
        self.assertEqual(code, 0)
        self.assertIn("::warning title=alerte des salles::RuntimeError: 401", sortie)


if __name__ == "__main__":
    unittest.main()
