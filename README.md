# Alex EntityMap Studio

Intégration Home Assistant (panel dédié dans la barre latérale) pour explorer
tes entités, avec trois vues accessibles depuis l'en-tête :

- **Entity Checker** — pièce, dernière utilisation, dépendances (ce qu'un
  script/une automatisation utilise), et **appelants** — qui référence cette
  entité, à travers automatisations, scripts, et dashboards.
- **Entity Info** — recherche une entité, vois son état et ses attributs en
  direct, et surtout les **actions possibles** sur son domaine (ex. pour une
  `light` : `light.turn_on`, `light.turn_off`, `light.toggle`...), avec leur
  description et leurs paramètres.
- **Automation Checker** — en cours de conception (voir plus bas).

La liste d'entités dans la barre latérale (filtre par domaine + recherche
texte) est **partagée** entre Entity Checker et Entity Info — sélectionner
une entité l'affiche dans la vue active, pas besoin de la rechercher deux
fois.

## Pourquoi une intégration plutôt qu'un simple outil de recherche texte

Une recherche texte classique (grep, recherche globale de l'éditeur) ne
trouve que les `entity_id` écrits **littéralement**. Beaucoup d'entity_id
dans Home Assistant sont en réalité **assemblés au moment de l'exécution**
via des templates Jinja (ex. `states('input_text.' ~ suffix)`) — une
recherche texte ne les trouve jamais. Cette intégration analyse la
**structure** de ces expressions (via le vrai analyseur syntaxique Jinja2,
pas une lecture de texte à l'œil) pour en déduire un motif regex, confronté
ensuite au registre réel des entités pour ne garder que les correspondances
qui existent vraiment.

## Limite assumée, honnêtement

Cette détection ne peut fonctionner que par **analyse statique** — lire les
fichiers de configuration et y repérer des motifs, sans jamais exécuter le
code. Elle couvre bien les enchaînements de concaténation simples (`~`,
directement ou via un alias `{% set %}` réutilisé plus loin dans le même
template) — de très loin le cas le plus courant dans Home Assistant. Elle ne
peut en revanche pas résoudre une valeur qui dépend elle-même d'un état
calculé à l'exécution (ex. `states(some_dynamic_lookup())`), ni les
constructions Jinja plus exotiques (boucles, macros). Ces cas sont
simplement laissés de côté plutôt que d'afficher un résultat trompeur.

Chaque correspondance trouvée par cette méthode est explicitement étiquetée
**« détecté par motif »** (pas litérale) dans le panel — jamais confondue
avec une correspondance exacte.

## Installation

Via HACS (dépôt personnalisé, catégorie **Intégration**) :

1. HACS → Intégrations → ⋮ → Dépôts personnalisés → coller l'URL de ce
   dépôt, catégorie *Integration*.
2. Installer « Alex EntityMap Studio », **redémarrer Home Assistant**
   (obligatoire pour toute intégration Python).
3. Réglages → Appareils et services → Ajouter une intégration → chercher
   « Alex EntityMap Studio » → confirmer. Aucun champ à remplir (instance
   unique).

Un panel « Alex EntityMap Studio » apparaît dans la barre latérale,
**réservé aux comptes administrateurs** (l'outil expose la configuration
complète des automatisations/scripts/dashboards — pas adapté à un compte
invité).

## Ce que scanne l'intégration

- **Automatisations et scripts** — via `configuration.yaml`, avec
  résolution complète des `!include`/`!include_dir_list`/
  `!include_dir_merge_list`/`!include_dir_named`/`!include_dir_merge_named`
  (fonctionne quelle que soit ton organisation de fichiers). Chargeur YAML
  **propre à l'intégration** (`ha_yaml.py`), testé directement plutôt que de
  s'appuyer sur une API interne de Home Assistant dont le comportement
  exact, appelée depuis une intégration tierce, n'a pas pu être confirmé de
  façon fiable.
- **Automatisations/scripts basés sur un blueprint** (`use_blueprint:`) —
  résolus explicitement : le fichier de blueprint lui-même
  (`/config/blueprints/<domaine>/<path>`) est lu, et chaque `!input x`
  qu'il contient est remplacé par la vraie valeur fournie par **cette
  instance précise** avant de chercher des références d'entités. Sans ça,
  la logique réelle d'un script/une automatisation généré par blueprint
  resterait invisible — elle vit dans un fichier séparé, jamais dans
  `automations.yaml`/`scripts.yaml`.
- **Dashboards gérés par l'interface** — fichiers `.storage/lovelace*`
  (JSON). Les dashboards en mode YAML pur sont couverts indirectement s'ils
  sont inclus depuis `configuration.yaml`.

## Pièce et dernière utilisation

- **Pièce** : celle de l'entité si définie, sinon celle de son appareil
  (repli standard).
- **Dernière utilisation** : `last_triggered` (attribut natif, pour
  `automation.*`/`script.*` — pas besoin de `recorder`/historique) ou
  `last_changed` (tout autre domaine, changement d'état natif là aussi).
  Aucune dépendance à une base de données d'historique.

## Navigation

Les entrées de « Dépendances » et « Appelants » sont cliquables :

- **Entité classique** (sensor, input_text...) → ouvre la fenêtre d'information
  standard de HA (`hass-more-info`).
- **Automatisation** → `/config/automation/edit/<id>`. Le `nav_id` utilisé est
  l'`unique_id` de l'entité (HA le définit comme l'`id:` de la config YAML pour
  cette plateforme — plus fiable qu'analyser le YAML nous-mêmes).
- **Script** → `/config/script/edit/<object_id>`.
- **Scène** → `/config/scene/edit/<id>`, même principe que les automatisations.
- **Dashboard** → reconstruit depuis le nom du fichier de stockage
  (`lovelace.dashboard_jc` → `/dashboard_jc/0`) — pointe vers la première vue
  du dashboard, pas encore vers la vue/carte précise (non tracée pour l'instant).

Sans correspondance résolue (alias/id introuvable dans le registre), l'entrée
reste affichée mais non cliquable, plutôt que de naviguer au mauvais endroit.

## Automation Checker

Choisis une automatisation ou un script dans le menu déroulant pour voir son
**graphe** (déclencheurs → conditions → actions, branches `if`/`choose`
comprises) : molette pour zoomer, glisser le fond pour déplacer la vue,
glisser un nœud pour le repositionner.

**Simulation** — choisis un déclencheur, force éventuellement l'état de
certaines entités (celles utilisées dans les conditions du graphe sont
proposées automatiquement, avec l'état réel actuel affiché en indication),
puis clique sur « Simuler » : le chemin réellement emprunté s'illumine en
jaune sur le graphe, les nœuds non atteints s'estompent. **Aucun service
n'est jamais appelé** — la simulation se contente de déterminer, par le
calcul, quel chemin serait suivi.

Ce qui est couvert par le moteur d'évaluation : conditions `state`,
`numeric_state`, `and`/`or`/`not`, imbriquées à volonté. Ce qui ne l'est
**pas** dans cette version, annoncé comme tel plutôt que deviné
silencieusement :
- Les conditions par **template Jinja brut** — la simulation s'arrête avec
  « indéterminé » plutôt que d'inventer un résultat.
- `repeat`, `parallel`, `wait_for_trigger` — représentés comme un nœud
  unique non détaillé dans le graphe, pas déployés en sous-étapes.
- Les automatisations/scripts basés sur un **blueprint** — signalés
  explicitement comme non supportés pour cette vue.

## Entity Info — actions possibles

Les actions listées viennent de la commande WebSocket **native** de Home
Assistant `get_services` (la même que celle utilisée par Outils de
développement → Actions dans l'interface officielle) — aucune commande
propre à cette intégration n'a été nécessaire pour ça. L'état et les
attributs affichés sont lus en direct côté panel (`hass.states`), sans
aller-retour serveur supplémentaire.

## Non couvert (pour l'instant)

Date de création/modification d'une entité — non tracée de façon fiable par
Home Assistant pour tout ce qui est défini en YAML pur, abandonné comme
convenu plutôt que d'afficher une information incertaine.
