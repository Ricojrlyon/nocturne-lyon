"""La politique de sécurité de la page (audit n° 15).

Les tests de page vérifient qu'elle ne bloque rien de ce dont la page a
besoin (tests/navigateur.py relève tout ce qu'elle bloque). Ceux-ci
vérifient l'inverse : qu'elle interdit toujours ce qu'elle doit
interdire, et qu'elle est posée avant tout ce qu'elle doit couvrir. Un
assouplissement doit être voulu : il passe alors par ce fichier.
"""
import re
import unittest

from tests.outils import RACINE

PAGE = (RACINE / "index.html").read_text(encoding="utf-8")
BALISE = re.compile(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">')


def regle() -> dict:
    trouvees = BALISE.findall(PAGE)
    if len(trouvees) != 1:
        raise AssertionError("%d règles de sécurité dans index.html, 1 attendue" % len(trouvees))
    return {d.split()[0]: d.split()[1:] for d in trouvees[0].split(";") if d.strip()}


class Securite(unittest.TestCase):

    def test_la_regle_est_posee_avant_tout_chargement(self):
        # Une règle posée par la page ne couvre que ce qui la SUIT.
        tete = PAGE.split("</head>", 1)[0]
        premier = min(tete.find(b) for b in ("<link", "<style", "<script") if b in tete)
        self.assertLess(BALISE.search(tete).start(), premier)

    def test_ce_que_la_regle_interdit(self):
        r = regle()
        self.assertEqual(r["default-src"], ["'none'"])
        self.assertEqual(r["script-src"], ["'self'", "'unsafe-inline'"])
        self.assertEqual(r["style-src"], ["'self'", "'unsafe-inline'"])
        self.assertEqual(r["img-src"], ["'self'", "https:", "data:"])
        self.assertEqual(r["font-src"], ["'self'"])
        self.assertEqual(r["connect-src"], ["'self'"])
        self.assertEqual(r["manifest-src"], ["'self'"])
        self.assertEqual(r["worker-src"], ["'self'"])
        self.assertEqual(r["base-uri"], ["'none'"])
        self.assertEqual(r["form-action"], ["'none'"])
        self.assertEqual(sorted(r), sorted(["default-src", "script-src", "style-src", "img-src",
                                            "font-src", "connect-src", "manifest-src",
                                            "worker-src", "base-uri", "form-action"]))


class PagesAnnexes(unittest.TestCase):
    """La page 404 porte la règle des mentions légales (audit A-40) : elle
    n'en avait aucune. Posée avant tout chargement, et sans script."""

    def test_la_page_404_a_sa_regle(self):
        page = (RACINE / "404.html").read_text(encoding="utf-8")
        tete = page.split("</head>", 1)[0]
        m = BALISE.search(tete)
        self.assertIsNotNone(m)
        premier = min(tete.find(b) for b in ("<link", "<style", "<script") if b in tete)
        self.assertLess(m.start(), premier)
        mentions = (RACINE / "mentions-legales.html").read_text(encoding="utf-8")
        self.assertEqual(m.group(1), BALISE.search(mentions).group(1))
        self.assertIn("default-src 'none'", m.group(1))
        self.assertNotIn("<script", page)


if __name__ == "__main__":
    unittest.main()
