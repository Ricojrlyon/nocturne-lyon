"""Le dédoublonnage, règle par règle : ce qui doit fusionner, et surtout ce
qui ne le doit pas. Priorités : salle 100, Petit Bulletin 60, Ville Morte 50."""
import unittest
from datetime import date, timedelta

from scrapers.base import OFFSITE_PLUSIEURS
from scrapers.dedup import canonical_venue_name, deduplicate
from tests.outils import evenement

J = date(2026, 10, 15)
PB = "https://www.petit-bulletin.fr/agenda/%d"
VM = "https://agenda.villemorte.fr/event/%d"


def titres(evs):
    return sorted((e.venue, e.date_start, e.time, e.title) for e in evs)


class PremierePasse(unittest.TestCase):
    """Même lieu, même jour, titres proches (≥ 0,7)."""

    def test_salle_et_agregateur_fusionnent_la_salle_gagne(self):
        salle = evenement("Le Périscope", "Soirée Funk", J, heure=None)
        pb = evenement("Le Périscope", "Soirée Funk au Périscope", J,
                       heure="21:00", url=PB % 1)
        out = deduplicate([(salle, 100), (pb, 60)])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].title, "Soirée Funk")        # l'identité de la salle
        self.assertEqual(out[0].time, "21:00")               # l'heure de l'agrégateur

    def test_deux_horaires_d_une_meme_source_font_deux_seances(self):
        a = evenement("Célestins", "Atelier théâtre", J, heure="14:00")
        b = evenement("Célestins", "Atelier théâtre", J, heure="15:00")
        self.assertEqual(len(deduplicate([(a, 100), (b, 100)])), 2)

    def test_deux_horaires_de_deux_sources_fusionnent(self):
        salle = evenement("Le Sucre", "Nuit Kompakt", J, heure="23:00")
        pb = evenement("Le Sucre", "Nuit Kompakt", J, heure="23:30", url=PB % 2)
        out = deduplicate([(salle, 100), (pb, 60)])
        self.assertEqual([(e.time, e.url) for e in out], [("23:00", salle.url)])

    def test_l_exposition_n_absorbe_pas_sa_conference(self):
        expo = evenement("Musée des Beaux-Arts", "Musée sentimental", J,
                         heure=None, fin=J + timedelta(days=180), categorie="expo")
        conf = evenement("Musée des Beaux-Arts", "Conférence : Musée sentimental",
                         J, heure="15:00", categorie="conference")
        self.assertEqual(len(deduplicate([(expo, 100), (conf, 100)])), 2)

    def test_une_plage_croise_les_dates_de_chacun_de_ses_jours(self):
        plage = evenement("La Commune", "Festival Jazz à la Commune", J,
                          heure=None, fin=J + timedelta(days=4))
        jour3 = evenement("La Commune", "Festival Jazz à la Commune",
                          J + timedelta(days=2), heure=None, url=PB % 3)
        out = deduplicate([(plage, 100), (jour3, 60)])
        self.assertEqual([e.url for e in out], [plage.url])


class HorsLesMurs(unittest.TestCase):
    """Un hors-les-murs se range, en passe 1, sous la salle où il se joue
    (BUG-15) : l'Opéra au Théâtre de La Renaissance, que le Petit Bulletin
    annonce à la Renaissance."""

    def opera(self, titre, heure="19:00", salle="Théâtre de La Renaissance"):
        ev = evenement("Opéra national de Lyon", titre, J, heure=heure)
        ev.offsite_venue = salle
        return ev

    def test_meme_concert_annonce_a_sa_salle_reelle(self):
        # « Quatuor Béla » est trop court pour la passe 2, entre deux lieux.
        op = self.opera("Quatuor Béla")
        pb = evenement("Théâtre de la Renaissance", "Quatuor Béla", J, heure="19:00", url=PB % 10)
        out = deduplicate([(op, 100), (pb, 60)])
        self.assertEqual([(e.venue, e.offsite_venue, e.url) for e in out],
                         [("Opéra national de Lyon", "Théâtre de La Renaissance", op.url)])

    def test_un_titre_qui_est_le_titre_cite_de_l_autre(self):
        op = self.opera('The Very Big Experimental Toubifri Orchestra "Le Lac"', heure="20:00")
        pb = evenement("Théâtre de la Renaissance", "Le Lac", J, heure="20:30", url=PB % 11)
        out = deduplicate([(op, 100), (pb, 60)])
        self.assertEqual([(e.title, e.time) for e in out], [(op.title, "20:00")])

    def test_le_titre_cite_ne_traverse_pas_les_lieux(self):
        # Entre deux lieux, un titre court reste à part : la règle du titre
        # cité ne joue que dans une même salle, un même jour.
        a = evenement("Le Sucre", 'DJ X "Le Lac"', J, heure="23:00")
        b = evenement("Le Transbordeur", "Le Lac", J, heure="23:00", url=PB % 13)
        self.assertEqual(len(deduplicate([(a, 100), (b, 60)])), 2)

    def test_plusieurs_salles_ne_nomment_aucune_salle(self):
        # OFFSITE_PLUSIEURS : deux productions en tournée le même soir ne se
        # croisent pas pour autant sous une salle « ailleurs ».
        a = self.opera("Gala lyrique", salle=OFFSITE_PLUSIEURS)
        b = evenement("Auditorium de Lyon", "Gala lyrique", J, heure="19:00")
        b.offsite_venue = OFFSITE_PLUSIEURS
        self.assertEqual(len(deduplicate([(a, 100), (b, 100)])), 2)

    def test_la_salle_qui_recoit_l_emporte(self):
        # BUG-30 : « Le Voyage d'hiver », que l'Opéra joue au TNP et que le
        # TNP publie aussi. À priorité égale, la carte du TNP reste, dans le
        # filtre du TNP, et prend le sous-titre plus long de l'Opéra.
        op = self.opera("Le Voyage d’hiver", heure="20:00", salle="Théâtre National Populaire")
        op.subtitle = "Opéra de chambre d'après Schubert"
        tnp = evenement("TNP - Théâtre National Populaire", "Le Voyage d’hiver", J)
        out = deduplicate([(op, 100), (tnp, 100)])
        self.assertEqual([(e.venue, e.url, e.subtitle) for e in out],
                         [("TNP - Théâtre National Populaire", tnp.url, op.subtitle)])

    def test_le_nom_de_salle_que_la_page_connait(self):
        # La page lit l'arrondissement sous le nom exact de la salle (BUG-30).
        chiens = self.opera("Chiens", salle="Les Célestins, Théâtre de Lyon")
        voyage = self.opera("Le Voyage d’hiver", salle="Théâtre National Populaire")
        out = deduplicate([(chiens, 100), (voyage, 100)])
        self.assertEqual(sorted(e.offsite_venue for e in out),
                         ["Célestins, théâtre de Lyon", "TNP - Théâtre National Populaire"])

    def test_plusieurs_salles_s_efface_devant_la_salle(self):
        # BUG-30 : « Bazar circus », que l'Opéra annonce dans trois salles
        # sans dire laquelle joue quand, et que le Radiant publie à la même
        # minute. La date que personne d'autre ne publie reste.
        op = self.opera("Bazar circus", heure="16:00", salle=OFFSITE_PLUSIEURS)
        seule = self.opera("Bazar circus", heure="11:00", salle=OFFSITE_PLUSIEURS)
        seule.date_start = (J - timedelta(days=1)).isoformat()
        radiant = evenement("Radiant-Bellevue", "BAZAR CIRCUS", J, heure="16:00")
        out = deduplicate([(op, 100), (seule, 100), (radiant, 100)])
        self.assertEqual(titres(out), [
            ("Opéra national de Lyon", seule.date_start, "11:00", "Bazar circus"),
            ("Radiant-Bellevue", J.isoformat(), "16:00", "BAZAR CIRCUS")])

    def test_a_une_autre_minute_les_deux_restent(self):
        op = self.opera("Bazar circus", heure="15:00", salle=OFFSITE_PLUSIEURS)
        radiant = evenement("Radiant-Bellevue", "BAZAR CIRCUS", J, heure="16:00")
        self.assertEqual(len(deduplicate([(op, 100), (radiant, 100)])), 2)


class DeuxiemePasse(unittest.TestCase):
    """Même jour, lieux différents, titres très proches (≥ 0,85)."""

    def test_meme_soiree_sous_deux_graphies_de_lieu(self):
        pb = evenement("Toï Toï le Zinc", "FeFan release party", J, url=PB % 4)
        vm = evenement("Dans toute la ville", "FeFan release party", J, url=VM % 4)
        out = deduplicate([(pb, 60), (vm, 50)])
        self.assertEqual([e.url for e in out], [pb.url])

    def test_titres_generiques_jamais_fusionnes_entre_lieux(self):
        a = evenement("Salle A", "Concert", J)
        b = evenement("Salle B", "Concert", J, url=PB % 5)
        self.assertEqual(len(deduplicate([(a, 100), (b, 60)])), 2)


class TroisiemePasse(unittest.TestCase):
    """Même lieu, même jour, titres trop éloignés pour le flou."""

    def test_appariement_a_la_minute_pres(self):
        # Le cas mesuré le 2026-09-20 à la Chapelle de la Trinité.
        lieu = "Chapelle de la Trinité"
        salle = [evenement(lieu, "Ovni baroque", J, heure="17:00"),
                 evenement(lieu, "Gaspard", J, heure="19:00")]
        pb = [evenement(lieu, "Ovni Sonore", J, heure="17:00", url=PB % 6),
              evenement(lieu, "Fanny Meteier", J, heure="19:00", url=PB % 7),
              evenement(lieu, "Atelier yoga", J, heure="10:00", url=PB % 8)]
        out = deduplicate([(e, 100) for e in salle] + [(e, 60) for e in pb])
        self.assertEqual(titres(out), titres(salle + pb[2:]))

    def test_effectifs_egaux_apparies_dans_l_ordre(self):
        # Le Transbordeur : deux affiches sans heure, deux noms de soirée.
        # Des affiches réelles : deux titres inventés trop semblables
        # (« ARTIST1 + ARTIST2 », « ARTIST3 + ARTIST4 ») fusionneraient dès
        # la première passe, et à raison.
        lieu = "Le Transbordeur"
        salle = [evenement(lieu, "Durden + Slmvx", J, heure=None),
                 evenement(lieu, "Meryl + Lawskie", J, heure=None)]
        pb = [evenement(lieu, "Transcendia x Transbo open-air", J, heure="18:00",
                        url=PB % 9),
              evenement(lieu, "23:59 X Organik", J, heure="23:59", url=PB % 10)]
        out = deduplicate([(e, 100) for e in salle] + [(e, 60) for e in pb])
        self.assertEqual(sorted((e.title, e.time) for e in out),
                         [("Durden + Slmvx", "18:00"), ("Meryl + Lawskie", "23:59")])

    def test_plus_de_quatre_heures_d_ecart_on_ne_touche_a_rien(self):
        lieu = "Radiant-Bellevue"
        salle = evenement(lieu, "Spectacle jeune public", J, heure="15:00")
        pb = evenement(lieu, "Rock en scène", J, heure="21:00", url=PB % 11)
        self.assertEqual(len(deduplicate([(salle, 100), (pb, 60)])), 2)

    def test_deux_agregateurs_sur_un_lieu_non_scrappe(self):
        lieu = "Théâtre de l'Élysée"
        pb = evenement(lieu, "Grand-merde", J, heure="20:00", url=PB % 12)
        vm = evenement(lieu, "GRAND-ME(R)DE / CIE Bleuir Le Cœur", J,
                       heure="20:00", url=VM % 12)
        out = deduplicate([(pb, 60), (vm, 50)])
        self.assertEqual([e.url for e in out], [pb.url])

    def test_un_reste_inegal_n_est_pas_duplique(self):
        # Le défaut corrigé le 2026-09-20 : après les paires à la minute,
        # le repli rendait le groupe ENTIER, paires comprises - doublons.
        lieu = "HEAT"
        salle = [evenement(lieu, "Afterwork", J, heure="17:00"),
                 evenement(lieu, "Live set", J, heure="19:00"),
                 evenement(lieu, "Brunch", J, heure=None)]
        pb = [evenement(lieu, "After work !", J, heure="17:00", url=PB % 13),
              evenement(lieu, "Clôture", J, heure="22:00", url=PB % 14)]
        out = deduplicate([(e, 100) for e in salle] + [(e, 60) for e in pb])
        urls = [e.url for e in out]
        self.assertEqual(len(urls), len(set(urls)))         # aucun doublon
        self.assertEqual(sorted(urls), sorted(e.url for e in salle + pb[1:]))


class GraphiesDesLieux(unittest.TestCase):

    def test_noms_canoniques(self):
        self.assertEqual(canonical_venue_name("sonic"), "Le Sonic")
        self.assertEqual(canonical_venue_name("LE PERISCOPE"), "Le Périscope")
        self.assertEqual(canonical_venue_name("Inconnu Random"), "Inconnu Random")

    def test_une_seule_graphie_elue_la_plus_accentuee(self):
        evs = [evenement("Théâtre de la Mouche", "Pièce %d" % i, J + timedelta(days=i),
                         url=VM % (20 + i)) for i in range(3)]
        evs.append(evenement("Théâtre de la Mouché", "Autre pièce", J, url=PB % 30))
        out = deduplicate([(e, 50) for e in evs[:3]] + [(evs[3], 60)])
        self.assertEqual({e.venue for e in out}, {"Théâtre de la Mouché"})

    def test_l_amphi_de_l_opera(self):
        # BUG-30 : le Petit Bulletin range les concerts de l'Amphi sous ce
        # nom, et « Crimi » est trop court pour la passe 2.
        self.assertEqual(canonical_venue_name("Amphithéâtre de l'Opéra"), "Opéra national de Lyon")
        op = evenement("Opéra national de Lyon", 'Crimi "Meli"', J)
        pb = evenement("Amphithéâtre de l'Opéra", "Crimi", J, url=PB % 50)
        self.assertEqual([e.url for e in deduplicate([(op, 100), (pb, 60)])], [op.url])

    def test_bizarre_sous_ses_deux_noms(self):
        # BUG-28 : « Bizarre! » (Petit Bulletin) et « La Machinerie -
        # Bizarre ! » (Ville Morte) sont la même salle ; le théâtre de La
        # Machinerie est un autre lieu.
        for nom in ("Bizarre!", "Bizarre !", "La Machinerie - Bizarre !"):
            self.assertEqual(canonical_venue_name(nom), "La Machinerie - Bizarre !")
        self.assertEqual(canonical_venue_name("La Machinerie - Théâtre de Vénissieux"),
                         "La Machinerie - Théâtre de Vénissieux")

    def test_un_spectacle_de_bizarre_une_seule_carte(self):
        pb = evenement("Bizarre!", "Hold Fast", J, url=PB % 40)
        vm = evenement("La Machinerie - Bizarre !", "Hold Fast - Cie Ma’, Marion Alzieu", J,
                       url=VM % 41)
        out = deduplicate([(pb, 60), (vm, 50)])
        self.assertEqual(titres(out), [("La Machinerie - Bizarre !", J.isoformat(), "20:00",
                                        "Hold Fast")])

    def test_jack_jack_et_la_mediane_sous_un_seul_nom(self):
        # BUG-34 : le Petit Bulletin et Ville Morte les écrivent chacun à sa
        # façon, et le filtre par lieu coupait chaque salle en deux.
        for nom in ("Jack Jack - MJC Aragon", "MJC Louis Aragon / Jack Jack"):
            self.assertEqual(canonical_venue_name(nom), "Jack Jack - MJC Aragon")
        for nom in ("Café La Médiane", "La Médiane, tiers-lieu féministe"):
            self.assertEqual(canonical_venue_name(nom), "La Médiane, tiers-lieu féministe")

    def test_un_concert_du_jack_jack_une_seule_carte(self):
        # « Monolord » est trop court pour la passe 2, entre deux lieux :
        # seul l'alias réunit les deux annonces.
        pb = evenement("Jack Jack - MJC Aragon", "Monolord", J, url=PB % 60)
        vm = evenement("MJC Louis Aragon / Jack Jack", "Monolord + Dopelord", J, url=VM % 61)
        out = deduplicate([(pb, 60), (vm, 50)])
        self.assertEqual(titres(out), [("Jack Jack - MJC Aragon", J.isoformat(), "20:00",
                                        "Monolord")])


if __name__ == "__main__":
    unittest.main()
