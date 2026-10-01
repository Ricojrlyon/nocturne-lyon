"""Le collecteur du Complexe : lecture du catalogue et des séances, et la
vérification anti-robot de son hébergeur (BUG-3). Pages synthétiques, qui
reprennent la structure du vrai site (classes « tly_ »)."""
import contextlib
import io
import unittest
from datetime import date
from unittest import mock

from scrapers import complexe
from tests.outils import FauxSite, date_figee

LISTING = complexe.LISTING
A = complexe.BASE + "/programme/spectacle-a/"
B = complexe.BASE + "/programme/spectacle-b/"
C = complexe.BASE + "/programme/seance-de-la-semaine/"

CATALOGUE = """<html><body>
<div class="tly_accordionContent">
  <div class="tly_productItem"><a class="tly_moreLink" href="%(c)s">+</a>
    <div class="tly_productTitle">Séance de la semaine</div>
    <div class="tly_productDate">01/10/2026</div></div>
</div>
<div class="tly_productItem">
  <div class="tly_featuredImg" style="background-image: url('/wp-content/a.jpg')"></div>
  <a class="tly_moreLink" href="%(a)s">+</a>
  <div class="tly_productTitle">Spectacle A</div>
  <div class="tly_productDate">Du 30/12/2026 au 02/01/2027</div>
</div>
<div class="tly_productItem">
  <a class="tly_moreLink" href="%(a)s">+</a>
  <div class="tly_productTitle">Spectacle A</div>
  <div class="tly_productDate">Du 05/01/2027 au 30/01/2027</div>
</div>
<div class="tly_productItem">
  <a class="tly_moreLink" href="%(b)s">+</a>
  <div class="tly_productTitle">Spectacle B</div>
  <div class="tly_productDate">Du 01/10/2026 au 31/10/2026</div>
</div>
</body></html>""" % {"a": A, "b": B, "c": C}


def seances(*lignes):
    return ('<html><body><div class="tly_productDatesTable">'
            + "".join('<div class="tly_productDate"><div class="tly_day">%s</div>'
                      '<div class="tly_hour">%s</div></div>' % l for l in lignes)
            + "</div></body></html>")


# À cheval sur le Nouvel An, et une séance au jour de la semaine faux : le
# 2 janvier 2027 est un samedi, pas un lundi.
PAGE_A = seances(("Jeudi 31 décembre", "20h30"), ("Vendredi 1 janvier", "21h00"),
                 ("Lundi 2 janvier", "20h00"))
PAGE_B = seances(("Jeudi 1 octobre", "20h00"), ("Vendredi 2 octobre", "20h00"))

VERIF = (202, b'<html><head><meta http-equiv="refresh" '
              b'content="0;/.well-known/sgcaptcha/?r=%2F"></head></html>',
         {"sg-captcha": "challenge"})


class LectureDuSite(unittest.TestCase):

    def test_catalogue(self):
        site = FauxSite({LISTING: CATALOGUE})
        shows = complexe._catalogue(site, [])
        par_url = {s["url"]: s for s in shows}
        # L'accordéon des sept jours est ignoré ; un spectacle présent deux
        # fois n'est gardé qu'une, à l'année la plus ancienne.
        self.assertEqual(sorted(par_url), sorted([A, B]))
        self.assertEqual(par_url[A]["annee"], 2026)
        self.assertEqual(par_url[A]["image"], complexe.BASE + "/wp-content/a.jpg")

    def test_seances_annee_roulee_et_jour_controle(self):
        site = FauxSite({A: PAGE_A})
        journal = io.StringIO()
        with contextlib.redirect_stderr(journal):
            out = complexe._seances(site, A, 2026, "Le Complexe", [])
        self.assertEqual(out, [("2026-12-31", "20:30"), ("2027-01-01", "21:00")])
        self.assertIn("1 séance(s) écartée(s)", journal.getvalue())


class VerificationAntiRobot(unittest.TestCase):

    def setUp(self):
        self.attentes = []
        for cible in (complexe.time, ):
            p = mock.patch.object(cible, "sleep", self.attentes.append)
            p.start()
            self.addCleanup(p.stop)

    def lancer(self, site):
        """fetch() complet, au 1er octobre 2026, servi par `site`."""
        with mock.patch.object(complexe, "Date", date_figee(date(2026, 10, 1))), \
                mock.patch.object(complexe.requests, "Session", lambda: site), \
                contextlib.redirect_stderr(io.StringIO()):
            return complexe.fetch()

    def attentes_de_verification(self):
        return [a for a in self.attentes if a != complexe.MIN_INTERVAL]

    def test_trois_signes_reconnus_et_page_normale_non(self):
        site = FauxSite({LISTING: CATALOGUE},
                        {LISTING: [(202, b"", {}), (200, b"", {"sg-captcha": "x"}),
                                   (200, b".well-known/sgcaptcha/", {})]})
        for _ in range(3):
            self.assertTrue(complexe._verification(site.request("GET", LISTING)))
        self.assertFalse(complexe._verification(site.request("GET", LISTING)))

    def test_jour_normal(self):
        evs = self.lancer(FauxSite({LISTING: CATALOGUE, A: PAGE_A, B: PAGE_B}))
        self.assertEqual([(e.date_start, e.time, e.title) for e in evs],
                         [("2026-12-31", "20:30", "Spectacle A"),
                          ("2027-01-01", "21:00", "Spectacle A"),
                          ("2026-10-01", "20:00", "Spectacle B"),
                          ("2026-10-02", "20:00", "Spectacle B")])
        self.assertTrue(all(e.venue == complexe.VENUE for e in evs))
        self.assertEqual(self.attentes_de_verification(), [])

    def test_refuse_une_fois_puis_servi(self):
        evs = self.lancer(FauxSite({LISTING: CATALOGUE, A: PAGE_A, B: PAGE_B},
                                   {LISTING: [VERIF]}))
        self.assertEqual(len(evs), 4)
        self.assertEqual(self.attentes_de_verification(), [60])

    def test_page_inconnue_sans_catalogue_puis_servie(self):
        evs = self.lancer(FauxSite({LISTING: CATALOGUE, A: PAGE_A, B: PAGE_B},
                                   {LISTING: [(200, b"<html>Un instant</html>", {})]}))
        self.assertEqual(len(evs), 4)
        self.assertEqual(self.attentes_de_verification(), [60])

    def test_refuse_pour_de_bon_on_renonce_en_le_disant(self):
        site = FauxSite({LISTING: CATALOGUE}, {LISTING: [VERIF] * 3})
        with self.assertRaises(complexe.VerificationAntiRobot):
            self.lancer(site)
        self.assertEqual(self.attentes_de_verification(), [60, 120])
        self.assertEqual(site.appels, [LISTING] * 3)

    def test_page_spectacle_refusee_pas_de_programme_troue(self):
        site = FauxSite({LISTING: CATALOGUE, A: PAGE_A, B: PAGE_B}, {A: [VERIF] * 3})
        with self.assertRaises(complexe.VerificationAntiRobot):
            self.lancer(site)
        self.assertNotIn(B, site.appels)    # plus rien demandé après le refus


if __name__ == "__main__":
    unittest.main()
