"""Outils communs aux tests : date figée, faux réseau, atelier d'aggregate.

Rien ici ne touche au réseau ni aux fichiers du dépôt : tout ce
qu'aggregate.main() écrirait est redirigé vers un dossier temporaire.
"""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import requests

import aggregate
from scrapers.base import Event

RACINE = Path(__file__).resolve().parent.parent
DONNEES = Path(__file__).resolve().parent / "donnees"


def date_figee(jour: date):
    """Une classe date dont today() rend toujours `jour`.

    Les modules sous test écrivent « from datetime import date » : on
    remplace ce nom dans LEUR espace, rien d'autre ne change.
    """
    class DateFigee(date):
        @classmethod
        def today(cls):
            return cls(jour.year, jour.month, jour.day)
    return DateFigee


def evenement(lieu: str, titre: str, jour: date, *, heure: str | None = "20:00",
              url: str = "", fin: date | None = None, categorie: str = "concert",
              sous_titre: str | None = None, image: str | None = None) -> Event:
    """Un événement de test. Sans `url`, une adresse fixe et propre à lui
    (jamais hash() : il change d'un lancement à l'autre)."""
    slug = lieu.lower().replace(" ", "-")
    empreinte = hashlib.md5(("%s|%s|%s" % (lieu, titre, jour)).encode()).hexdigest()[:10]
    return Event(venue=lieu, venue_slug=slug, title=titre, subtitle=sous_titre,
                 category=categorie, date_start=jour.isoformat(),
                 date_end=fin.isoformat() if fin else None, time=heure,
                 url=url or "https://%s.exemple.org/%s" % (slug, empreinte),
                 image=image)


def lire_gz(nom: str):
    with gzip.open(DONNEES / nom, "rt", encoding="utf-8") as f:
        return json.load(f)


def ecrire_gz(nom: str, donnees) -> None:
    DONNEES.mkdir(exist_ok=True)
    with gzip.open(DONNEES / nom, "wt", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, separators=(",", ":"),
                  sort_keys=True)


class FausseSession:
    """Une session réseau scriptée : chaque appel consomme la réponse
    suivante de `script`. Un entier est un code HTTP, une exception est
    levée, « ok » rend une page 200. Les appels sont notés."""

    def __init__(self, *script):
        self.script = list(script)
        self.appels = []

    def request(self, methode, url, **options):
        self.appels.append((methode, url, options))
        quoi = self.script.pop(0) if self.script else "ok"
        if isinstance(quoi, BaseException):
            raise quoi
        r = requests.Response()
        r.status_code = 200 if quoi == "ok" else int(quoi)
        r._content = b"ok"
        r.url = url
        return r


class FauxSite:
    """Un site servi sans réseau : `pages[url]` est le HTML de l'adresse.

    `avant[url]` donne les réponses qui passent D'ABORD, une par appel :
    une exception est levée, un triplet (code, corps, en-têtes) est rendu
    tel quel. Une adresse inconnue rend 404.
    """

    def __init__(self, pages: dict, avant: dict | None = None):
        self.pages = pages
        self.avant = {u: list(v) for u, v in (avant or {}).items()}
        self.appels = []

    def request(self, methode, url, **options):
        self.appels.append(url)
        file = self.avant.get(url)
        quoi = file.pop(0) if file else None
        if isinstance(quoi, BaseException):
            raise quoi
        r = requests.Response()
        r.url, r.encoding = url, "utf-8"
        if quoi is not None:
            code, corps, entetes = quoi
            r.status_code, r._content = code, corps
            r.headers = requests.structures.CaseInsensitiveDict(entetes)
        elif url in self.pages:
            r.status_code, r._content = 200, self.pages[url].encode("utf-8")
        else:
            r.status_code, r._content = 404, b"introuvable"
        return r


class Atelier:
    """aggregate.main() dans un dossier temporaire, à une date figée.

    events.json y est lu et écrit. Le cache des heures, le géocodage et la
    lecture d'index.html sont neutralisés ; ce que le programme affiche est
    capturé dans `journal`.
    """

    def __init__(self, dossier: Path, jour: date):
        self.dossier, self.jour = dossier, jour
        self.journal = io.StringIO()
        self.fichier = dossier / "events.json"

    def veille(self, evenements, *, age_jours: int = 1,
               reprises: dict | None = None, lieux: dict | None = None) -> None:
        """Écrit le fil publié « hier » — ou il y a `age_jours` jours.

        `lieux` : la trace des lieux de chaque collecteur, absente sinon.
        """
        d = {"generated_at": (self.jour - timedelta(days=age_jours)).isoformat()
             + "T06:00:00+00:00",
             "events": [e.to_dict() for e in evenements]}
        if reprises:
            d["reprises"] = reprises
        if lieux:
            d["lieux_des_collecteurs"] = lieux
        self.fichier.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def lancer(self, salles, agregateurs=(), forcer: bool = False,
               github: bool = False) -> int:
        """salles : [(nom, fonction)] ; agregateurs : [(nom, fonction, priorité)].

        `github` : comme sur GitHub Actions, les alertes s'écrivent aussi en
        annotations (« ::warning »), capturées dans le journal.
        """
        env = {k: v for k, v in os.environ.items()
               if k not in ("NOCTURNE_FORCER_ECRITURE", "GITHUB_ACTIONS")}
        if forcer:
            env["NOCTURNE_FORCER_ECRITURE"] = "1"
        if github:
            env["GITHUB_ACTIONS"] = "true"
        with mock.patch.object(aggregate, "__file__", str(self.dossier / "aggregate.py")), \
                mock.patch.object(aggregate, "date", date_figee(self.jour)), \
                mock.patch.object(aggregate, "save_detail_cache", lambda *a, **k: None), \
                mock.patch.object(aggregate, "resolve_new_venues", lambda *a, **k: None), \
                mock.patch.object(aggregate, "frontend_hardcoded_venues", lambda: set()), \
                mock.patch.object(aggregate, "SCRAPERS", list(salles)), \
                mock.patch.object(aggregate, "AGGREGATORS", list(agregateurs)), \
                mock.patch.dict(os.environ, env, clear=True), \
                contextlib.redirect_stdout(self.journal), \
                contextlib.redirect_stderr(self.journal):
            return aggregate.main()

    def publie(self) -> dict:
        return json.loads(self.fichier.read_text(encoding="utf-8"))


@contextlib.contextmanager
def atelier(jour: date):
    with tempfile.TemporaryDirectory() as tmp:
        yield Atelier(Path(tmp), jour)
