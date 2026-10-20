/* *nocturne installé comme une application : ce service garde sur le
   téléphone la page, ses polices et le dernier programme chargé, pour que
   l'application s'ouvre même sans réseau - dans le métro, par exemple.

   En ligne, la page et le programme viennent TOUJOURS du site (le réseau
   d'abord) : rien de périmé n'est servi tant que le site répond, et la mise
   à jour de la nuit arrive comme avant. Le reste - polices, logos, icônes,
   qui ne changent pas - est servi du téléphone, puis rafraîchi en douce.
   Les affiches, venues des sites des salles, ne sont pas gardées : sans
   réseau, la carte montre son motif.

   Changer la liste AU_DEPART ou la logique ci-dessous : changer aussi le
   nom CACHE, pour que l'ancienne réserve soit vidée. */
// nocturne-2 : l'icône redessinée (fond nuit, N crème), à reprendre tout de suite.
const CACHE = 'nocturne-2';
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

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(noms =>
    Promise.all(noms.filter(n => n !== CACHE).map(n => caches.delete(n)))));
});

self.addEventListener('fetch', e => {
  const r = e.request;
  if (r.method !== 'GET') return;
  const url = new URL(r.url);
  if (url.origin !== self.location.origin) return;     // affiches : le navigateur s'en charge
  if (r.mode === 'navigate') e.respondWith(reseauDAbord(r, estLaPage(url) ? PAGE : null, PAGE));
  else if (url.pathname.endsWith('.json')) e.respondWith(reseauDAbord(r, r, r));
  else e.respondWith(reserveDAbord(e, r));
});

function estLaPage(url) {
  return url.pathname === CHEMIN || url.pathname === CHEMIN + 'index.html';
}

/* Le réseau d'abord ; la réserve si le réseau manque. `garder` : sous quelle
   clé ranger une réponse réussie (null : ne rien ranger) ; `secours` : ce
   qu'on sert sans réseau. */
async function reseauDAbord(r, garder, secours) {
  const reserve = await caches.open(CACHE);
  try {
    const reponse = await fetch(r);
    if (reponse.ok && garder) await reserve.put(garder, reponse.clone());
    return reponse;
  } catch (panne) {
    const garde = await reserve.match(secours, { ignoreSearch: true });
    if (garde) return garde;
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
