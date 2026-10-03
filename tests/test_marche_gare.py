"""Le Marché Gare (BUG-26) : un titre sans ses mentions ni ses genres, et ni
soirée « Hors les murs » ni formation professionnelle. Page synthétique, qui
reprend la structure de la vraie carte d'agenda."""
import contextlib
import io
import unittest
from datetime import date
from unittest import mock

import aggregate
from scrapers import marche_gare
from tests.outils import FauxSite, evenement


def carte(slug, jour, mois, heure, titre, mentions=(), genres=(), sous_titre=None):
    # Sur le vrai site, une mention porte data-term-name comme un genre.
    badges = "".join('<span class="badge bg-primary" data-first-level="true" '
                     'data-term-name="x">%s</span>' % m for m in mentions)
    taxo = "".join('<span class="uppercase font-display" data-first-level="true" '
                   'data-term-name="x">%s</span>' % g for g in genres)
    st = ('<div class="agenda--subtitle text-2xl">%s</div>' % sous_titre) if sous_titre else ""
    return ('<a class="agenda--item" href="/agenda/%s"><div class="agenda--item-date">'
            '<span class="evt-date-day">%s.</span> <span class="sr-only">%s</span> '
            '<span class="evt-date-hour">%s</span></div>'
            '<div class="agenda--label-container">%s</div>'
            '<div class="agenda--taxo-container agenda--evt-categories">%s</div>'
            '<div class="agenda--title"><span class="leading-none">%s</span></div>%s</a>'
            % (slug, jour, mois, heure, badges, taxo, titre, st))


CARTES = [
    carte("hypno5e", "10", "octobre", "20:00", "HYPNO5E + HIPPOTRAKTOR",
          mentions=["Épuisé sur ce point de vente"], genres=["Post-Metal"]),
    carte("ferdi", "07", "novembre", "20:00", "FERDI", genres=["Groove", "Jazz"],
          sous_titre="+ EUROS CHILDS"),
    carte("zinee", "20", "novembre", "20:00", "ZINÉE", mentions=["Hors les murs"],
          genres=["Rap"], sous_titre="> à Bizarre! Vénissieux"),
    carte("mao", "09", "novembre", "09:00", "FORMATION PRO 'MAO'", genres=["Formation"],
          sous_titre="Du 9 au 20 novembre"),
    carte("annule", "19", "octobre", "20:00", "TEENAGE FANCLUB", mentions=["Annulé"],
          genres=["Indie Rock"]),
]


class Cartes(unittest.TestCase):

    def lire(self):
        self.addCleanup(marche_gare._ECARTES.clear)
        site = FauxSite({marche_gare.URL: "<html><body>%s</body></html>" % "".join(CARTES)})
        with mock.patch("requests.request", site.request), \
                contextlib.redirect_stderr(io.StringIO()) as journal:
            evs = marche_gare.fetch()
        return {e.url.rsplit("/", 1)[-1]: e for e in evs}, journal.getvalue()

    def test_le_titre_sans_les_mentions_ni_les_genres(self):
        lu, _ = self.lire()
        e = lu["hypno5e"]
        self.assertEqual((e.title, e.subtitle, e.category, e.time),
                         ("HYPNO5E + HIPPOTRAKTOR", "Épuisé sur ce point de vente",
                          "Post-Metal", "20:00"))

    def test_les_genres_ensemble_font_la_categorie(self):
        # « Groove » seul ne se range dans aucune famille de la page.
        lu, _ = self.lire()
        e = lu["ferdi"]
        self.assertEqual((e.title, e.subtitle, e.category), ("FERDI", "+ EUROS CHILDS",
                                                             "Groove / Jazz"))

    def test_hors_les_murs_et_formation_ecartes(self):
        lu, journal = self.lire()
        self.assertNotIn("zinee", lu)
        self.assertNotIn("mao", lu)
        self.assertIn("1 hors les murs (ZINÉE > à Bizarre! Vénissieux), 1 formation(s)", journal)

    def test_un_annule_reste_reconnu_apres_la_dedup(self):
        # La mention reste en tête du titre : le filtre des annulés (BUG-19)
        # la lit après la déduplication.
        lu, _ = self.lire()
        self.assertEqual(lu["annule"].title, "Annulé – TEENAGE FANCLUB")
        self.assertTrue(aggregate._est_annule(lu["annule"]))

    def test_l_agregateur_ne_republie_pas_ce_que_la_salle_ecarte(self):
        # Le Petit Bulletin annonçait Ivanoé « au Marché Gare », quand la
        # salle le donne hors les murs, à la MJC du Vieux-Lyon.
        self.lire()
        pb = [(evenement("Marché Gare", titre, date(2026, 11, 20),
                         url="https://www.petit-bulletin.fr/agenda-1"), 60)
              for titre in ("Zinée", "Teenage Fanclub")]
        with contextlib.redirect_stdout(io.StringIO()):
            garde = aggregate._appliquer_les_regles_des_salles(pb)
        self.assertEqual([e.title for e, _ in garde], ["Teenage Fanclub"])


if __name__ == "__main__":
    unittest.main()
