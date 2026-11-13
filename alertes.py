"""L'alerte des salles en panne (audit A-02, novembre 2026).

Une salle en panne ne laissait que des annotations sur un passage VERT, et
GitHub n'envoie d'e-mail que pour un passage rouge. L'IAC a été
injoignable du 23 octobre au 9 novembre 2026 : abandonnée au 7e jour, elle
a quitté le site une dizaine de jours sans que personne le sache.

Ce script tourne à la fin du passage de nuit (update.yml). Il lit l'état
des sources qu'aggregate.py vient d'écrire (fichier désigné par
NOCTURNE_ETAT) et le fil publié (events.json), et tient UNE issue GitHub
par salle en panne, « [salle en panne] <salle> » :

  - il l'ouvre au 2e jour de reprise de la veille (une panne d'une nuit
    ne dérange personne), ou le jour où la salle est abandonnée ;
  - tant que la panne dure, il récrit son texte (sans e-mail), et ajoute
    un commentaire (avec e-mail) le jour de l'abandon ;
  - il la ferme quand la salle est revenue : plus reprise, plus en échec,
    et de nouveau présente dans le fil.

L'issue mentionne le propriétaire du dépôt : GitHub lui envoie l'e-mail.
Le script ne fait JAMAIS échouer le passage : une erreur est notée en
annotation, et c'est tout.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import requests

from scrapers.dedup import canonical_venue_name

TITRE = "[salle en panne] "
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre")
JOURS_MAX = 7      # comme REPRISE_JOURS_MAX dans aggregate.py


def _jour(iso: str) -> str:
    d = date.fromisoformat(iso)
    return "%d %s %d" % (d.day, MOIS[d.month - 1], d.year)


def situations(etat: dict) -> dict:
    """{salle: situation} des salles en panne ce jour-là.

    Une situation dit si la salle est reprise de la veille (et depuis
    quand), abandonnée ce jour-là, en échec (et pourquoi).
    """
    out: dict = {}

    def sit(salle):
        return out.setdefault(salle, {"reprise": None, "abandon": None, "raison": None})
    for salle, depuis in (etat.get("reprises") or {}).items():
        sit(salle)["reprise"] = depuis
    for salle, depuis, _jours in etat.get("abandons") or []:
        sit(salle)["abandon"] = depuis
    for source, info in (etat.get("en_echec") or {}).items():
        for salle in info.get("lieux") or [source]:
            sit(salle)["raison"] = info.get("raison")
    return out


def corps(salle: str, s: dict | None, depuis: str, aujourd_hui: str,
          proprietaire: str) -> str:
    """Le texte de l'issue, en français simple."""
    if s is None:
        etat = "toujours absente du site : son collecteur ne rend plus rien"
    elif s["abandon"]:
        etat = ("**abandonnée** : en panne depuis %d jours, elle n'est plus reprise "
                "et ses événements quittent le site" % JOURS_MAX)
    elif s["reprise"]:
        jours = (date.fromisoformat(aujourd_hui) - date.fromisoformat(s["reprise"])).days + 1
        etat = ("reprise de la veille, jour %d sur %d : le site montre encore "
                "son programme d'avant la panne" % (jours, JOURS_MAX))
    else:
        etat = "en échec, sans rien à reprendre"
    raison = (s or {}).get("raison") or "son programme a fondu (liste vide ou très courte)"
    return (
        "@%s : **%s** ne rend plus son programme depuis le %s.\n\n"
        "- Dernier passage : %s.\n"
        "- État : %s.\n"
        "- Raison relevée : %s.\n\n"
        "Ce que ça veut dire : pendant %d jours, le site garde le programme "
        "de la veille de cette salle ; au-delà, il le retire. Si le site de la "
        "salle est seulement en panne, il n'y a rien à faire. S'il a changé "
        "(nouvelle adresse, nouvelle présentation), son collecteur est à "
        "réparer.\n\n"
        "Cette issue se ferme seule quand la salle revient."
        % (proprietaire, salle, _jour(depuis), _jour(aujourd_hui), etat, raison, JOURS_MAX))


def decider(etat: dict, ouvertes: dict, presentes: set,
            proprietaire: str = "proprietaire") -> list:
    """Les actions à mener sur les issues.

    `ouvertes` : {salle: {"numero": n, "depuis": iso}} des issues ouvertes.
    `presentes` : les salles et sources qui ont des événements ce jour-là.
    Rend des tuples : ("ouvrir", salle, corps), ("modifier", numero, corps),
    ("commenter", numero, texte), ("fermer", numero, texte).
    """
    aujourd_hui = etat["date"]
    pannes = situations(etat)
    actions = []
    for salle, s in sorted(pannes.items()):
        if salle in ouvertes:
            continue
        deuxieme_jour = s["reprise"] and s["reprise"] < aujourd_hui
        if deuxieme_jour or s["abandon"]:
            depuis = s["reprise"] or s["abandon"] or aujourd_hui
            actions.append(("ouvrir", salle, corps(salle, s, depuis, aujourd_hui, proprietaire)))
    for salle, issue in sorted(ouvertes.items()):
        s = pannes.get(salle)
        if s is None and salle in presentes:
            actions.append(("fermer", issue["numero"],
                            "%s est revenue au passage du %s : elle publie de nouveau "
                            "son programme. Issue fermée." % (salle, _jour(aujourd_hui))))
            continue
        actions.append(("modifier", issue["numero"],
                        corps(salle, s, issue["depuis"], aujourd_hui, proprietaire)))
        if s and s["abandon"]:
            actions.append(("commenter", issue["numero"],
                            "@%s : %s est en panne depuis %d jours. Elle n'est plus "
                            "reprise : ses événements quittent le site à ce passage."
                            % (proprietaire, salle, JOURS_MAX)))
    return actions


def presentes(fil: dict, etat: dict) -> set:
    """Les salles présentes dans le fil publié, et les sources qui ont
    rendu des événements (un agrégateur n'est pas une salle du fil)."""
    salles = Counter(canonical_venue_name(e.get("venue") or "")
                     for e in fil.get("events") or [] if isinstance(e, dict))
    sources = {nom for nom, n, err in etat.get("rapport") or [] if n and not err}
    return {s for s, n in salles.items() if n} | sources


class GitHub:
    """Le peu de l'API GitHub dont l'alerte a besoin."""

    def __init__(self, depot: str, jeton: str, session=None):
        self.base = "https://api.github.com/repos/%s" % depot
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": "Bearer %s" % jeton,
                                     "Accept": "application/vnd.github+json"})

    def _appel(self, methode, chemin, **json_):
        r = self.session.request(methode, self.base + chemin, json=json_ or None, timeout=30)
        r.raise_for_status()
        return r.json() if r.content else None

    def ouvertes(self) -> dict:
        out = {}
        for i in self._appel("GET", "/issues?state=open&per_page=100") or []:
            if "pull_request" in i or not i.get("title", "").startswith(TITRE):
                continue
            out[i["title"][len(TITRE):]] = {"numero": i["number"],
                                            "depuis": i["created_at"][:10]}
        return out

    def appliquer(self, actions) -> None:
        for quoi, cle, texte in actions:
            if quoi == "ouvrir":
                self._appel("POST", "/issues", title=TITRE + cle, body=texte)
            elif quoi == "modifier":
                self._appel("PATCH", "/issues/%d" % cle, body=texte)
            elif quoi == "commenter":
                self._appel("POST", "/issues/%d/comments" % cle, body=texte)
            elif quoi == "fermer":
                self._appel("POST", "/issues/%d/comments" % cle, body=texte)
                self._appel("PATCH", "/issues/%d" % cle, state="closed")


def resume(etat: dict) -> str:
    """Le tableau des sources, pour la page du passage sur GitHub."""
    pannes = situations(etat)
    lignes = ["| Source | Événements | État |", "|---|---|---|"]
    for nom, n, err in etat.get("rapport") or []:
        if err:
            quoi = "en échec : %s" % err[:120].replace("|", "/")
        elif nom in pannes and pannes[nom]["reprise"]:
            quoi = "reprise de la veille depuis le %s" % _jour(pannes[nom]["reprise"])
        elif n == 0:
            quoi = "aucun événement"
        else:
            quoi = "ok"
        lignes.append("| %s | %d | %s |" % (nom, n, quoi))
    return "\n".join(lignes) + "\n"


def main() -> int:
    chemin = os.environ.get("NOCTURNE_ETAT")
    jeton = os.environ.get("GITHUB_TOKEN")
    depot = os.environ.get("GITHUB_REPOSITORY")
    if not (chemin and Path(chemin).exists()):
        print("[alerte] pas d'état des sources ce passage : rien à faire")
        return 0
    try:
        etat = json.loads(Path(chemin).read_text(encoding="utf-8"))
        sommaire = os.environ.get("GITHUB_STEP_SUMMARY")
        if sommaire:
            with open(sommaire, "a", encoding="utf-8") as f:
                f.write("### Les sources de ce passage\n\n" + resume(etat))
        if not (jeton and depot):
            print("[alerte] hors de GitHub : pas d'issue")
            return 0
        fil = json.loads((Path(__file__).parent / "events.json").read_text(encoding="utf-8"))
        gh = GitHub(depot, jeton)
        actions = decider(etat, gh.ouvertes(), presentes(fil, etat),
                          os.environ.get("GITHUB_REPOSITORY_OWNER") or depot.split("/")[0])
        gh.appliquer(actions)
        for quoi, cle, _ in actions:
            print("[alerte] %s %s" % (quoi, cle))
    except Exception as exc:  # noqa: BLE001 - l'alerte ne bloque jamais le passage
        print("::warning title=alerte des salles::%s: %s" % (type(exc).__name__, exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
