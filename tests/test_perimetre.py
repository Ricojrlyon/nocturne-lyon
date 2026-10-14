"""Le périmètre du site (2026-10-14) : Lyon, Villeurbanne, Caluire-et-Cuire,
Oullins-Pierre-Bénite, Vaulx-en-Velin, Décines-Charpieu et La Mulatière.
Ce qui se joue dans une autre commune est écarté avant publication ; ce
que rien ne situe est gardé.

Les lieux viennent du vrai lieux.json, le géocodage d'un faux
venue_arrondissements.json.
"""
import contextlib
import io
import unittest
from datetime import date, timedelta

import aggregate
from scrapers.base import OFFSITE_PLUSIEURS
from tests.outils import atelier, evenement

J = date(2026, 10, 14)
GEO = {
    "Le Toboggan": {"arr": "Autre", "commune": "Décines-Charpieu", "confidence": "low"},
    "Le Faitout": {"arr": "Autre", "commune": "La Mulatière", "confidence": "low"},
    "MJC Oullins": {"arr": "Autre", "commune": "Oullins", "confidence": "low"},
    "Parc de la Tête-d'Or": {"arr": "Autre", "commune": "Lyon", "confidence": "low"},
    "Salle au bord de Lyon": {"arr": "5e", "commune": "Sainte-Foy-lès-Lyon", "confidence": "low"},
    "Salle des fêtes de Brignais": {"arr": "Autre", "commune": "Brignais", "confidence": "low"},
    "Théâtre Théo Argence": {"arr": "Autre", "commune": "Saint-Priest", "confidence": "low"},
    "Lieu introuvable": {"arr": None, "confidence": "failed"},
}


def soiree(lieu, ailleurs=None):
    e = evenement(lieu, "Soirée à %s %s" % (lieu, ailleurs or ""), J + timedelta(days=1))
    e.offsite_venue = ailleurs
    return e


def gardes(evenements, geo=GEO):
    with contextlib.redirect_stdout(io.StringIO()):        # le journal, sans bruit
        restent = aggregate._dans_le_perimetre(evenements, geo)
    return [(e.venue, e.offsite_venue) for e in restent]


class Perimetre(unittest.TestCase):

    def test_lyon_villeurbanne_et_les_communes_gardees(self):
        lieux = ["Le Sucre", "Le Transbordeur", "Radiant-Bellevue", "Grrrnd Zero",
                 "LDLC Arena", "Théâtre de La Renaissance", "Le Toboggan", "Le Faitout",
                 "Parc de la Tête-d'Or", "Salle au bord de Lyon"]
        self.assertEqual(gardes([soiree(l) for l in lieux]), [(l, None) for l in lieux])

    def test_les_autres_communes_sont_ecartees(self):
        # Feyzin et Vienne par lieux.json, Brignais par le géocodage.
        lieux = ["L'Épicerie Moderne", "Théâtre François Ponsard", "Salle des fêtes de Brignais"]
        self.assertEqual(gardes([soiree(l) for l in lieux]), [])

    def test_un_lieu_que_rien_ne_situe_est_garde(self):
        lieux = ["Lieu introuvable", "Un lieu jamais géocodé"]
        self.assertEqual(gardes([soiree(l) for l in lieux]), [(l, None) for l in lieux])

    def test_les_graphies_se_rapprochent_sans_casse_ni_accents(self):
        self.assertEqual(gardes([soiree("Théâtre de la Renaissance"),
                                 soiree("l'epicerie moderne"),
                                 soiree("salle des fetes de brignais")]),
                         [("Théâtre de la Renaissance", None)])

    def test_oullins_sous_son_ancien_nom(self):
        self.assertEqual(gardes([soiree("MJC Oullins")]), [("MJC Oullins", None)])

    def test_c_est_la_salle_ou_l_on_va_qui_compte(self):
        opera = "Opéra national de Lyon"
        self.assertEqual(gardes([soiree(opera, "Théâtre Théo Argence"),
                                 soiree(opera, "Théâtre de La Renaissance"),
                                 soiree(opera, OFFSITE_PLUSIEURS),
                                 soiree(opera)]),
                         [(opera, "Théâtre de La Renaissance"), (opera, OFFSITE_PLUSIEURS),
                          (opera, None)])

    def test_sans_geocodage_lieux_json_suffit(self):
        self.assertEqual(gardes([soiree("L'Épicerie Moderne"), soiree("Le Toboggan")], geo=None),
                         [("Le Toboggan", None)])

    def test_dans_la_chaine_entiere(self):
        # Un passage complet : l'agenda publie à Feyzin, à Vienne et à
        # Décines ; seuls les deux premiers disparaissent, et le journal le
        # dit, commune par commune.
        salle = [evenement("Le Sucre", "Nuit %d" % i, J + timedelta(days=1 + i)) for i in range(12)]
        agenda = [evenement(l, "Concert à %s" % l, J + timedelta(days=2),
                            url="https://www.petit-bulletin.fr/x/%d" % i)
                  for i, l in enumerate(["L'Épicerie Moderne", "Théâtre François Ponsard",
                                         "Le Toboggan"])]
        with atelier(J) as a:
            code = a.lancer([("Le Sucre", lambda: salle)],
                            [("Petit Bulletin", lambda: agenda, 60)], geo=GEO)
            lieux = {e["venue"] for e in a.publie()["events"]}
            journal = a.journal.getvalue()
        self.assertEqual(code, 0)
        self.assertEqual(lieux, {"Le Sucre", "Le Toboggan"})
        self.assertIn("[périmètre] 2 événement(s) écarté(s)", journal)
        self.assertIn("Feyzin 1 : L'Épicerie Moderne 1", journal)


if __name__ == "__main__":
    unittest.main()
