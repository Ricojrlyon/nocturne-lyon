/* Le pilote des tests de page, injecté à la fin de la page servie.

   Il attend le premier rendu, puis joue des SCÉNARIOS comme le ferait un
   visiteur - clics sur les puces, frappe dans la recherche, cases cochées -
   et relève, après chacun, chaque journée affichée et chaque carte : sa
   balise, son lien, et son texte hors visuel (l'affiche ou le motif, qui
   dépendent du réseau, sont exclus). Le relevé part au serveur de test.

   Il défile jusqu'au bout avant chaque relevé : un affichage qui ne
   construirait les journées qu'au fil du défilement serait relevé en entier,
   et comparable à l'affichage d'un seul tenant. */
(async () => {
  const pause = ms => new Promise(r => setTimeout(r, ms));
  const norme = s => (s || '').replace(/\s+/g, ' ').trim();
  const envoyer = corps => fetch('/__resultats', { method: 'POST', body: JSON.stringify(corps) });
  // Le service hors ligne prêt, ou null au bout de dix secondes : sans
  // limite, un service qui ne s'installe jamais ferait attendre le pilote
  // jusqu'à ce que le test se croie sans navigateur, et soit sauté.
  const servicePret = () => Promise.race([navigator.serviceWorker.ready, pause(10000).then(() => null)]);
  const SCENARIOS = window.__SCENARIOS || 'tous';

  // « laCarte » et non « carte » : ce nom-là est celui du plan de la page.
  const laCarte = el => {
    const c = el.cloneNode(true);
    // Hors relevé : l'image de fond et les boutons de carte (« + agenda »…),
    // qui ne disent rien de l'événement.
    c.querySelectorAll('.visual-bg, .carte-bouton, .nouveau').forEach(x => x.remove());
    return [el.tagName.toLowerCase(), el.getAttribute('href') || '', norme(c.textContent)];
  };

  // On ne conclut que sur trois relevés de suite sans journée nouvelle : une
  // image de retard du navigateur ne doit pas passer pour la fin du fil.
  const toutAfficher = async () => {
    let avant = -1, stable = 0;
    for (let i = 0; i < 1000 && stable < 3; i++) {
      const n = document.querySelectorAll('#feed section.day').length;
      stable = n === avant ? stable + 1 : 0;
      avant = n;
      window.scrollTo(0, document.documentElement.scrollHeight);
      await pause(80);
    }
    window.scrollTo(0, 0);
    await pause(80);
  };

  const releve = async () => {
    await toutAfficher();
    const vide = document.querySelector('#feed .empty-state');
    return {
      vide: vide ? norme(vide.textContent) : null,
      jours: [...document.querySelectorAll('#feed section.day')].map(s => [
        s.id,
        norme((s.querySelector('.day-bar') || {}).textContent),
        [...s.querySelectorAll('.event')].map(laCarte),
      ]),
    };
  };

  const effacer = async () => {
    document.getElementById('clearFilters').click();
    await pause(400);
  };

  // Une famille éteinte ou rallumée dans la barre du 10 octobre, au milieu
  // de la page. On relève le haut de cette journée, avant et après, dans
  // trois positions : sa barre à sa place en haut de l'écran ; sa barre
  // collée en haut, le visiteur au milieu de la journée ; la journée plus
  // bas, la veille à l'écran au-dessus d'elle.
  const familleAuMilieu = async () => {
    const jour = () => document.getElementById('day-2026-10-10');
    const haut = () => Math.round(jour().getBoundingClientRect().top);
    window.scrollTo(0, 0);
    await effacer();
    await pause(400);
    document.querySelectorAll('.day-pill')[9].click();
    await pause(800);
    const f = jour().querySelector('.fam-chip:not([disabled])').dataset.famille;
    const toucher = async () => {
      const avant = haut();
      jour().querySelector('.fam-chip[data-famille="' + f + '"]').click();
      await pause(800);
      return [avant, haut()];
    };
    const suivi = [await toucher()];
    window.scrollBy(0, 600);
    await pause(300);
    suivi.push(await toucher());
    window.scrollBy(0, -350);
    await pause(300);
    window.scrollBy(0, haut() - 350);     // la veille, affichée, a pu grandir
    await pause(300);
    suivi.push(await toucher());
    return suivi;
  };

  try {
    for (let i = 0; i < 600; i++) {
      if (document.querySelector('#feed .event, #feed .empty-state')) break;
      await pause(100);
    }
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    await pause(800);

    const res = {
      erreurs: window.__erreurs,
      // « state » est déclaré en const par la page : il n'est pas attaché à
      // window, mais reste lisible par son nom depuis un autre script.
      evenements: (typeof state !== 'undefined' && state.events) ? state.events.length : null,
      pied: norme((document.getElementById('lastUpdated') || {}).textContent),
      index: norme((document.getElementById('footerIndex') || {}).textContent),
      pastilles: [...document.querySelectorAll('.day-pill')].map(p => norme(p.textContent)),
      scenarios: {},
    };
    // Le nombre de sources du pied de page : seul le contrôle du fil du jour
    // (verif_page) le vérifie, la référence figée ne le relève pas.
    if (SCENARIOS === 'tout') res.sources = norme((document.getElementById('footerSources') || {}).textContent);
    res.scenarios.tout = await releve();

    if (SCENARIOS === 'tous') {
      const cliquer = async sel => {
        const el = typeof sel === 'string' ? document.querySelector(sel) : sel;
        if (!el) throw new Error('commande introuvable : ' + sel);
        el.click();
        await pause(400);
      };
      const cocher = async (conteneur, nom) => {
        const lab = [...document.querySelectorAll(conteneur + ' label')]
          .find(l => norme((l.querySelector('.fl-nom') || {}).textContent) === nom);
        if (!lab) throw new Error('case introuvable : ' + nom);
        lab.querySelector('input').click();
        await pause(400);
      };

      await cliquer('#filterWhen .chip[data-when="weekend"]');
      res.scenarios.ce_weekend = await releve();
      await cliquer('#filterWhen .chip[data-when="nextweek"]');
      res.scenarios.semaine_pro = await releve();
      await effacer();

      const champ = document.getElementById('filterSearch');
      champ.value = 'comedy';
      champ.dispatchEvent(new Event('input', { bubbles: true }));
      await pause(700);
      res.scenarios.recherche_comedy = await releve();
      await effacer();

      await cliquer(document.querySelector('.fam-chip[data-famille="sport"]:not([disabled])'));
      res.scenarios.sans_sport = await releve();
      await effacer();

      await cocher('#filterLieux', 'Le Sucre');
      res.scenarios.lieu_le_sucre = await releve();
      await effacer();

      await cocher('#filterTags', 'théâtre');
      res.scenarios.tag_theatre = await releve();
      await effacer();

      await cliquer('#filterArr .chip[data-arr="7e"]');
      res.scenarios.septieme = await releve();
      await effacer();

      await cliquer('#feed button.event[data-venue-day]');
      const deplie = await releve();
      res.scenarios.premier_groupe_deplie = { vide: deplie.vide, jours: deplie.jours.slice(0, 1) };
    } else if (SCENARIOS === 'rafale') {
      // Un visiteur pressé, AVEC les fondus : cinq filtres en rafale, sans
      // attendre qu'un fondu s'achève, puis le retour à « tout ».
      for (const quand of ['weekend', 'nextweek', 'all', 'weekend', 'all']) {
        document.querySelector('#filterWhen .chip[data-when="' + quand + '"]').click();
        await pause(30);
      }
      await pause(2500);
      res.scenarios.apres_rafale = await releve();
    } else if (SCENARIOS === 'nouveau') {
      // « nouveau » depuis ta dernière visite. Première visite : aucun
      // repère. On retire alors de la mémoire deux événements, comme s'ils
      // venaient d'être annoncés, et on revient : eux seuls sont marqués.
      const etape = sessionStorage.getItem('essai-nouveau');
      if (!etape) {
        const premiere = document.querySelectorAll('#feed .nouveau').length;
        const cles = [...new Set([...document.querySelectorAll('#feed a.event .carte-etoile')]
          .slice(0, 2).map(b => b.dataset.evenement))];
        const retirer = new Set(cles.map(c => stableHash(c).toString(36)));
        const vus = JSON.parse(localStorage.getItem('nocturne:vus') || '[]');
        localStorage.setItem('nocturne:vus', JSON.stringify(vus.filter(v => !retirer.has(v))));
        sessionStorage.removeItem('nocturne:vus-avant');
        sessionStorage.setItem('essai-nouveau', JSON.stringify({ cles, premiere, total: vus.length }));
        location.reload();
        return;
      }
      const avant = JSON.parse(etape);
      const marquees = [...new Set([...document.querySelectorAll('#feed a.event')]
        .filter(c => c.querySelector('.nouveau'))
        .map(c => c.querySelector('.carte-etoile').dataset.evenement))].sort();
      if (!avant.marquees) {
        // Deuxième visite : on note les repères, puis on recharge la page
        // pendant la même visite - ils doivent rester.
        avant.marquees = marquees;
        sessionStorage.setItem('essai-nouveau', JSON.stringify(avant));
        location.reload();
        return;
      }
      res.nouveau = { premiere_visite: avant.premiere, total_vus: avant.total,
                      marquees: avant.marquees, apres_rechargement: marquees,
                      attendues: avant.cles.slice().sort() };
    } else if (SCENARIOS === 'hors_ligne') {
      // L'application sans réseau. Premier passage : le service hors ligne
      // s'installe, le réseau est coupé, la page est rechargée. Second
      // passage : elle vient du téléphone, et son relevé « tout » part au
      // serveur, qui reçoit encore les envois.
      if (sessionStorage.getItem('hors_ligne') !== 'coupe') {
        if (!(await servicePret())) throw new Error("le service hors ligne ne s'est pas installé");
        sessionStorage.setItem('hors_ligne', 'coupe');
        await fetch('/__couper', { method: 'POST' });
        location.reload();
        return;
      }
      res.hors_ligne = { servie_par_le_telephone: !!navigator.serviceWorker.controller };
    } else if (SCENARIOS === 'navigation') {
      // Les polices téléchargées depuis l'ouverture, et par quoi : le
      // préchargement (« link ») ou la feuille de style (« css »).
      res.polices_chargees = performance.getEntriesByType('resource')
        .filter(r => r.name.endsWith('.woff2'))
        .map(r => [r.name.split('/').pop(), r.initiatorType]);
      // Le rangement de quelques catégories : rubrique et famille.
      res.rangement = ['Impro', 'Jazz', 'Théâtre']
        .map(c => [c, bucketOf(c), FAMILLE_DE_BUCKET.get(bucketOf(c))]);
      // L'application installable : son service hors ligne s'est installé.
      const service = await servicePret();
      res.service_hors_ligne = service && [service.active && service.active.state,
                                           new URL(service.scope).pathname];
      // La barre des 14 jours, touchée juste après un rendu : la journée
      // n'est pas encore construite (affichage par morceaux), et doit
      // pourtant arriver en haut de l'écran. On relève celle qui y est.
      res.pastilles_suivies = [];
      for (const i of [1, 6, 13]) {
        window.scrollTo(0, 0);
        document.getElementById('clearFilters').click();
        document.querySelectorAll('.day-pill')[i].click();
        await pause(600);
        const s = [...document.querySelectorAll('#feed section.day')]
          .find(s => s.getBoundingClientRect().bottom > 12);
        res.pastilles_suivies.push(s ? [s.id, Math.round(s.getBoundingClientRect().top)] : null);
      }
      // La recherche du navigateur (Ctrl+F) porte sur tout le fil : il se
      // construit d'un coup avant qu'elle ne s'ouvre.
      window.scrollTo(0, 0);
      await effacer();
      res.journees_avant_ctrl_f = document.querySelectorAll('#feed section.day').length;
      document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', ctrlKey: true, bubbles: true }));
      res.journees_apres_ctrl_f = document.querySelectorAll('#feed section.day').length;
      res.familles_suivies = await familleAuMilieu();
      // Audit n° 18 : le jour courant signalé aux lecteurs d'écran ; Échap
      // qui ferme le calendrier et rend la main au bouton « date » ; la
      // hauteur des boutons de famille.
      await effacer();
      window.scrollTo(0, 0);
      document.getElementById('dateBtn').click();
      await pause(200);
      const pastilles = [...document.querySelectorAll('.day-pill')];
      res.aujourd_hui = [...document.querySelectorAll('[aria-current="date"]')]
        .map(b => b.dataset.day || 'pastille ' + pastilles.indexOf(b));
      const caseDuJour = document.querySelector('#datepicker [data-day]:not([disabled])');
      caseDuJour.focus();
      caseDuJour.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      await pause(200);
      res.apres_echap = [document.getElementById('datepicker').classList.contains('open'),
                         document.activeElement ? document.activeElement.id : null];
      res.boutons_de_famille = [...new Set([...document.querySelectorAll('.fam-chip')]
        .map(c => Math.round(c.getBoundingClientRect().height)))];
      // Le lien suit les filtres, et « partager » le copie (sans écran
      // tactile, pas de menu de partage).
      await effacer();
      res.adresses = [location.search];
      document.querySelector('#filterWhen .chip[data-when="weekend"]').click();
      await pause(300);
      res.adresses.push(location.search);
      document.querySelector('#filterArr .chip[data-arr="7e"]').click();
      await pause(300);
      res.adresses.push(location.search);
      Object.defineProperty(navigator, 'clipboard', { configurable: true,
        value: { writeText: async t => { window.__copie = t; } } });
      document.getElementById('partager').click();
      await pause(200);
      const message = document.getElementById('toast');
      res.partage = [window.__copie === location.href, !message.hidden,
                     norme(message.textContent).startsWith('lien copié · 127.0.0.1')];
      await effacer();
      res.adresses.push(location.search);
      // « + agenda » : le fichier de la première carte simple, sans ouvrir
      // son lien. Le téléchargement est intercepté.
      window.scrollTo(0, 0);
      const carteSimple = document.querySelector('#feed a.event');
      const capture = {};
      const vraiCreer = URL.createObjectURL, vraiClic = HTMLAnchorElement.prototype.click;
      URL.createObjectURL = b => { capture.blob = b; return 'blob:essai'; };
      HTMLAnchorElement.prototype.click = function () {
        if (this.download) capture.nom = this.download; else vraiClic.call(this);
      };
      let lienOuvert = null;
      document.addEventListener('click', e => { lienOuvert = !e.defaultPrevented; }, { once: true });
      carteSimple.querySelector('.carte-bouton[data-action="agenda"]').click();
      await pause(200);
      URL.createObjectURL = vraiCreer;
      HTMLAnchorElement.prototype.click = vraiClic;
      const SAUT = String.fromCharCode(13, 10);
      const texte = capture.blob ? await capture.blob.text() : '';
      const lignes = texte.split(SAUT + ' ').join('').split(SAUT).filter(Boolean);
      const titre = norme(carteSimple.querySelector('.event-title').textContent);
      const BARRE = String.fromCharCode(92);
      res.agenda = {
        nom: capture.nom, type: capture.blob && capture.blob.type, lien_ouvert: lienOuvert,
        premiere: lignes[0], derniere: lignes[lignes.length - 1],
        debut: lignes.find(l => l.startsWith('DTSTART;')),
        titre_juste: lignes.includes('SUMMARY:' + texteAgenda(titre)),
        lien_juste: lignes.includes('URL:' + carteSimple.getAttribute('href')),
        lignes_courtes: texte.split(SAUT).every(l => l.length <= 61),
        echappement: texteAgenda('Rock, pop; jazz') === 'Rock' + BARRE + ', pop' + BARRE + '; jazz',
      };
      // « ▶ écouter » (nouveauté n° 5) : sur les cartes de musique, et
      // elles seules ; une recherche YouTube sur le titre, la carte fermée.
      const ouvertes = [];
      const vraiOpen = window.open;
      window.open = (url, cible, options) => { ouvertes.push([url, cible, options]); return null; };
      const cartesSimples = [...document.querySelectorAll('#feed a.event')];
      const musicale = cartesSimples.find(c => c.querySelector('[data-action="ecouter"]'));
      let carteOuverte = null;
      document.addEventListener('click', e => { carteOuverte = !e.defaultPrevented; }, { once: true });
      musicale.querySelector('[data-action="ecouter"]').click();
      await pause(100);
      window.open = vraiOpen;
      const titreMusical = norme(musicale.querySelector('.event-title').textContent);
      res.ecouter = {
        juste: ouvertes.length === 1
          && ouvertes[0][0] === 'https://www.youtube.com/results?search_query=' + encodeURIComponent(titreMusical)
          && ouvertes[0][1] === '_blank',
        carte_ouverte: carteOuverte,
        seulement_musique: cartesSimples.length > 10 && cartesSimples.every(c =>
          !!c.querySelector('[data-action="ecouter"]')
          === (familleOf(c.querySelector('.event-cat').textContent) === 'musique')),
      };
      // Les boutons d'une carte de musique : deux icônes sans texte, nommées
      // pour les lecteurs d'écran, à droite de l'heure, l'agenda au bord.
      const avecHeure = cartesSimples.find(c => c.querySelector('[data-action="ecouter"]')
                                              && c.querySelector('.event-time'));
      const droite = avecHeure.querySelector('.event-foot-right');
      const ag = droite.querySelector('[data-action="agenda"]'), yt = droite.querySelector('[data-action="ecouter"]');
      const avant = (a, b) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
      res.boutons_de_carte = [norme(ag.textContent), norme(yt.textContent), !!ag.querySelector('svg'),
                              !!yt.querySelector('svg'), ag.getAttribute('aria-label'), yt.getAttribute('aria-label'),
                              avant(droite.querySelector('.event-time'), yt), avant(yt, ag),
                              droite.lastElementChild === ag];
      // ★ favoris et mes salles (nouveauté n° 3).
      await effacer();
      window.scrollTo(0, 0);
      const garde = document.querySelector('#feed a.event');
      const etoile = garde.querySelector('.carte-etoile');
      let ouvert = null;
      document.addEventListener('click', e => { ouvert = !e.defaultPrevented; }, { once: true });
      etoile.click();
      await pause(200);
      const memoire = cle => JSON.parse(localStorage.getItem(cle) || '[]');
      const cleGardee = etoile.dataset.evenement;
      res.favoris = { presse: etoile.getAttribute('aria-pressed'), lien_ouvert: ouvert,
                      memoire: memoire('nocturne:favoris').length };
      document.getElementById('voirFavoris').click();
      await pause(500);
      const visibles = () => [...document.querySelectorAll('#feed .event')];
      res.favoris.seul = visibles().length > 0
        && visibles().every(c => (c.querySelector('.carte-etoile') || {}).dataset?.evenement === cleGardee);
      // Une période touchée ne l'éteint pas : le filtre reste actif.
      document.querySelector('#filterWhen .chip[data-when="weekend"]').click();
      await pause(300);
      res.favoris.apres_une_periode = document.getElementById('voirFavoris').getAttribute('aria-pressed');
      document.querySelector('#filterWhen .chip[data-when="all"]').click();
      await pause(300);
      if (document.getElementById('filterDrawer').hidden) document.getElementById('drawerToggle').click();
      const sucre = [...document.querySelectorAll('#filterLieux label')]
        .find(l => norme(l.querySelector('.fl-nom').textContent) === 'Le Sucre');
      sucre.querySelector('.salle-etoile').click();
      await pause(500);
      res.favoris.salles = memoire('nocturne:salles');
      res.favoris.case_cochee = [...document.querySelectorAll('#filterLieux label')]
        .find(l => norme(l.querySelector('.fl-nom').textContent) === 'Le Sucre').querySelector('input').checked
        || state.lieux.size > 0;
      res.favoris.en_tete = norme(document.querySelector('#filterLieux label .fl-nom').textContent);
      res.favoris.ligne = norme(document.getElementById('mesSalles').textContent);
      res.favoris.avec_la_salle = visibles().length > 1 && visibles().every(c =>
        norme((c.querySelector('.event-venue') || {}).textContent) === 'Le Sucre'
        || (c.querySelector('.carte-etoile') || {}).dataset?.evenement === cleGardee);
      document.querySelector('#filterLieux .salle-etoile').click();
      await pause(300);
      etoile.isConnected || (await pause(100));
      await effacer();
      res.favoris.apres_effacer = [document.getElementById('voirFavoris').getAttribute('aria-pressed'),
                                   visibles().length > 1];
      document.getElementById('drawerToggle').click();
      // Audit n° 20 : le bouton « date » dit la période choisie, puis
      // redevient « date → » ; un filtre sans résultat le dit, et son
      // « tout effacer » ramène tout le fil.
      const bouton = document.getElementById('dateBtn');
      const choisir = async (a, b) => {
        bouton.click();
        await pause(200);
        document.querySelector('#datepicker [data-day="' + a + '"]').click();
        await pause(100);
        document.querySelector('#datepicker [data-day="' + b + '"]').click();
        await pause(600);
        return [norme(bouton.textContent), bouton.getAttribute('aria-label')];
      };
      res.bouton_date = [await choisir('2026-10-12', '2026-10-18'),
                         await choisir('2026-10-28', '2026-11-01'),
                         await choisir('2026-10-15', '2026-10-15')];
      await effacer();
      res.bouton_date.push([norme(bouton.textContent), bouton.getAttribute('aria-label')]);
      const champ = document.getElementById('filterSearch');
      champ.value = 'aucun événement ne porte ce titre';
      champ.dispatchEvent(new Event('input', { bubbles: true }));
      await pause(700);
      const vide = document.querySelector('#feed .empty-state');
      res.filtre_vide = vide ? [norme((vide.querySelector('p') || {}).textContent),
                                norme((vide.querySelector('button') || {}).textContent)] : null;
      if (vide) vide.querySelector('.vide-effacer').click();
      await pause(700);
      res.apres_tout_effacer = [document.querySelectorAll('#feed .event').length > 0, champ.value];
    } else if (SCENARIOS === 'carte') {
      // La vue carte (nouveauté n° 6). Premier passage : l'interrupteur, le
      // plan du 1er octobre, sa fiche, ses gestes, la barre des jours, le
      // retour à la liste. Second passage : la page rouverte par un lien.
      const etape = sessionStorage.getItem('essai-carte');
      if (etape) {
        const avant = JSON.parse(etape);
        res.carte = avant.carte;
        res.erreurs_premier_passage = avant.erreurs;
        res.carte_par_lien = {
          adresse: location.search,
          titre: norme((document.querySelector('.carte-vue .ct-jour') || {}).textContent),
          journees: document.querySelectorAll('#feed section.day').length,
          interrupteur: [...document.querySelectorAll('#vueSwitch [data-vue]')].map(b => b.getAttribute('aria-pressed')),
        };
      } else {
        const c = {};
        const jourDeListe = document.getElementById('day-2026-10-01');
        c.compte_de_la_liste = parseInt(jourDeListe.querySelector('.db-count').textContent, 10);
        document.querySelector('#vueSwitch [data-vue="carte"]').click();
        await pause(800);
        c.adresse = location.search;
        c.interrupteur = [...document.querySelectorAll('#vueSwitch [data-vue]')].map(b => b.getAttribute('aria-pressed'));
        c.titre = norme(document.querySelector('.carte-vue .ct-jour').textContent);
        c.compte = norme(document.querySelector('.carte-vue .ct-compte').textContent);
        c.journees = document.querySelectorAll('#feed section.day').length;
        const cadre = document.querySelector('.carte-cadre');
        const fiche = document.querySelector('.carte-fiche');
        const pins = () => [...document.querySelectorAll('.carte-pin')];
        const nombre = b => Number(b.textContent.split(' · ').pop());
        const total = () => pins().reduce((s, p) => s + Number(p.textContent), 0)
          + [...document.querySelectorAll('[data-ailleurs]')].reduce((s, b) => s + nombre(b), 0);
        c.total = total();
        c.pastilles = pins().length;
        const rc = cadre.getBoundingClientRect();
        c.dans_le_cadre = pins().every(p => {
          const r = p.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
          return x >= rc.left && x <= rc.right && y >= rc.top && y <= rc.bottom;
        });
        const noms = [...document.querySelectorAll('.carte-nom:not([hidden])')].map(n => n.getBoundingClientRect());
        const boites = noms.concat(pins().map(p => p.getBoundingClientRect()));
        const couvre = (a, b) => a.left < b.right - 0.5 && b.left < a.right - 0.5
                              && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5;
        c.noms = noms.length;
        c.sans_chevauchement = boites.every((a, i) => boites.every((b, j) =>
          i === j || (i >= noms.length && j >= noms.length) || !couvre(a, b)));
        // Les tuiles : leurs adresses, et le cadre couvert jusqu'aux coins.
        const tuiles = [...document.querySelectorAll('.carte-tuiles img')];
        const rt = tuiles.map(i => i.getBoundingClientRect());
        const point = (x, y) => rt.some(r => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom);
        c.tuiles = [tuiles.length > 0,
                    tuiles.every(i => /^https:[/][/]tile[.]openstreetmap[.]org[/]1[1-7][/][0-9]+[/][0-9]+[.]png$/.test(i.src)),
                    [[rc.left + 1, rc.top + 1], [rc.right - 1, rc.top + 1], [rc.left + 1, rc.bottom - 1],
                     [rc.right - 1, rc.bottom - 1]].every(([x, y]) => point(x, y))];
        c.projection = versLePlan(45.7578, 4.832, 12).map(Math.floor);
        // La fiche d'un lieu : ses soirées, des liens ; Échap la ferme et
        // rend la main à la pastille.
        const seul = pins().find(p => !p.classList.contains('groupe'));
        seul.click();
        await pause(300);
        const nomDuLieu = seul.getAttribute('aria-label').split(' : ')[0];
        c.fiche = [!fiche.hidden,
                   fiche.querySelectorAll('.fiche-evts li').length === Number(seul.textContent),
                   norme(fiche.querySelector('.fl-nom').textContent).startsWith(nomDuLieu),
                   seul.classList.contains('choisi'),
                   document.activeElement === fiche.querySelector('.fiche-lieu'),
                   [...fiche.querySelectorAll('.fiche-evts a')].every(a => a.target === '_blank' && /^https?:/.test(a.href))];
        fiche.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        await pause(200);
        c.apres_echap = [fiche.hidden, document.activeElement === seul, seul.classList.contains('choisi')];
        // Les boutons + et −.
        const z0 = carte.z;
        document.querySelector('.carte-zoom [data-zoom="1"]').click();
        await pause(200);
        const z1 = carte.z;
        document.querySelector('.carte-zoom [data-zoom="-1"]').click();
        await pause(200);
        c.boutons_de_zoom = [z1 - z0, carte.z - z0];
        // Ctrl + molette zoome autour du pointeur : le lieu qui y était y
        // reste ; la molette seule ne zoome pas.
        const p0 = carte.places[0];
        const ecran = () => {
          const [x, y] = versLePlan(p0.pos[0], p0.pos[1], carte.z), r = cadre.getBoundingClientRect();
          return [r.left + cadre.clientWidth / 2 + x - carte.cx, r.top + cadre.clientHeight / 2 + y - carte.cy];
        };
        const [ax, ay] = ecran();
        const molette = ctrlKey => cadre.dispatchEvent(new WheelEvent('wheel',
          { deltaY: -100, ctrlKey, clientX: ax, clientY: ay, bubbles: true, cancelable: true }));
        const zm = carte.z;
        molette(false);
        await pause(100);
        const sansCtrl = carte.z - zm;
        molette(true);
        await pause(200);
        const [bx, by] = ecran();
        c.molette = [sansCtrl, carte.z - zm, Math.round(Math.hypot(bx - ax, by - ay))];
        // Le doigt (ou la souris) fait glisser le plan ; un tremblé de moins
        // de 5 px n'est pas un glissé.
        const [cx0, cy0] = [carte.cx, carte.cy];
        const doigt = (type, x, y) => cadre.dispatchEvent(new PointerEvent(type,
          { pointerId: 7, pointerType: 'touch', isPrimary: true, clientX: x, clientY: y, bubbles: true }));
        doigt('pointerdown', 400, 300);
        doigt('pointermove', 403, 301);
        const tremble = [carte.cx - cx0, carte.cy - cy0];
        doigt('pointermove', 340, 270);
        doigt('pointerup', 340, 270);
        await pause(100);
        c.glisse = [tremble, [Math.round(carte.cx - cx0), Math.round(carte.cy - cy0)]];
        // Une pastille touchée au clavier après un glissé ouvre sa fiche.
        const encore = pins().find(p => !p.classList.contains('groupe'));
        encore.click();
        await pause(200);
        c.clavier_apres_glisse = !fiche.hidden;
        document.querySelector('.fiche-fermer').click();
        await pause(200);
        // Les flèches du clavier déplacent le plan.
        cadre.focus();
        const cx1 = carte.cx;
        cadre.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
        c.fleche = Math.round(carte.cx - cx1);
        // Une pastille de plusieurs lieux proches zoome sur eux.
        const groupe = pins().find(p => p.classList.contains('groupe'));
        const zg = carte.z;
        if (groupe) groupe.click();
        await pause(300);
        c.groupe = groupe ? [carte.z - zg, fiche.hidden] : null;
        // Une famille éteinte dans l'en-tête du plan : ses lieux s'en vont.
        const fam = document.querySelector('.carte-vue .fam-chip:not([disabled])');
        const cle = fam.dataset.famille, avantFamille = total();
        fam.click();
        await pause(500);
        c.famille = [document.querySelector('.carte-vue .fam-chip[data-famille="' + cle + '"]').getAttribute('aria-pressed'),
                     total() < avantFamille];
        document.querySelector('.carte-vue .fam-chip[data-famille="' + cle + '"]').click();
        await pause(500);
        c.famille.push(total() === avantFamille);
        // La barre des 14 jours choisit la journée du plan ; les flèches de
        // l'en-tête passent à la suivante et reviennent.
        document.querySelectorAll('.day-pill')[2].click();
        await pause(500);
        c.pastille = [norme(document.querySelector('.ct-jour').textContent), location.search,
                      [...document.querySelectorAll('.day-pill.choisi')].map(p => p.dataset.jour)];
        document.querySelector('.carte-saut[data-saut="1"]').click();
        await pause(500);
        c.suivant = norme(document.querySelector('.ct-jour').textContent);
        document.querySelector('.carte-saut[data-saut="-1"]').click();
        await pause(500);
        c.precedent = norme(document.querySelector('.ct-jour').textContent);
        // « tout effacer » garde la carte ; « liste » ramène les journées.
        await effacer();
        c.apres_effacer = !!document.querySelector('.carte-vue');
        document.querySelector('#vueSwitch [data-vue="liste"]').click();
        await pause(800);
        c.retour = [location.search, document.querySelectorAll('#feed section.day').length > 0,
                    document.querySelectorAll('.day-pill.choisi').length, !!document.querySelector('.carte-vue')];
        sessionStorage.setItem('essai-carte', JSON.stringify({ carte: c, erreurs: window.__erreurs }));
        location.href = location.pathname + '?vue=carte&jour=2026-10-03';
        return;
      }
    } else if (SCENARIOS === 'pastilles_fondues') {
      // Avec les fondus : la pastille d'un jour proche fait défiler la page
      // en douceur, celle d'un jour lointain la fait sauter, par-dessus des
      // journées qui, juste après un rendu, n'ont encore jamais été
      // affichées. On attend que la page s'arrête, puis on relève la
      // journée arrivée en haut de l'écran.
      res.pastilles_suivies = [];
      for (const i of [0, 6, 13]) {
        window.scrollTo(0, 0);
        await effacer();
        await pause(400);
        document.querySelectorAll('.day-pill')[i].click();
        for (let k = 0; k < 30 && window.scrollY === 0; k++) await pause(100);
        let y = -1, stable = 0;
        for (let k = 0; k < 100 && stable < 3; k++) {
          stable = window.scrollY === y ? stable + 1 : 0;
          y = window.scrollY;
          await pause(100);
        }
        const s = [...document.querySelectorAll('#feed section.day')]
          .find(s => s.getBoundingClientRect().bottom > 12);
        res.pastilles_suivies.push(s ? [s.id, Math.round(s.getBoundingClientRect().top)] : null);
      }
      res.familles_suivies = await familleAuMilieu();
    }
    res.erreurs_fin = window.__erreurs;
    await envoyer(res);
  } catch (e) {
    await envoyer({ echec: String(e && e.stack || e), erreurs: window.__erreurs });
  }
})();
