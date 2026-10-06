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
  const SCENARIOS = window.__SCENARIOS || 'tous';

  const carte = el => {
    const c = el.cloneNode(true);
    c.querySelectorAll('.visual-bg').forEach(x => x.remove());
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
        [...s.querySelectorAll('.event')].map(carte),
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
    } else if (SCENARIOS === 'navigation') {
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
