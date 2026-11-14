"""Le passage de nuit (passage.py, audit lot 8) : son créneau et sa
publication, sans réseau.

Le créneau sur des dates fixes, changements d'heure de 2026 et 2027
compris ; la publication contre de vrais dépôts git, créés pour le test
dans un dossier temporaire : un « GitHub » (dépôt nu), le robot, et
quelqu'un qui pousse pendant le passage."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import passage

UTC = timezone.utc
CRONS = ((0, 17), (1, 17), (9, 17))      # update.yml, en UTC


def utc(texte: str) -> datetime:
    return datetime.fromisoformat(texte).replace(tzinfo=UTC)


class Creneau(unittest.TestCase):

    def test_un_lancement_a_la_main_travaille_toujours(self):
        for evenement in ("workflow_dispatch", "push", ""):
            lancer, _ = passage.creneau(evenement, "2026-11-14T03:00:00+00:00",
                                        utc("2026-11-14T10:00:00"))
            self.assertTrue(lancer, evenement)

    def test_le_premier_passage_du_jour_travaille(self):
        lancer, raison = passage.creneau("schedule", "2026-11-13T04:20:00+00:00",
                                         utc("2026-11-14T00:17:00"))
        self.assertTrue(lancer)
        self.assertIn("2026-11-14", raison)

    def test_les_suivants_s_arretent(self):
        lancer, raison = passage.creneau("schedule", "2026-11-14T04:20:00+00:00",
                                         utc("2026-11-14T05:40:00"))
        self.assertFalse(lancer)
        self.assertIn("05:20", raison)                 # 04:20 UTC, 5 h 20 à Paris

    def test_le_jour_est_celui_de_paris_et_non_d_utc(self):
        # Publié à 0 h 30 à Paris le 14 (encore le 13 en UTC) : il est du
        # jour, le passage programmé de 01:17 UTC s'arrête.
        self.assertFalse(passage.creneau("schedule", "2026-11-13T23:30:00+00:00",
                                         utc("2026-11-14T01:17:00"))[0])
        # L'été, Paris a deux heures d'avance : 22:30 UTC, c'est déjà le 2.
        self.assertFalse(passage.creneau("schedule", "2026-07-01T22:30:00+00:00",
                                         utc("2026-07-02T00:17:00"))[0])
        # 21:30 UTC, 23 h 30 à Paris : la veille, le passage travaille.
        self.assertTrue(passage.creneau("schedule", "2026-07-01T21:30:00+00:00",
                                        utc("2026-07-02T00:17:00"))[0])
        # Un passage très retardé, arrivé à 23:30 UTC : il est déjà 0 h 30
        # le lendemain à Paris, et le programme du matin est de la veille.
        self.assertTrue(passage.creneau("schedule", "2026-11-14T04:20:00+00:00",
                                        utc("2026-11-14T23:30:00"))[0])

    def test_un_programme_sans_date_lisible_est_refait(self):
        for genere in (None, "", "hier", 42):
            self.assertTrue(passage.creneau("schedule", genere, utc("2026-11-14T00:17:00"))[0],
                            genere)
        # Une date sans fuseau est lue en UTC, comme l'écrit aggregate.py.
        self.assertFalse(passage.creneau("schedule", "2026-11-14T04:20:00",
                                         utc("2026-11-14T09:17:00"))[0])

    def test_un_passage_par_jour_sur_deux_ans_changements_d_heure_compris(self):
        # Chaque jour de 2026 et 2027, les trois passages programmés, que
        # GitHub retarde de 0 à 7 heures : un seul travaille, le premier
        # arrivé. Les jours de changement d'heure (29/03 et 25/10/2026,
        # 28/03 et 31/10/2027) en font partie.
        genere = "2025-12-31T04:00:00+00:00"
        jour, travaux = date(2026, 1, 1), {}
        while jour < date(2028, 1, 1):
            retards = [(jour.toordinal() * 7 + i * 3) % 8 for i in range(3)]
            arrivees = sorted(datetime(jour.year, jour.month, jour.day, h, m, tzinfo=UTC)
                              + timedelta(hours=r) for (h, m), r in zip(CRONS, retards))
            for arrivee in arrivees:
                lancer, _ = passage.creneau("schedule", genere, arrivee)
                if lancer:
                    travaux[jour] = travaux.get(jour, 0) + 1
                    genere = (arrivee + timedelta(minutes=12)).isoformat()
            jour += timedelta(days=1)
        self.assertEqual(len(travaux), 730)
        self.assertEqual(set(travaux.values()), {1})

    def test_un_passage_rate_est_rattrape_par_le_suivant(self):
        # Le premier passage de la nuit tombe au rouge (rien de publié) :
        # le deuxième travaille ; s'il échoue aussi, celui de 09:17 UTC.
        veille = "2026-11-13T04:20:00+00:00"
        for h in (0, 1, 9):
            self.assertTrue(passage.creneau("schedule", veille,
                                            utc("2026-11-14T%02d:17:00" % h))[0])

    def test_le_job_lit_le_fil_et_repond_a_github(self):
        with tempfile.TemporaryDirectory() as tmp:
            fil, sortie = Path(tmp) / "events.json", Path(tmp) / "sortie"
            fil.write_text(json.dumps({"generated_at": "2026-11-14T04:20:00+00:00",
                                       "events": []}), encoding="utf-8")
            with mock.patch("sys.stdout"):
                self.assertFalse(passage.lancer_creneau(fil, str(sortie), "schedule",
                                                        utc("2026-11-14T09:17:00")))
                self.assertTrue(passage.lancer_creneau(fil, str(sortie), "workflow_dispatch",
                                                       utc("2026-11-14T09:17:00")))
                # Un fil absent ou abîmé : le passage travaille.
                self.assertTrue(passage.lancer_creneau(Path(tmp) / "absent.json", str(sortie),
                                                       "schedule", utc("2026-11-14T09:17:00")))
            self.assertEqual(sortie.read_text(encoding="utf-8"),
                             "lancer=non\nlancer=oui\nlancer=oui\n")


GIT = shutil.which("git")
IDENTITE = {"GIT_AUTHOR_NAME": "essai", "GIT_AUTHOR_EMAIL": "essai@example.invalid",
            "GIT_COMMITTER_NAME": "essai", "GIT_COMMITTER_EMAIL": "essai@example.invalid",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


@unittest.skipUnless(GIT, "git absent")
class Publication(unittest.TestCase):
    """Un « GitHub » (dépôt nu), le robot qui a committé son fil, et
    quelqu'un qui pousse pendant le passage."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        racine = Path(self._tmp.name)
        self.env = mock.patch.dict(os.environ, IDENTITE)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.github, self.robot, self.humain = racine / "github.git", racine / "robot", racine / "humain"
        self.git(racine, "init", "--bare", "--initial-branch=main", str(self.github))
        self.git(racine, "clone", "-q", str(self.github), str(self.robot))
        self.git(self.robot, "symbolic-ref", "HEAD", "refs/heads/main")
        self.ecrire(self.robot, events="fil de la veille\n", readme="lisez-moi\n")
        self.git(self.robot, "add", "-A")
        self.git(self.robot, "commit", "-q", "-m", "départ")
        self.git(self.robot, "push", "-q", "-u", "origin", "main")
        self.git(racine, "clone", "-q", str(self.github), str(self.humain))

    def git(self, ou, *args) -> str:
        r = subprocess.run(["git", *args], cwd=ou, capture_output=True, text=True,
                           encoding="utf-8")
        self.assertEqual(r.returncode, 0, (args, r.stderr))
        return r.stdout

    @staticmethod
    def ecrire(ou: Path, events=None, readme=None):
        if events is not None:
            (ou / "events.json").write_text(events, encoding="utf-8", newline="")
        if readme is not None:
            (ou / "README.md").write_text(readme, encoding="utf-8", newline="")

    def pousse_pendant_le_passage(self, **fichiers):
        self.ecrire(self.humain, **fichiers)
        self.git(self.humain, "commit", "-qam", "envoi pendant le passage")
        self.git(self.humain, "push", "-q")

    def robot_committe(self):
        self.ecrire(self.robot, events="fil du jour\n")
        self.git(self.robot, "commit", "-qam", "Update events")

    def sur_github(self, fichier: str) -> str:
        return self.git(self.github, "show", "main:%s" % fichier)

    def publier(self, **options):
        # Ce que git dit (les refus du crochet) reste dans le test.
        vrai = subprocess.run

        def muet(args, **k):
            k["capture_output"] = True
            return vrai(args, **k)
        with mock.patch("builtins.print") as dit, mock.patch("passage.subprocess.run", muet):
            ok = passage.publier(self.robot, pause=0, **options)
        self.messages = " ".join(str(a) for appel in dit.call_args_list for a in appel.args)
        return ok

    def refuser_les_envois(self, combien: int):
        """Un crochet du « GitHub » qui refuse les `combien` premiers envois."""
        crochet = self.github / "hooks" / "pre-receive"
        crochet.write_text("#!/bin/sh\nn=$(cat refus 2>/dev/null || echo 0)\n"
                           "if [ \"$n\" -lt %d ]; then echo $((n + 1)) > refus; "
                           "echo refus >&2; exit 1; fi\nexit 0\n" % combien,
                           encoding="utf-8", newline="")
        crochet.chmod(0o755)

    def test_sans_rien_entre_deux_le_fil_part(self):
        self.robot_committe()
        self.assertTrue(self.publier())
        self.assertEqual(self.sur_github("events.json"), "fil du jour\n")

    def test_un_envoi_pendant_le_passage_ne_bloque_plus_la_publication(self):
        # Audit A-27 : quelqu'un pousse une retouche ET le fil (un fichier
        # du robot téléversé à la main) pendant le passage. Avant : conflit
        # au « pull --rebase », passage rouge, rien de publié. Maintenant :
        # la retouche reste, le fil du robot l'emporte.
        self.pousse_pendant_le_passage(events="fil téléversé à la main\n",
                                       readme="lisez-moi, retouché\n")
        self.robot_committe()
        self.assertTrue(self.publier())
        self.assertEqual(self.sur_github("events.json"), "fil du jour\n")
        self.assertEqual(self.sur_github("README.md"), "lisez-moi, retouché\n")
        self.assertIn("envoi pendant le passage", self.git(self.github, "log", "--format=%s"))

    def test_un_envoi_refuse_est_retente(self):
        self.refuser_les_envois(2)
        self.robot_committe()
        self.assertTrue(self.publier())
        self.assertEqual(self.sur_github("events.json"), "fil du jour\n")
        self.assertIn("essai 1 sur 3", self.messages)
        self.assertIn("essai 2 sur 3", self.messages)

    def test_trois_refus_et_le_passage_le_dit(self):
        self.refuser_les_envois(3)
        self.robot_committe()
        self.assertFalse(self.publier())
        self.assertEqual(self.sur_github("events.json"), "fil de la veille\n")
        self.assertIn("::error title=publication::", self.messages)
        # Le dépôt n'est pas laissé au milieu d'un rebase.
        self.assertFalse((self.robot / ".git" / "rebase-merge").exists())
        self.assertFalse((self.robot / ".git" / "rebase-apply").exists())

    def test_un_conflit_sans_issue_laisse_le_depot_en_etat(self):
        # Le fil supprimé pendant que le robot le récrit : « -X theirs » ne
        # tranche pas ce conflit-là. Trois essais, chacun défait, puis un
        # échec franc ; jamais un dépôt laissé au milieu d'un rebase.
        self.git(self.humain, "rm", "-q", "events.json")
        self.git(self.humain, "commit", "-qm", "fil supprimé")
        self.git(self.humain, "push", "-q")
        self.robot_committe()
        self.assertFalse(self.publier())
        self.assertIn("essai 2 sur 3", self.messages)
        self.assertFalse((self.robot / ".git" / "rebase-merge").exists())
        self.assertFalse((self.robot / ".git" / "rebase-apply").exists())
        self.assertEqual(self.git(self.robot, "log", "-1", "--format=%s"), "Update events\n")

    def test_le_jeton_ne_va_qu_aux_commandes_git(self):
        # Le jeton passe par l'environnement des commandes git de la
        # publication, sans être écrit dans le dépôt.
        vus = []
        vrai = subprocess.run

        def espion(args, **k):
            vus.append((args, k.get("env") or {}))
            return vrai(args, **k)
        self.robot_committe()
        with mock.patch("passage.subprocess.run", espion):
            self.assertTrue(self.publier(jeton="jeton-d-essai"))
        self.assertTrue(vus)
        for args, env in vus:
            self.assertEqual(env.get("GIT_CONFIG_KEY_0"), "http.https://github.com/.extraheader")
            self.assertEqual(env.get("GIT_CONFIG_VALUE_0"),
                             "AUTHORIZATION: basic eC1hY2Nlc3MtdG9rZW46amV0b24tZC1lc3NhaQ==")
            self.assertNotIn("jeton-d-essai", " ".join(args))
        self.assertNotIn("jeton", (self.robot / ".git" / "config").read_text(encoding="utf-8"))
        self.assertNotIn("extraheader", (self.robot / ".git" / "config").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
