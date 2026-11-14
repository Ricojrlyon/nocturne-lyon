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
    c.querySelectorAll('.visual-bg, .carte-bouton').forEach(x => x.remove());
    // Une carte simple est un <article> dont le titre porte le lien (audit
    // A-19) : relevée comme avant, « a » et son lien quand elle en a un,
    // « div » sinon. La référence dit ce que voit et suit le visiteur.
    const lien = el.querySelector('.event-lien');
    const balise = el.tagName === 'ARTICLE' ? (lien ? 'a' : 'div') : el.tagName.toLowerCase();
    return [balise, lien ? lien.getAttribute('href') : (el.getAttribute('href') || ''),
            norme(c.textContent)];
  };
  // Les cartes simples qui portent un lien, et ce lien.
  const CARTE_A_LIEN = '#feed article.event:has(.event-lien)';
  const lienDe = carte => carte.querySelector('.event-lien').getAttribute('href');

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
      // Le fuseau que le navigateur a réellement pris (voir navigateur.jouer).
      fuseau: Intl.DateTimeFormat().resolvedOptions().timeZone,
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
      // Le repère « nouveau » est retiré : un téléphone qui gardait ses
      // empreintes les voit effacées à l'ouverture, et aucune carte ne le
      // porte plus.
      if (!sessionStorage.getItem('essai-nouveau')) {
        localStorage.setItem('nocturne:vus', JSON.stringify(['abc', 'def']));
        sessionStorage.setItem('nocturne:vus-avant', JSON.stringify(['abc']));
        sessionStorage.setItem('essai-nouveau', '1');
        location.reload();
        return;
      }
      res.nouveau = [document.querySelectorAll('#feed .event').length > 0,
                     document.querySelectorAll('.nouveau').length,
                     localStorage.getItem('nocturne:vus'),
                     sessionStorage.getItem('nocturne:vus-avant')];
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
      // L'agenda : un menu de trois choix ; « iPhone et autres » donne le
      // fichier de la première carte simple, sans ouvrir son lien. Le
      // téléchargement est intercepté.
      window.scrollTo(0, 0);
      const carteSimple = document.querySelector(CARTE_A_LIEN);
      const capture = {};
      const vraiCreer = URL.createObjectURL, vraiClic = HTMLAnchorElement.prototype.click;
      URL.createObjectURL = b => { capture.blob = b; return 'blob:essai'; };
      HTMLAnchorElement.prototype.click = function () {
        if (this.download) capture.nom = this.download; else vraiClic.call(this);
      };
      let lienOuvert = null;
      document.addEventListener('click', e => { lienOuvert = !e.defaultPrevented; }, { once: true });
      const boutonAgenda = carteSimple.querySelector('.carte-bouton[data-action="agenda"]');
      const menu = document.getElementById('menuAgenda');
      boutonAgenda.click();
      await pause(200);
      res.menu_agenda = [!menu.hidden, boutonAgenda.getAttribute('aria-expanded'),
                         [...menu.querySelectorAll('[data-agenda]')].map(b => b.dataset.agenda),
                         document.activeElement === menu.querySelector('[data-agenda="google"]')];
      menu.querySelector('[data-agenda="ics"]').click();
      await pause(200);
      res.menu_agenda.push(menu.hidden, boutonAgenda.getAttribute('aria-expanded'));
      URL.createObjectURL = vraiCreer;
      HTMLAnchorElement.prototype.click = vraiClic;
      // Google Agenda et Outlook.com : une adresse, ouverte dans un autre
      // onglet, la soirée remplie.
      const evAgenda = CARTES.get(boutonAgenda.dataset.cle);
      const ouvertesAgenda = [];
      const vraiOpenAgenda = window.open;
      window.open = (url, cible) => { ouvertesAgenda.push([url, cible]); return null; };
      for (const service of ['google', 'outlook']) {
        boutonAgenda.click();
        await pause(150);
        menu.querySelector('[data-agenda="' + service + '"]').click();
        await pause(150);
      }
      window.open = vraiOpenAgenda;
      const g = new URL(ouvertesAgenda[0][0]), o = new URL(ouvertesAgenda[1][0]);
      const titreAgenda = (evAgenda.title || '').trim(), lieuAgenda = adresseAgenda(evAgenda);
      const lienCarte = new URL(lienDe(carteSimple)).href;   // nettoyé (audit A-39)
      res.agenda_en_ligne = {
        date: evAgenda._date, heure: evAgenda.time || '',
        google: [g.origin + g.pathname, g.searchParams.get('action'), g.searchParams.get('text') === titreAgenda,
                 g.searchParams.get('dates'), g.searchParams.get('ctz'),
                 g.searchParams.get('location') === lieuAgenda && lieuAgenda.startsWith(evAgenda.venue),
                 g.searchParams.get('details') === lienCarte, ouvertesAgenda[0][1]],
        outlook: [o.origin + o.pathname, o.searchParams.get('subject') === titreAgenda,
                  o.searchParams.get('startdt'), o.searchParams.get('enddt'),
                  o.searchParams.get('location') === lieuAgenda, o.searchParams.get('body') === lienCarte,
                  ouvertesAgenda[1][1]],
      };
      res.decalages = ['2026-03-28', '2026-03-29', '2026-07-01', '2026-10-24', '2026-10-25', '2026-12-01']
        .map(decalageDeParis);
      // Échap ferme le menu et rend la main à l'icône ; un clic ailleurs le ferme.
      boutonAgenda.click();
      await pause(150);
      menu.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      await pause(100);
      res.menu_agenda.push(menu.hidden, document.activeElement === boutonAgenda);
      boutonAgenda.click();
      await pause(150);
      document.querySelector('.masthead').click();
      await pause(100);
      res.menu_agenda.push(menu.hidden);
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
        lien_juste: lignes.includes('URL:' + new URL(lienDe(carteSimple)).href),
        // 75 octets par ligne au plus, la limite de la norme (audit A-39).
        lignes_courtes: texte.split(SAUT).every(l => new TextEncoder().encode(l).length <= 75),
        echappement: texteAgenda('Rock, pop; jazz') === 'Rock' + BARRE + ', pop' + BARRE + '; jazz',
      };
      // « ▶ écouter » (nouveauté n° 5) : sur les cartes de musique, et
      // elles seules ; une recherche YouTube sur le titre, la carte fermée.
      const ouvertes = [];
      const vraiOpen = window.open;
      window.open = (url, cible, options) => { ouvertes.push([url, cible, options]); return null; };
      const cartesSimples = [...document.querySelectorAll(CARTE_A_LIEN)];
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
      const garde = document.querySelector(CARTE_A_LIEN);
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
      // Le bouton des filtres d'une journée : au milieu de la page, il
      // épingle le haut (filtres et barre des jours) en haut de l'écran sans
      // rien faire bouger ; la barre de la journée se cale dessous, une
      // journée rejointe par la barre des jours aussi ; un second clic le
      // rend à sa place.
      window.scrollTo(0, 0);
      await pause(200);
      const jourLoin = document.querySelectorAll('#feed section.day')[2];
      // Au milieu d'une journée, sa ligne colle deux pixels au-dessus du
      // bord de l'écran : pas de fil de cartes au-dessus d'elle.
      jourLoin.scrollIntoView();
      await pause(200);
      window.scrollBy(0, 120);
      await pause(200);
      res.ligne_collee = Math.round(jourLoin.querySelector('.day-bar').getBoundingClientRect().top * 10) / 10;
      jourLoin.scrollIntoView();
      await pause(300);
      const hautF = document.getElementById('hautFiltres');
      const boutonF = jourLoin.querySelector('.db-filtres');
      const cache = hautF.getBoundingClientRect().bottom <= 0;
      const yAvant = window.scrollY;
      boutonF.click();
      await pause(300);
      const barreF = jourLoin.querySelector('.day-bar');
      const epingle = [cache, boutonF.getAttribute('aria-expanded'), Math.round(hautF.getBoundingClientRect().top),
                       Math.round(barreF.getBoundingClientRect().top - hautF.getBoundingClientRect().bottom),
                       window.scrollY === yAvant];
      document.querySelectorAll('.day-pill')[6].click();
      await pause(700);
      epingle.push(Math.round(document.getElementById('day-2026-10-07').getBoundingClientRect().top
                              - hautF.getBoundingClientRect().bottom));
      const boutonF2 = document.querySelector('#day-2026-10-07 .db-filtres');
      const y2 = window.scrollY;
      boutonF2.click();
      await pause(300);
      epingle.push(boutonF2.getAttribute('aria-expanded'), document.body.classList.contains('filtres-epingles'),
                   getComputedStyle(document.documentElement).getPropertyValue('--haut-filtres').trim(),
                   window.scrollY === y2, getComputedStyle(hautF).position);
      res.filtres_epingles = epingle;
      window.scrollTo(0, 0);
    } else if (SCENARIOS === 'service') {
      // Le service hors ligne (audit A-12, A-40), en trois temps.
      const actif = async () => {
        const reg = await servicePret();
        for (let i = 0; reg && reg.active && reg.active.state !== 'activated' && i < 100; i++) await pause(100);
        return reg;
      };
      // Les mentions légales sont VISITÉES (un cadre serait bloqué par la
      // règle de sécurité de la page) ; leur copie de test porte un relais
      // (RELAIS_MENTIONS, tests/navigateur.py) qui ramène ici, ou rapporte.
      const etape = sessionStorage.getItem('service') || '0';
      if (etape === '0') {
        // En ligne : le service s'installe ; une réserve d'un AUTRE projet
        // du même site, et une vieille réserve de nocturne ; puis les
        // mentions légales sont visitées, et le relais revient ici.
        if (!(await actif())) throw new Error("le service hors ligne ne s'est pas installé");
        await (await caches.open('autre-projet-1')).put('/autre', new Response('autre'));
        await (await caches.open('nocturne-1')).put('/vieux', new Response('vieux'));
        sessionStorage.setItem('service', '0b');
        location.href = 'mentions-legales.html';
        return;
      }
      if (etape === 'u') {
        // Le service est désinstallé : il se réinstallera au chargement.
        for (const r of await navigator.serviceWorker.getRegistrations()) await r.unregister();
        sessionStorage.setItem('service', '1');
        location.reload();
        return;
      }
      if (etape === '3') {
        // Les mentions légales demandées sans réseau, et c'est l'accueil
        // qui est venu : le relevé le dit.
        const r = JSON.parse(sessionStorage.getItem('releve') || '{}');
        r.service.mentions_sans_reseau = document.title;
        await envoyer(r);
        return;
      }
      if (etape === '1') {
        // Le service se réinstalle, et fait son ménage en s'activant.
        if (!(await actif())) throw new Error("le service hors ligne ne s'est pas réinstallé");
        await pause(300);
        sessionStorage.setItem('reserves', JSON.stringify((await caches.keys()).sort()));
        await fetch('/__lent', { method: 'POST' });
        sessionStorage.setItem('service', '2');
        location.reload();
        return;
      }
      // Le site met six secondes à répondre : la page vient du téléphone
      // au bout de trois. Puis le réseau coupé : les mentions légales
      // s'ouvrent, et non l'accueil.
      const navigation = performance.getEntriesByType('navigation')[0];
      res.service = {
        reserves: JSON.parse(sessionStorage.getItem('reserves') || '[]'),
        page_en_moins_de_5_s: !!navigation && navigation.responseEnd < 5000,
        controlee: !!navigator.serviceWorker.controller,
      };
      res.erreurs_fin = window.__erreurs;
      sessionStorage.setItem('releve', JSON.stringify(res));
      await fetch('/__couper', { method: 'POST' });
      sessionStorage.setItem('service', '3');
      location.href = 'mentions-legales.html';
      return;
    } else if (SCENARIOS === 'lot7') {
      // Audit, lot 7 : la performance profonde et l'accessibilité.
      await effacer();
      window.scrollTo(0, 0);
      await pause(300);
      // A-15 : le premier morceau couvre deux écrans, pas 400 événements.
      // Mesuré juste après le rendu, avant que la page ne construise la
      // suite pendant ses moments libres.
      renderFeed();
      const journees = [...document.querySelectorAll('#feed section.day')];
      const derniere = journees[journees.length - 1];
      res.premier_morceau = {
        journees: journees.length,
        cartes: document.querySelectorAll('#feed .event').length,
        derniere_commence_avant_deux_ecrans: derniere.getBoundingClientRect().top < 2 * window.innerHeight,
        couvre_deux_ecrans: derniere.getBoundingClientRect().bottom >= 2 * window.innerHeight,
      };
      await pause(800);
      // A-15 : les enseignes, déclarées une fois, citées par les cartes.
      const enseignes = [...document.querySelectorAll('#feed svg.enseigne')];
      const symboles = document.querySelectorAll('.planche-enseignes symbol');
      const premiere = enseignes[0] && enseignes[0].querySelector('use');
      res.enseignes = {
        cartes: enseignes.length,
        textes_dans_les_cartes: document.querySelectorAll('#feed svg.enseigne text').length,
        toutes_citent: enseignes.every(s => {
          const u = s.querySelector('use');
          return u && document.getElementById(u.getAttribute('href').slice(1)) !== null;
        }),
        symboles: symboles.length,
        dessinee: !!premiere && premiere.getBBox().width > 0,
      };
      // A-15 : la recherche ne normalise plus rien à chaque frappe, et le
      // jour de Paris n'est demandé qu'une fois par filtrage.
      const vraiNorm = window.normSearch, vraiJour = window.aujourdhui;
      let normalisations = 0, jours = 0;
      window.normSearch = s => { normalisations++; return vraiNorm(s); };
      window.aujourdhui = () => { jours++; return vraiJour(); };
      state.search = 'le';
      const trouves = filtered().length;
      state.search = '';
      window.normSearch = vraiNorm;
      window.aujourdhui = vraiJour;
      res.recherche = { trouves_plus_de_cent: trouves > 100, normalisations, jours };
      // A-19 : plus rien de cliquable DANS un lien ; de vrais boutons ; le
      // lien du titre couvre la carte, les boutons passent dessus.
      // (l'ancienne carte, un lien entier, si la page n'a pas encore la nouvelle)
      const carte = document.querySelector('#feed article.event:has(.event-lien)')
        || document.querySelector('#feed a.event');
      const r = carte.getBoundingClientRect();
      const lieu = carte.querySelector('.event-venue').getBoundingClientRect();
      const agenda = carte.querySelector('[data-action="agenda"]').getBoundingClientRect();
      const sous = (x, y) => document.elementFromPoint(x, y);
      carte.scrollIntoView({ block: 'center' });
      await pause(200);
      const r2 = carte.getBoundingClientRect();
      const dy = r2.top - r.top;
      // La couche visuelle passe sous le texte (z-index -1) dans une carte
      // isolée : sans cela, le titre, qui n'est plus positionné, passerait
      // dessous, ou la couche sous le fond de la carte.
      const fond = carte.querySelector('.visual-bg');
      res.calques = [fond ? getComputedStyle(fond).zIndex : null, getComputedStyle(carte).isolation];
      res.cartes_accessibles = {
        dans_un_lien: document.querySelectorAll('#feed a button, #feed a [role="button"], #feed a [tabindex], #feed a a').length,
        boutons: [...new Set([...document.querySelectorAll('#feed .carte-bouton')].map(b => b.tagName))],
        lieu_ouvre_le_lien: !!(sous(lieu.left + 2, lieu.top + dy + lieu.height / 2) || { closest: () => null }).closest('.event-lien'),
        agenda_est_un_bouton: (sous(agenda.left + agenda.width / 2, agenda.top + dy + agenda.height / 2) || {}).dataset?.action === 'agenda',
        coin_ouvre_le_lien: !!(sous(r2.left + 6, r2.top + r2.height / 2) || { closest: () => null }).closest('.event-lien'),
      };
      // A-19 : le nombre de résultats et « aucun événement » sont annoncés.
      const annonce = document.getElementById('annonce')
        || { getAttribute: () => null, textContent: '' };   // page sans annonce
      const champ = document.getElementById('filterSearch');
      champ.value = 'aucun événement ne porte ce titre';
      champ.dispatchEvent(new Event('input', { bubbles: true }));
      await pause(700);
      const vide = norme(annonce.textContent);
      await effacer();
      champ.value = '';
      champ.dispatchEvent(new Event('input', { bubbles: true }));
      await pause(700);
      res.annonces = { role: annonce.getAttribute('role'), vide, plein: norme(annonce.textContent) };
    } else if (SCENARIOS === 'lot6') {
      // Audit, lot 6 : la page, sa logique et sa conformité.
      if (!sessionStorage.getItem('lot6')) {
        // A-40 : un vieux favori gardé AVANT l'ouverture quitte la mémoire
        // au chargement suivant.
        localStorage.setItem('nocturne:favoris', JSON.stringify(['Ancienne salle|Spectacle fini|2026-07-01']));
        sessionStorage.setItem('lot6', '1');
        location.reload();
        return;
      }
      res.favori_ancien_au_chargement = JSON.parse(localStorage.getItem('nocturne:favoris') || '[]');
      await effacer();
      window.scrollTo(0, 0);
      await pause(300);
      // A-17 : le bouton agenda d'une carte retrouve SA séance, heure
      // comprise, même quand le même titre se joue deux fois le même soir.
      // Le 3 octobre, « COMEDY CLUB » au Complexe, à 20:00 et à 22:00 : la
      // journée est amenée à l'écran, le groupe du Complexe déplié.
      document.querySelectorAll('.day-pill')[2].click();
      await pause(700);
      const groupeComplexe = [...document.querySelectorAll('#feed button.event[data-venue-day]')]
        .find(b => b.dataset.venueDay.includes('2026-10-03') && b.dataset.venueDay.includes('Complexe'));
      if (groupeComplexe) {
        groupeComplexe.click();
        await pause(500);
      }
      const cartes = [...document.querySelectorAll('#feed .event')];
      const vues = cartes.map(c => {
        const b = c.querySelector('[data-action="agenda"]');
        const e = b && CARTES.get(b.dataset.cle);
        return { e, heure: norme((c.querySelector('.event-time:not(.empty)') || {}).textContent) };
      }).filter(v => v.e);
      const soirs = new Map();
      for (const v of vues) {
        const k = cleEvenement(v.e) + '|' + v.e._date;
        soirs.set(k, (soirs.get(k) || new Set()).add(v.heure));
      }
      res.agenda_par_seance = {
        soirs_a_deux_seances: [...soirs.values()].filter(h => h.size > 1).length,
        heures_fausses: vues.filter(v => (v.e.time || '') !== v.heure).length,
      };
      // A-18 : la zone de toucher de l'étoile, de l'agenda et de YouTube.
      const zone = b => {
        const s = getComputedStyle(b, '::before');
        return Math.min(parseFloat(s.width), parseFloat(s.height));
      };
      res.zones_de_toucher = Math.min(...[...document.querySelectorAll('#feed .carte-bouton')]
        .slice(0, 60).map(zone));
      res.encre_discrete = getComputedStyle(document.documentElement).getPropertyValue('--ink-mute').trim();
      // A-11 : des événements abîmés ne font pas tomber la page.
      const lus = evenementsLisibles([
        { date_start: '2026-10-5', title: 'date mal écrite' },
        { date_start: '2026-02-30', title: 'date impossible' },
        { date_start: '2026-10-05', date_end: 'demain', title: 'fin illisible' },
        { date_start: '2026-10-05', title: 42, venue: 7, time: 2030 },
        { date_start: '2026-10-05', title: 'Juste', venue: 'Le Sucre', time: '24:00' },
        null, 'texte']);
      res.evenements_lisibles = lus.map(e => [e.title, e.venue, e.time]);
      // A-39 : le fichier d'agenda.
      const SAUT = String.fromCharCode(13, 10);
      const longue = 'SUMMARY:' + 'Soirée électro 🎛️ à la Croix-Rousse, ébène & été '.repeat(5);
      const pliee = ligneAgenda(longue);
      res.pliage = [pliee.split(SAUT + ' ').join('') === longue,
                    pliee.split(SAUT).every(l => new TextEncoder().encode(l).length <= 75),
                    pliee.split(SAUT).length > 1];
      res.heures_agenda = ['00:00', '9:30', '23:59', '24:00', '25:10', '20:60'].map(h => HEURE_AGENDA.test(h));
      const lienAbime = lienPropre({ venue: 'Salle inconnue', url: 'https://exemple.org/soir' + String.fromCharCode(10) + 'ee' });
      res.lien_propre = [lienAbime.includes(String.fromCharCode(10)), lienAbime.startsWith('https://exemple.org/'),
                         lienPropre({ venue: 'Salle inconnue', url: 'javascript:alert(1)' })];
      res.decalages_a_l_instant = [['2026-03-29', '01:30'], ['2026-03-29', '03:30'], ['2026-10-25', '01:30'],
                                   ['2026-10-25', '04:00'], ['2026-07-01', '21:00'], ['2026-12-01', '21:00']]
        .map(([j, h]) => decalageDeParis(j, h));
      // A-40 : une date d'adresse impossible ; les favoris d'événements passés.
      res.dates_valides = ['2026-10-01', '2026-02-29', '2028-02-29', '2026-13-45', '2026-10-5', '', null]
        .map(dateValide);
      const present = state.events[0];
      const avant = new Set(state.favoris);
      state.favoris = new Set(['Ancienne salle|Spectacle fini|2026-07-01',
                               'Ancienne salle|Hier encore|2026-09-20',
                               cleEvenement(present)]);
      elaguerLesFavoris();
      res.favoris_elagues = [...state.favoris].map(c => c === cleEvenement(present) ? 'présent' : c);
      state.favoris = avant;
      ecrireMemoire('nocturne:favoris', [...avant]);
      // A-14 : la place du programme est réservée tant qu'il est vide.
      const feed = document.getElementById('feed');
      const garde = [...feed.childNodes];
      feed.replaceChildren();
      res.place_reservee = Math.round(feed.getBoundingClientRect().height) >= window.innerHeight;
      feed.replaceChildren(...garde);
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
        // Les vraies cartes, sous le plan, avec leurs boutons.
        c.fiche = [!fiche.hidden,
                   fiche.querySelectorAll('.event').length === Number(seul.textContent),
                   norme(fiche.querySelector('.fl-nom').textContent).startsWith(nomDuLieu),
                   seul.classList.contains('choisi'),
                   document.activeElement === fiche.querySelector('.fiche-lieu'),
                   [...fiche.querySelectorAll('.event-lien')].every(a => a.target === '_blank' && /^https?:/.test(a.href)),
                   !!fiche.querySelector('.event .carte-etoile'),
                   fiche.getBoundingClientRect().top >= document.querySelector('.carte-boite').getBoundingClientRect().bottom];
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
        const pendantMolette = [carte.z === zm, cadre.classList.contains('zoom-en-cours')];
        await pause(500);
        const [bx, by] = ecran();
        c.molette = [sansCtrl, carte.z - zm, Math.round(Math.hypot(bx - ax, by - ay))];
        c.molette_en_continu = [pendantMolette, cadre.classList.contains('zoom-en-cours')];
        document.querySelector('.carte-zoom [data-zoom="-1"]').click();
        await pause(200);
        // Deux doigts écartés du simple au double : le plan suit sans être
        // reconstruit (zoom inchangé, agrandi tel quel) ; au lever, il est
        // reconstruit un cran plus près, et le point qui était sous le
        // milieu des doigts au début est sous leur milieu à la fin.
        const rp = cadre.getBoundingClientRect();
        const Wp = cadre.clientWidth, Hp = cadre.clientHeight, zp = carte.z;
        const monde = [carte.cx + 350 - Wp / 2, carte.cy + 200 - Hp / 2];
        const touche = (type, id, x, y) => cadre.dispatchEvent(new PointerEvent(type,
          { pointerId: id, pointerType: 'touch', isPrimary: id === 21, clientX: rp.left + x, clientY: rp.top + y, bubbles: true }));
        const unePastille = document.querySelector('.carte-pin'), unNom = document.querySelector('.carte-nom:not([hidden])');
        const largeur0 = unePastille.getBoundingClientRect().width;
        touche('pointerdown', 21, 300, 200);
        touche('pointerdown', 22, 400, 200);
        touche('pointermove', 22, 500, 200);
        const pendantPince = [carte.z === zp, cadre.classList.contains('zoom-en-cours'),
                              /scale[(](1[.]99|2)/.test(carte.calque.style.transform),
                              Math.abs(unePastille.getBoundingClientRect().width - largeur0) <= 1,
                              getComputedStyle(unNom).opacity === '0'];
        touche('pointerup', 22, 500, 200);
        touche('pointerup', 21, 300, 200);
        await pause(100);
        c.pince = [pendantPince, [Math.round(carte.z - zp), Math.round(Wp / 2 + monde[0] * 2 - carte.cx),
                                  Math.round(Hp / 2 + monde[1] * 2 - carte.cy), cadre.classList.contains('zoom-en-cours')]];
        // Au zoom, l'ancien fond reste dessous le temps que les nouvelles
        // tuiles arrivent (ici, bloquées par le banc : très vite).
        document.querySelector('.carte-zoom [data-zoom="-1"]').click();
        const fondsJusteApres = document.querySelectorAll('.carte-tuiles').length;
        await pause(600);
        c.ancien_fond = [fondsJusteApres, document.querySelectorAll('.carte-tuiles').length];
        // Le fondu d'un bouton, pour qui ne demande pas moins d'animations :
        // le plan s'agrandit d'abord tel quel, puis est reconstruit.
        const vraiMatchMedia = window.matchMedia;
        window.matchMedia = q => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} });
        const zf = carte.z;
        document.querySelector('.carte-zoom [data-zoom="1"]').click();
        await pause(80);
        const pendantFondu = [carte.z === zf, cadre.classList.contains('zoom-en-cours')];
        await pause(500);
        window.matchMedia = vraiMatchMedia;
        c.fondu = [pendantFondu, carte.z - zf, cadre.classList.contains('zoom-en-cours')];
        document.querySelector('.carte-zoom [data-zoom="-1"]').click();
        await pause(200);
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
        // Le jour montré, plein ; aujourd'hui garde son fond ocre léger.
        c.couleurs_des_jours = [getComputedStyle(document.querySelector('.day-pill.choisi')).backgroundColor,
                                getComputedStyle(document.querySelector('.day-pill.today')).backgroundColor];
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
