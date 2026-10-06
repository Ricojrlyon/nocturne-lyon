"""Les tests automatiques de nocturne.

Deux familles, qui se lancent depuis la racine du dépôt :

  python -m unittest discover -s tests -t . -v
      les tests de la LOGIQUE : dates, dédoublonnage, nouveaux essais
      réseau, garde-fous, et deux références figées - la chaîne de
      publication rejouée sur une collecte réelle, et la page rejouée sur
      des données et une date fixes. Sans réseau, en quelques dizaines de
      secondes. Le workflow les lance AVANT de collecter : un échec arrête
      le passage, et le site garde sa version de la veille.

  python -m unittest tests.verif_fil tests.verif_page -v
      les contrôles du fil FRAIS, lancés APRÈS la collecte et avant la
      publication : forme d'events.json, et la page qui s'affiche avec.

Les tests de page ont besoin d'un navigateur de la famille Chrome (Chrome,
Chromium, Edge). Sans lui, ils sont sautés et le disent.

Quand un changement est VOULU - une règle de dédoublonnage, le dessin
d'une carte -, les références se recalculent avec :

  python -m tests.regenerer
"""
