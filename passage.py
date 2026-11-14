"""Le passage de nuit : son créneau et sa publication (audit, lot 8).

Deux étapes de update.yml, écrites en Python pour être essayées par les
tests (tests/test_passage.py) au lieu de ne l'être qu'en production.

  python3 passage.py creneau   le passage travaille-t-il ? (job « creneau »)
  python passage.py publier    envoie le commit du robot, en trois essais

Le créneau (audit A-25). Trois passages sont programmés chaque jour, et
GitHub les retarde de plusieurs heures. Le PREMIER arrivé travaille ; les
suivants s'arrêtent si le programme publié est déjà du jour, à Paris.
Avant, un seul des deux passages de la nuit avait le droit de travailler,
celui de 2 h 17 à Paris : en hiver le second, qui arrivait en moyenne
1 h 21 après le premier, jeté. Si GitHub l'abandonnait (sa documentation
le prévoit en cas de forte charge), rien n'était publié ce jour-là.
Désormais, un passage abandonné ou tombé au rouge est rattrapé par le
suivant. Un lancement à la main travaille toujours.

La publication (audit A-27). Un commit poussé pendant le passage (une
retouche du code, un fichier téléversé à la main) faisait échouer
l'envoi, et rien n'était publié. Trois essais de « git pull --rebase -X
theirs » puis « git push » : en cas de conflit sur un fichier du robot,
c'est la version du robot qui l'emporte (pendant un rebase, « theirs »
désigne le commit rejoué, ici celui du robot).

Le jeton d'écriture (audit A-42) n'est plus rangé dans le dépôt au
checkout, où les tests et les paquets installés pouvaient le lire : il
n'arrive qu'ici, par la variable JETON, et ne passe qu'aux commandes git
de la publication, sans jamais être écrit sur le disque.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
RACINE = Path(__file__).resolve().parent
ESSAIS = 3
PAUSE = 10          # secondes ; 10, puis 20, entre les essais


def creneau(evenement: str, genere_le, maintenant: datetime) -> tuple[bool, str]:
    """(le passage travaille-t-il ?, pourquoi).

    `evenement` : ce qui a lancé le passage (« schedule » pour un passage
    programmé) ; `genere_le` : la date du programme publié
    (`generated_at` d'events.json) ; `maintenant` : l'heure du passage.
    Le jour est celui de Paris : un programme publié à 0 h 30 à Paris est
    du jour, même s'il est encore la veille en UTC.
    """
    if evenement != "schedule":
        return True, "lancé à la main : le passage travaille toujours."
    aujourd_hui = maintenant.astimezone(PARIS).date()
    try:
        publie = datetime.fromisoformat(str(genere_le))
    except ValueError:
        return True, "date du programme publié illisible (%r) : le passage travaille." % genere_le
    if publie.tzinfo is None:
        publie = publie.replace(tzinfo=timezone.utc)
    publie = publie.astimezone(PARIS)
    if publie.date() == aujourd_hui:
        return False, ("programme déjà publié aujourd'hui, à %s à Paris : "
                       "rien à refaire." % publie.strftime("%H:%M"))
    return True, ("dernier programme publié le %s à Paris : ce passage fait celui du %s."
                  % (publie.date().isoformat(), aujourd_hui.isoformat()))


def lancer_creneau(fil: Path, sortie: str | None, evenement: str,
                   maintenant: datetime) -> bool:
    """Le job « creneau » : lit le fil publié, décide, écrit « lancer=oui »
    ou « lancer=non » pour le job suivant (fichier GITHUB_OUTPUT)."""
    try:
        genere_le = json.loads(fil.read_text(encoding="utf-8")).get("generated_at")
    except (OSError, ValueError, AttributeError):
        genere_le = None
    lancer, raison = creneau(evenement, genere_le, maintenant)
    print(raison, flush=True)
    if sortie:
        with open(sortie, "a", encoding="utf-8") as f:
            f.write("lancer=%s\n" % ("oui" if lancer else "non"))
    return lancer


def publier(dossier: Path | str = RACINE, *, jeton: str | None = None,
            essais: int = ESSAIS, pause: float = PAUSE, attendre=time.sleep) -> bool:
    """Envoie le commit du robot ; rend True s'il est parti.

    Chaque essai reprend la branche distante (« git pull --rebase -X
    theirs ») puis envoie (« git push »). Un essai raté est défait
    (« git rebase --abort ») avant le suivant : le dépôt n'est jamais
    laissé au milieu d'un rebase.
    """
    env = dict(os.environ)
    if jeton:
        cle = base64.b64encode(("x-access-token:%s" % jeton).encode()).decode()
        env.update(GIT_CONFIG_COUNT="1",
                   GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                   GIT_CONFIG_VALUE_0="AUTHORIZATION: basic %s" % cle)

    def git(*args, discret=False) -> bool:
        r = subprocess.run(["git", *args], cwd=dossier, env=env,
                           capture_output=discret)
        return r.returncode == 0

    for essai in range(1, essais + 1):
        if git("pull", "--rebase", "-X", "theirs") and git("push"):
            return True
        git("rebase", "--abort", discret=True)      # rien à défaire : sans effet
        if essai < essais:
            print("::warning title=publication, essai %d sur %d::envoi refusé, "
                  "nouvel essai dans %d s" % (essai, essais, pause * essai), flush=True)
            attendre(pause * essai)
    print("::error title=publication::envoi refusé %d fois : rien n'est publié ; "
          "le passage programmé suivant reprendra" % essais, flush=True)
    return False


def main(argv: list[str]) -> int:
    quoi = argv[1] if len(argv) > 1 else ""
    if quoi == "creneau":
        lancer_creneau(RACINE / "events.json", os.environ.get("GITHUB_OUTPUT"),
                       os.environ.get("EVENEMENT", ""), datetime.now(timezone.utc))
        return 0
    if quoi == "publier":
        return 0 if publier(jeton=os.environ.get("JETON")) else 1
    print("usage : python passage.py creneau | publier", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
