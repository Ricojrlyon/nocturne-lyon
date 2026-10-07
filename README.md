# *nocturne · lyon

Agrégateur d'événements culturels lyonnais (concerts, clubs, danse, expos,
sport, lieux hybrides), scrappés chaque matin sur les sites de trente-cinq
salles, musées et clubs, et affichés sur une page statique hébergée par
GitHub Pages :
<https://ricojrlyon.github.io/nocturne-lyon/>

## Fonctionnement

```
35 scrapers venue ──┐
                    ├─→ dédup 3 passes ─→ events.json ─→ index.html (GitHub Pages)
2 agrégateurs ──────┘         │
(Petit Bulletin,              ├─→ venue_arrondissements.json (géocodage Nominatim)
 Ville Morte)                 └─→ detail_times.json (cache des heures)
```

Le pipeline tourne chaque nuit à 2 h 17, heure de Paris, été comme hiver,
via GitHub Actions
([.github/workflows/update.yml](.github/workflows/update.yml)) et committe
les trois fichiers de données. GitHub ne compte qu'en heure UTC, qui ne
change pas d'heure : deux créneaux sont programmés, 00:17 et 01:17 UTC, et
un premier job ne laisse travailler que celui qui tombe à 2 h 17 à Paris
ce jour-là (le jour du passage à l'heure d'été, où 2 h 17 n'existe pas,
c'est 1 h 17). GitHub retarde ses passages programmés de plusieurs heures,
cinq à sept en octobre 2026 : partir la nuit vise un site à jour au
réveil, et la minute 17 évite l'heure pile, la plus demandée. Un seul passage
tourne à la fois : lancé à la main pendant le passage programmé, ou
l'inverse, le second attend la fin du premier, puis repart du fil que
celui-ci vient de publier. Deux passages simultanés échouaient à la
publication du second.

- **`scrapers/*.py`** : un module par salle (`requests` + BeautifulSoup).
  Chaque module expose `fetch() -> List[Event]`. Les échecs d'une salle ne
  font pas tomber le run : la salle est signalée en erreur, jusqu'en tête
  de la page du passage sur GitHub, et les autres passent.
  Chaque robot s'annonce aux sites sous son vrai nom, « nocturne-lyon-events »,
  avec le lien du projet. Deux exceptions : celui du LOU garde l'identité
  d'un navigateur, la billetterie du club étant protégée contre les robots,
  et celui du Complexe a sa propre formule (voir plus bas).
  Les appels réseau passent par `base.get` (exceptions motivées dans
  `scrapers/base.py`), qui réessaie sur une coupure, un délai dépassé ou un
  502/503/504, jamais sur une vraie réponse du site.
- **`scrapers/mapado.py`** : lecture commune des billetteries Mapado,
  utilisée par `improvidence.py`, `espace_gerson.py` et
  `chapelle_trinite.py`. Ces boutiques sont
  des Next.js dont chaque page embarque son état d'hydratation en JSON :
  on lit ce JSON, pas le HTML, les classes CSS de Mapado étant des
  hachages regénérés à chaque déploiement. Point d'attention : une
  boutique n'est PAS une salle : Improvidence programme aussi à Bordeaux,
  l'Espace Gerson à la Salle Victor Hugo et à la Bourse du Travail (que
  nocturne scrappe déjà). Chaque scraper fournit donc son prédicat de
  lieu, appliqué sur le Venue que Mapado expose en clair.
- **`scrapers/agendarts.py`** : agend'Arts, qui n'a pas de site à soi :
  la salle publie sur un blog WordPress.com, un billet par spectacle,
  servi sans clé par l'API publique. La date de publication n'est pas
  celle du spectacle (un billet de juin annonce un concert de décembre),
  et la vraie date s'écrit en toutes lettres dans la première phrase.
  Elle est lue par deux sources indépendantes : la prose, et les liens
  de billetterie HelloAsso dont le slug porte la date complète. Sur 325
  couples jour+mois, elles concordent 324 fois ; le désaccord restant
  est un cas que la prose seule ne peut pas voir. L'année, elle, n'est
  presque jamais écrite : quatre sources y répondent dans l'ordre, et à
  défaut la date est abandonnée plutôt que projetée sur l'année en
  cours. Deux règles évitent des dates fausses : seule la suite de
  quantièmes COLLÉE au nom du mois compte, sans quoi « Les 3 becs »
  produirait un 3 septembre ; et une suite introduite par une
  annulation est écartée, publier une séance annulée étant plus grave
  que d'en manquer une.
- **`scrapers/auditorium.py`** : l'Auditorium-Orchestre national de
  Lyon. Drupal, lu en deux temps : une page d'agenda par mois donne les
  cartes, la fiche donne les dates. La carte ne suffit pas : elle écrit
  « jeu. 1 oct », sans millésime ni horaire, là où la fiche écrit « Jeu.
  1 oct 2026 à 20h Ven. 2 oct 2026 à 18h ». L'heure change d'une séance à
  l'autre du même concert, 20h le jeudi et 18h le vendredi : publier la
  première pour les deux serait faux un soir sur deux. Trois pièges :
  le HTML sert les cartes en double (199 balises pour 148 spectacles) ;
  l'orchestre joue hors les murs jusqu'à Bruxelles, et la Salle Molière
  est déjà dans nocturne, d'où une liste BLANCHE de salles maison, les
  lieux extérieurs étant un ensemble ouvert quand les salles du bâtiment
  sont une liste fermée ; enfin deux genres sont écartés : les séances
  scolaires, facturées « 8 € par élève » et réservées aux classes, donc
  pas des sorties ; et les ateliers, publics ceux-là, mais qui pesaient
  104 des 186 événements du lieu, davantage que toute sa programmation
  de concerts, une même séance se répétant à 9h, 10h et 11h le même
  matin.
- **`scrapers/comedie_odeon.py`** : la Comédie Odéon. Le type
  « spectacle » n'est pas exposé à l'API REST, mais /spectacle/ porte
  TOUT en une requête : les cartes et un calendrier mensuel dont chaque
  cellule nomme les spectacles du jour. C'est le calendrier qui fait foi
  pour les dates : la fiche ne décrit qu'un rythme en français (« Du
  mercredi au samedi à 20h », « Relâches : 15/10 + 16/10 ») qu'il serait
  fragile de régénérer. Le gain est net : le Petit Bulletin publiait
  « La Machine de Turing » comme une plage de 53 jours, relâches
  comprises ; le scraper en rend les 29 vraies dates, dont celle à 19h
  au lieu de 20h. L'heure vient du résumé de la carte, à défaut de la
  fiche, qui donne aussi l'horaire d'un jour particulier : « Le 09/10 à
  19h », ou une date en toutes lettres, « Jeudi 11 mars 2027 à 21h » ;
  sans quoi « Jovany » se jouait ses deux soirs à l'heure du premier.
- **`scrapers/confluences.py`** : le Musée des Confluences, seul site
  du dépôt à exposer une JSON:API Drupal. Deux taxonomies y donnent
  gratuitement ce qu'il faut deviner ailleurs : `field_activites` porte
  le genre, `field_public` sépare le grand public des classes et des
  groupes. Le choix central est de NE PAS tout prendre : le musée
  publie 6 447 séances sur six mois (plus du double du site entier)
  parce qu'il exprime ses visites et ses ateliers récurrents en RRULE,
  une seule visite jouée quatre jours par semaine pendant cinq mois
  pesant quatre-vingt-dix séances. Le type `slot` est pire : 1 200
  créneaux pour une seule semaine, dont 583 à neuf heures, soit une
  grille de réservation de groupes. On retient donc conférences,
  cinéma, spectacles et concerts pour le grand public, plus les
  expositions temporaires, dont `field_state` dit « En cours », « À
  venir » ou « Passées », et dont la période est écrite en toutes
  lettres, là où les agrégateurs n'en donnaient qu'une approximation.
  Aucune des fiches retenues ne porte de récurrence encore vive ; si
  cela changeait, elle serait signalée plutôt que tronquée en silence à
  sa première date.
- **`scrapers/croix_rousse.py`** : le Théâtre de la Croix-Rousse.
  WordPress dont l'API REST est OUVERTE, à la différence du TNP : elle
  donne la liste de la saison avec titres, affiches et genres. Les dates
  n'y sont pas (l'ACF est vide partout) et viennent du HTML des
  fiches. Deux pièges : `title.rendered` est du HTML, pas du texte, et
  treize titres portaient des entités qui se seraient affichées telles
  quelles ; et la taxonomie nommée `genre` contient en réalité les noms
  d'ARTISTES, ce sont les termes `event_type` qui portent les genres.
  Certains n'y sont que des précisions (« heroic fantasy »,
  « bruitages », « radio »), qui cèdent la place au genre que le
  spectacle porte aussi, « théâtre » ou « cabaret » ; seule, une
  précision reste la catégorie.
  Un lien de billetterie par séance permet de distinguer « 14h30 19h30 »
  (deux séances) de « 17h > 17h50 », une seule avec son heure de fin.
- **`scrapers/iac.py`** : l'IAC de Villeurbanne. Site artisanal de 2013
  (jQuery 1.8.2), mais dont les dates sont balisées en microdonnées, ce
  qui sauve tout : la prose, elle, est un piège. La fiche du vernissage
  annonce « jeudi 17 septembre 2024 » alors qu'elle est classée en 2026,
  où le 17 septembre est bien un jeudi. On part donc de l'EXPOSITION, qui
  porte à la fois sa période et la liste de ses rendez-vous satellites,
  chacun avec son `<time datetime>`. Une requête donne l'exposition et
  toute sa programmation. L'attribut `itemprop` dit la forme de la date
  et il faut le lire : `startDate endDate` sur un seul `<time>` est une
  date unique, deux `<time>` distincts un intervalle, qui n'est PAS une
  série continue, « Visites en famille » allant du 11 octobre au 22
  novembre pour deux dimanches seulement. On garde tout sauf les visites,
  et parmi elles celles du week-end seulement, dont le rythme est dans le
  nom ; leurs relâches sont lues dans la fiche (« Pas de visite les
  dimanches 4 et 11 octobre »). Tout est in situ par construction : les
  expositions ex situ et les galeries nomades se tiennent ailleurs.
- **`scrapers/mac_lyon.py`** : le macLYON, musée d'art contemporain.
  Drupal sans JSON:API, mais dont la LISTE suffit : elle porte le titre,
  le sous-titre, la période, l'affiche et le type. Aucune fiche à ouvrir,
  et c'est heureux : la liste est plus complète qu'elles. Le concert de
  musique de chambre n'a aucun champ de date sur sa fiche, ni « Date » ni
  « Informations horaires » ; sa date n'existe que sur la liste. HORS LES
  MURS est le piège de ce musée : il répertorie sous son agenda des
  expositions qui se tiennent ailleurs, et son champ de type le dit en
  clair : « Hors les murs » y figure au même titre que « Exposition » ou
  « Concert ». Les deux entrées ainsi marquées sont *Jeune création
  internationale*, à l'IAC, et *Musée sentimental*, au Musée des
  Beaux-Arts : précisément les deux expositions que nocturne scrappe déjà
  chez leur véritable hôte. Sans ce filtre elles paraîtraient deux fois
  sous deux lieux, ce que la déduplication ne peut pas voir puisqu'elle
  groupe PAR lieu. Le champ « Lieu » des fiches dit la même chose, mais
  en prose, et l'un de ses libellés maison contient « Musée d'art
  contemporain », un mot que porte aussi l'Institut d'art contemporain :
  le marqueur de la liste est plus sûr parce qu'il est catégoriel.
- **`scrapers/maison_de_la_danse.py`** : la Maison de la Danse, seul
  site du dépôt à demander un `Crawl-delay` (10 s). Il est respecté, et
  c'est ce qui rend `detail_cache` indispensable : le délai est posé DANS
  le fetcher, donc il ne frappe que les fiches réellement téléchargées :
  216 s au premier passage, 11 s aux suivants. Trois pièges y ont coûté
  des spectacles entiers, tous silencieux : une série porte le titre de
  sa période, « NOVEMBRE - DÉCEMBRE », qui peut couvrir trois mois ou
  plus (« SEPTEMBRE - NOVEMBRE ») et se déplie mois par mois ; quand la
  série saute un mois, le nom du jour, confronté à l'année de la saison
  écrite dans l'adresse, désigne le bon ;
  un quantième qui se répète un mois plus tard (mercredi 28 octobre puis
  samedi 28 novembre) ne recule pas, seul le nom du jour distingue ce cas
  d'une double séance ; et une fiche sans bloc « Lieu » n'est pas un
  accueil extérieur mais un lieu non précisé : les accueils, eux, le
  renseignent toujours.
- **`scrapers/opera_lyon.py`** : l'Opéra national de Lyon. Le programme
  s'étale sur plusieurs pages (« En voir plus ») : le robot les suit, vingt
  au plus, jusqu'à la première qui n'apporte rien de neuf ; lue seule, la
  première page arrêtait l'Opéra à la mi-novembre. La catégorie vient de
  l'adresse de chaque production (« en famille » va au jeune public), et
  un titre qui commence par « Atelier » reste un atelier, même rangé parmi
  les concerts. Les visites guidées sont écartées. Un CYCLE, une plage de
  plusieurs jours dont la fiche n'annonce aucune représentation, l'est
  aussi : chacun de ses rendez-vous a sa propre fiche, et la plage aurait
  peint chacun de ses jours d'une carte où rien ne se joue. Hors les murs,
  l'Amphi et la Grande salle restent des salles de la maison ; une
  production jouée ailleurs est rangée sous la salle qui la reçoit, et
  celle qui se joue dans plusieurs salles est marquée « ailleurs ».
- **`scrapers/tnp.py`** : le TNP, seul scraper à s'être vu REFUSER une
  API qui existe : le site est un WordPress mais son robots.txt interdit
  /wp-json/. On lit donc /agenda/, qui a l'avantage de rendre la saison
  entière en une requête, avec des attributs `datetime` lisibles à la
  machine. Les affiches viennent des fiches spectacle : dix-sept fiches
  pour cent vingt-trois représentations, le même spectacle se jouant dix
  à dix-sept fois. La fiche montre l'affiche en treize tailles, et le
  robot prend celle de 800 px (l'`og:image`, l'original, pèse 1,3 à
  1,6 Mo). Un parcours « Hors les murs », dont le point de départ n'est
  donné qu'à l'inscription, est publié marqué « ailleurs » plutôt que
  placé à Villeurbanne.
- **`scrapers/tng.py`** : le TNG. Les cartes du programme n'écrivent que
  la plage d'un spectacle (« 02 > 06 oct. », le mois une seule fois),
  relâches et séances scolaires comprises. Le scraper ouvre donc chaque
  fiche et publie une date par séance TOUT PUBLIC : la liste scolaire,
  réservée aux classes, est écartée comme à l'Auditorium, et le nom du
  jour contrôle chaque date. Une séance à plus d'une semaine des dates de
  la carte est une erreur de saisie de la fiche et n'est pas retenue ;
  faute de séance, la carte fait foi. La fiche est relue à chaque
  passage, sans le cache des heures : une séance ajoutée ou retirée se
  voit dès le lendemain. Le site coupe certains extraits au milieu d'un
  caractère (« Pop� » pour « Pop’Corn ») : le losange est retiré du
  sous-titre.
- **`scrapers/beaux_arts.py`** : le Musée des Beaux-Arts. Drupal sans
  JSON:API, mais très régulier : une liste paginée qui porte le type de
  chaque rendez-vous, et une fiche où chaque séance occupe sa ligne,
  datée et horodatée. Le choix central est d'ÉCARTER LES VISITES : sur
  les 321 séances que le musée programme en six mois, 283 sont des
  visites guidées, et trois seulement commencent à 18h ou plus tard :
  c'est un programme de journée. Restent une trentaine de vraies
  sorties : nocturnes, conférences, « Le musée fait son cinéma »,
  cartes blanches de midi à des chorégraphes et des autrices,
  week-ends thématiques. Le filtre porte sur le type ET sur le titre :
  trois séries de visites sont rangées sous un type qui nomme le PUBLIC
  et non l'activité (« LSF Sourds malentendants », « DBDD Aveugles
  malvoyants »), et neuf visites passaient. Lire le type sur la liste
  permet de n'ouvrir qu'une trentaine de fiches au lieu de
  quatre-vingt-treize ; la liste ne donne en revanche qu'une date par
  rendez-vous, d'où la lecture des fiches retenues. Les EXPOSITIONS
  viennent d'ailleurs : le musée les tient hors de sa liste de
  rendez-vous, sur un article. Leur fiche existe et figure même dans la
  liste, mais sans aucune date de séance : son champ horaire dit
  « ouverte du mercredi au lundi de 10h à 18h », ce qui est un horaire
  et non une période. C'est pourquoi la première version publiait
  trente-sept rendez-vous et pas une exposition. L'affiche est celle du
  bandeau de la fiche : la première image de la page était la vignette
  d'un AUTRE rendez-vous, suggéré en bas de page.
- **`scrapers/celestins.py`** : Les Célestins, seule salle du dépôt à
  offrir une API JSON DOCUMENTÉE : le site tourne sous Roadiz, son
  robots.txt n'interdit que /api/docs, et /api/docs.json rend la
  spécification OpenAPI complète. Deux appels sont nécessaires,
  /api/event_dates pour les représentations et /api/events pour les
  affiches, que la première sérialisation n'embarque pas. Or c'est
  précisément l'image qui manquait, le Petit Bulletin remontant cette
  salle sans une seule. Point d'attention : les Célestins programment
  HORS LES MURS, 17 représentations sur 228 au TNP, au TNG et à la
  Croix-Rousse. Sans filtre de lieu elles seraient publiées sous
  « Célestins », et la dédup ne pourrait rien y faire puisqu'elle
  regroupe justement par lieu.
- **`scrapers/complexe.py`** : Le Complexe café-théâtre, seul scraper à
  exiger un User-Agent PARTICULIER : le pare-feu du site renvoie 403 à
  toute chaîne contenant « Mozilla/5.0 (compatible », donc l'UA y est
  franc, sans déguisement en navigateur. Ne pas l'aligner sur les autres.
  Autre particularité : les dates des séances n'y portent pas d'année,
  déduite de la plage du catalogue puis roulée quand le mois recule, et
  validée par le nom du jour de la semaine, qui écarte toute déduction
  fausse plutôt que de publier une date erronée. Enfin, son hébergeur
  (SiteGround) sert parfois aux machines de GitHub une vérification
  anti-robot à la place de la page : le scraper la reconnaît et renonce
  aussitôt en le disant, et le garde-fou reprend le programme de la
  veille. Il ne redemande plus : sur les refus d'octobre 2026, deux
  nouveaux essais dans le même passage n'en ont rattrapé aucun, et
  coûtaient trois minutes.
- **`scrapers/halle_tony_garnier.py`** : la Halle Tony Garnier. La
  catégorie suit le type que la salle donne à chaque date dans la classe
  de sa carte : concert, spectacle, salon, ciné-concert. Tout était
  « concert » : les humoristes et les spectacles sur glace manquaient à
  la scène, et la musique affichait le Salon des vignerons. Le type
  « évènement » ne dit rien du genre (ce sont les séances du Festival
  Lumière) et laisse la catégorie vide, à compléter.
- **`scrapers/marche_gare.py`** : le Marché Gare. La carte d'agenda
  range à part les mentions (« Gratuit », « Épuisé sur ce point de
  vente », « Hors les murs », « Annulé »), les genres, le titre et le
  sous-titre ; lue d'un bloc, elle donnait « Épuisé sur ce point de vente
  Post-Metal HYPNO5E + HIPPOTRAKTOR ». Le titre est celui de la carte ;
  les genres font la catégorie, tous ensemble : « Groove » seul se
  range dans la musique, « Groove / Jazz » dans le jazz ; les
  mentions utiles passent au sous-titre, et « Annulé » reste en tête du
  titre, où le filtre des annulés le lit. Les soirées « Hors les murs »
  et les formations professionnelles sont écartées, et les agrégateurs
  ne peuvent pas les republier sur ce lieu : le Petit Bulletin
  annonçait Ivanoé « au Marché Gare », quand la salle le donne à la MJC
  du Vieux-Lyon. L'agenda n'affiche d'abord qu'une partie du programme :
  le robot suit les pages « Afficher plus », dix au plus, et une page
  suivante qui échoue fait échouer la collecte, rattrapée alors en entier
  par le garde-fou plutôt que publiée à moitié.
- **`scrapers/radiant.py`** : le Radiant-Bellevue, à Caluire. La page
  d'accueil porte la saison entière, et un spectacle y paraît parfois
  deux fois : d'abord sans genre dans le bandeau « à la une », puis dans
  la liste, où genre et classement « Scolaires » sont lus. Les dates
  viennent de la carte, l'heure de la fiche, qui liste chaque séance avec
  la sienne : une heure pour toutes publiait les matinées du dimanche à
  l'heure du soir. Les séances du matin restent écartées, et un spectacle
  « Scolaires » (séances en semaine, tarif « pour les écoles ») n'est
  pas publié.
- **`scrapers/aggregators/`** : sources multi-lieux : Petit Bulletin et
  Ville Morte (API Gancio). Priorité inférieure aux scrapers venue : en cas
  de doublon, le scraper de la salle gagne l'identité et hérite des champs
  manquants (heure, catégorie…).
  Le Petit Bulletin écrit ses dates en toutes lettres, et leur lecture a
  ses règles, chacune payée d'une erreur publiée :
  - toutes les dates d'une annonce comptent (« Jeudi 1 octobre et
    Vendredi 2 octobre »), chacune à l'heure de son jour quand le texte
    en donne une (« jeudi à 20h, vendredi à 18h ») ;
  - le jour de la semaine fixe l'année ; sans lui, une date passée de
    moins de quinze jours reste dans l'année en cours : « jeudi 1
    octobre », lu le 2, devenait une soirée fantôme un an plus tard ;
  - une plage courte ne garde que les jours de jeu qu'elle nomme (« du
    mardi au vendredi à 19h30, samedi à 19h », « relâche le jeudi »),
    chacun à son heure ; devant une tournure inconnue, rien n'est ôté :
    mieux vaut une séance de trop qu'une vraie séance perdue ;
  - deux jours qui se suivent sous un horaire qui passe minuit (« de 22h
    à 4h30 ») ne font qu'une nuit ;
  - l'année du début d'une longue plage (« Du 16 octobre 2026 au 15 août
    2027 ») est lue elle aussi : sans elle, la plage devenait son seul
    premier jour.
- **`scrapers/dedup.py`** : canonicalisation des noms de lieux
  (`VENUE_CANONICAL`) + déduplication en 3 passes : (lieu, jour) avec
  fuzzy-match des titres ≥ 0,7 (les plages multi-jours sont indexées sur
  chaque jour couvert), cross-venue ≥ 0,85 (titres génériques exclus),
  puis pairing scraper/agrégateur à effectifs égaux avec garde temporel 4 h.
  Le lieu est celui où l'on va : un hors-les-murs se range sous sa vraie
  salle, et un titre cité vaut le titre long qui le cite (« Le Lac » pour
  « … "Le Lac" »), sans quoi le concert de l'Opéra au Théâtre de La
  Renaissance et l'annonce du Petit Bulletin restaient deux cartes. À
  priorité égale, la salle qui reçoit l'emporte sur celle qui annonce le
  spectacle hors les murs ; et une date « dans plusieurs salles », que
  l'Opéra publie sans dire où elle se joue, s'efface devant la salle qui
  publie la même représentation (même titre, même jour, même minute).
  Les deux premières passes portent un garde supplémentaire
  (`_seances_distinctes`) : au sein d'une MÊME source, deux horaires
  connus et différents sont deux représentations, jamais un doublon. Sans
  lui, une matinée et sa soirée fusionnaient : 44 couples de séances
  réelles perdus sur sept salles avant correction.
- **`scrapers/categorie.py`** : comble la catégorie quand la source n'en
  donne aucune, après la déduplication et sans jamais écraser une
  catégorie de source. Le TITRE d'abord, où ces salles annoncent le genre
  en clair (« Projection Ciné-Club », « [Punk Rock] », « comedy club »),
  puis un défaut de LIEU, réservé aux salles réellement mono-genre : à
  Marché Gare les événements sans catégorie comprenaient une projection et
  deux formations, au Bieristan des quiz. Ce qui ne se déduit pas reste
  vide, « LP » ou « Face B » ne disant rien. Attention : les étiquettes
  produites doivent être reconnues par `TYPE_BUCKETS` (index.html),
  sinon le comblement ne sert à rien. Deux pièges vérifiés : « ciné » ne
  correspond pas à sa propre regex et « électro » accentué non plus.
- **`scrapers/geo.py`** : géocodage Nominatim des lieux inconnus →
  arrondissement, mis en cache dans `venue_arrondissements.json`. Les lieux
  déjà hardcodés dans `VENUE_ARRONDISSEMENT` (index.html, source de vérité,
  parsée au run par aggregate.py) ne sont jamais interrogés.
- **`scrapers/detail_cache.py`** : cache persistant `url → heure` pour les
  12 modules qui ouvrent des pages détail (TTL 30 j si heure trouvée,
  7 j sinon, purge à 60 j). Divise le temps de run par ~8 dès le 2ᵉ passage.
  Le TNG n'y passe plus : ses fiches portent les séances, qui changent.
- **`aggregate.py`** : orchestre le tout, filtre le passé (les événements
  en cours sont conservés jusqu'à leur `date_end`), écarte les événements
  que leur source dit annulés (voir « Politique éditoriale »), écrit
  `events.json`. Quatre garde-fous, du plus local au plus large :
  - une salle scrappée en direct qui tombe sous le quart de la veille
    garde ses événements de la veille, sept jours au plus (journal
    `reprises` dans `events.json`). Une petite salle, sous dix
    événements, n'est reprise que si son collecteur échoue (erreur,
    page introuvable) ou ne rend plus rien alors qu'elle annonçait la
    veille des dates encore à venir ; pas quand elle a simplement moins
    de dates. Pour
    savoir quels lieux reprendre, le fil garde ceux que chaque collecteur
    a rendus (`lieux_des_collecteurs`) : le handball, par exemple, joue
    dans deux gymnases. Une salle reprise écarte aussi les plages
    d'agrégateur qu'elle porte, comme un jour où elle répond. Ce que la
    source publie encore ce jour-là n'est pas repris : une exposition
    déjà ouverte commence, pour sa source, chaque jour « aujourd'hui », et
    sa copie de la veille s'affichait sinon en double ;
  - de même pour le Petit Bulletin et Ville Morte, avec la même règle ;
  - un fil qui tombe sous les trois quarts de ce que la veille comptait
    encore à venir n'est pas publié ; passé trois jours sans publication,
    il l'est quand même, avec une alerte ;
  - si toutes les sources échouent, le `events.json` précédent n'est pas
    écrasé.

  `NOCTURNE_FORCER_ECRITURE=1` publie ce qui a été réellement scrappé,
  pour une perte réelle : salle fermée, saison finie. Sur GitHub, c'est la
  case « Forcer la publication » d'un lancement à la main (Actions →
  Update events daily → Run workflow). Elle coupe tous les garde-fous du
  passage : une salle en panne passagère ce jour-là ne serait pas reprise
  non plus.
- **`index.html`** : frontend vanilla JS autonome : filtres par date/lieu/
  arrondissement/type, recherche insensible aux accents (titre, lieu,
  line-up), expansion des événements multi-jours, groupes de lieux.
  Pour les lecteurs d'écran, le champ de recherche porte un nom, les
  boutons de filtre disent s'ils sont enfoncés, et chaque journée est un
  titre de niveau 2.
  Sa politique de sécurité (`Content-Security-Policy`, en tête de page)
  ne lui permet de charger que ses propres fichiers (scripts, styles,
  polices, fil, logos) et des images en `https` (les affiches des
  salles) ; elle ne peut rien envoyer ailleurs que vers le site, ni
  charger un script d'un autre site, ni contenir formulaire, objet ou
  cadre. Limite : le code et les styles étant écrits dans la page, la
  règle doit tolérer ce qui y est écrit (« unsafe-inline ») ;
  l'échappement des textes venus des sites reste la première protection.
- **`404.html`** : la page des adresses introuvables, que GitHub Pages sert
  d'elle-même : « erreur 404 », « page introuvable » et un lien vers la page
  d'accueil. Son lien et ses polices sont en adresses complètes
  (`/nocturne-lyon/…`), car elle s'affiche à n'importe quelle profondeur
  d'adresse ; elle est exclue des moteurs de recherche.
- **`.nojekyll`** : fichier vide qui demande à GitHub Pages de publier les
  fichiers tels quels, sans les passer par Jekyll, son moteur de modèles.
  Jekyll ne modifiait rien aujourd'hui, mais il aurait pu un jour
  interpréter un fichier, et il écartait ceux dont le nom commence par
  « _ » ou par un point ; la publication est aussi plus rapide (35 s au
  lieu de 50 environ). Ces fichiers-là (`.gitignore`,
  `scrapers/__init__.py`) sont désormais consultables sur le site, comme
  ils l'étaient déjà sur GitHub, le dépôt étant public ; le dossier
  `.github` reste exclu par GitHub. Vérifié à la mise en place : les
  autres fichiers servis sont identiques à l'octet près.
- **`logos/`** : marques de salle affichées sur les cartes groupées, une
  par lieu, déclarées dans `VENUE_LOGOS` (index.html). Voir « Logos de
  salle » plus bas.

## Lancer localement

```bash
pip install -r requirements.txt   # requests + beautifulsoup4
pip install tzdata                # Windows uniquement (zoneinfo)

python aggregate.py               # run complet → events.json + caches
python -m scrapers.le_sucre       # tester un scraper isolément
```

Premier run : nettement plus long, le temps de remplir le cache des heures.
Runs suivants : 7 à 8 minutes ; plusieurs salles imposent un délai entre deux
pages (mesuré sur GitHub en octobre 2026).

## Tests

```bash
python -m unittest discover -s tests -t . -v      # la logique, sans réseau
python -m unittest tests.verif_fil tests.verif_page -v   # le fil du jour
```

- **La logique**, en une minute et demie environ et sans réseau : la
  lecture des dates en français, le dédoublonnage règle par règle, les
  nouveaux essais réseau, la vérification anti-robot du Complexe, les
  annulés, l'heure de chaque séance, les catégories, les garde-fous
  d'`aggregate.py`, ce que la page et les collecteurs doivent avoir en
  commun (l'horizon, le marqueur « ailleurs », la table des lieux que le
  robot relit dans la page), ce que la politique de sécurité de la page
  interdit, et deux RÉFÉRENCES figées.
  La chaîne de publication entière est rejouée sur une collecte réelle
  (celle du 1er octobre 2026, dans `tests/donnees/`) et doit rendre le
  même fil au caractère près ; la page est jouée dans un navigateur sans
  fenêtre, sur ces données et à cette date, et ses neuf scénarios de
  visiteur doivent afficher les mêmes cartes, sans erreur et sans rien
  que sa politique de sécurité bloque.
- **Le fil du jour**, après la collecte : la forme d'`events.json`, et la
  page qui s'affiche avec, sans erreur et avec les bons compteurs.

Le workflow lance les premiers AVANT de collecter et les seconds AVANT de
publier : un échec arrête le passage, qui passe au rouge, et le site garde
sa version de la veille plutôt que d'en publier une cassée.

Les tests de page demandent un navigateur de la famille Chrome (Chrome,
Chromium ou Edge) ; sans lui, ils sont sautés et le disent. Ils le sont
aussi, avec une alerte, quand le navigateur ne rend rien : c'est alors la
machine qui fait défaut, pas le site, et cela ne doit pas bloquer la
publication.

Quand un changement est VOULU (une règle de dédoublonnage, le dessin
d'une carte), les références se recalculent avec
`python -m tests.regenerer` (ou `chaine`, ou `page`) ; relire ensuite le
diff de `tests/donnees/`.

## Ajouter une salle

1. Créer `scrapers/ma_salle.py` exposant `fetch() -> List[Event]`
   (s'inspirer de `heat.py` pour un listing simple, `transbordeur.py` pour
   une API WP REST paginée). Utiliser `detail_cache.get_time()` si les
   heures nécessitent des pages détail.
2. L'enregistrer dans `SCRAPERS` (aggregate.py).
3. Ajouter le lieu dans `VENUE_ARRONDISSEMENT` et `VENUE_GROUPS`
   (index.html) : la liste des lieux connus du géocodage en découle
   automatiquement.
4. Si les agrégateurs orthographient le lieu autrement, ajouter les
   variantes dans `VENUE_CANONICAL` (scrapers/dedup.py). C'est ce qui
   permet à la dédup de regrouper les deux sources : sans l'entrée, elles
   tombent dans deux groupes distincts et ne se croisent jamais.
5. Facultatif : si la salle joue plusieurs fois par soir, lui donner un
   logo (voir « Logos de salle »). Sans entrée, ses cartes groupées
   gardent le motif, comme la majorité des lieux.

## Logos de salle

Une carte groupée réunit plusieurs spectacles d'un même lieu le même
jour. N'ayant pas d'affiche à montrer, elle tirait un motif de secours.
Les salles qui jouent plusieurs fois par soir y portent désormais leur
marque : dix-sept lieux, 653 des 837 cartes groupées au 1er octobre 2026.

Le logo est traité comme une affiche (`brightness(0.50) contrast(1.06)`,
trame sérigraphie et voile du haut par-dessus), à deux détails près.

- **Un cartouche blanc sur toute la carte.** Les logos ajourés posaient
  sinon leurs traits sombres sur un fond sombre ; le cartouche leur rend
  le support pour lequel ils ont été dessinés. C'est lui qui porte le
  filtre, pas la marque : un filtre s'appliquant à tout son sous-arbre,
  son blanc et celui qu'un fichier contient déjà subissent le même
  traitement, sans quoi un rectangle se dessinerait autour de la marque.
- **`brightness` à 0,50** et non 0,60 comme les affiches : un aplat n'a
  pas le bruit d'une photographie et ressort plus fort à luminosité
  égale.

Deux règles à respecter en ajoutant un logo :

1. **Recadrer le fichier sur la boîte de son dessin.** Les fichiers
   d'origine portent des marges vides très inégales (67 % pour
   Improvidence, 55 % pour Le Complexe, 0 % pour Gerson), et sans
   recadrage une même valeur de hauteur donne des dessins de tailles
   très différentes.
2. **Choisir `k` d'après le ratio**, `k` étant la hauteur en pour cent de
   la carte. 150 % convient aux marques compactes (ratios 0,98 à 1,37).
   Les Subsistances sont à 102 % : leur lettrage est un ruban de ratio
   2,12 qui sortirait largement de la carte à 150 %.

Le fichier est committé dans le dépôt plutôt que lié chez la salle : un
lien direct casse au premier changement de thème et ferait dépendre nos
cartes d'un serveur tiers. SVG quand la salle en publie un (l'Institut
Lumière et les Célestins, 1 à 25 ko et nets à toute échelle), PNG
recadré sinon.

Le motif reste sous le logo, éteint par une classe que l'`onerror`
retire : si le fichier manque, la carte retombe d'elle-même sur le
motif.

## Politique éditoriale

- **Événements annulés : écartés, quelle que soit la source.** Le mot
  doit être placé là où une source l'écrit pour annuler : en tête du
  titre (« Annulé … », « ANNULE // … », « (Annulé) … »), seul entre
  parenthèses ou crochets, en fin de titre après un tiret, ou comme titre
  ou sous-titre entier (« Concert annulé »). Ailleurs, il ne suffit pas,
  et un titre qui annonce un remplacement est gardé : « Almond Butyl -
  annulé / remplacé par Viviane Cavale » était une soirée qui a bien eu
  lieu. « Reporté » n'est pas traité, la date affichée pouvant être la
  nouvelle. Le filtre passe APRÈS la déduplication : quand la salle écrit
  « Annulé » et qu'un agrégateur republie le même spectacle sans le dire,
  les deux disparaissent. Chaque événement écarté est nommé dans le
  journal du passage.
- **Ville Morte : aucun filtre propre**, tout son agenda remonte, hors
  annulés. C'est la déduplication qui écarte les doublons quand un
  événement est aussi publié par la salle elle-même.
- **Petit Bulletin : un seul filtre propre**, quatre catégories d'arts
  plastiques : Peinture & Dessin, Art contemporain et numérique,
  Photographie, Design & Architecture (`CATEGORIES_ECARTEES`). La
  décision a changé deux fois, et pour des raisons différentes. Deux
  filtres existaient à l'origine (musées et galeries d'un côté, une
  liste de catégories de l'autre) parce que les accrochages saturaient
  le feed ; ils ont été levés en août 2026, au motif que le lecteur peut
  éteindre la famille « expos » d'un bouton. Le filtre de catégories
  revient en septembre parce que les événements longs sont désormais
  déployés sur chacun de leurs jours : un accrochage de trois mois pesait
  une carte, il en pèse quatre-vingt-dix. Mesuré à la réintroduction :
  100 événements écartés, dont 68 en galerie et 9 en musée non scrappé,
  soit 1 443 jours cumulés d'accrochage sur l'horizon.
- **Affiches : la taille réduite que publie la salle.** Une carte fait
  400 px de large au plus, et l'original d'une affiche pèse parfois
  plusieurs mégaoctets. Chaque robot prend donc, parmi les tailles que le
  site publie lui-même, celle qui s'approche le plus de 800 px, assez pour
  les écrans denses : au Transbordeur, à la Comédie Odéon et au TNP dans
  les tailles que la page liste, aux Subsistances quand la carte montre
  l'original, aux Célestins par la transformation d'image du site
  (600 px). Deux sites fabriquent une version légère sans la citer : la
  copie WebP de 768 px de la Croix-Rousse (une centaine de Ko au lieu de
  2 à 2,7 Mo) et la version « carte » de 800 × 500 de l'agenda des
  Confluences. Le robot la vérifie avant de la prendre. Faute de version
  réduite, l'original reste.
- **Familles d'affichage** (`FAMILLES`, index.html) : musique, scène,
  expos, sport, autres. Cinq et non dix-neuf : les buckets restent la
  maille fine, mais autant de sections dans une journée seraient
  illisibles.
  Chaque barre de journée porte un bouton par famille : l'état est
  GLOBAL, le compte est celui du JOUR. Au-delà de dix cartes la journée
  se découpe en sections titrées par famille (`SEUIL_SECTIONS`) ; en
  deçà elle reste une grille continue, la journée médiane ne faisant que
  cinq cartes. Tout bucket non rangé dans une
  famille tombe dans « autres », et une alerte console le signale : le
  repli évite de perdre un événement, il ne doit pas masquer un oubli.
- **Événements longs** (expos, festivals au long cours) : conservés sous
  forme de plage `date_start`..`date_end`, et affichés sur CHACUN de leurs
  jours. Un seuil `LONG_RUN_THRESHOLD` repliait les séries de plus de
  trente jours en une carte unique datée d'aujourd'hui : une exposition
  de cinq mois n'apparaissait qu'une fois puis disparaissait du site. Il
  est supprimé (septembre 2026). La carte porte alors sa fin plutôt que
  son rang (« jusqu'au 30/10 », et « dernier » le dernier jour), car
  « 12/29 » ne renseigne pas le lecteur. L'année s'ajoute quand elle
  diffère de celle de la carte : un agrégateur annonce des accrochages à
  deux ans, et « jusqu'au 22/10 » lu en 2026 se comprendrait mal pour une
  fermeture en 2028. Les cartes groupées portent la même échéance dans
  leur colonne de droite, là où l'heure manque.
- **Horizon d'affichage** (`HORIZON_JOURS`, index.html) : 180 jours, la
  valeur des scrapers. Il n'en existait pas, faute d'utilité tant qu'une
  longue plage ne pesait qu'une carte ; sans lui, une exposition annoncée
  jusqu'en octobre 2028 fabriquerait six cent soixante-quinze journées.
  Ce qui commence au-delà garde une carte à sa date d'ouverture, pour ne
  pas disparaître.
- **Horizon** (`HORIZON_JOURS`, scrapers/base.py) : une seule valeur,
  180 jours, pour tous les collecteurs de salle ; ce qui commence au-delà
  est écarté avant la lecture des pages détail. La page a la même
  (`HORIZON_JOURS`, index.html), et un test vérifie qu'elles restent
  égales.
- **Plages d'agrégateur sur un lieu scrappé** : écartées. Un agrégateur
  qui voit un spectacle joué plusieurs soirs le publie souvent comme une
  seule plage « du 18 au 28 août ». Le frontend déployant une plage sur
  chacun de ses jours, elle doublerait les séances que le scraper de la
  salle rapporte précisément, et en inventerait les soirs sans
  représentation. La dédup ne peut pas rattraper ce cas : il suffit que la
  plage gagne un seul jour pour être émise, puis repeindre toute sa durée.
  La règle vaut aussi un soir où la salle ne répond pas et où ses séances
  sont reprises de la veille : elle ne reconnaissait la salle qu'à ses
  événements du jour, et la plage « Un grand cri d'amour » du Petit
  Bulletin, du 2 octobre au 28 décembre, s'affichait alors tous les soirs
  pour un spectacle du lundi.
- **La Rayonne et le Marché Gare** : leurs formations et ateliers
  professionnels sont écartés : c'est une programmation parallèle, pas
  un choix éditorial.
- **Séances scolaires** : écartées (elles sont réservées aux classes) à
  l'Auditorium, au TNG, et au Radiant pour les spectacles qu'il classe
  « Scolaires ».
- **Ce qu'une salle écarte vaut sur son lieu pour les agrégateurs**
  (`FILTRES_DE_SALLE`, aggregate.py) : les visites de l'IAC, les soirées
  hors les murs et les formations du Marché Gare. Sans cela, le Petit
  Bulletin republiait ce que la salle venait d'écarter : neuf visites de
  l'IAC, et Ivanoé « au Marché Gare ».

## Données générées (committées par le bot)

| Fichier | Contenu |
|---|---|
| `events.json` | les événements agrégés, consommés par index.html |
| `venue_arrondissements.json` | cache géocodage lieu → arrondissement |
| `detail_times.json` | cache url → heure des pages détail |
