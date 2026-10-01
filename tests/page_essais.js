/* Le pilote des tests de page, injecté à la fin de la page servie.

   Il attend le premier rendu, puis joue des SCÉNARIOS comme le ferait un
   visiteur — clics sur les puces, frappe dans la recherche, cases cochées —
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

  const toutAfficher = async () => {
    let avant = -1;
    for (let i = 0; i < 500; i++) {
      const n = document.querySelectorAll('#feed section.day').length;
      if (n === avant) break;
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
    }
    res.erreurs_fin = window.__erreurs;
    await envoyer(res);
  } catch (e) {
    await envoyer({ echec: String(e && e.stack || e), erreurs: window.__erreurs });
  }
})();
