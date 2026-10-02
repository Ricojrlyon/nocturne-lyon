"""Le collecteur du TNG : une date par séance tout public (BUG-16). Pages
synthétiques, qui reprennent la structure du vrai site : la carte du
programme et sa plage « 02 > 06 oct. », et la fiche du spectacle avec ses
deux listes de séances, tout public (div.event-sessions) et scolaires
(div.event-school)."""
import contextlib
import io
import unittest
from datetime import date
from unittest import mock

from scrapers import tng
from tests.outils import FauxSite, date_figee

FICHE = tng.HOST + "/evenement/%s/"
ECOLE = ('<div class="event-school event-accordion"><div class="event-accordion-content">'
         '<p class="month">octobre</p><div class="session-line"><p class="date">lun 05</p>'
         '<span class="hour">10:00</span> | <span class="hour">14:30</span></div></div></div>')


def carte(slug, titre, quand):
    """La carte du programme : la date en morceaux (« 02 », « > », « 06 », « oct. »)."""
    return ('<a href="/evenement/%s/"><div class="dates">%s</div><h3>%s</h3><p>TNG-Vaise</p></a>'
            % (slug, "".join("<span>%s</span>" % x for x in quand.split()), titre))


def fiche(mois, seances, ecole=""):
    """seances : [(jour, [heures])], toutes du même mois."""
    lignes = "".join('<div class="session-line"><p class="date">%s</p>%s</div>'
                     % (jour, " | ".join('<a href="#"><span class="hour">%s<em class="bds-session">O</em>'
                                         '</span></a>' % h for h in heures))
                     for jour, heures in seances)
    return ('<html><body><div class="event-sessions"><p class="month">%s</p>%s</div>%s</body></html>'
            % (mois, lignes, ecole))


class SeancesToutPublic(unittest.TestCase):

    def lire(self, cartes, fiches, jour=date(2026, 10, 1)):
        """fetch() complet, au 1er octobre 2026 : [(date, fin, heure, titre)]."""
        pages = {tng.URL: "<html><body>%s</body></html>" % "".join(cartes)}
        pages.update({FICHE % slug: html for slug, html in fiches.items()})
        site = FauxSite(pages)
        # Le journal du collecteur (son DIAGNOSTIC quand il ne rend rien) ne
        # doit pas se mêler à celui du passage.
        with mock.patch.object(tng, "Date", date_figee(jour)), \
                mock.patch.object(tng.time, "sleep", lambda s: None), \
                mock.patch("requests.request", site.request), \
                contextlib.redirect_stderr(io.StringIO()):
            return [(e.date_start, e.date_end, e.time, e.title) for e in tng.fetch()]

    def test_une_date_par_seance_tout_public_sans_les_scolaires(self):
        # « Boule de neige », 2 > 6 octobre : publié jusqu'ici au 6, jour
        # de séances scolaires seulement. Ses séances publiques : le 2 et le 3.
        lu = self.lire([carte("boule", "Boule de neige", "02 > 06 oct.")],
                       {"boule": fiche("octobre", [("ven 02", ["19:30"]), ("sam 03", ["17:00"])], ECOLE)})
        self.assertEqual(lu, [("2026-10-02", None, "19:30", "Boule de neige"),
                              ("2026-10-03", None, "17:00", "Boule de neige")])

    def test_plusieurs_heures_un_meme_jour(self):
        lu = self.lire([carte("spunk", "Spunk!", "20 > 23 janv.")],
                       {"spunk": fiche("janvier", [("mer 20", ["15:00"]), ("sam 23", ["15:00", "19:00"])])})
        self.assertEqual([(d, h) for d, _, h, _ in lu],
                         [("2027-01-20", "15:00"), ("2027-01-23", "15:00"), ("2027-01-23", "19:00")])

    def test_un_jour_de_la_semaine_faux_est_ecarte(self):
        # Le 3 octobre 2026 est un samedi, pas un lundi.
        lu = self.lire([carte("boule", "Boule de neige", "02 > 06 oct.")],
                       {"boule": fiche("octobre", [("ven 02", ["19:30"]), ("lun 03", ["17:00"])])})
        self.assertEqual([(d, h) for d, _, h, _ in lu], [("2026-10-02", "19:30")])

    def test_seance_loin_de_la_carte_la_carte_garde_le_jour_et_la_fiche_l_heure(self):
        # La fiche de l'atelier du « Chat sur la photo » (carte : 30 janvier)
        # reprenait la séance du 16 janvier d'un autre atelier.
        lu = self.lire([carte("atelier", "Atelier intergénérationnel", "30 janv.")],
                       {"atelier": fiche("janvier", [("sam 16", ["14:30"])])})
        self.assertEqual(lu, [("2027-01-30", None, "14:30", "Atelier intergénérationnel")])

    def test_dernieres_seances_publiques_passees_rien_n_est_publie(self):
        # Le 4 octobre, la carte court encore jusqu'au 6, mais il ne reste
        # que les séances scolaires : la plage ne doit pas revenir.
        lu = self.lire([carte("boule", "Boule de neige", "02 > 06 oct.")],
                       {"boule": fiche("octobre", [("ven 02", ["19:30"]), ("sam 03", ["17:00"])], ECOLE)},
                       jour=date(2026, 10, 4))
        self.assertEqual(lu, [])

    def test_fiche_sans_seances_la_plage_de_la_carte_entiere(self):
        # Fiche introuvable : la plage « 02 > 06 oct. », lue en entier — et
        # non plus sa seule date de fin.
        lu = self.lire([carte("boule", "Boule de neige", "02 > 06 oct.")], {})
        self.assertEqual(lu, [("2026-10-02", "2026-10-06", None, "Boule de neige")])


if __name__ == "__main__":
    unittest.main()
