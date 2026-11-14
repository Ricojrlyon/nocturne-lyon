"""Les pages enregistrées (audit A-28) : enregistrer une source une fois,
la rejouer hors ligne dans les tests.

    python -m tests.enregistrer celestins     une source, par le nom de son module
    python -m tests.enregistrer --liste       les sources, enregistrées ou non

Pourquoi. 18 sources sur 37 n'avaient aucun test de lecture, et les autres
n'étaient essayées que sur de petits bouts de HTML écrits à la main autour
d'un bug ancien. Une retouche du code qui change la lecture d'une salle ne
se voyait qu'en production.

L'enregistrement lance le collecteur de la salle comme le passage de nuit,
UNE fois, et garde dans tests/pages/<source>.json.xz :
  - chaque réponse reçue (adresse, code, en-têtes, contenu) ;
  - les fiches que le cache connaissait déjà (le cache est « chaud », comme
    la nuit : une fiche déjà lue n'est pas redemandée) ;
  - les fichiers du dépôt que le collecteur lit (le relevé de l'ASVEL) ;
  - le jour, et ce que le collecteur en a tiré : les événements attendus.

Le test (tests/test_pages_enregistrees.py) rejoue tout cela sans réseau, à
la date de l'enregistrement, et exige les mêmes événements au caractère
près. Une requête qui n'a pas été enregistrée échoue.

Il ne voit pas la refonte du site d'une salle : c'est le rôle des
garde-fous du passage et de l'alerte des salles en panne.

Politesse : avant la première requête vers un site, l'outil lit son
robots.txt, refuse ce qu'il interdit, et respecte son Crawl-delay ; le
collecteur garde ses propres délais. Les fichiers du robot ne sont jamais
écrits : le cache des fiches est lu, copié, et la copie seule sert.
"""
from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import io
import json
import lzma
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter
from requests.structures import CaseInsensitiveDict
from requests.utils import get_encoding_from_headers

import aggregate
from scrapers import base, detail_cache
from tests.outils import date_figee

PAGES = Path(__file__).resolve().parent / "pages"
UA = "nocturne-lyon-events/1.0 (+https://github.com/Ricojrlyon/nocturne-lyon)"

# Les sources jamais enregistrées, et pourquoi.
EXCLUES = {
    "rugby": "la billetterie du LOU (billetterie.lourugby.fr) ne doit jamais être sollicitée",
    "sonic": "son robots.txt interdit tout robot (Disallow: /)",
    "complexe": "son hébergeur filtre les robots (SiteGround) : on ne le sollicite pas pour un test",
}

# Les fichiers du dépôt qu'un collecteur lit, par module : (attribut qui
# porte le chemin). Gardés dans l'enregistrement, rejoués tels quels.
FICHIERS_LUS = {
    "asvel": ("BETCLIC_FIGE",),
}

# La vraie méthode d'envoi de requests, avant toute substitution.
_ENVOI = HTTPAdapter.send


class Interdit(Exception):
    """Une adresse que le robots.txt du site interdit."""


def sources() -> dict:
    """{nom du module : (nom affiché, fonction du collecteur)}."""
    out = {}
    for nom, fonction, *_ in list(aggregate.SCRAPERS) + list(aggregate.AGGREGATORS):
        out[fonction.__module__.rsplit(".", 1)[1]] = (nom, fonction)
    return out


def chemin(cle: str) -> Path:
    return PAGES / ("%s.json.xz" % cle)


def lire(cle: str) -> dict:
    with lzma.open(chemin(cle), "rt", encoding="utf-8") as f:
        return json.load(f)


def _ecrire(cle: str, donnees: dict) -> None:
    PAGES.mkdir(exist_ok=True)
    texte = json.dumps(donnees, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    # xz et non gzip : gzip ne voit les répétitions que sur 32 Ko, et les
    # pages d'une même salle se répètent d'une page à l'autre (les 55
    # pages Mapado d'Improvidence : 1 540 Ko en gzip, 91 Ko en xz). Le même
    # enregistrement donne le même fichier, octet pour octet.
    chemin(cle).write_bytes(lzma.compress(texte.encode("utf-8"),
                                          preset=9 | lzma.PRESET_EXTREME))


def trier(evenements: list) -> list:
    """Les événements dans un ordre fixe : un collecteur qui parcourt un
    ensemble ne les rend pas toujours dans le même."""
    return sorted(evenements, key=lambda e: json.dumps(e, ensure_ascii=False, sort_keys=True))


def _corps(requete) -> str:
    corps = requete.body
    if corps is None:
        return ""
    if isinstance(corps, str):
        corps = corps.encode("utf-8")
    return hashlib.sha256(corps).hexdigest()[:16]


def _cle(requete) -> dict:
    return {"methode": requete.method, "url": requete.url, "corps": _corps(requete)}


def regles(statut: int, texte: str) -> RobotFileParser:
    """Les règles d'un robots.txt, d'après sa réponse : 401 ou 403, tout
    est interdit ; absent, tout est permis (la convention du format)."""
    rp = RobotFileParser()
    if statut in (401, 403):
        rp.disallow_all = True
    elif statut == 200:
        rp.parse(texte.splitlines())
    else:
        rp.allow_all = True
    return rp


# ---------------------------------------------------------------------------
# L'enregistrement
# ---------------------------------------------------------------------------

class _CacheEspion(dict):
    """Le cache des fiches, qui note les adresses consultées."""

    def __init__(self, *args):
        super().__init__(*args)
        self.vues: list = []

    def get(self, cle, defaut=None):
        self.vues.append(cle)
        return super().get(cle, defaut)


class Enregistreur:
    def __init__(self):
        self.echanges: list = []
        self.robots: dict = {}
        self.dernier: dict = {}
        self.incidents: list = []

    def _regles(self, adapter, url: str) -> RobotFileParser:
        p = urlsplit(url)
        site = "%s://%s" % (p.scheme, p.netloc)
        if site not in self.robots:
            demande = requests.Request("GET", site + "/robots.txt",
                                       headers={"User-Agent": UA}).prepare()
            try:
                r = _ENVOI(adapter, demande, timeout=30)
                statut, texte = r.status_code, r.content.decode("utf-8", "replace")
            except requests.RequestException:
                statut, texte = 0, ""
            self.robots[site] = regles(statut, texte)
        return self.robots[site]

    def envoyer(self, adapter, requete, **options):
        rp = self._regles(adapter, requete.url)
        if not rp.can_fetch(UA, requete.url):
            raise Interdit("interdit par le robots.txt du site : %s" % requete.url)
        hote = urlsplit(requete.url).netloc
        delai = rp.crawl_delay(UA) or 0
        if delai and hote in self.dernier:
            reste = self.dernier[hote] + float(delai) - time.monotonic()
            if reste > 0:
                time.sleep(reste)
        try:
            r = _ENVOI(adapter, requete, **options)
            contenu = r.content
        except requests.RequestException as exc:
            self.echanges.append(dict(_cle(requete), erreur=type(exc).__name__))
            self.incidents.append("%s %s : %s" % (requete.method, requete.url, exc))
            raise
        finally:
            self.dernier[hote] = time.monotonic()
        echange = dict(_cle(requete), statut=r.status_code, raison=r.reason,
                       entetes={k: v for k, v in r.headers.items() if k.lower() != "set-cookie"})
        try:
            echange["texte"] = contenu.decode("utf-8")
        except UnicodeDecodeError:
            echange["octets"] = base64.b64encode(contenu).decode("ascii")
        if r.status_code >= 500:
            self.incidents.append("%s %s : %d" % (requete.method, requete.url, r.status_code))
        self.echanges.append(echange)
        return r


def enregistrer(cle: str) -> dict:
    """Enregistre la source `cle` (nom de son module) ; rend l'enregistrement."""
    if cle in EXCLUES:
        raise SystemExit("%s : jamais enregistrée, %s." % (cle, EXCLUES[cle]))
    connues = sources()
    if cle not in connues:
        raise SystemExit("source inconnue : %s (voir --liste)" % cle)
    nom, fonction = connues[cle]
    jour = date.today()
    try:
        original = json.loads(Path(detail_cache._CACHE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        original = {}
    espion = _CacheEspion(copy.deepcopy(original))
    enreg = Enregistreur()
    module = sys.modules[fonction.__module__]
    fichiers = [[module.__name__, attr, Path(getattr(module, attr)).read_text(encoding="utf-8")]
                for attr in FICHIERS_LUS.get(cle, ())]
    with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as pile:
        pile.enter_context(mock.patch.object(
            HTTPAdapter, "send", lambda adapter, requete, **o: enreg.envoyer(adapter, requete, **o)))
        pile.enter_context(mock.patch.object(detail_cache, "_CACHE_FILE",
                                              Path(tmp) / "detail_times.json"))
        pile.enter_context(mock.patch.object(detail_cache, "_cache", espion))
        pile.enter_context(mock.patch.object(detail_cache, "_dirty", False))
        evenements = fonction()
    if enreg.incidents:
        print("ATTENTION, des incidents réseau pendant l'enregistrement ; mieux vaut le "
              "refaire plus tard :\n  " + "\n  ".join(enreg.incidents), file=sys.stderr)
    return {
        "source": nom,
        "jour": jour.isoformat(),
        "cache": {u: original[u] for u in dict.fromkeys(espion.vues) if u in original},
        "fichiers": fichiers,
        "echanges": enreg.echanges,
        "attendu": trier([e.to_dict() for e in evenements]),
    }


# ---------------------------------------------------------------------------
# Le rejeu
# ---------------------------------------------------------------------------

class Rejeu:
    """Sert les réponses enregistrées, dans l'ordre où elles sont venues ;
    note toute requête qui n'a pas été enregistrée."""

    def __init__(self, echanges: list):
        self.reponses: dict = {}
        for e in echanges:
            self.reponses.setdefault((e["methode"], e["url"], e["corps"]), []).append(e)
        self.servies: dict = {}
        self.manquantes: list = []

    def envoyer(self, adapter, requete, **_options):
        cle = (requete.method, requete.url, _corps(requete))
        liste = self.reponses.get(cle)
        if not liste:
            self.manquantes.append("%s %s" % (requete.method, requete.url))
            raise requests.ConnectionError("requête non enregistrée : %s %s" % cle[:2])
        n = self.servies.get(cle, 0)
        self.servies[cle] = n + 1
        e = liste[min(n, len(liste) - 1)]
        if "erreur" in e:
            classe = getattr(requests.exceptions, e["erreur"], requests.ConnectionError)
            raise classe("erreur enregistrée : %s %s" % cle[:2])
        r = requests.Response()
        r.status_code = e["statut"]
        r.reason = e.get("raison")
        r.headers = CaseInsensitiveDict(e["entetes"])
        r._content = (e["texte"].encode("utf-8") if "texte" in e
                      else base64.b64decode(e["octets"]))
        r._content_consumed = True
        r.encoding = get_encoding_from_headers(r.headers)
        r.url = requete.url
        r.request = requete
        r.connection = adapter
        return r


def rejouer(cle: str, enregistrement: dict) -> tuple:
    """(les événements du collecteur rejoué, les requêtes non enregistrées)."""
    _nom, fonction = sources()[cle]
    rejeu = Rejeu(enregistrement["echanges"])
    figee = date_figee(date.fromisoformat(enregistrement["jour"]))
    with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as pile:
        # Le jour de l'enregistrement, partout où un collecteur le lit.
        for nom_module, module in list(sys.modules.items()):
            if nom_module == "scrapers" or nom_module.startswith("scrapers."):
                for attr in ("date", "Date"):
                    if getattr(module, attr, None) is date:
                        pile.enter_context(mock.patch.object(module, attr, figee))
        pile.enter_context(mock.patch.object(
            HTTPAdapter, "send", lambda adapter, requete, **o: rejeu.envoyer(adapter, requete, **o)))
        pile.enter_context(mock.patch("time.sleep", lambda _s: None))
        pile.enter_context(mock.patch.object(detail_cache, "_CACHE_FILE",
                                              Path(tmp) / "detail_times.json"))
        pile.enter_context(mock.patch.object(detail_cache, "_cache",
                                              copy.deepcopy(enregistrement["cache"])))
        pile.enter_context(mock.patch.object(detail_cache, "_dirty", False))
        pile.enter_context(mock.patch.object(detail_cache, "_last_fetch_monotonic", None))
        pile.enter_context(mock.patch.object(base, "_INJOIGNABLES", set()))
        for nom_module, attr, contenu in enregistrement.get("fichiers", []):
            fichier = Path(tmp) / attr
            fichier.write_text(contenu, encoding="utf-8")
            pile.enter_context(mock.patch.object(sys.modules[nom_module], attr, fichier))
        pile.enter_context(contextlib.redirect_stdout(io.StringIO()))
        pile.enter_context(contextlib.redirect_stderr(io.StringIO()))
        evenements = fonction()
    return trier([e.to_dict() for e in evenements]), rejeu.manquantes


# ---------------------------------------------------------------------------

def _liste() -> None:
    for cle, (nom, _f) in sorted(sources().items()):
        if cle in EXCLUES:
            etat = "jamais : " + EXCLUES[cle]
        elif chemin(cle).exists():
            e = lire(cle)
            etat = "enregistrée le %s, %d événements, %d requêtes" % (
                e["jour"], len(e["attendu"]), len(e["echanges"]))
        else:
            etat = "pas enregistrée"
        print("%-20s %-28s %s" % (cle, nom, etat))


def main(argv: list) -> int:
    if len(argv) != 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    if argv[1] == "--liste":
        _liste()
        return 0
    cle = argv[1]
    donnees = enregistrer(cle)
    obtenus, manquantes = rejouer(cle, donnees)
    if manquantes or obtenus != donnees["attendu"]:
        print("REFUSÉ : le rejeu ne redonne pas ce qui a été lu (%d requêtes manquantes, "
              "%d événements au lieu de %d) ; rien n'est écrit."
              % (len(manquantes), len(obtenus), len(donnees["attendu"])), file=sys.stderr)
        return 1
    _ecrire(cle, donnees)
    taille = chemin(cle).stat().st_size
    print("%s : %d événements, %d requêtes, %d Ko -> %s" % (
        donnees["source"], len(donnees["attendu"]), len(donnees["echanges"]),
        taille // 1024, chemin(cle).relative_to(PAGES.parent.parent)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
