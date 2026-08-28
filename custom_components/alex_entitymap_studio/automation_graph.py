"""Construction d'un graphe (noeuds/aretes) a partir de la configuration
d'une automatisation ou d'un script, pour Alex EntityMap Studio -- vue
"Automation Checker".

Module volontairement independant de Home Assistant (aucun import hass) :
prend directement la config deja chargee (dict Python, apres resolution
des !include/blueprint faite ailleurs), pas un fichier a lire lui-meme --
testable et relisible isolement, meme principe que harmony.py et
jinja_pattern.py dans les autres integrations de ce depot.

Perimetre couvert pour cette premiere version : sequences lineaires,
`if`/`then`/`else`, `choose`/`default`, conditions embarquees (arretent
l'execution si fausses, comportement reel de HA). Les constructions plus
avancees (`repeat`, `parallel`, `wait_for_trigger`) sont representees comme
un noeud unique "opaque" (decrit mais pas deploye en sous-graphe) plutot que
d'etre pleinement modelisees -- explicitement hors perimetre pour cette
version, annonce comme tel cote panel plutot que de simuler un comportement
partiel et trompeur.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class GraphNode:
    id: str
    kind: str  # "trigger" | "condition" | "if" | "choose" | "action" | "stop" | "opaque"
    label: str  # description courte, lisible d'un coup d'oeil dans le graphe
    detail: str = ""  # description plus complete (affichee au clic/survol)
    raw: dict | None = None  # etape YAML d'origine, pour un affichage brut si besoin


@dataclass
class GraphEdge:
    source: str
    target: str
    label: str | None = None  # "vrai" / "faux" / "option 1" / "défaut" / None (sequence simple)


@dataclass
class AutomationGraph:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    trigger_ids: list[str] = field(default_factory=list)  # noeuds de depart, un par declencheur


def _truncate(text: str, length: int = 60) -> str:
    text = " ".join(str(text).split())  # aplati les retours a la ligne d'un template multi-lignes
    return text if len(text) <= length else text[: length - 1] + "…"


# ---------------------------------------------------------------------------
# Descriptions lisibles -- transforment une etape YAML brute en texte
# comprehensible, pour l'affichage dans le graphe.
# ---------------------------------------------------------------------------
def describe_trigger(trig: dict) -> str:
    platform = trig.get("platform") or trig.get("trigger") or "?"
    if platform == "state":
        entities = trig.get("entity_id")
        entities = entities if isinstance(entities, list) else [entities]
        entities_str = ", ".join(str(e) for e in entities if e)
        to_state = trig.get("to")
        from_state = trig.get("from")
        if to_state is not None and from_state is not None:
            return f"État de {entities_str} : {from_state} → {to_state}"
        if to_state is not None:
            return f"État de {entities_str} devient {to_state}"
        return f"Changement d'état de {entities_str}"
    if platform == "numeric_state":
        entities = trig.get("entity_id")
        entities_str = ", ".join(entities) if isinstance(entities, list) else str(entities)
        above = trig.get("above")
        below = trig.get("below")
        if above is not None and below is not None:
            return f"{entities_str} entre {above} et {below}"
        if above is not None:
            return f"{entities_str} > {above}"
        if below is not None:
            return f"{entities_str} < {below}"
        return f"Valeur numérique de {entities_str}"
    if platform == "time":
        return f"À {trig.get('at', '?')}"
    if platform == "sun":
        return f"{'Lever' if trig.get('event') == 'sunrise' else 'Coucher'} du soleil"
    if platform == "event":
        return f"Événement « {trig.get('event_type', '?')} »"
    if platform == "webhook":
        return f"Webhook « {trig.get('webhook_id', '?')} »"
    if platform == "mqtt":
        return f"MQTT « {trig.get('topic', '?')} »"
    if platform == "template":
        return f"Modèle : {_truncate(trig.get('value_template', ''))}"
    if platform == "homeassistant":
        return f"Démarrage/arrêt HA ({trig.get('event', '?')})"
    return f"Déclencheur « {platform} »"


def _describe_single_condition(cond) -> str:
    if isinstance(cond, str):
        # Raccourci Jinja pur (une chaine de template directement comme condition).
        return f"Modèle : {_truncate(cond)}"
    if not isinstance(cond, dict):
        return "Condition"
    ctype = cond.get("condition", "?")
    if ctype == "state":
        entities = cond.get("entity_id")
        entities_str = ", ".join(entities) if isinstance(entities, list) else str(entities)
        state = cond.get("state")
        state_str = ", ".join(state) if isinstance(state, list) else str(state)
        return f"{entities_str} est {state_str}"
    if ctype == "numeric_state":
        entities = cond.get("entity_id")
        entities_str = ", ".join(entities) if isinstance(entities, list) else str(entities)
        above = cond.get("above")
        below = cond.get("below")
        if above is not None and below is not None:
            return f"{entities_str} entre {above} et {below}"
        if above is not None:
            return f"{entities_str} > {above}"
        if below is not None:
            return f"{entities_str} < {below}"
        return f"Valeur numérique de {entities_str}"
    if ctype == "template":
        return f"Modèle : {_truncate(cond.get('value_template', ''))}"
    if ctype == "time":
        after = cond.get("after")
        before = cond.get("before")
        if after and before:
            return f"Il est entre {after} et {before}"
        if after:
            return f"Il est après {after}"
        if before:
            return f"Il est avant {before}"
        return "Condition horaire"
    if ctype == "and":
        return " ET ".join(_describe_single_condition(c) for c in cond.get("conditions", []))
    if ctype == "or":
        return " OU ".join(_describe_single_condition(c) for c in cond.get("conditions", []))
    if ctype == "not":
        return "NON (" + " ET ".join(_describe_single_condition(c) for c in cond.get("conditions", [])) + ")"
    return f"Condition « {ctype} »"


def describe_condition_list(conditions) -> str:
    if isinstance(conditions, (str, dict)):
        conditions = [conditions]
    return " ET ".join(_describe_single_condition(c) for c in conditions)


def describe_action_step(step: dict) -> tuple[str, str]:
    """Renvoie (kind, label) pour une etape d'action simple (pas if/choose/
    condition, deja geres a part par le parseur)."""
    if "delay" in step:
        return "action", f"Attendre {step['delay']}"
    if "wait_template" in step:
        return "action", f"Attendre que : {_truncate(step['wait_template'])}"
    if "wait_for_trigger" in step:
        return "opaque", "Attendre un déclencheur (non détaillé dans cette version)"
    if "repeat" in step:
        return "opaque", "Répéter (contenu non déployé dans cette version)"
    if "parallel" in step:
        return "opaque", "En parallèle (branches non déployées dans cette version)"
    if "stop" in step:
        return "stop", f"Arrêt : {step.get('stop', '')}" if step.get("stop") else "Arrêt"
    if "event" in step:
        return "action", f"Émettre l'événement « {step['event']} »"
    if "scene" in step:
        return "action", f"Activer la scène {step['scene']}"
    service = step.get("service") or step.get("action")
    if service:
        target = step.get("target", {})
        entity_id = target.get("entity_id") or step.get("entity_id")
        if entity_id:
            entities_str = ", ".join(entity_id) if isinstance(entity_id, list) else str(entity_id)
            return "action", f"{service} → {entities_str}"
        return "action", str(service)
    return "opaque", "Étape non reconnue"


# ---------------------------------------------------------------------------
# Parseur recursif -- transforme une sequence d'etapes en noeuds/aretes.
# ---------------------------------------------------------------------------
class _IdGen:
    def __init__(self):
        self._n = 0

    def next(self) -> str:
        self._n += 1
        return f"n{self._n}"


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _parse_sequence(
    steps: list,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    idgen: _IdGen,
    incoming: list[tuple[str, str | None]],
) -> list[tuple[str, str | None]]:
    """Parse une sequence d'etapes. `incoming` = liste de (node_id, label a
    utiliser pour l'arete) d'ou partir. Renvoie la meme forme en sortie --
    peut contenir plusieurs entrees si la derniere etape est un if/choose
    (plusieurs chemins de sortie possibles)."""
    current = incoming
    for step in steps:
        if not isinstance(step, dict):
            continue

        if "if" in step:
            cond_id = idgen.next()
            nodes.append(GraphNode(cond_id, "if", describe_condition_list(step["if"]), raw=step))
            for prev_id, prev_label in current:
                edges.append(GraphEdge(prev_id, cond_id, prev_label))
            then_out = _parse_sequence(step.get("then", []), nodes, edges, idgen, [(cond_id, "vrai")])
            else_steps = step.get("else", [])
            else_out = (
                _parse_sequence(else_steps, nodes, edges, idgen, [(cond_id, "faux")])
                if else_steps
                else [(cond_id, "faux")]
            )
            current = then_out + else_out
            continue

        if "choose" in step:
            choose_id = idgen.next()
            nodes.append(GraphNode(choose_id, "choose", "Choisir…", raw=step))
            for prev_id, prev_label in current:
                edges.append(GraphEdge(prev_id, choose_id, prev_label))
            branch_out: list[tuple[str, str | None]] = []
            for i, option in enumerate(step["choose"]):
                option_label = f"Option {i + 1} : {describe_condition_list(option.get('conditions', []))}"
                branch_out += _parse_sequence(
                    option.get("sequence", []), nodes, edges, idgen, [(choose_id, option_label)]
                )
            default_steps = step.get("default", [])
            branch_out += (
                _parse_sequence(default_steps, nodes, edges, idgen, [(choose_id, "défaut")])
                if default_steps
                else [(choose_id, "défaut (rien)")]
            )
            current = branch_out
            continue

        if "condition" in step and isinstance(step.get("condition"), str):
            # Condition EMBARQUEE directement (pas if/choose) : si fausse,
            # arrete cette execution -- comportement reel de HA, pas une
            # simplification. La cle "condition" contient ici le TYPE de la
            # condition ("state", "and", "numeric_state"...), toujours une
            # chaine a ce niveau -- une liste n'apparait que pour le
            # `condition:`/`conditions:` de TETE d'une automatisation (deja
            # gere a part dans parse_to_graph) ou a l'interieur du
            # `conditions:` propre a un and/or.
            cond_id = idgen.next()
            nodes.append(GraphNode(cond_id, "condition", _describe_single_condition(step), raw=step))
            for prev_id, prev_label in current:
                edges.append(GraphEdge(prev_id, cond_id, prev_label))
            stop_id = idgen.next()
            nodes.append(GraphNode(stop_id, "stop", "Arrêt (condition non remplie)"))
            edges.append(GraphEdge(cond_id, stop_id, "faux"))
            current = [(cond_id, "vrai")]
            continue

        # Action simple (ou construction opaque type repeat/parallel).
        action_id = idgen.next()
        kind, label = describe_action_step(step)
        nodes.append(GraphNode(action_id, kind, label, raw=step))
        for prev_id, prev_label in current:
            edges.append(GraphEdge(prev_id, action_id, prev_label))
        current = [(action_id, None)]

    return current


def parse_to_graph(config: dict) -> AutomationGraph:
    """Point d'entree principal : transforme la config d'une automatisation
    ou d'un script (deja resolue -- !include/blueprint geres en amont) en
    graphe de noeuds/aretes."""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    idgen = _IdGen()

    triggers = _as_list(config.get("trigger") or config.get("triggers"))
    trigger_ids: list[str] = []
    incoming: list[tuple[str, str | None]] = []
    for trig in triggers:
        if not isinstance(trig, dict):
            continue
        tid = idgen.next()
        trigger_ids.append(tid)
        nodes.append(GraphNode(tid, "trigger", describe_trigger(trig), raw=trig))
        incoming.append((tid, None))

    # Script sans declencheur (appele directement) : point de depart unique
    # implicite, pour que le graphe ait quand meme un noeud de tete.
    if not incoming:
        start_id = idgen.next()
        nodes.append(GraphNode(start_id, "trigger", "Lancement (script/action directe)"))
        trigger_ids.append(start_id)
        incoming.append((start_id, None))

    top_conditions = config.get("condition") or config.get("conditions")
    if top_conditions:
        cond_id = idgen.next()
        # Represente la LISTE de conditions de tete comme un "and" en bonne
        # et due forme (c'est bien sa semantique reelle dans HA : plusieurs
        # conditions au meme niveau = toutes doivent etre vraies) -- jamais
        # `{"condition": <liste>}` directement, que ni evaluate_condition ni
        # referenced_entities_in_conditions ne savent lire (la cle
        # "condition" y est censee contenir un TYPE, pas une liste).
        nodes.append(
            GraphNode(
                cond_id,
                "condition",
                describe_condition_list(top_conditions),
                raw={"condition": "and", "conditions": _as_list(top_conditions)},
            )
        )
        for prev_id, prev_label in incoming:
            edges.append(GraphEdge(prev_id, cond_id, prev_label))
        stop_id = idgen.next()
        nodes.append(GraphNode(stop_id, "stop", "Arrêt (condition non remplie)"))
        edges.append(GraphEdge(cond_id, stop_id, "faux"))
        incoming = [(cond_id, "vrai")]

    actions = _as_list(config.get("action") or config.get("actions") or config.get("sequence"))
    _parse_sequence(actions, nodes, edges, idgen, incoming)

    return AutomationGraph(nodes=nodes, edges=edges, trigger_ids=trigger_ids)


# ---------------------------------------------------------------------------
# Moteur d'evaluation des conditions -- prend `get_state` en parametre
# (jamais d'acces direct a hass) pour que les etats forces par l'utilisateur
# (vue "Automation Checker", simulation) soient la SEULE source de verite
# utilisee, sans aucun risque d'effet de bord ni de fuite vers les vraies
# valeurs quand une valeur est explicitement forcee.
#
# Renvoie True/False quand la condition est evaluable, ou None quand elle ne
# peut pas etre determinee de facon fiable (etat inconnu, ou construction non
# couverte par cette version -- template Jinja arbitraire notamment) --
# jamais une supposition silencieuse presentee comme un resultat certain.
# ---------------------------------------------------------------------------
@dataclass
class StateSnapshot:
    state: str
    attributes: dict = field(default_factory=dict)


def evaluate_condition(condition, get_state) -> bool | None:
    if isinstance(condition, list):
        results = [evaluate_condition(c, get_state) for c in condition]
        if any(r is None for r in results):
            return None
        return all(results)

    if isinstance(condition, str):
        return None  # template Jinja brut -- non evalue dans cette version, voir limite assumee

    if not isinstance(condition, dict):
        return None

    ctype = condition.get("condition")

    if ctype == "state":
        entity_id = condition.get("entity_id")
        if isinstance(entity_id, list):
            sub_results = [evaluate_condition({**condition, "entity_id": e}, get_state) for e in entity_id]
            if any(r is None for r in sub_results):
                return None
            return all(sub_results)
        snap = get_state(entity_id)
        if snap is None:
            return None
        expected = condition.get("state")
        expected_list = expected if isinstance(expected, list) else [expected]
        return snap.state in expected_list

    if ctype == "numeric_state":
        entity_id = condition.get("entity_id")
        snap = get_state(entity_id)
        if snap is None:
            return None
        try:
            value = float(snap.state)
        except (TypeError, ValueError):
            return None
        above = condition.get("above")
        below = condition.get("below")
        if above is not None and value <= float(above):
            return False
        if below is not None and value >= float(below):
            return False
        return True

    if ctype == "and":
        return evaluate_condition(condition.get("conditions", []), get_state)

    if ctype == "or":
        sub_results = [evaluate_condition(c, get_state) for c in condition.get("conditions", [])]
        if any(r is True for r in sub_results):
            return True
        if any(r is None for r in sub_results):
            return None
        return False

    if ctype == "not":
        inner = evaluate_condition(condition.get("conditions", []), get_state)
        return None if inner is None else not inner

    # template / time / sun / zone / trigger / device... : pas evalue dans
    # cette version -- indetermine plutot qu'une fausse certitude.
    return None


@dataclass
class SimulationResult:
    visited_node_ids: list[str] = field(default_factory=list)
    taken_edges: list[tuple[str, str]] = field(default_factory=list)  # (source, target) reellement empruntes
    undetermined_at: str | None = None  # id du noeud ou la simulation s'est arretee, faute de pouvoir evaluer
    stopped_reason: str | None = None  # "condition_false" | "undetermined" | "end_of_branch" | None (arrivee normale en fin d'action)


def _condition_payload_for_node(node: GraphNode):
    """Extrait la structure de condition a evaluer selon le type de noeud --
    un noeud 'if' porte l'etape complete (if/then/else) dans `raw`, un noeud
    'condition' embarque porte directement le dict de condition."""
    if node.kind == "if":
        return (node.raw or {}).get("if", [])
    return node.raw or {}


def simulate_from_trigger(graph: AutomationGraph, trigger_id: str, get_state) -> SimulationResult:
    """Suit le chemin reellement emprunte depuis un declencheur donne, en
    evaluant chaque condition/if/choose rencontre avec `get_state` --
    n'appelle JAMAIS aucun service, se contente de determiner le chemin."""
    node_by_id = {n.id: n for n in graph.nodes}
    result = SimulationResult(visited_node_ids=[trigger_id])
    current_id = trigger_id

    while True:
        node = node_by_id.get(current_id)
        if node is None:
            break
        outgoing = [e for e in graph.edges if e.source == current_id]
        if not outgoing:
            result.stopped_reason = "end_of_branch"
            break

        if node.kind in ("if", "condition"):
            payload = _condition_payload_for_node(node)
            outcome = evaluate_condition(payload, get_state)
            if outcome is None:
                result.undetermined_at = current_id
                result.stopped_reason = "undetermined"
                break
            label = "vrai" if outcome else "faux"
            edge = next((e for e in outgoing if e.label == label), None)
            if edge is None:
                result.stopped_reason = "end_of_branch"
                break
            result.taken_edges.append((edge.source, edge.target))
            result.visited_node_ids.append(edge.target)
            if node_by_id[edge.target].kind == "stop":
                # Arrive directement sur un stop (ex. condition embarquee
                # fausse) : s'arreter ICI avec la raison precise, plutot que
                # de reboucler -- sinon la verification generique "plus
                # d'arete sortante" du haut de la boucle ecraserait cette
                # raison par un "end_of_branch" trop vague au tour suivant.
                result.stopped_reason = "condition_false"
                break
            current_id = edge.target
            continue

        if node.kind == "choose":
            options = (node.raw or {}).get("choose", [])
            # Les N premieres aretes sortantes correspondent aux options
            # dans l'ORDRE ou elles ont ete generees (voir _parse_sequence) ;
            # la derniere est le defaut -- jamais reordonnees ailleurs dans
            # ce module, donc cette correspondance par position reste fiable.
            chosen_edge = None
            for i, option in enumerate(options):
                outcome = evaluate_condition(option.get("conditions", []), get_state)
                if outcome is None:
                    result.undetermined_at = current_id
                    result.stopped_reason = "undetermined"
                    break
                if outcome:
                    chosen_edge = outgoing[i] if i < len(outgoing) else None
                    break
            else:
                chosen_edge = outgoing[-1] if outgoing else None  # aucune option vraie -> defaut
            if result.stopped_reason == "undetermined":
                break
            if chosen_edge is None:
                result.stopped_reason = "end_of_branch"
                break
            result.taken_edges.append((chosen_edge.source, chosen_edge.target))
            result.visited_node_ids.append(chosen_edge.target)
            current_id = chosen_edge.target
            continue

        # Noeud simple (trigger/action/stop/opaque) : au plus une suite.
        edge = outgoing[0]
        result.taken_edges.append((edge.source, edge.target))
        result.visited_node_ids.append(edge.target)
        current_id = edge.target

        if node_by_id[edge.target].kind == "stop":
            result.stopped_reason = "condition_false"
            break

    return result


def referenced_entities_in_conditions(graph: AutomationGraph) -> list[str]:
    """Entites utilisees dans les conditions/if/choose du graphe -- pour
    proposer, cote panel, un formulaire de valeurs a forcer avant de
    simuler, plutot que de laisser l'utilisateur deviner quels entity_id
    comptent."""
    seen: set[str] = set()

    def scan(cond):
        if isinstance(cond, list):
            for c in cond:
                scan(c)
            return
        if not isinstance(cond, dict):
            return
        entity_id = cond.get("entity_id")
        if isinstance(entity_id, str):
            seen.add(entity_id)
        elif isinstance(entity_id, list):
            seen.update(e for e in entity_id if isinstance(e, str))
        for sub in cond.get("conditions", []):
            scan(sub)

    for node in graph.nodes:
        if node.kind == "if":
            scan((node.raw or {}).get("if", []))
        elif node.kind == "condition":
            scan(node.raw or {})
        elif node.kind == "choose":
            for option in (node.raw or {}).get("choose", []):
                scan(option.get("conditions", []))

    return sorted(seen)
