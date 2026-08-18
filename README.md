# *nocturne · lyon

Agrégateur d'événements culturels lyonnais — concerts, clubs, danse, expos,
lieux hybrides — scrappés chaque nuit sur les sites d'une quinzaine de salles
et affichés sur une page statique hébergée par GitHub Pages :
<https://ricojrlyon.github.io/nocturne-lyon/>

## Fonctionnement

```
16 scrapers venue ──┐
                    ├─→ dédup 3 passes ─→ events.json ─→ index.html (GitHub Pages)
2 agrégateurs ──────┘         │
(Petit Bulletin,              ├─→ venue_arrondissements.json (géocodage Nominatim)
 Ville Morte)                 └─→ detail_times.json (cache des heures)
```

Le pipeline tourne quotidiennement à 06h00 UTC via GitHub Actions
([.github/workflows/update.yml](.github/workflows/update.yml)) et committe
les trois fichiers de données.

- **`scrapers/*.py`** — un module par salle (`requests` + BeautifulSoup).
  Chaque module expose `fetch() -> List[Event]`. Les échecs d'une salle ne
  font pas tomber le run : la salle est signalée en erreur, les autres passent.
  Exception notable : `improvidence.py` ne parse pas de HTML. Le site de la
  salle est rendu côté client, on lit donc sa billetterie Mapado, dont
  chaque page embarque son état d'hydratation Next.js en JSON — bien plus
  stable que des classes CSS générées.
- **`scrapers/aggregators/`** — sources multi-lieux : Petit Bulletin et
  Ville Morte (API Gancio). Priorité inférieure aux scrapers venue : en cas
  de doublon, le scraper de la salle gagne l'identité et hérite des champs
  manquants (heure, catégorie…).
- **`scrapers/dedup.py`** — canonicalisation des noms de lieux
  (`VENUE_CANONICAL`) + déduplication en 3 passes : (lieu, jour) avec
  fuzzy-match des titres ≥ 0,7 (les plages multi-jours sont indexées sur
  chaque jour couvert), cross-venue ≥ 0,85 (titres génériques exclus),
  puis pairing scraper/agrégateur à effectifs égaux avec garde temporel 4 h.
- **`scrapers/geo.py`** — géocodage Nominatim des lieux inconnus →
  arrondissement, mis en cache dans `venue_arrondissements.json`. Les lieux
  déjà hardcodés dans `VENUE_ARRONDISSEMENT` (index.html, source de vérité,
  parsée au run par aggregate.py) ne sont jamais interrogés.
- **`scrapers/detail_cache.py`** — cache persistant `url → heure` pour les
  6 scrapers qui fetchent des pages détail (TTL 30 j si heure trouvée,
  7 j sinon, purge à 60 j). Divise le temps de run par ~8 dès le 2ᵉ passage.
- **`aggregate.py`** — orchestre le tout, filtre le passé (les événements
  en cours sont conservés jusqu'à leur `date_end`), écrit `events.json`.
  Garde-fou : si toutes les sources échouent ou rendent 0 événement, le
  `events.json` précédent n'est pas écrasé.
- **`index.html`** — frontend vanilla JS autonome : filtres par date/lieu/
  arrondissement/type, recherche insensible aux accents (titre, lieu,
  line-up), expansion des événements multi-jours, groupes de lieux.

## Lancer localement

```bash
pip install -r requirements.txt   # requests + beautifulsoup4
pip install tzdata                # Windows uniquement (zoneinfo)

python aggregate.py               # run complet → events.json + caches
python -m scrapers.le_sucre       # tester un scraper isolément
```

Premier run : quelques minutes (remplissage du cache des heures).
Runs suivants : ~30 secondes.

## Ajouter une salle

1. Créer `scrapers/ma_salle.py` exposant `fetch() -> List[Event]`
   (s'inspirer de `heat.py` pour un listing simple, `transbordeur.py` pour
   une API WP REST paginée). Utiliser `detail_cache.get_time()` si les
   heures nécessitent des pages détail.
2. L'enregistrer dans `SCRAPERS` (aggregate.py).
3. Ajouter le lieu dans `VENUE_ARRONDISSEMENT` et `VENUE_GROUPS`
   (index.html) — la liste des lieux connus du géocodage en découle
   automatiquement.
4. Si les agrégateurs orthographient le lieu autrement, ajouter les
   variantes dans `VENUE_CANONICAL` (scrapers/dedup.py). C'est ce qui
   permet à la dédup de regrouper les deux sources : sans l'entrée, elles
   tombent dans deux groupes distincts et ne se croisent jamais.

## Politique éditoriale

- **Agrégateurs : aucun filtre.** Petit Bulletin et Ville Morte remontent
  l'intégralité de leur agenda — plus de blocage par catégorie, par lieu ni
  par tag. C'est la déduplication qui écarte les doublons quand un événement
  est aussi publié par la salle elle-même.
- **Événements longs** (expos, festivals au long cours) : conservés sous
  forme de plage `date_start`..`date_end` au lieu d'être jetés. Le frontend
  les affiche avec un badge « en cours » au-delà de 30 jours.
- **Horizon** : les événements à plus de 180 jours sont écartés avant la
  phase de fetch des pages détail (scrapers de salle uniquement).
- **Plages d'agrégateur sur un lieu scrappé** : écartées. Un agrégateur
  qui voit un spectacle joué plusieurs soirs le publie souvent comme une
  seule plage « du 18 au 28 août ». Le frontend déployant une plage sur
  chacun de ses jours, elle doublerait les séances que le scraper de la
  salle rapporte précisément, et en inventerait les soirs sans
  représentation. La dédup ne peut pas rattraper ce cas : il suffit que la
  plage gagne un seul jour pour être émise, puis repeindre toute sa durée.
- **Exclusions restantes** : formations et ateliers professionnels de
  La Rayonne, seul filtre encore en place — il vise une programmation
  parallèle, pas un choix éditorial.

## Données générées (committées par le bot)

| Fichier | Contenu |
|---|---|
| `events.json` | les événements agrégés, consommés par index.html |
| `venue_arrondissements.json` | cache géocodage lieu → arrondissement |
| `detail_times.json` | cache url → heure des pages détail |
