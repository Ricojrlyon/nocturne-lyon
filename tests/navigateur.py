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
import urllib.parse
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

REGLE_DU_BANC = "default-src 'self' 'unsafe-inline' data: blob:"

# La page a sa propre politique de sécurité (audit n° 15), qui s'ajoute à
# celle du banc. Ce qu'ELLE bloque est une erreur : la page se priverait
# d'un de ses fichiers, d'un script, d'un style. Ce que bloque la règle
# du banc - les affiches des salles - est voulu, et n'est pas relevé.
# Un script bloqué, c'est aussi le pilote ; un envoi bloqué, c'est son
# relevé. L'échec part alors tout de suite, par une navigation, que la
# règle ne peut pas bloquer (le serveur répond 204 : la page reste) ;
# sans cela, le test se croirait privé de navigateur et serait sauté,
# quand c'est la page qui ne marche plus.
TETE = """<meta http-equiv="Content-Security-Policy" content="%s">
<script>window.__erreurs = [];
addEventListener('error', e => { if (e instanceof ErrorEvent) __erreurs.push('erreur : ' + e.message); });
addEventListener('unhandledrejection', e => __erreurs.push('promesse rejetée : ' + String(e.reason)));
addEventListener('securitypolicyviolation', e => {
  if (e.originalPolicy === %s) return;
  const quoi = 'bloqué par la page : ' + e.effectiveDirective + ' ' + e.blockedURI;
  __erreurs.push(quoi);
  if (/^(script|connect)-src/.test(e.effectiveDirective))
    location.href = '/__echec?' + encodeURIComponent(quoi);
});
</script>""" % (REGLE_DU_BANC, json.dumps(REGLE_DU_BANC))


# Le relais des mentions légales (scénario « service », voir page_essais.js).
RELAIS_MENTIONS = """<script>(async () => {
  const etape = sessionStorage.getItem('service');
  if (etape === '0b') {
    sessionStorage.setItem('service', 'u');
    location.href = 'index.html';
  } else if (etape === '3') {
    const res = JSON.parse(sessionStorage.getItem('releve') || '{}');
    res.service.mentions_sans_reseau = document.title;
    await fetch('/__resultats', { method: 'POST', body: JSON.stringify(res) });
  }
})();</script>
"""


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
    """Saute le test quand c'est la MACHINE qui fait défaut, pas le site ;
    sur GitHub, le fait échouer.

    Pas de navigateur, ou un navigateur qui ne rend rien, pas même la page
    témoin (voir releve_ou_probleme) : le test n'a rien pu observer. Sur
    un poste, il est sauté et le dit. Sur GitHub, il échoue (audit A-26) :
    sauté, il laissait partir la publication sans aucun contrôle de la
    page. Un relevé obtenu, lui, est comparé strictement.
    """
    if not trouver():
        probleme = "aucun navigateur de la famille Chrome"
    if not probleme:
        return
    if os.environ.get("GITHUB_ACTIONS") == "true":
        cas.fail("navigateur indisponible sur GitHub, la page n'a pas pu être contrôlée : %s"
                 % probleme)
    cas.skipTest(probleme)


# Ce que jouer() lève quand le navigateur n'a rien renvoyé.
RIEN_RENDU = (RuntimeError, OSError, subprocess.TimeoutExpired)

# La page témoin : la plus simple qui soit, elle ne fait que répondre.
TEMOIN = """<!doctype html><meta charset="utf-8"><title>témoin</title>
<script>fetch('/__resultats', { method: 'POST', body: '{"temoin": true}' });</script>
"""


def releve_ou_probleme(**options) -> tuple:
    """(relevé, None) ou (None, la raison pour laquelle on n'a rien observé).

    Audit A-26. La page qui ne rend rien a droit à un nouvel essai (un
    navigateur peut caler une fois). Rien encore : le navigateur joue la
    page témoin. S'il la rend, il marche, et c'est la page du site qui ne
    rend rien : un relevé « echec », que les tests refusent. S'il ne la
    rend pas, c'est la machine qui fait défaut (voir
    sauter_si_indisponible).
    """
    if not trouver():
        return None, "aucun navigateur de la famille Chrome"
    for _essai in range(2):
        try:
            return jouer(**options), None
        except RIEN_RENDU as exc:
            derniere = exc
    if temoin():
        quoi = ("la page n'a rien rendu en deux essais, quand le navigateur rend la page "
                "témoin : %s" % derniere)
        return {"echec": quoi, "erreurs": [quoi]}, None
    return None, "le navigateur n'a rien rendu, pas même la page témoin : %s" % derniere


def temoin(delai: int = 60) -> bool:
    """Le navigateur rend-il la page témoin ? Une page sans rien du site."""
    navigateur = trouver()
    if not navigateur:
        return False
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        site, profil = Path(tmp) / "site", Path(tmp) / "profil"
        site.mkdir()
        (site / "temoin.html").write_text(TEMOIN, encoding="utf-8")
        try:
            serveur = _ouvrir(navigateur, site, profil, "temoin.html", delai=delai)
        except RIEN_RENDU:
            return False
        return serveur.resultats == [{"temoin": True}]


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
    # Les lieux, que la page lit au chargement comme le fil (audit n° 21),
    # et l'application installable : sa description, ses icônes et son
    # service hors ligne, qui s'installe pendant les essais.
    # Les mentions légales, avec un relais pour le scénario « service » :
    # la page y passe, puis y revient sans réseau, et c'est elle qui
    # rapporte ce qui s'est affiché (sa règle de sécurité est celle du
    # banc, pour laisser passer ce relais).
    mentions = (RACINE / "mentions-legales.html").read_text(encoding="utf-8")
    debut = mentions.index('<meta http-equiv="Content-Security-Policy"')
    fin = mentions.index(">", debut) + 1
    mentions = (mentions[:debut] + '<meta http-equiv="Content-Security-Policy" content="%s">'
                % REGLE_DU_BANC + mentions[fin:])
    (dossier / "mentions-legales.html").write_text(
        mentions.replace("</body>", RELAIS_MENTIONS + "</body>", 1), encoding="utf-8")
    for f in ("lieux.json", "manifest.webmanifest", "sw.js", "icone-192.png", "icone-512.png"):
        shutil.copy(RACINE / f, dossier / f)
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
        elif self.path == "/__couper":
            # Le scénario « hors_ligne » coupe le réseau : voir do_GET.
            self.server.coupe = True
        elif self.path == "/__lent":
            # Le scénario « service » ralentit le réseau : voir do_GET.
            self.server.lent = True
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        # Un échec signalé par navigation (voir TETE) : noté comme un relevé.
        if self.path.startswith("/__echec?"):
            quoi = urllib.parse.unquote(self.path.split("?", 1)[1])
            self.server.resultats.append({"echec": quoi, "erreurs": [quoi]})
            self.send_response(204)
            self.end_headers()
            return
        if getattr(self.server, "lent", False) and not getattr(self.server, "coupe", False):
            # Réseau lent : six secondes avant chaque réponse, plus que les
            # trois que le service hors ligne accorde au site.
            time.sleep(6)
        if getattr(self.server, "coupe", False):
            # Réseau coupé : la connexion se ferme sans un mot, comme sur
            # un téléphone sans réseau - le navigateur y voit une panne, et
            # non une page d'erreur du serveur.
            self.close_connection = True
            return
        super().do_GET()


def jouer(evenements: dict, lieux: dict | None, *, date_figee: bool,
          scenarios: str = "tous", animations: bool = False,
          delai: int = 240, requete: str = "", fuseau: str = "Europe/Paris") -> dict:
    """Joue la page et rend le relevé du pilote.

    `animations` : sans lui, le navigateur demande « moins d'animations » et
    la page se met à jour d'un coup ; avec lui, elle joue ses fondus, comme
    chez la plupart des visiteurs.

    `fuseau` : le fuseau horaire du « téléphone » (variable TZ). Le relevé
    dit celui que le navigateur a réellement pris (`fuseau`) : sous
    Windows, il peut garder celui du système.

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
        # `requete` : ce qui suit l'adresse, « ?quand=weekend » - un lien
        # partagé.
        serveur = _ouvrir(navigateur, site, profil, "index.html" + requete, delai=delai,
                          animations=animations, fuseau=fuseau)
        if not serveur.resultats and serveur.coupe:
            # Le réseau a été coupé (scénario « hors_ligne ») et la page n'a
            # plus rien envoyé : elle ne s'est pas rouverte sans réseau. C'est
            # la page qui manque, pas le navigateur : un échec, pas un saut.
            return {"echec": "réseau coupé : la page ne s'est pas rouverte depuis le téléphone",
                    "erreurs": []}
        if not serveur.resultats:
            raise RuntimeError("le navigateur n'a renvoyé aucun relevé (%s)"
                               % Path(navigateur).name)
        return serveur.resultats[-1]


def _ouvrir(navigateur: str, site: Path, profil: Path, page: str, *, delai: int,
            animations: bool = False, fuseau: str = "Europe/Paris") -> _Serveur:
    """Sert le dossier `site`, y ouvre `page` dans le navigateur, et attend
    le premier relevé, la fin du navigateur ou celle du délai. Rend le
    serveur, avec ce qu'il a reçu."""
    def gestionnaire(*a, **k):
        return _Gestionnaire(*a, directory=str(site), **k)

    serveur = _Serveur(("127.0.0.1", 0), gestionnaire)
    serveur.resultats = []
    serveur.coupe = False
    serveur.lent = False
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    try:
        url = "http://127.0.0.1:%d/%s" % (serveur.server_address[1], page)
        env = dict(os.environ, TZ=fuseau, LANG="fr_FR.UTF-8")
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
    return serveur
