"""Chargeur YAML autonome, gerant les tags !include*/!secret de Home
Assistant nous-memes plutot que de dependre d'une API interne de HA dont on
n'a pas de confirmation fiable qu'elle resout correctement les inclusions
quand elle est appelee depuis une integration tierce.

Implementation volontairement autonome (PyYAML pur, testable ici sans
instance HA reelle) plutot qu'un pari sur une fonction interne potentiellement
fragile. Couvre les tags documentes par HA :
https://www.home-assistant.io/docs/configuration/splitting_configuration/
"""
from __future__ import annotations

import os

import yaml


class _IncludeLoader(yaml.SafeLoader):
    """SafeLoader PyYAML etendu avec les tags d'inclusion de HA. `_root`
    est pose sur chaque instance avant chargement : le repertoire de
    configuration de base, auquel tous les chemins !include sont relatifs
    (comportement HA reel -- pas relatif au fichier qui inclut, toujours
    au dossier de config racine, meme en cas d'inclusions imbriquees)."""

    _root: str = ""


def _include(loader: _IncludeLoader, node: yaml.Node):
    path = os.path.join(loader._root, loader.construct_scalar(node))
    return _load_file(path, loader._root)


def _include_dir_list(loader: _IncludeLoader, node: yaml.Node):
    dir_path = os.path.join(loader._root, loader.construct_scalar(node))
    if not os.path.isdir(dir_path):
        return []
    out = []
    for name in sorted(os.listdir(dir_path)):
        if name.startswith(".") or not name.endswith((".yaml", ".yml")):
            continue
        out.append(_load_file(os.path.join(dir_path, name), loader._root))
    return out


def _include_dir_merge_list(loader: _IncludeLoader, node: yaml.Node):
    out = []
    for item in _include_dir_list(loader, node):
        if isinstance(item, list):
            out.extend(item)
    return out


def _include_dir_named(loader: _IncludeLoader, node: yaml.Node):
    dir_path = os.path.join(loader._root, loader.construct_scalar(node))
    if not os.path.isdir(dir_path):
        return {}
    out = {}
    for name in sorted(os.listdir(dir_path)):
        if name.startswith(".") or not name.endswith((".yaml", ".yml")):
            continue
        key = os.path.splitext(name)[0]
        out[key] = _load_file(os.path.join(dir_path, name), loader._root)
    return out


def _include_dir_merge_named(loader: _IncludeLoader, node: yaml.Node):
    out = {}
    for value in _include_dir_named(loader, node).values():
        if isinstance(value, dict):
            out.update(value)
    return out


def _secret(loader: _IncludeLoader, node: yaml.Node):
    # La vraie valeur n'a aucune importance pour un scan de references
    # d'entites -- un simple marqueur suffit, pas besoin de lire
    # secrets.yaml.
    return "!secret!"


def _unknown_tag(loader: _IncludeLoader, node: yaml.Node):
    """Filet de securite : un tag non reconnu (specifique a une
    integration, ex. !input dans un blueprint) ne doit jamais faire
    planter tout le scan -- on renvoie sa valeur brute en texte."""
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None


_IncludeLoader.add_constructor("!include", _include)
_IncludeLoader.add_constructor("!include_dir_list", _include_dir_list)
_IncludeLoader.add_constructor("!include_dir_merge_list", _include_dir_merge_list)
_IncludeLoader.add_constructor("!include_dir_named", _include_dir_named)
_IncludeLoader.add_constructor("!include_dir_merge_named", _include_dir_merge_named)
_IncludeLoader.add_constructor("!secret", _secret)
_IncludeLoader.add_constructor(None, _unknown_tag)  # tout autre tag inconnu


def _load_file(path: str, root: str):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        loader = _IncludeLoader(fh)
        loader._root = root
        try:
            return loader.get_single_data()
        finally:
            loader.dispose()


def load_yaml_with_includes(config_yaml_path: str) -> dict:
    """Point d'entree : charge configuration.yaml avec resolution complete
    des inclusions. Renvoie {} si le fichier est introuvable ou invalide,
    plutot que de lever une exception qui interromprait tout le scan."""
    root = os.path.dirname(config_yaml_path)
    try:
        result = _load_file(config_yaml_path, root)
    except yaml.YAMLError:
        return {}
    return result if isinstance(result, dict) else {}
