# *nocturne · lyon

Le programme culturel de Lyon (concerts, clubs, théâtre, danse, expos,
sport), relevé chaque nuit sur les sites de 35 salles, musées et clubs et
de deux agendas, et affiché sur une page statique hébergée par GitHub
Pages : <https://ricojrlyon.github.io/nocturne-lyon/>

## Fonctionnement

```
35 collecteurs de salle ──┐
                          ├─→ dédoublonnage ─→ events.json ─→ index.html
2 agrégateurs ────────────┘        │
(Petit Bulletin, Ville Morte)      ├─→ venue_arrondissements.json (géocodage)
                                   └─→ detail_times.json (cache des fiches)
```

Chaque nuit, GitHub Actions ([update.yml](.github/workflows/update.yml))
lance `aggregate.py`, qui :

1. interroge chaque source (un module par salle dans `scrapers/`) ;
2. écarte ce qui est passé, ce qui commence au-delà de 180 jours, les
   événements annulés et ceux qui se jouent hors de Lyon, Villeurbanne et
   cinq communes voisines (Caluire-et-Cuire, Oullins-Pierre-Bénite,
   Vaulx-en-Velin, Décines-Charpieu, La Mulatière) ;
3. fusionne les doublons entre sources (`scrapers/dedup.py`) : la salle
   l'emporte sur les agrégateurs ;
4. comble les catégories manquantes, situe les nouveaux lieux
   (Nominatim) ;
5. écrit `events.json`, que la page lit.

**Garde-fous.** Une salle qui tombe sous le quart de ce qu'elle annonçait
la veille garde son programme de la veille, sept jours au plus ; un fil
qui perd plus d'un quart de ses événements n'est pas publié (trois jours
au plus) ; si tout échoue, le fichier précédent reste en place. Pour une
perte réelle (salle fermée), la case « Forcer la publication » d'un
lancement à la main publie ce qui a été relevé.

**Alertes.** Une salle en panne depuis plus d'une nuit ouvre une issue
GitHub (`alertes.py`), qui envoie un e-mail et se ferme quand la salle
revient.

**Horaires.** Trois passages sont programmés (00:17, 01:17 et 09:17 UTC) :
le premier arrivé travaille, les suivants s'arrêtent si le programme du
jour est déjà publié (`passage.py`). Le site est en général à jour entre
6 h 30 et 9 h 40. Les tests tournent avant la collecte, le fil est
contrôlé avant d'être publié, et la publication fait trois essais. Les
tests tournent aussi à chaque envoi de code
([tests.yml](.github/workflows/tests.yml)).

## Fichiers

| Fichier | Rôle |
|---|---|
| `aggregate.py` | le passage : collecte, filtres, garde-fous, écriture du fil |
| `scrapers/*.py` | un collecteur par salle ; `base.py` pour le commun |
| `scrapers/aggregators/` | Petit Bulletin et Ville Morte |
| `scrapers/dedup.py`, `categorie.py` | doublons, catégories |
| `scrapers/geo.py`, `detail_cache.py` | géocodage, cache des fiches |
| `alertes.py`, `passage.py` | alertes des salles en panne ; créneau et publication du passage |
| `index.html` | la page, autonome (HTML, CSS et JavaScript sans dépendance) |
| `lieux.json` | où est chaque lieu (arrondissement, commune, position) |
| `sw.js`, `manifest.webmanifest` | l'application installable, hors ligne |
| `mentions-legales.html`, `404.html` | pages annexes |
| `fonts/`, `logos/` | polices (licence SIL OFL, textes joints) et logos de salle |
| `tests/` | les tests, et les pages enregistrées des salles (`tests/pages/`) |

Données écrites par le robot, jamais à la main : `events.json`,
`venue_arrondissements.json`, `detail_times.json`.

## La page

`index.html` se suffit à lui-même : pas de framework ni d'étape de build.
On y trouve :
- les filtres « tout », « ce weekend », « semaine pro », une date, les
  favoris, la recherche, l'arrondissement, les lieux et les tags ;
- les familles (musique, scène, expos, sport, autres) dans la barre de
  chaque journée : toucher une famille ne montre plus qu'elle, en toucher
  une seconde l'ajoute ;
- une vue carte (fond OpenStreetMap) ;
- sur chaque carte : l'étoile des favoris (gardés sur le téléphone),
  l'ajout à l'agenda et l'écoute sur YouTube ;
- des liens partageables qui rouvrent la même vue.

Le style « signal » : vert acide, rose et bleu sur fond sombre quadrillé,
deux rubans sous le titre, un bandeau au-dessus du programme dont le
chiffre suit le réglage choisi. Il tient dans un bloc à la fin de la
feuille de style.

L'application s'installe depuis le navigateur et s'ouvre sans réseau
(`sw.js`). Modifier `sw.js` ou un fichier qu'il garde (polices, icônes,
manifeste) demande de changer son nom `CACHE` ; un test l'exige et donne
la ligne à ajouter.

## Respect des sites lus

- Le robot s'annonce sous son vrai nom (`base.UA`), avec l'adresse du
  projet. Seule exception : la billetterie du LOU, protégée contre les
  robots, reçoit une identité de navigateur (`scrapers/rugby.py`).
- robots.txt est respecté, et le délai demandé aussi (Maison de la Danse
  et La Rayonne : dix secondes).
- Une fiche déjà lue est gardée en cache.
- Nominatim est interrogé une fois par seconde au plus.
- Quand l'hébergeur du Complexe demande une vérification anti-robot, le
  robot n'insiste pas : la salle est reprise de la veille.

## Lancer localement

```bash
pip install -r requirements.txt   # versions figées, avec leur empreinte
pip install tzdata                # sous Windows seulement
python aggregate.py               # un passage complet
python -m scrapers.le_sucre       # une salle seule
```

## Tests

```bash
python -m unittest discover -s tests -t . -v           # la logique, sans réseau
python -m unittest tests.verif_fil tests.verif_page -v # le fil du jour
```

- La logique : dates, dédoublonnage, garde-fous, pannes, catégories,
  périmètre, et deux références figées (la chaîne de publication rejouée
  sur une collecte réelle, et la page jouée dans un navigateur de la
  famille Chrome).
- Les pages enregistrées : `python -m tests.enregistrer <salle>`
  enregistre une fois les pages d'une salle (robots.txt respecté), et le
  test les rejoue sans réseau.
- Après un changement voulu d'affichage ou de lecture :
  `python -m tests.regenerer` pour les références, ou réenregistrer la
  salle ; puis relire le diff.

Sur GitHub, un navigateur absent fait échouer les tests de page ; sur un
poste, ils sont sautés et le disent.

## Ajouter une salle

1. Créer `scrapers/ma_salle.py` avec `fetch() -> List[Event]`, en lisant
   le robots.txt du site.
2. L'ajouter à `SCRAPERS` dans `aggregate.py`.
3. Ajouter le lieu à `lieux.json` (arrondissement, commune hors Lyon et
   Villeurbanne, position), et à `VENUE_GROUPS` dans `index.html`.
4. Si les agendas l'écrivent autrement, ajouter les variantes à
   `VENUE_CANONICAL` (`scrapers/dedup.py`).
5. Enregistrer ses pages : `python -m tests.enregistrer ma_salle`.
