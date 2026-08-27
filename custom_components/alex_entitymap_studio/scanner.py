"""Scanner de configuration pour Alex EntityMap Studio.

Construit la carte des references entre entites en parcourant :
- les automatisations et scripts (via configuration.yaml, avec resolution
  des `!include`/`!include_dir_*` grace au chargeur YAML natif de HA) ;
- les dashboards geres par l'interface (fichiers JSON dans .storage/).

Pour chaque chaine de caracteres rencontree dans ces configurations, on
cherche soit une reference litterale a un entity_id (comparee directement
au registre des entites, pas a une liste de domaines codee en dur -- plus
robuste et jamais perimee), soit -- si la chaine contient du Jinja avec de
la concatenation -- un motif regex extrait via jinja_pattern.py, ensuite
confronte lui aussi au registre reel pour ne retenir que les entites qui
existent vraiment.

Toute correspondance obtenue par motif (pas litterale) est marquee
`confidence: "pattern"` plutot que `"exact"`, pour que le panel puisse
l'annoncer clairement comme une detection indicative, jamais une certitude.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .ha_yaml import load_yaml_file, load_yaml_with_includes
from .jinja_pattern import find_templated_entity_refs

_LOGGER = logging.getLogger(__name__)

_ENTITY_ID_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9_]+$")
# Pour reperer des entity_id litteraux glisses A L'INTERIEUR d'un template
# (ex. states('light.salon') sans aucune concatenation) : mêmes forme mais
# recherchee comme sous-chaine entre guillemets, pas ancree sur toute la
# chaine.
_ENTITY_ID_SUBSTR_RE = re.compile(r"[a-z_][a-z0-9_]*\.[a-z0-9_]+")


@dataclass
class Reference:
    """Une reference trouvee vers un entity_id, depuis une automatisation,
    un script, ou une carte de dashboard."""

    entity_id: str
    source_type: str  # "automation" | "script" | "dashboard"
    source_id: str  # alias/id de l'automatisation ou du script, titre de la vue+carte pour un dashboard
    confidence: str  # "exact" | "pattern" | "blueprint" (marqueur d'usage, pas une entite)
    via_blueprint: bool = False  # trouvee en resolvant le fichier de blueprint, pas dans le corps direct


@dataclass
class EntityInfo:
    """Fiche d'une entite pour le panel."""

    entity_id: str
    domain: str
    name: str
    area: str | None
    last_used: str | None  # ISO 8601, ou None si jamais observe
    last_used_kind: str  # "last_triggered" | "last_changed" | "inconnu"
    disabled: bool
    hidden: bool
    references: list[Reference] = field(default_factory=list)  # qui APPELLE cette entite
    dependencies: list[Reference] = field(default_factory=list)  # ce que CETTE entite utilise (scripts/automatisations)


def _load_yaml_tree(hass: HomeAssistant) -> dict:
    """Charge configuration.yaml avec resolution complete des !include /
    !include_dir_* -- via notre propre chargeur (ha_yaml.py), teste
    directement plutot qu'un pari sur une API interne de HA dont on n'avait
    pas de confirmation fiable de comportement quand appelee depuis une
    integration tierce."""
    config_path = os.path.join(hass.config.config_dir, "configuration.yaml")
    try:
        return load_yaml_with_includes(config_path)
    except Exception:  # noqa: BLE001 - on ne veut jamais planter le scan entier
        _LOGGER.exception("Echec du chargement de configuration.yaml")
        return {}


def _iter_strings(obj, path: str = ""):
    """Parcourt recursivement une structure YAML/JSON (dict/list/scalaire)
    et produit (chemin, chaine) pour chaque valeur texte rencontree."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _iter_strings(value, f"{path}.{key}" if path else str(key))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from _iter_strings(value, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def _candidates_from_string(
    text: str, extra_aliases: dict[str, str] | None = None
) -> tuple[set[str], set[str]]:
    """Renvoie (litteraux, motifs) trouves dans une chaine : les entity_id
    ecrits tels quels, et les motifs regex extraits de tout Jinja avec
    concatenation. Ni l'un ni l'autre n'est encore confronte au registre
    reel -- fait par l'appelant, qui a acces au registre.

    `extra_aliases` : voir find_templated_entity_refs -- propage les
    valeurs deja connues d'un contexte exterieur (typiquement les valeurs
    resolues d'un blueprint pour l'instance precise en cours d'analyse)."""
    literals: set[str] = set()
    patterns: set[str] = set()

    stripped = text.strip()
    if _ENTITY_ID_RE.match(stripped):
        literals.add(stripped)

    for m in _ENTITY_ID_SUBSTR_RE.finditer(text):
        literals.add(m.group(0))

    if "{{" in text or "{%" in text:
        for result in find_templated_entity_refs(text, extra_aliases):
            if result.resolved and result.regex:
                patterns.add(result.regex)

    return literals, patterns


def _known_entity_ids(hass: HomeAssistant) -> set[str]:
    registry = er.async_get(hass)
    return {e.entity_id for e in registry.entities.values()}


def _matches_from_candidates(
    literals: set[str], patterns: set[str], known_ids: set[str]
) -> list[tuple[str, str]]:
    """Confronte les candidats au registre reel. Renvoie une liste de
    (entity_id, confidence).

    Un `states('input_datetime.matin')` (chaine constante, sans
    concatenation) passe par le meme chemin d'extraction que les motifs
    dynamiques -- resultat techniquement classe "pattern" alors qu'il n'y a
    en realite aucun joker. Priorite au littteral ici, pour la meme entite,
    plutot que de laisser les deux coexister comme si c'etaient deux faits
    distincts."""
    best: dict[str, str] = {}
    for lit in literals:
        if lit in known_ids:
            best[lit] = "exact"
    if patterns:
        combined = "^(?:" + "|".join(patterns) + ")$"
        try:
            compiled = re.compile(combined)
        except re.error:
            return list(best.items())
        for entity_id in known_ids:
            if compiled.match(entity_id) and entity_id not in best:
                best[entity_id] = "pattern"
    return list(best.items())


def _scan_blueprint(
    hass: HomeAssistant,
    source_type: str,
    use_blueprint: dict,
    source_id: str,
    known_ids: set[str],
) -> list[Reference]:
    """Resout un `use_blueprint: {path, input}` en lisant le fichier de
    blueprint lui-meme (`/config/blueprints/<domaine>/<path>`) -- sans ca,
    la logique reelle reste invisible : elle vit dans un fichier separe,
    jamais dans automations.yaml/scripts.yaml. Chaque `!input x` rencontre
    dans le blueprint est substitue par la vraie valeur fournie par CETTE
    instance precise (`input.x`) avant de chercher des entites.

    Construit aussi des alias GLOBAUX (valables pour tout le fichier, pas
    juste la chaine en cours) a partir du bloc `variables:` de tete du
    blueprint (`niveaux: !input niveaux` etc.) -- sans ca, une variable
    Jinja comme `light_type`, reutilisee bien plus loin dans le fichier
    (ex. dans un `states('input_text.' ~ ... ~ light_type ~ ...)`),
    resterait un joker generique alors qu'elle a en realite une valeur
    connue et fixe pour cette instance precise, ce qui elargissait le motif
    final bien au-dela de ce qui concerne reellement CE script (il matchait
    les entites de TOUS les scripts utilisant le meme blueprint)."""
    path = use_blueprint.get("path")
    resolved_inputs = use_blueprint.get("input") or {}
    if not path or not isinstance(resolved_inputs, dict):
        return []

    full_path = os.path.join(hass.config.config_dir, "blueprints", source_type, path)
    bp_tree = load_yaml_file(full_path)
    if not bp_tree:
        return []

    declared_inputs = set((bp_tree.get("blueprint") or {}).get("input") or {})

    # Alias globaux : pour chaque `cle: !input x` du bloc `variables:` de
    # tete, si `x` est un input declare ET fourni par cette instance, la
    # variable `cle` (utilisee plus loin dans TOUT le fichier) vaut
    # litteralement cette valeur -- pas un joker.
    global_aliases: dict[str, str] = {}
    for var_name, raw_value in (bp_tree.get("variables") or {}).items():
        if isinstance(raw_value, str) and raw_value in declared_inputs and raw_value in resolved_inputs:
            resolved = resolved_inputs[raw_value]
            if isinstance(resolved, str):
                global_aliases[var_name] = re.escape(resolved)

    body = {k: v for k, v in bp_tree.items() if k != "blueprint"}

    refs: list[Reference] = []

    # Signale explicitement l'usage du blueprint lui-meme, meme si aucune
    # entite n'est trouvee a l'interieur -- demande explicite : au minimum,
    # savoir que ce script depend de ce blueprint precis.
    refs.append(Reference(f"blueprint:{path}", source_type, source_id, "blueprint", via_blueprint=True))

    for _path, text in _iter_strings(body):
        # La chaine EST exactement un nom d'input du blueprint (provient
        # d'un tag !input, charge tel quel comme texte brut) : on substitue
        # la vraie valeur avant de chercher des entites. Cette valeur (ex.
        # `light.cagibi_light_all`) est deja directement visible dans
        # `use_blueprint.input` du script lui-meme -- pas besoin d'avoir lu
        # le fichier de blueprint pour la voir, donc PAS marquee
        # "via_blueprint" malgre le fait qu'on la trouve dans ce scan-la.
        if text in declared_inputs and text in resolved_inputs:
            value = resolved_inputs[text]
            if isinstance(value, str):
                literals, patterns = _candidates_from_string(value, global_aliases)
                for entity_id, confidence in _matches_from_candidates(literals, patterns, known_ids):
                    refs.append(Reference(entity_id, source_type, source_id, confidence, via_blueprint=False))
            continue
        # Reference litterale ou templatee trouvee dans la STRUCTURE meme du
        # blueprint (ex. le motif `suffix`, qui combine plusieurs valeurs
        # d'une facon invisible sans lire le fichier) -- genuinement
        # "via_blueprint" cette fois.
        literals, patterns = _candidates_from_string(text, global_aliases)
        for entity_id, confidence in _matches_from_candidates(literals, patterns, known_ids):
            refs.append(Reference(entity_id, source_type, source_id, confidence, via_blueprint=True))

    # La deduplication (y compris entre litteral et motif pour une meme
    # entite) se fait globalement dans build_entity_map, une fois toutes
    # les sources rassemblees -- pas seulement au sein de ce blueprint.
    return refs


def _scan_automations_and_scripts(hass: HomeAssistant, known_ids: set[str]) -> list[Reference]:
    tree = _load_yaml_tree(hass)
    refs: list[Reference] = []

    for source_type, key in (("automation", "automation"), ("script", "script")):
        section = tree.get(key)
        if not section:
            continue
        # `automation:` est une liste d'entrees ; `script:` est un mapping
        # {object_id: config}.
        entries = section if isinstance(section, list) else [
            {"id": obj_id, **(cfg or {})} for obj_id, cfg in section.items()
        ]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            source_id = entry.get("alias") or entry.get("id") or "?"

            use_blueprint = entry.get("use_blueprint")
            if isinstance(use_blueprint, dict):
                # Config generee par un blueprint : la logique reelle vit
                # dans le fichier de blueprint, pas ici -- scan dedie.
                refs.extend(_scan_blueprint(hass, source_type, use_blueprint, str(source_id), known_ids))
                continue

            for _path, text in _iter_strings(entry):
                literals, patterns = _candidates_from_string(text)
                for entity_id, confidence in _matches_from_candidates(literals, patterns, known_ids):
                    refs.append(Reference(entity_id, source_type, str(source_id), confidence))
    return refs


def _scan_dashboards(hass: HomeAssistant, known_ids: set[str]) -> list[Reference]:
    refs: list[Reference] = []
    storage_dir = os.path.join(hass.config.config_dir, ".storage")
    if not os.path.isdir(storage_dir):
        return refs

    for filepath in glob.glob(os.path.join(storage_dir, "lovelace*")):
        try:
            with open(filepath, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        title = os.path.basename(filepath)
        for _path, text in _iter_strings(data):
            literals, patterns = _candidates_from_string(text)
            for entity_id, confidence in _matches_from_candidates(literals, patterns, known_ids):
                refs.append(Reference(entity_id, "dashboard", title, confidence))
    return refs


def _entity_area(hass: HomeAssistant, entry: er.RegistryEntry) -> str | None:
    """Pièce effective d'une entité : la sienne si definie, sinon celle de
    son appareil (repli standard, la sienne prevaut toujours si presente)."""
    area_reg = ar.async_get(hass)
    area_id = entry.area_id
    if area_id is None and entry.device_id:
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get(entry.device_id)
        if device:
            area_id = device.area_id
    if area_id is None:
        return None
    area = area_reg.async_get_area(area_id)
    return area.name if area else None


def _slug(text: str) -> str:
    """Slugification simple (minuscules, non-alphanumerique -> underscore),
    pour rapprocher un alias/id d'automatisation/script de l'object_id de
    son entite -- ne reproduit pas exactement l'algorithme interne de HA,
    donc cette correspondance reste du meilleur effort, pas une garantie
    (annonce comme telle dans le panel)."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


# Un meme fait (cette source reference cette entite) peut etre trouve
# plusieurs fois par des chemins differents -- ex. une fois comme litteral
# exact, une fois comme "motif" via l'extraction states(...) generique
# meme quand il n'y avait en realite aucun joker dynamique (voir
# _matches_from_candidates). Priorite : exact > pattern > blueprint (ce
# dernier n'est pas une entite, un marqueur d'usage a part).
_CONFIDENCE_RANK = {"exact": 2, "pattern": 1, "blueprint": 0}


def _dedupe_refs(refs: list[Reference]) -> list[Reference]:
    """Deduplique globalement par (entite, source) -- garde la version la
    plus fiable quand le meme fait a ete trouve par plusieurs chemins
    (litteral + motif pour la meme entite, ou la meme reference repetee a
    plusieurs endroits d'un meme fichier/blueprint)."""
    best: dict[tuple[str, str, str], Reference] = {}
    for ref in refs:
        key = (ref.entity_id, ref.source_type, ref.source_id)
        current = best.get(key)
        if current is None or _CONFIDENCE_RANK.get(ref.confidence, 0) > _CONFIDENCE_RANK.get(
            current.confidence, 0
        ):
            best[key] = ref
    return list(best.values())


def build_entity_map(hass: HomeAssistant) -> list[EntityInfo]:
    """Point d'entree principal : construit la liste complete des entites
    avec leurs infos, references (qui les appelle) et dependances (ce
    qu'elles appellent, pour les scripts/automatisations)."""
    registry = er.async_get(hass)
    known_ids = _known_entity_ids(hass)

    all_refs = _dedupe_refs(_scan_automations_and_scripts(hass, known_ids) + _scan_dashboards(hass, known_ids))
    refs_by_entity: dict[str, list[Reference]] = {}
    for ref in all_refs:
        refs_by_entity.setdefault(ref.entity_id, []).append(ref)

    # Index inverse pour les dependances : pour chaque (type de source,
    # alias/id slugifie), la liste des entites qu'elle reference.
    deps_by_source: dict[tuple[str, str], list[Reference]] = {}
    for ref in all_refs:
        if ref.source_type not in ("automation", "script"):
            continue
        key = (ref.source_type, _slug(ref.source_id))
        deps_by_source.setdefault(key, []).append(ref)

    results: list[EntityInfo] = []
    for entry in registry.entities.values():
        domain = entry.entity_id.split(".", 1)[0]
        object_id = entry.entity_id.split(".", 1)[1]
        state = hass.states.get(entry.entity_id)

        last_used = None
        last_used_kind = "inconnu"
        if state is not None:
            if domain in ("automation", "script") and state.attributes.get("last_triggered"):
                last_used = state.attributes["last_triggered"]
                last_used_kind = "last_triggered"
            elif state.last_changed:
                last_used = state.last_changed.isoformat()
                last_used_kind = "last_changed"

        dependencies: list[Reference] = []
        if domain in ("automation", "script"):
            dependencies = deps_by_source.get((domain, _slug(object_id)), [])

        results.append(
            EntityInfo(
                entity_id=entry.entity_id,
                domain=domain,
                name=entry.name or (state.attributes.get("friendly_name") if state else None) or entry.entity_id,
                area=_entity_area(hass, entry),
                last_used=last_used,
                last_used_kind=last_used_kind,
                disabled=entry.disabled_by is not None,
                hidden=entry.hidden_by is not None,
                references=refs_by_entity.get(entry.entity_id, []),
                dependencies=dependencies,
            )
        )
    return results
