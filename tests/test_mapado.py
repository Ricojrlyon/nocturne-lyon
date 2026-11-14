"""Les boutiques Mapado (Improvidence, Espace Gerson, Chapelle de la
Trinité) : la page d'un spectacle ne porte que ses sept prochaines séances
(audit A-05). La suite ne se lit que par l'API de Mapado, que son
robots.txt interdit : on compte ce qui manque, et on le dit. Page
synthétique, qui reprend le JSON embarqué de la vraie (relevé du
14/11/2026, « La fin justifie les moyens » : 7 séances sur 33)."""
import contextlib
import io
import json
import unittest
from datetime import date
from unittest import mock

from scrapers import mapado
from tests.outils import FauxSite, date_figee

BOUTIQUE = "https://salle.mapado.com"


def page(debuts, annoncees):
    donnees = {"props": {"pageProps": {"entities": {"eventDates": {
        "@context": "/v1/contexts/EventDate", "@id": "/v1/event_dates",
        "@type": "hydra:PagedCollection", "hydra:totalItems": annoncees,
        "hydra:member": [{"@type": "EventDate", "startDate": d} for d in debuts]},
        "recommendedTicketings": {"@context": "/v1/contexts/Ticketing",
                                  "@type": "hydra:PagedCollection", "hydra:totalItems": 11,
                                  "hydra:member": []}}}}}
    return ('<html><body><script id="__NEXT_DATA__" type="application/json">%s</script>'
            '</body></html>' % json.dumps(donnees))


SEPT = ["2027-01-%02dT19:30:00+01:00" % j for j in (9, 15, 16, 22, 23, 28, 29)]


class SeancesAnnoncees(unittest.TestCase):

    def test_le_total_de_la_collection_des_seances(self):
        site = FauxSite({BOUTIQUE + "/event/fin": page(SEPT, 33)})
        debuts, annoncees = mapado.sessions(site, BOUTIQUE, "fin")
        self.assertEqual((len(debuts), annoncees), (7, 33))

    def test_le_manque_est_dit(self):
        site = FauxSite({BOUTIQUE + "/event/fin": page(SEPT, 33),
                         BOUTIQUE + "/event/court": page(SEPT[:2], 2)})
        journal = io.StringIO()
        with mock.patch.object(mapado.requests, "Session", lambda: site), \
                mock.patch.object(mapado, "shows", lambda s, shop, g: [
                    {"slug": "fin", "title": "La fin justifie les moyens"},
                    {"slug": "court", "title": "Court"}]), \
                mock.patch.object(mapado.time, "sleep", lambda s: None), \
                mock.patch.object(mapado, "Date", date_figee(date(2026, 11, 14))), \
                contextlib.redirect_stderr(journal):
            evs = mapado.fetch_venue(BOUTIQUE, "Salle", "salle", None, lambda v: True)
        self.assertEqual(len(evs), 9)
        self.assertIn("1 spectacle(s) dont la page ne montre que les prochaines séances",
                      journal.getvalue())
        self.assertIn("« La fin justifie les moyens » 7/33", journal.getvalue())
        self.assertNotIn("Court", journal.getvalue())


if __name__ == "__main__":
    unittest.main()
