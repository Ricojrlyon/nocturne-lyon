"""Le géocodage des lieux (scrapers/geo.py), sans réseau : ce qu'il garde
d'une réponse de Nominatim. Depuis la vue carte (nouveauté n° 6), la
position aussi."""
import unittest
from datetime import date
from unittest import mock

from scrapers import geo


def resultat(postcode, ville, lat="45.75123456", lon="4.83987654"):
    return {"lat": lat, "lon": lon, "address": {"postcode": postcode, "city": ville}}


class Cache(unittest.TestCase):
    """Audit A-35 : ce que le géocodage met en cache, et ce qu'il purge."""

    def test_une_panne_sur_la_seconde_requete_n_est_pas_notee(self):
        # Rien à Lyon, puis Nominatim injoignable pour Villeurbanne : le
        # lieu était noté « failed » trente jours sans avoir été cherché.
        reponses = [[], None]
        with mock.patch.object(geo, "_search", lambda q: reponses.pop(0)), \
                mock.patch.object(geo.time, "sleep", lambda s: None):
            self.assertIsNone(geo._geocode_one("La Salle des Fêtes"))

    def test_une_entree_sans_date_est_datee_pour_pouvoir_etre_purgee(self):
        cache = {"Groupama Stadium": {"arr": "Autre", "confidence": "low"},
                 "Lieu récent": {"arr": "2e", "confidence": "high",
                                 "seen_at": date.today().isoformat()}}
        ecrit = []
        with mock.patch.object(geo, "_load_cache", lambda: cache), \
                mock.patch.object(geo, "_save_cache", ecrit.append):
            geo.resolve_new_venues([], verbose=False)
        self.assertEqual(cache["Groupama Stadium"]["seen_at"], date.today().isoformat())
        self.assertEqual(len(ecrit), 1)


class Geocodage(unittest.TestCase):

    def test_un_arrondissement_de_lyon_et_sa_position(self):
        self.assertEqual(geo._match_lyon([resultat("69002", "Lyon")]),
                         {"arr": "2e", "confidence": "high", "pos": [45.75123, 4.83988]})

    def test_villeurbanne(self):
        self.assertEqual(geo._match_lyon([resultat("69100", "Villeurbanne")])["arr"], "Villeurbanne")

    def test_une_reponse_sans_coordonnees_garde_l_arrondissement(self):
        r = resultat("69007", "Lyon")
        del r["lat"]
        self.assertEqual(geo._match_lyon([r]), {"arr": "7e", "confidence": "high"})

    def test_hors_de_lyon_la_commune_et_la_position(self):
        sortie = {}

        def faux(requete):
            return [resultat("69300", "Caluire-et-Cuire", "45.79", "4.84")]
        avant = geo._search
        geo._search = faux
        geo.time.sleep, dormir = (lambda s: None), geo.time.sleep
        try:
            sortie = geo._geocode_one("Radiant-Bellevue")
        finally:
            geo._search = avant
            geo.time.sleep = dormir
        self.assertEqual(sortie, {"arr": "Autre", "confidence": "low",
                                  "commune": "Caluire-et-Cuire", "pos": [45.79, 4.84]})


if __name__ == "__main__":
    unittest.main()
