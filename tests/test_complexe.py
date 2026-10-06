"""Le collecteur du Complexe : lecture du catalogue et des séances, et la
vérification anti-robot de son hébergeur (BUG-3), à laquelle il renonce
sans attendre depuis SUIVI-1. Pages synthétiques, qui reprennent la
structure du vrai site (classes « tly_ »)."""
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
        with mock.patch.object(complexe, "Date", date_figee(date(2026, 10, 1))), \
                contextlib.redirect_stderr(journal):
            out = complexe._seances(site, A, 2026, "Le Complexe", [])
        self.assertEqual(out, [("2026-12-31", "20:30"), ("2027-01-01", "21:00")])
        self.assertIn("1 séance(s) écartée(s)", journal.getvalue())

    def test_seances_apres_le_nouvel_an_seulement(self):
        # BUG-12 : la page ne garde que les séances à venir. Plage « du
        # 23/09/2026 au 20/01/2027 », année 2026 : il ne restait à Jules
        # Robin que « mercredi 20 janvier », écartée à chaque passage - et
        # au 1er janvier, tous les spectacles à cheval sur deux ans
        # perdaient ainsi leurs séances de l'année neuve.
        cas = [
            (date(2026, 10, 1), [("Mercredi 20 janvier", "20h30")], [("2027-01-20", "20:30")]),
            (date(2027, 1, 5), [("Vendredi 8 janvier", "20h00"), ("Samedi 9 janvier", "20h00"),
                                ("Mardi 2 février", "19h30")],
             [("2027-01-08", "20:00"), ("2027-01-09", "20:00"), ("2027-02-02", "19:30")]),
            # Passée de moins de quinze jours, pas encore retirée : même année.
            (date(2026, 10, 1), [("Mardi 29 septembre", "20h00")], [("2026-09-29", "20:00")]),
            # Le contrôle du jour garde le dernier mot : le 20 janvier 2027
            # est un mercredi, pas un lundi.
            (date(2026, 10, 1), [("Lundi 20 janvier", "20h30")], []),
        ]
        for jour, lignes, attendu in cas:
            site = FauxSite({A: seances(*lignes)})
            with mock.patch.object(complexe, "Date", date_figee(jour)), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(complexe._seances(site, A, 2026, "Le Complexe", []), attendu,
                                 "au %s" % jour)


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

    def test_refuse_on_renonce_aussitot_en_le_disant(self):
        # SUIVI-1 : plus d'attente. Le refus n'est pas redemandé, même quand
        # un nouvel essai aurait été servi - mesuré, il ne l'était jamais
        # dans le même passage.
        site = FauxSite({LISTING: CATALOGUE, A: PAGE_A, B: PAGE_B}, {LISTING: [VERIF]})
        with self.assertRaisesRegex(complexe.VerificationAntiRobot, "1 essai"):
            self.lancer(site)
        self.assertEqual(self.attentes_de_verification(), [])
        self.assertEqual(site.appels, [LISTING])

    def test_page_inconnue_sans_catalogue_rendue_vide(self):
        # Ni la vérification ni le catalogue : rendue vide aussitôt, et le
        # garde-fou d'aggregate.py reprend la veille.
        site = FauxSite({LISTING: CATALOGUE, A: PAGE_A, B: PAGE_B},
                        {LISTING: [(200, b"<html>Un instant</html>", {})]})
        self.assertEqual(self.lancer(site), [])
        self.assertEqual(self.attentes_de_verification(), [])
        self.assertEqual(site.appels, [LISTING])

    def test_avec_un_budget_d_attente_le_refus_est_redemande(self):
        # La mécanique reste en état, pour le jour où une attente servirait :
        # il suffit de rendre un budget à ATTENTES_VERIFICATION.
        site = FauxSite({LISTING: CATALOGUE, A: PAGE_A, B: PAGE_B}, {LISTING: [VERIF]})
        with mock.patch.object(complexe, "ATTENTES_VERIFICATION", (60, 120)):
            evs = self.lancer(site)
        self.assertEqual(len(evs), 4)
        self.assertEqual(self.attentes_de_verification(), [60])

    def test_page_spectacle_refusee_pas_de_programme_troue(self):
        site = FauxSite({LISTING: CATALOGUE, A: PAGE_A, B: PAGE_B}, {A: [VERIF] * 3})
        with self.assertRaises(complexe.VerificationAntiRobot):
            self.lancer(site)
        self.assertNotIn(B, site.appels)    # plus rien demandé après le refus
        self.assertEqual(self.attentes_de_verification(), [])


if __name__ == "__main__":
    unittest.main()
