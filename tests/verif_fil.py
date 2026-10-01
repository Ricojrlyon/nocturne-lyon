"""Contrôle du fil FRAIS, après la collecte et avant la publication.

aggregate.py vient d'écrire events.json : on vérifie sa FORME avant de le
laisser partir. Un champ manquant, une date illisible ou un lien douteux
ne casseraient peut-être rien tout de suite — mais la page les lirait.

  python -m unittest tests.verif_fil -v
"""
import json
import re
import unittest
from datetime import date

from tests.outils import RACINE

ISO = re.compile(r"\d{4}-\d{2}-\d{2}$")
HEURE = re.compile(r"([01]\d|2[0-3]):[0-5]\d$")
LIEN = re.compile(r"https?://")
CHAMPS = {"venue", "venue_slug", "title", "subtitle", "category", "date_start",
          "date_end", "time", "url", "image", "offsite_venue", "id"}


class FilPublie(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fil = json.loads((RACINE / "events.json").read_text(encoding="utf-8"))
        cls.evs = cls.fil["events"]

    def test_entete(self):
        self.assertEqual(self.fil["count"], len(self.evs))
        date.fromisoformat(self.fil["generated_at"][:10])
        self.assertGreater(len(self.evs), 500, "fil anormalement maigre")

    def test_chaque_evenement_a_ses_champs(self):
        for e in self.evs:
            self.assertEqual(set(e), CHAMPS, e.get("title"))

    def test_textes_et_dates(self):
        for e in self.evs:
            quoi = "%s — %s" % (e["venue"], e["title"])
            self.assertTrue(e["title"].strip() and e["venue"].strip(), quoi)
            self.assertRegex(e["date_start"], ISO, quoi)
            date.fromisoformat(e["date_start"])
            if e["date_end"]:
                self.assertRegex(e["date_end"], ISO, quoi)
                self.assertGreaterEqual(e["date_end"], e["date_start"], quoi)
            if e["time"]:
                self.assertRegex(e["time"], HEURE, quoi)

    def test_liens_et_images(self):
        for e in self.evs:
            self.assertTrue(e["url"] == "" or LIEN.match(e["url"]), e["url"])
            self.assertTrue(e["image"] is None or LIEN.match(e["image"]), e["image"])

    def test_rien_de_passe(self):
        jour = self.fil["generated_at"][:10]
        passes = [e["title"] for e in self.evs if (e["date_end"] or e["date_start"]) < jour]
        self.assertEqual(passes, [])

    def test_identifiants_uniques(self):
        ids = [e["id"] for e in self.evs]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
