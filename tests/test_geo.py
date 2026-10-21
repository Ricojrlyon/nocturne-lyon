"""Le géocodage des lieux (scrapers/geo.py), sans réseau : ce qu'il garde
d'une réponse de Nominatim. Depuis la vue carte (nouveauté n° 6), la
position aussi."""
import unittest

from scrapers import geo


def resultat(postcode, ville, lat="45.75123456", lon="4.83987654"):
    return {"lat": lat, "lon": lon, "address": {"postcode": postcode, "city": ville}}


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
