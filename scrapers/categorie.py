"""Comble la catégorie d'un événement quand la source n'en donne aucune.

108 événements sur 1628 arrivaient sans catégorie, et tous depuis des
scrapers de SALLE - les agrégateurs, eux, en fournissent toujours une.
Sans catégorie, un événement tombe dans le bucket « autre » du frontend :
il échappe au filtre par type et, dès qu'on regroupera les journées
chargées par famille, il irait grossir un tas indistinct.

Deux étages, dans cet ordre :

1. LE TITRE. C'est de loin la meilleure source, parce que ces salles
   annoncent le genre dans le titre lui-même - « Projection Ciné-Club »,
   « Formation Les métiers des musiques actuelles », « HOTEL PARASITE
   [Punk Rock] », « Blaguistan comedy club », « Vernissage ». 56 des 108
   se règlent ainsi.

2. LE LIEU, et seulement pour les salles qui ne programment qu'une
   chose. Un défaut par lieu appliqué largement serait faux : à Marché
   Gare les événements sans catégorie comprennent une projection et deux
   formations, au Bieristan des quiz et un karaoké, à HEAT un bar
   d'échecs. C'est pourquoi le titre passe D'ABORD, et pourquoi cette
   table reste courte.

Ce qui ne se laisse pas déduire reste vide, et c'est voulu : « LP »,
« SAINT LEVANT », « Face B » ou « Ouverture de saison » ne disent rien:
leur inventer une catégorie serait deviner, pas déduire.

ATTENTION - COUPLAGE AVEC LE FRONTEND. Les étiquettes produites ici
doivent être reconnues par TYPE_BUCKETS (index.html), sinon le
comblement ne sert à rien : l'événement quitte « sans catégorie » pour
retomber dans « autre ». Deux pièges vérifiés à l'écriture : « ciné » ne
correspond PAS à sa propre regex, qui exige « cinéma » ou « projection »
- d'où l'étiquette « projection » ; et « électro » accentué ne
correspond pas à `\belectro` - d'où l'étiquette « club ». Toute
étiquette ajoutée ici doit être testée contre TYPE_BUCKETS.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# Ordre = priorité, première correspondance gagnante. « chanson » passe
# avant « rock » pour que « Chanson Pop DINAA » ne parte pas en rock sur
# son \bpop\b ; « humour » avant « théâtre » pour que « café-théâtre »
# n'aille pas en théâtre.
INDICES_TITRE = [
    ("atelier",    r"\bformation\b|\batelier\b|\bworkshop\b|masterclass"),
    ("projection", r"\bprojection\b|cin[ée]-?club|court[s]?[\s-]m[ée]trage"
                   r"|r[ée]trospective"),
    ("expo",       r"\bvernissage\b|\bexposition\b|\bbiennale\b|\bvisite[s]?\b"
                   r"|\baccrochage\b"),
    ("humour",     r"comedy\s*club|\bstand[\s-]?up\b|one[\s-]?man|\bhumour\b"
                   r"|blagu|caf[ée][\s-]*th[ée][aâ]tre"),
    ("conférence", r"conf[ée]rence|table\s*ronde|\bd[ée]bat\b|\bcolloque\b"),
    ("théâtre",    r"\bth[ée][aâ]tre\b|\bcabaret\b"),
    ("danse",      r"\bdanse\b|\bballet\b|cho[ré]graph"),
    # « jeux » n'a volontairement pas de bucket dans le frontend : quiz,
    # blind test et karaoké ne sont ni musique ni scène, ils appartiennent
    # au reste. L'étiquette existe déjà dans les données du Petit Bulletin.
    ("jeux",       r"\bquiz+\b|blind\s*test|\bkaraok[ée]\b|\bbingo\b|\bloto\b"
                   r"|\b[ée]checs?\b|\bchess\b|loup\s*garou"),
    ("chanson",    r"\bchanson\b|\bfolk\b|\bslam\b|\bpo[ée]sie\b"),
    ("rock",       r"\bpunk\b|\brock\b|\bpop\b|\bgarage\b|\bgrunge\b|\bkraut\b"
                   r"|\bm[ée]tal\b|hardcore|noise"),
    ("rap",        r"\br\.?a\.?p\.?\b|hip[\s-]?hop|\btrap\b"),
    ("club",       r"\btechno\b|\belectro|\bhouse\b|\bdj\b|\bdub\b|\bclub\b"),
    ("jazz",       r"\bjazz\b|\bblues\b|\bjam\b|\bswing\b"),
    ("musique",    r"\bconcert\b|\bmusique[s]?\b|\bfanfare\b|\bchorale\b"
                   r"|open\s*mic|\blive\b|release\s*(?:show|party)"),
]
INDICES_TITRE = [(c, re.compile(p, re.I)) for c, p in INDICES_TITRE]

# Repli. N'ajouter ici qu'un lieu dont TOUTE la programmation relève d'un
# même genre - sinon on étiquette faux au lieu de laisser vide.
LIEUX_MONOGENRE = {
    "Grrrnd Zero":      "musique",   # salle de concert associative
    "Trokson":          "musique",   # bar-concert rock
    "Toï Toï le Zinc":  "musique",   # salle de concert
    "Marché Gare":      "musique",   # scène de musiques actuelles
    "IAC Villeurbanne": "expo",      # centre d'art, expositions seules
}


def deduire(titre: Optional[str], lieu: Optional[str]) -> Optional[str]:
    """Catégorie déduite, ou None si rien de sûr ne s'en dégage."""
    for cat, rx in INDICES_TITRE:
        if rx.search(titre or ""):
            return cat
    return LIEUX_MONOGENRE.get(lieu or "")


def combler(events: Iterable) -> tuple:
    """Renseigne la catégorie manquante, sur place.

    Ne touche JAMAIS une catégorie fournie par la source : celle-ci fait
    foi, même quand elle est moins précise que ce qu'on déduirait.
    Rend (nombre comblé, nombre restant sans catégorie).
    """
    comble = restant = 0
    for e in events:
        if (getattr(e, "category", None) or "").strip():
            continue
        cat = deduire(getattr(e, "title", None), getattr(e, "venue", None))
        if cat:
            e.category = cat
            comble += 1
        else:
            restant += 1
    return comble, restant
