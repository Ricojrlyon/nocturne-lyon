"""Le collecteur du TNP : un parcours « Hors les murs » n'est pas au TNP
(BUG-33). Agenda synthétique, qui reprend la structure du vrai : un bloc
par journée, sa date dans un <time>, puis une ligne par représentation
avec son heure, son titre et sa salle."""
import contextlib
import io
import unittest
from datetime import date
from unittest import mock

from scrapers import tnp
from scrapers.base import OFFSITE_PLUSIEURS
from tests.outils import FauxSite, date_figee

FICHE = tnp.BASE + "/spectacle/%s/"


def ligne(slug, titre, heure, salle):
    return ('<div class="agenda-manifestation-item"><time class="agenda-manifestation-item__time" '
            'datetime="%s">%s</time><a class="agenda-manifestation-item__title" href="%s">%s</a>'
            '<p class="manifestation-place">%s</p></div>'
            % (heure, heure, FICHE % slug, titre, salle))


def fiche(slug):
    return ('<html><head><meta property="og:image" content="%s/affiches/%s.jpg"></head>'
            '<body></body></html>' % (tnp.BASE, slug))


class HorsLesMurs(unittest.TestCase):

    def test_un_parcours_hors_les_murs_n_est_pas_au_tnp(self):
        # Le point de départ n'est donné qu'à l'inscription : la page
        # l'étiquette « ailleurs », sans arrondissement, au lieu de
        # l'envoyer à Villeurbanne.
        jour = ('<div class="agenda__month-day"><time datetime="2026-10-04">4 oct.</time>%s</div>'
                % "".join([
                    ligne("parcours", "Avant que les mots ne retournent à l’air", "16:00",
                          "Hors les murs"),
                    ligne("affaire", "Une petite affaire", "20:00", "salle Roger-Planchon"),
                    ligne("studio", "Lecture", "18:00", "Studio 24")]))
        site = FauxSite({tnp.AGENDA: "<html><body>%s</body></html>" % jour,
                         **{FICHE % s: fiche(s) for s in ("parcours", "affaire", "studio")}})
        with mock.patch.object(tnp, "Date", date_figee(date(2026, 10, 1))), \
                mock.patch.object(tnp.requests, "Session", lambda: site), \
                mock.patch.object(tnp.time, "sleep", lambda s: None), \
                contextlib.redirect_stderr(io.StringIO()) as journal:
            lu = {e.title: (e.venue, e.offsite_venue, e.time) for e in tnp.fetch()}
        self.assertEqual(lu, {
            "Avant que les mots ne retournent à l’air": (tnp.VENUE, OFFSITE_PLUSIEURS, "16:00"),
            "Une petite affaire": (tnp.VENUE, None, "20:00"),
            # Une autre salle inconnue reste publiée, et signalée.
            "Lecture": (tnp.VENUE, None, "18:00")})
        self.assertIn("hors les murs, marqué(s) « ailleurs » : Avant que les mots",
                      journal.getvalue())
        self.assertIn("salle(s) hors des deux habituelles, conservée(s) : Studio 24",
                      journal.getvalue())


if __name__ == "__main__":
    unittest.main()
