"""Ce que la page et les collecteurs doivent avoir en commun (audit n° 10).

Deux valeurs vivent des deux côtés, en Python et dans index.html, sans
que rien ne les relie : le marqueur « ailleurs » d'un spectacle joué dans
plusieurs salles, et l'horizon de 180 jours. Modifiée d'un seul côté,
l'une d'elles décalerait le site en silence. Ici, elle bloque la
publication. La table des lieux, elle, n'existe plus qu'en un endroit,
lieux.json, que la page et le robot lisent tous deux (audit n° 21) : on
vérifie qu'ils le lisent bien, et qu'il est bien formé.
"""
import importlib
import json
import pkgutil
import re
import unittest

import aggregate
import scrapers
from scrapers import base
from tests.outils import RACINE

PAGE = (RACINE / "index.html").read_text(encoding="utf-8")


def constante_de_la_page(nom: str) -> str:
    m = re.search(r"^const %s = (.+?);" % nom, PAGE, re.M)
    if not m:
        raise AssertionError("%s introuvable dans index.html" % nom)
    return m.group(1).strip()


class Coherence(unittest.TestCase):

    def test_le_marqueur_ailleurs_est_le_meme_dans_la_page(self):
        self.assertEqual(constante_de_la_page("OFFSITE_PLUSIEURS"),
                         "'%s'" % base.OFFSITE_PLUSIEURS)

    def test_l_horizon_de_la_page_est_celui_des_collecteurs(self):
        self.assertEqual(constante_de_la_page("HORIZON_JOURS"), str(base.HORIZON_JOURS))

    def test_chaque_collecteur_prend_l_horizon_commun(self):
        # La valeur se lit dans base.HORIZON_JOURS, jamais en dur : le jour
        # où l'horizon change, il change partout.
        dossier = RACINE / "scrapers"
        en_dur = [f.relative_to(RACINE).as_posix() for f in sorted(dossier.rglob("*.py"))
                  if re.search(r"days\s*=\s*180\b|HORIZON_DAYS\s*=\s*\d",
                               f.read_text(encoding="utf-8"))]
        self.assertEqual(en_dur, [])
        for info in pkgutil.walk_packages(scrapers.__path__, "scrapers."):
            module = importlib.import_module(info.name)
            if hasattr(module, "HORIZON_DAYS"):
                self.assertEqual(module.HORIZON_DAYS, base.HORIZON_JOURS, info.name)

    def test_les_polices_prechargees_sont_celles_de_polices_css(self):
        # Audit n° 17. Une police préchargée qui n'existerait plus, ou que
        # polices.css n'emploierait plus, serait une requête perdue à
        # chaque visite.
        css = (RACINE / "fonts" / "polices.css").read_text(encoding="utf-8")
        balises = re.findall(r'<link rel="preload"[^>]*>', PAGE)
        self.assertGreater(len(balises), 0)
        for b in balises:
            police = re.search(r'href="fonts/([^"]+)"', b).group(1)
            self.assertTrue((RACINE / "fonts" / police).exists(), police)
            self.assertIn("url(%s)" % police, css)
            self.assertIn(' as="font" type="font/woff2" crossorigin>', b)

    def test_l_adresse_officielle_et_l_icone_de_l_iphone(self):
        # Audit n° 19. L'adresse officielle est celle de l'aperçu au
        # partage ; l'icône de l'écran d'accueil existe, en PNG carré de
        # 180 px, le format qu'attend l'iPhone.
        canonique = re.search(r'<link rel="canonical" href="([^"]+)">', PAGE).group(1)
        partage = re.search(r'<meta property="og:url" content="([^"]+)">', PAGE).group(1)
        self.assertEqual(canonique, partage)
        icone = re.search(r'<link rel="apple-touch-icon" href="([^"]+)">', PAGE).group(1)
        png = (RACINE / icone).read_bytes()
        self.assertEqual(png[1:4] + png[12:16], b"PNGIHDR")
        self.assertEqual((int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")),
                         (180, 180))

    def test_l_application_installable(self):
        # Sa description est lue par la page, ses icônes existent à leur
        # taille, et tout ce que le service hors ligne garde au départ
        # existe dans le dépôt : un seul fichier manquant, et il ne
        # s'installerait pas.
        self.assertIn('<link rel="manifest" href="manifest.webmanifest">', PAGE)
        m = json.loads((RACINE / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual((m["start_url"], m["scope"], m["display"]), ("./", "./", "standalone"))
        self.assertTrue(m["name"] and m["short_name"])
        tailles = set()
        for icone in m["icons"]:
            png = (RACINE / icone["src"]).read_bytes()
            self.assertEqual(png[1:4] + png[12:16], b"PNGIHDR", icone["src"])
            cote = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
            self.assertEqual("%dx%d" % cote, icone["sizes"], icone["src"])
            tailles.add((icone["sizes"], icone["purpose"]))
        self.assertLessEqual({("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")},
                             tailles)
        sw = (RACINE / "sw.js").read_text(encoding="utf-8")
        au_depart = re.findall(r"'([^']+)'", sw.split("const AU_DEPART = [", 1)[1].split("];", 1)[0])
        self.assertIn("./", au_depart)
        for f in au_depart:
            self.assertTrue((RACINE / ("index.html" if f == "./" else f)).exists(), f)
        self.assertIn("navigator.serviceWorker.register('sw.js')", PAGE)

    def test_au_doigt_seul_le_bouton_touche_reagit(self):
        # Sur un téléphone, toucher l'étoile allumait toute la carte : le
        # voile gris du toucher, et le « survol » qui restait sur la carte.
        # La carte ne s'allume plus qu'à la souris ; l'étoile, elle,
        # s'allume au survol et grossit au toucher.
        self.assertIn("-webkit-tap-highlight-color: transparent;", PAGE)
        self.assertRegex(PAGE, r"@media \(hover: hover\) \{\s+\.event:hover \{")
        self.assertNotRegex(PAGE, r"\n  \.event:hover \{")
        self.assertRegex(PAGE, r"\.carte-etoile:hover \{ color: var\(--warm\); transform: scale")
        self.assertIn(".event:active:not(:has(.carte-bouton:active))", PAGE)

    def test_un_message_pour_qui_n_a_pas_javascript(self):
        # Audit n° 19 : sans JavaScript, la page le dit au lieu de rester
        # vide.
        bloc = re.search(r"<noscript>(.*?)</noscript>", PAGE, re.S)
        self.assertIsNotNone(bloc)
        self.assertIn("le programme a besoin de JavaScript", bloc.group(1))

    def test_la_page_et_le_robot_lisent_le_meme_fichier_de_lieux(self):
        # La page ne garde plus de table de lieux : elle lit lieux.json au
        # chargement. Le robot lit le même fichier ; les autres tests
        # remplacent sa lecture par un ensemble vide, ici elle lit le vrai.
        self.assertIn("const VENUE_ARRONDISSEMENT = {};", PAGE)
        self.assertIn("const VENUE_COMMUNE = {};", PAGE)
        self.assertIn("fetch('lieux.json'", PAGE)
        lieux = json.loads((RACINE / "lieux.json").read_text(encoding="utf-8"))["lieux"]
        self.assertEqual(aggregate.frontend_hardcoded_venues(),
                         {l["nom"] for l in lieux if "arr" in l})

    def test_le_fichier_des_lieux_est_bien_forme(self):
        # Une ligne par lieu : un nom, unique ; un arrondissement de la liste
        # de la page, une commune écrite en entier, ou les deux ; une note
        # libre. Une faute ici ferait échouer le chargement de la page.
        ordre = json.loads(constante_de_la_page("ARRONDISSEMENT_ORDER").replace("'", '"'))
        lieux = json.loads((RACINE / "lieux.json").read_text(encoding="utf-8"))["lieux"]
        self.assertGreater(len(lieux), 100)
        noms = [l["nom"] for l in lieux]
        self.assertEqual(len(noms), len(set(noms)))
        for l in lieux:
            self.assertLessEqual(set(l), {"nom", "arr", "commune", "pos", "note"}, l)
            self.assertTrue(l["nom"].strip(), l)
            self.assertTrue("arr" in l or "commune" in l, l)
            if "arr" in l:
                self.assertIn(l["arr"], ordre, l)
            if "commune" in l:
                self.assertNotIn("…", l["commune"], l)
            if "pos" in l:
                # La vue carte (nouveauté n° 6) : [latitude, longitude], dans
                # la grande région lyonnaise. Inversées, elles tomberaient
                # dans l'océan Indien : le test le verrait.
                lat, lon = l["pos"]
                self.assertTrue(45.4 < lat < 46.0 and 4.4 < lon < 5.2, l)

    def test_presque_tous_les_lieux_ont_une_position(self):
        # Sans position, un lieu va dans la liste « non placés » de la carte.
        lieux = json.loads((RACINE / "lieux.json").read_text(encoding="utf-8"))["lieux"]
        places = [l for l in lieux if "pos" in l]
        self.assertGreater(len(places), 0.8 * len(lieux))


if __name__ == "__main__":
    unittest.main()
