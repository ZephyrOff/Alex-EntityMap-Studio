"""Extraction de motifs regex a partir d'expressions Jinja concatenees.

Beaucoup d'entity_id dans Home Assistant ne sont pas ecrits litteralement
mais assembles au moment de l'execution (ex. 'light_scheduler_' ~ light_type
~ '_' ~ periode ~ '_' ~ light_position). Une simple recherche texte ne les
trouve jamais. Ce module analyse la structure de ces expressions (via le
vrai analyseur syntaxique Jinja2, pas une lecture de texte a l'oeil) pour en
deduire un motif regex : les segments litteraux restent fixes, les segments
variables deviennent des jokers.

Porte volontairement limitee aux enchainements de concatenation simples
(l'operateur `~`, le cas de tres loin le plus courant dans HA) et aux alias
definis via `{% set x = ... %}` puis reutilises plus loin dans le meme
template -- exactement le motif du script light_scheduler qui a motive cet
outil. Tout ce qui sort de ce cas (boucles, macros, concatenations
conditionnelles complexes) est signale comme non resolu plutot que de
produire un resultat trompeur.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from jinja2 import Environment, nodes
from jinja2.exceptions import TemplateSyntaxError

_ENV = Environment()

# Motif joker pour un segment variable : evite d'etre trop gourmand (pas de
# point ni d'underscore) pour rester utile dans un entity_id typique
# (domain.object_id, segments separes par des underscores).
_WILDCARD = r"[^.\s]+?"


@dataclass
class PatternResult:
    """Resultat de l'extraction pour une expression Jinja."""

    regex: str | None  # None si non resolu
    resolved: bool
    reason: str = ""  # explication si non resolu


def _const_str(node: nodes.Node) -> str | None:
    """Renvoie la valeur si le noeud est une constante chaine, sinon None."""
    if isinstance(node, nodes.Const) and isinstance(node.value, str):
        return node.value
    return None


def _flatten_concat(node: nodes.Node) -> list[nodes.Node] | None:
    """Aplati une chaine de concatenations `~` (associative a gauche dans
    l'AST Jinja) en une liste ordonnee de noeuds. Renvoie None si le noeud
    n'est pas une concatenation."""
    if not isinstance(node, nodes.Concat):
        return None
    return list(node.nodes)


def _pattern_from_parts(parts: list[nodes.Node], aliases: dict[str, str]) -> PatternResult:
    """Construit un motif regex a partir des parties d'une concatenation.
    `aliases` : noms de variables deja resolus plus tot (via `{% set %}`)
    vers leur propre motif regex, pour les substituer si reutilises ici."""
    segments: list[str] = []
    for part in parts:
        literal = _const_str(part)
        if literal is not None:
            segments.append(re.escape(literal))
            continue
        if isinstance(part, nodes.Name) and part.name in aliases:
            segments.append(aliases[part.name])
            continue
        if isinstance(part, nodes.Name):
            # Variable non resolue (ex. vient d'un `states(...)` calcule a
            # l'execution, pas d'un choix fixe) : joker generique.
            segments.append(_WILDCARD)
            continue
        # Sous-expression plus complexe (filtre, appel de fonction...) :
        # on essaie recursivement si c'est elle-meme une concatenation,
        # sinon joker generique par prudence.
        sub = _flatten_concat(part)
        if sub is not None:
            sub_result = _pattern_from_parts(sub, aliases)
            if not sub_result.resolved:
                return sub_result
            segments.append(sub_result.regex)
        else:
            segments.append(_WILDCARD)
    return PatternResult(regex="".join(segments), resolved=True)


def extract_set_aliases(
    template_source: str, extra_aliases: dict[str, str] | None = None
) -> dict[str, str]:
    """Parcourt un bloc de template et resout chaque `{% set x = ... %}`
    en un motif regex, dans l'ordre d'apparition (pour que les alias
    ulterieurs puissent reutiliser les precedents, comme le fait
    `suffix` a partir de `light_type`/`periode`/`light_position` dans le
    script light_scheduler).

    `extra_aliases` : alias deja connus depuis un contexte exterieur (ex.
    des valeurs de blueprint resolues) -- utilises comme point de depart
    pour resoudre les `{% set %}` de CE texte. Indispensable : sans ca, un
    `{% set suffix = ... ~ light_type ~ ... %}` ne peut jamais beneficier
    d'un `light_type` deja connu venant de l'exterieur, meme si l'appelant
    le lui fournit par ailleurs -- le motif de `suffix` resterait large
    (jocker generique) alors que les references DIRECTES a `light_type`
    ailleurs dans le meme texte, elles, seraient bien resolues -- une
    incoherence qui laissait filtrer des correspondances trop larges.

    Une meme variable peut etre affectee plusieurs fois avec des
    concatenations DIFFERENTES (typiquement une branche par cas, comme le
    scheduler actif/inactif qui redefinissent chacun `suffix`
    differemment) : tous les motifs rencontres sont conserves et combines
    en union regex plutot que le dernier ecrasant les precedents --
    sinon une des deux formes reelles ne serait jamais retrouvee."""
    raw_patterns: dict[str, list[str]] = {
        name: [pattern] for name, pattern in (extra_aliases or {}).items()
    }
    try:
        ast = _ENV.parse(template_source)
    except TemplateSyntaxError:
        return {name: _union(pats) for name, pats in raw_patterns.items()}

    for node in ast.find_all(nodes.Assign):
        target = node.target
        if not isinstance(target, nodes.Name):
            continue
        # Alias courants au moment de CETTE affectation (une union de tout
        # ce qui a ete vu jusqu'ici pour chaque nom, extra_aliases compris).
        current_aliases = {name: _union(pats) for name, pats in raw_patterns.items()}

        parts = _flatten_concat(node.node)
        if parts is None:
            literal = _const_str(node.node)
            if literal is not None:
                raw_patterns.setdefault(target.name, []).append(re.escape(literal))
            elif isinstance(node.node, nodes.Name) and node.node.name in current_aliases:
                raw_patterns.setdefault(target.name, []).append(current_aliases[node.node.name])
            continue

        result = _pattern_from_parts(parts, current_aliases)
        if result.resolved:
            raw_patterns.setdefault(target.name, []).append(result.regex)

    return {name: _union(pats) for name, pats in raw_patterns.items()}


def _union(patterns: list[str]) -> str:
    """Combine plusieurs motifs regex en une union, sans repli inutile si
    un seul motif (evite un groupe non-capturant superflu dans le cas
    courant, purement cosmetique)."""
    unique = list(dict.fromkeys(patterns))  # deduplique en gardant l'ordre
    if len(unique) == 1:
        return unique[0]
    return "(?:" + "|".join(unique) + ")"


def extract_pattern(expr_source: str, aliases: dict[str, str] | None = None) -> PatternResult:
    """Extrait un motif regex pour UNE expression Jinja (le contenu d'un
    `{{ ... }}`, ou l'argument d'un appel comme `states(...)`). `aliases`
    peut etre fourni pour reutiliser des `{% set %}` deja resolus dans le
    meme template (voir extract_set_aliases)."""
    aliases = aliases or {}
    try:
        ast = _ENV.parse("{{ %s }}" % expr_source)
    except TemplateSyntaxError as exc:
        return PatternResult(regex=None, resolved=False, reason=f"syntaxe Jinja invalide: {exc}")

    output_nodes = [n for n in ast.body if isinstance(n, nodes.Output)]
    if not output_nodes or not output_nodes[0].nodes:
        return PatternResult(regex=None, resolved=False, reason="expression vide")

    expr_node = output_nodes[0].nodes[0]

    literal = _const_str(expr_node)
    if literal is not None:
        return PatternResult(regex=re.escape(literal), resolved=True)

    if isinstance(expr_node, nodes.Name) and expr_node.name in aliases:
        return PatternResult(regex=aliases[expr_node.name], resolved=True)

    parts = _flatten_concat(expr_node)
    if parts is not None:
        return _pattern_from_parts(parts, aliases)

    return PatternResult(
        regex=None,
        resolved=False,
        reason="expression trop complexe (ni constante, ni concatenation simple)",
    )


def find_templated_entity_refs(
    text: str, extra_aliases: dict[str, str] | None = None
) -> list[PatternResult]:
    """Trouve, dans un texte quelconque (une valeur YAML, potentiellement
    multi-lignes), tous les blocs `{{ ... }}` et `{% set ... %}` /
    `states(...)` avec concatenation, et renvoie les motifs regex extraits
    (resolus ou non). Fonction d'entree principale du scanner.

    `extra_aliases` : alias deja connus depuis un contexte EXTERIEUR a ce
    texte precis (ex. les valeurs d'un blueprint resolues via
    `variables: light_type: !input light_type` plus haut dans le meme
    fichier, mais dans une chaine YAML differente de celle qu'on analyse
    ici) -- sans ca, une variable comme `light_type` reste un joker
    generique alors qu'elle a en realite une valeur connue et fixe pour
    cette instance precise, ce qui rend le motif final bien trop large."""
    # Passe extra_aliases DIRECTEMENT a extract_set_aliases (pas juste
    # fusionne apres coup) : sinon un `{% set suffix = ... ~ light_type ~
    # ... %}` a l'interieur de ce texte ne pourrait jamais en beneficier,
    # meme si light_type est bien connu par ailleurs (voir la note dans
    # extract_set_aliases pour le detail du probleme que ca causait).
    aliases = extract_set_aliases(text, extra_aliases)
    results: list[PatternResult] = []

    for match in re.finditer(r"\{\{(.*?)\}\}", text, re.DOTALL):
        results.append(extract_pattern(match.group(1).strip(), aliases))

    # `states('...' ~ x)` peut aussi apparaitre sans etre dans un `{{ }}`
    # explicite quand c'est deja a l'interieur d'un bloc `{% %}` (ex. dans
    # un `{% set %}` lui-meme, deja couvert par extract_set_aliases, ou
    # dans un `{% if %}`) -- on cherche directement les appels a states()
    # avec un argument concatene, pour ne pas les manquer.
    for match in re.finditer(r"states\(\s*(.+?)\s*\)", text):
        results.append(extract_pattern(match.group(1).strip(), aliases))

    return results
