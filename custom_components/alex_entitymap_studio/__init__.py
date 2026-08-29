"""L'integration Alex EntityMap Studio.

Explore les entites de cette instance HA : pièce, derniere utilisation,
dependances (ce qu'un script/une automatisation utilise) et appelants (qui
reference cette entite -- automatisations, scripts, dashboards), avec
detection des references construites dynamiquement via Jinja (voir
jinja_pattern.py) en plus des references litterales.

Le scan (lecture de fichiers, potentiellement couteux) tourne dans
l'executor a chaque appel de la commande websocket -- pas de cache
permanent pour l'instant : la configuration peut changer a tout moment
(nouvelle automatisation, carte de dashboard ajoutee...) et la fraicheur du
resultat importe plus que la vitesse pour cet outil d'exploration ponctuelle.

Vue "Automation Checker" (automation_graph.py) : transforme une
automatisation/un script en graphe, puis simule son execution depuis un
declencheur choisi -- avec des etats EVENTUELLEMENT forces par
l'utilisateur, jamais d'appel a un service reel.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict

import voluptuous as vol
from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from . import automation_graph, scanner
from .const import DOMAIN, PANEL_ICON, PANEL_TITLE, PANEL_URL_PATH

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Initialise l'integration : commande websocket + panel."""
    hass.data.setdefault(DOMAIN, {})

    if not hass.data[DOMAIN].get("ws_registered"):
        websocket_api.async_register_command(hass, websocket_get_map)
        websocket_api.async_register_command(hass, websocket_get_automation_graph)
        websocket_api.async_register_command(hass, websocket_simulate_automation)
        hass.data[DOMAIN]["ws_registered"] = True

    await _async_register_panel(hass)
    return True


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/get_map"})
@websocket_api.async_response
async def websocket_get_map(hass: HomeAssistant, connection, msg) -> None:
    """Lance le scan (dans l'executor, operations bloquantes de lecture de
    fichiers) et renvoie le resultat serialise au panel."""
    try:
        entities = await hass.async_add_executor_job(scanner.build_entity_map, hass)
    except Exception as exc:  # noqa: BLE001 - ne jamais planter la commande websocket
        _LOGGER.exception("Echec du scan Alex EntityMap Studio")
        connection.send_error(msg["id"], "scan_failed", str(exc))
        return

    connection.send_result(msg["id"], {"entities": [asdict(e) for e in entities]})


def _build_names_lookup(hass: HomeAssistant) -> dict[str, str]:
    """Construit {entity_id ou device_id: nom convivial reel}, pour un
    affichage lisible dans le graphe plutot que des identifiants bruts --
    voir automation_graph.py. Calcule a chaque appel (pas de cache) : les
    noms peuvent changer, et le cout reste negligeable pour un outil
    d'exploration ponctuelle, meme principe que le reste de cette
    integration."""
    names: dict[str, str] = {}
    for state in hass.states.async_all():
        friendly = state.attributes.get("friendly_name")
        if friendly:
            names[state.entity_id] = friendly
    device_reg = dr.async_get(hass)
    for device in device_reg.devices.values():
        label = device.name_by_user or device.name
        if label:
            names[device.id] = label
    return names


GET_AUTOMATION_GRAPH_SCHEMA = {
    vol.Required("type"): f"{DOMAIN}/get_automation_graph",
    vol.Required("entity_id"): str,
}


@websocket_api.websocket_command(GET_AUTOMATION_GRAPH_SCHEMA)
@websocket_api.async_response
async def websocket_get_automation_graph(hass: HomeAssistant, connection, msg) -> None:
    """Construit le graphe d'une automatisation/d'un script -- lecture
    seule, aucun effet de bord. Les automatisations/scripts bases sur un
    blueprint sont explicitement signales comme non supportes plutot que de
    construire un graphe vide ou trompeur."""
    entity_id = msg["entity_id"]
    try:
        config = await hass.async_add_executor_job(scanner.find_automation_or_script_config, hass, entity_id)
    except Exception as exc:  # noqa: BLE001 - ne jamais planter la commande websocket
        _LOGGER.exception("Echec de la localisation de la config pour %s", entity_id)
        connection.send_error(msg["id"], "scan_failed", str(exc))
        return

    if config is None:
        connection.send_error(msg["id"], "not_found", "Automatisation ou script introuvable dans la configuration YAML.")
        return
    if "use_blueprint" in config:
        connection.send_error(
            msg["id"],
            "blueprint_not_supported",
            "Cette automatisation/ce script utilise un blueprint -- non supporté pour le graphe dans cette version.",
        )
        return

    try:
        names = _build_names_lookup(hass)
        graph = automation_graph.parse_to_graph(config, names=names)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("Echec de la construction du graphe pour %s", entity_id)
        connection.send_error(msg["id"], "parse_failed", str(exc))
        return

    connection.send_result(
        msg["id"],
        {
            "nodes": [asdict(n) for n in graph.nodes],
            "edges": [asdict(e) for e in graph.edges],
            "trigger_ids": graph.trigger_ids,
            "condition_entities": automation_graph.referenced_entities_in_conditions(graph),
        },
    )


SIMULATE_AUTOMATION_SCHEMA = {
    vol.Required("type"): f"{DOMAIN}/simulate_automation",
    vol.Required("entity_id"): str,
    vol.Required("trigger_id"): str,
    vol.Optional("overrides", default=dict): {str: str},
}


@websocket_api.websocket_command(SIMULATE_AUTOMATION_SCHEMA)
@websocket_api.async_response
async def websocket_simulate_automation(hass: HomeAssistant, connection, msg) -> None:
    """Simule l'execution depuis un declencheur choisi -- les etats forces
    par l'utilisateur (`overrides`) priment sur les vrais etats actuels,
    utilises seulement en repli pour les entites non forcees. N'appelle
    JAMAIS aucun service : se contente de determiner le chemin qui SERAIT
    emprunte."""
    entity_id = msg["entity_id"]
    try:
        config = await hass.async_add_executor_job(scanner.find_automation_or_script_config, hass, entity_id)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("Echec de la localisation de la config pour %s", entity_id)
        connection.send_error(msg["id"], "scan_failed", str(exc))
        return

    if config is None or "use_blueprint" in config:
        connection.send_error(msg["id"], "not_found", "Introuvable, ou basé sur un blueprint (non supporté).")
        return

    try:
        names = _build_names_lookup(hass)
        graph = automation_graph.parse_to_graph(config, names=names)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("Echec de la construction du graphe pour %s", entity_id)
        connection.send_error(msg["id"], "parse_failed", str(exc))
        return

    overrides = msg.get("overrides") or {}

    def get_state(target_entity_id: str) -> automation_graph.StateSnapshot | None:
        if target_entity_id in overrides:
            return automation_graph.StateSnapshot(state=overrides[target_entity_id])
        real = hass.states.get(target_entity_id)
        if real is None:
            return None
        return automation_graph.StateSnapshot(state=real.state, attributes=dict(real.attributes))

    result = automation_graph.simulate_from_trigger(graph, msg["trigger_id"], get_state)

    connection.send_result(
        msg["id"],
        {
            "visited_node_ids": result.visited_node_ids,
            "taken_edges": [{"source": s, "target": t} for s, t in result.taken_edges],
            "uncertain_node_ids": result.uncertain_node_ids,
            "uncertain_edges": [{"source": s, "target": t} for s, t in result.uncertain_edges],
            "undetermined_at": result.undetermined_at,
            "stopped_reason": result.stopped_reason,
        },
    )


async def _async_register_panel(hass: HomeAssistant) -> None:
    """Enregistre le panel dans la barre laterale (une seule fois)."""
    registered_key = f"{DOMAIN}_panel_registered"
    if hass.data.get(registered_key):
        return
    hass.data[registered_key] = True

    panel_dir = os.path.join(os.path.dirname(__file__), "panel")
    panel_static_url = f"/{DOMAIN}_panel"

    await hass.http.async_register_static_paths(
        [StaticPathConfig(panel_static_url, panel_dir, cache_headers=False)]
    )

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="alex-entitymap-studio-panel",
        frontend_url_path=PANEL_URL_PATH,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=f"{panel_static_url}/alex-entitymap-studio-panel.js",
        embed_iframe=False,
        require_admin=True,
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Decharge l'entree de configuration."""
    return True
