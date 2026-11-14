/* *nocturne installé comme une application : ce service garde sur le
   téléphone la page, ses polices et le dernier programme chargé, pour que
   l'application s'ouvre même sans réseau - dans le métro, par exemple.

   En ligne, la page et le programme viennent du site (le réseau d'abord) :
   rien de périmé n'est servi tant que le site répond, et la mise à jour de
   la nuit arrive comme avant. Un réseau qui ne répond pas en
   ATTENTE_RESEAU millisecondes (le métro, une barre de réseau) cède la
   place à la copie du téléphone, quand il en a une ; la réponse du site,
   si elle finit par venir, la remplace pour la fois suivante (audit
   A-40 : sans délai, on attendait le réseau sans fin). Le reste - polices, logos, icônes,
   qui ne changent pas - est servi du téléphone, puis rafraîchi en douce.
   Les affiches, venues des sites des salles, ne sont pas gardées : sans
   réseau, la carte montre son motif.

   Changer la liste AU_DEPART ou la logique ci-dessous : changer aussi le
   nom CACHE, pour que l'ancienne réserve soit vidée. */
// nocturne-2 : l'icône redessinée (fond nuit, N crème), à reprendre tout de suite.
// nocturne-3 : le ménage ne touche plus que les réserves de nocturne, le
// réseau a trois secondes, et une page autre que l'accueil n'est plus
// servie par l'accueil (audit A-12, A-40).
const CACHE = 'nocturne-3';
// Le préfixe de nos réserves : le ménage ne touche qu'à elles.
const PREFIXE = 'nocturne-';
const ATTENTE_RESEAU = 3000;
const AU_DEPART = [
  './',
  'fonts/polices.css',
  'fonts/jetbrains-mono-latin.woff2',
  'fonts/bricolage-grotesque-latin.woff2',
  'fonts/bungee-latin.woff2',
  'fonts/inter-tight-latin.woff2',
  'manifest.webmanifest',
  'icone-192.png',
  'icone-512.png',
];
// Le programme aussi, pour qu'une application ouverte hors ligne dès sa
// première fois ait de quoi s'afficher. Un fichier manquant ne bloque pas
// l'installation (le géocodage peut être absent).
const DONNEES = ['events.json', 'lieux.json', 'venue_arrondissements.json'];

// La page elle-même, quelle que soit l'adresse qui y mène.
const PAGE = new URL('./', self.location.href).href;
const CHEMIN = new URL(PAGE).pathname;

self.addEventListener('install', e => {
  e.waitUntil((async () => {
    const reserve = await caches.open(CACHE);
    await reserve.addAll(AU_DEPART);
    await Promise.all(DONNEES.map(u => reserve.add(u).catch(() => {})));
    // La nouvelle version prend la place de l'ancienne sans attendre que
    // toutes les fenêtres soient fermées : les deux servent la même chose.
    await self.skipWaiting();
  })());
});

// Seules NOS anciennes réserves partent (audit A-12) : toutes celles de
// l'adresse ricojrlyon.github.io étaient vidées, celles des autres projets
// du compte (*films) comprises.
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(noms =>
    Promise.all(noms.filter(n => n.startsWith(PREFIXE) && n !== CACHE)
                    .map(n => caches.delete(n)))));
});

self.addEventListener('fetch', e => {
  const r = e.request;
  if (r.method !== 'GET') return;
  const url = new URL(r.url);
  if (url.origin !== self.location.origin) return;     // affiches : le navigateur s'en charge
  // Une autre page du site (les mentions légales) se garde et se sert
  // sous sa propre adresse : sans réseau, elle était remplacée par
  // l'accueil (audit A-40).
  if (r.mode === 'navigate') {
    e.respondWith(estLaPage(url) ? reseauDAbord(e, r, PAGE, PAGE) : reseauDAbord(e, r, r, r));
  }
  else if (url.pathname.endsWith('.json')) e.respondWith(reseauDAbord(e, r, r, r));
  else e.respondWith(reserveDAbord(e, r));
});

function estLaPage(url) {
  return url.pathname === CHEMIN || url.pathname === CHEMIN + 'index.html';
}

/* Le réseau d'abord ; la réserve si le réseau manque, ou s'il tarde plus
   d'ATTENTE_RESEAU. `garder` : sous quelle clé ranger une réponse réussie ;
   `secours` : ce qu'on sert sans réseau. */
async function reseauDAbord(e, r, garder, secours) {
  const reserve = await caches.open(CACHE);
  const reseau = fetch(r).then(async reponse => {
    if (reponse.ok && garder) await reserve.put(garder, reponse.clone());
    return reponse;
  });
  // La réponse du site, même tardive, range sa copie pour la fois suivante.
  e.waitUntil(reseau.catch(() => {}));
  const garde = () => reserve.match(secours, { ignoreSearch: true });
  const delai = new Promise(ok => setTimeout(ok, ATTENTE_RESEAU))
    .then(async () => (await garde()) || reseau);
  try {
    return await Promise.race([reseau, delai]);
  } catch (panne) {
    const copie = await garde();
    if (copie) return copie;
    throw panne;
  }
}

/* La réserve d'abord, rafraîchie ensuite ; le réseau si elle n'a rien. */
async function reserveDAbord(e, r) {
  const reserve = await caches.open(CACHE);
  const garde = await reserve.match(r, { ignoreSearch: true });
  const frais = fetch(r).then(reponse => {
    if (reponse.ok) e.waitUntil(reserve.put(r, reponse.clone()));
    return reponse;
  });
  if (garde) {
    e.waitUntil(frais.catch(() => {}));
    return garde;
  }
  return frais;
}
