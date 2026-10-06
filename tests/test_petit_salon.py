"""Le collecteur du Petit Salon (BUG-10) : sa programmation depuis la refonte
du site, le 2 octobre 2026 - une carte par soirée, datée « ven 02 Oct ».
Pages synthétiques, qui reprennent la structure du vrai site (classes
« card-event »)."""
import unittest
from datetime import date
from unittest import mock

from scrapers import petit_salon
from tests.outils import FauxSite, date_figee

PROGRAMMATION = "https://www.lpslyon.fr/programmation/"
RESERVER = "https://shotgun.live/events/soiree"
AFFICHE = "https://www.lpslyon.fr/wp-content/uploads/2026/09/affiche.jpg"


def carte(quand, titre):
    return """
<article class="card-event card-item">
  <div class="card-event-image"><img src="%(affiche)s" alt=""></div>
  <div class="card-event-content">
    <div class="card-event-badges"><div class="badge"><span class="suptitle">Grande salle</span>
      <span class="tag">Techno</span></div></div>
    <div class="card-event-meta">
      <span class="card-event-date"><span class="card-event-date-marks">////</span>
        <span class="card-event-date-text">%(quand)s</span></span>
      <div class="card-event-row">
        <a class="card-event-title-link" href="https://www.lpslyon.fr/programmation/soiree/">
          <span class="card-event-title-track"><h2 class="card-event-title">%(titre)s</h2></span>
          <span aria-hidden="true" class="card-event-title-marquee"><span>%(titre)s</span></span></a>
        <a class="btn" href="%(reserver)s">Réserver</a>
      </div>
    </div>
  </div>
</article>""" % {"quand": quand, "titre": titre, "affiche": AFFICHE, "reserver": RESERVER}


class Programmation(unittest.TestCase):

    def lire(self, *cartes):
        """fetch() complet, au 2 octobre 2026 ; {titre: soirée}."""
        site = FauxSite({PROGRAMMATION: "<html><body><h1>Programmation</h1>%s</body></html>"
                                        % "".join(cartes)})
        with mock.patch.object(petit_salon, "Date", date_figee(date(2026, 10, 2))), \
                mock.patch("requests.request", site.request):
            return {e.title: e for e in petit_salon.fetch()}

    def test_une_carte_une_soiree(self):
        e = self.lire(carte("ven 16 Oct", "FLYMEON, LUKE NOIZE & MORE"))["FLYMEON, LUKE NOIZE & MORE"]
        self.assertEqual((e.venue, e.date_start, e.time, e.category),
                         ("Le Petit Salon", "2026-10-16", None, "club"))
        self.assertEqual(e.url, RESERVER)                   # le bouton « Réserver »
        self.assertEqual(e.image, AFFICHE)

    def test_les_douze_mois(self):
        # WordPress abrège en français « Jan Fév Mar … Sep … Déc » ; « Jan »,
        # « Fév », « Mar », « Sep » sont plus courts que les clés de
        # FR_MONTHS. Le « mar » de mardi, avant le quantième, n'est pas mars.
        # Une date passée de plus de quinze jours est celle de l'an prochain.
        mois = ["Jan", "Fév", "Mar", "Avr", "Mai", "Juin", "Juil", "Août", "Sep", "Oct", "Nov", "Déc"]
        lu = self.lire(*[carte("mar 10 %s" % m, "SOIRÉE %d" % i) for i, m in enumerate(mois, 1)])
        self.assertEqual({t: e.date_start for t, e in lu.items()},
                         {"SOIRÉE %d" % i: "%d-%02d-10" % (2027 if i < 10 else 2026, i)
                          for i in range(1, 13)})

    def test_un_titre_avec_des_chiffres_ne_change_ni_le_jour_ni_le_lien(self):
        lu = self.lire(carte("ven 16 Oct", "10 ANS DU CLUB : DJ X & MORE"),
                       carte("sam 24 Oct", "AFTER DU 14 JUIL"),
                       carte("sam 31 Oct", "HALLOWEEN 31 OCT : RAVE"))
        self.assertEqual({t: e.date_start for t, e in lu.items()},
                         {"10 ANS DU CLUB : DJ X & MORE": "2026-10-16",
                          "AFTER DU 14 JUIL": "2026-10-24",
                          "HALLOWEEN 31 OCT : RAVE": "2026-10-31"})
        self.assertEqual({e.url for e in lu.values()}, {RESERVER})


if __name__ == "__main__":
    unittest.main()
