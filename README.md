# Alex EntityMap Studio

Intégration Home Assistant (panel dédié dans la barre latérale) pour explorer
tes entités : pièce, dernière utilisation, dépendances (ce qu'un
script/une automatisation utilise), et **appelants** — qui référence cette
entité, à travers automatisations, scripts, et dashboards.

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
  résolution complète des `!include`/`!include_dir_*` grâce au chargeur YAML
  natif de Home Assistant (fonctionne quelle que soit ton organisation de
  fichiers).
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

## Non couvert (pour l'instant)

Date de création/modification d'une entité — non tracée de façon fiable par
Home Assistant pour tout ce qui est défini en YAML pur, abandonné comme
convenu plutôt que d'afficher une information incertaine.
