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

from . import scanner
from .const import DOMAIN, PANEL_ICON, PANEL_TITLE, PANEL_URL_PATH

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Initialise l'integration : commande websocket + panel."""
    hass.data.setdefault(DOMAIN, {})

    if not hass.data[DOMAIN].get("ws_registered"):
        websocket_api.async_register_command(hass, websocket_get_map)
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
