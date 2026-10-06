"""La page dans un vrai navigateur, sans réseau : un serveur local sert une
copie du site, et un navigateur de la famille Chrome la joue sans fenêtre.

Dans la copie servie, index.html reçoit en tête une règle qui interdit
TOUTE ressource extérieure - les affiches des salles, notamment, qui
rendraient le test dépendant du réseau - et, au besoin, une date figée ;
en fin de page, le pilote tests/page_essais.js, qui relève l'affichage et
le renvoie au serveur.
"""
from __future__ import annotations

import http.server
import json
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
PILOTE = Path(__file__).resolve().parent / "page_essais.js"

# Les navigateurs essayés, dans l'ordre : variable NOCTURNE_NAVIGATEUR,
# puis ce que le PATH connaît, puis les emplacements habituels de Windows.
NOMS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
        "chrome", "msedge", "microsoft-edge")
CHEMINS_WINDOWS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)

# Le 1er octobre 2026 à 10 h à Paris : la date de la collecte de référence.
DATE_FIGEE_JS = """<script>(() => {
  const T = Date.UTC(2026, 9, 1, 8, 0, 0);
  class DateFigee extends Date {
    constructor(...a) { if (a.length) super(...a); else super(T); }
    static now() { return T; }
  }
  window.Date = DateFigee;
})();</script>"""

TETE = """<meta http-equiv="Content-Security-Policy" content="default-src 'self' 'unsafe-inline' data: blob:">
<script>window.__erreurs = [];
addEventListener('error', e => { if (e instanceof ErrorEvent) __erreurs.push('erreur : ' + e.message); });
addEventListener('unhandledrejection', e => __erreurs.push('promesse rejetée : ' + String(e.reason)));
</script>"""


def trouver() -> str | None:
    """Le chemin d'un navigateur de la famille Chrome, ou None."""
    choix = os.environ.get("NOCTURNE_NAVIGATEUR")
    if choix and Path(choix).exists():
        return choix
    for nom in NOMS:
        chemin = shutil.which(nom)
        if chemin:
            return chemin
    if platform.system() == "Windows":
        for chemin in CHEMINS_WINDOWS:
            if Path(chemin).exists():
                return chemin
    return None


def sauter_si_indisponible(cas, probleme: str | None) -> None:
    """Saute le test quand c'est la MACHINE qui fait défaut, pas le site.

    Pas de navigateur, ou un navigateur qui ne rend rien du tout : le test
    n'a rien pu observer, et bloquer la publication pour cela figerait le
    site sans raison. Il est sauté, avec une alerte visible sur GitHub. Un
    relevé obtenu, lui, est comparé strictement.
    """
    if not trouver():
        probleme = "aucun navigateur de la famille Chrome"
    if not probleme:
        return
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("::warning title=test de page sauté::%s" % probleme)
    cas.skipTest(probleme)


def releve_ou_probleme(**options) -> tuple:
    """(relevé, None) ou (None, la raison pour laquelle on n'a rien observé)."""
    if not trouver():
        return None, "aucun navigateur de la famille Chrome"
    try:
        return jouer(**options), None
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        return None, "le navigateur n'a rien rendu : %s" % exc


def preparer_site(dossier: Path, evenements: dict, lieux: dict | None,
                  date_figee: bool, scenarios: str = "tous") -> None:
    """Une copie servable du site dans `dossier`, avec ses injections."""
    html = (RACINE / "index.html").read_text(encoding="utf-8")
    tete = TETE + (DATE_FIGEE_JS if date_figee else "")
    assert html.count("<head>") == 1 and html.count("</body>") == 1
    html = html.replace("<head>", "<head>\n" + tete, 1)
    pilote = ("<script>window.__SCENARIOS = %s;</script>\n<script>%s</script>\n"
              % (json.dumps(scenarios), PILOTE.read_text(encoding="utf-8")))
    html = html.replace("</body>", pilote + "</body>", 1)
    (dossier / "index.html").write_text(html, encoding="utf-8")
    for d in ("fonts", "logos"):
        if (RACINE / d).exists():
            shutil.copytree(RACINE / d, dossier / d)
    (dossier / "events.json").write_text(json.dumps(evenements, ensure_ascii=False),
                                         encoding="utf-8")
    if lieux is not None:
        (dossier / "venue_arrondissements.json").write_text(
            json.dumps(lieux, ensure_ascii=False), encoding="utf-8")


class _Serveur(http.server.ThreadingHTTPServer):
    resultats: list


class _Gestionnaire(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):          # silencieux
        pass

    def do_POST(self):
        if self.path == "/__resultats":
            corps = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.server.resultats.append(json.loads(corps.decode("utf-8")))
        self.send_response(204)
        self.end_headers()


def jouer(evenements: dict, lieux: dict | None, *, date_figee: bool,
          scenarios: str = "tous", animations: bool = False,
          delai: int = 240) -> dict:
    """Joue la page et rend le relevé du pilote.

    `animations` : sans lui, le navigateur demande « moins d'animations » et
    la page se met à jour d'un coup ; avec lui, elle joue ses fondus, comme
    chez la plupart des visiteurs.

    Lève RuntimeError si aucun navigateur n'est disponible, ou s'il n'a rien
    renvoyé - l'appelant décide alors de sauter ou d'échouer.
    """
    navigateur = trouver()
    if not navigateur:
        raise RuntimeError("aucun navigateur de la famille Chrome")
    # ignore_cleanup_errors : sous Windows, un processus du navigateur qui
    # s'attarde un instant tient encore son profil, et le ménage échouerait.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        site, profil = Path(tmp) / "site", Path(tmp) / "profil"
        site.mkdir()
        preparer_site(site, evenements, lieux, date_figee, scenarios)

        def gestionnaire(*a, **k):
            return _Gestionnaire(*a, directory=str(site), **k)

        serveur = _Serveur(("127.0.0.1", 0), gestionnaire)
        serveur.resultats = []
        fil = threading.Thread(target=serveur.serve_forever, daemon=True)
        fil.start()
        try:
            url = "http://127.0.0.1:%d/index.html" % serveur.server_address[1]
            env = dict(os.environ, TZ="Europe/Paris", LANG="fr_FR.UTF-8")
            # « Moins d'animations » : la page se met alors à jour d'un coup,
            # sans fondu (voir render() dans index.html). Avec le fondu, la
            # mise à jour arrive une image plus tard, et un relevé pouvait
            # saisir l'affichage d'AVANT selon le chronométrage.
            #
            # En TEMPS RÉEL, et non sous l'horloge accélérée de Chrome
            # (--virtual-time-budget) : celle-ci fait filer les pauses du
            # pilote en un instant, quand les fondus suivent le temps de
            # l'écran - un relevé tombait alors avant la fin du dernier
            # fondu. Le navigateur reste ouvert jusqu'au relevé, puis il est
            # fermé.
            options = ["--headless=new", "--disable-gpu", "--no-first-run",
                       "--no-default-browser-check", "--disable-extensions",
                       "--force-prefers-reduced-motion", "--remote-debugging-port=0",
                       "--user-data-dir=%s" % profil, "--lang=fr-FR",
                       "--window-size=1280,900", url]
            if animations:
                options.remove("--force-prefers-reduced-motion")
            if platform.system() == "Linux":
                options.insert(1, "--no-sandbox")
            proc = subprocess.Popen([navigateur] + options, env=env,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            try:
                fin = time.monotonic() + delai
                while (not serveur.resultats and proc.poll() is None
                       and time.monotonic() < fin):
                    time.sleep(0.2)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
        finally:
            serveur.shutdown()
            serveur.server_close()
        if not serveur.resultats:
            raise RuntimeError("le navigateur n'a renvoyé aucun relevé (%s)"
                               % Path(navigateur).name)
        return serveur.resultats[-1]
