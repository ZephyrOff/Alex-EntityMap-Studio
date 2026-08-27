"""Flux de configuration pour Alex EntityMap Studio.

Instance unique, sans champ a saisir -- l'integration n'a besoin d'aucune
information de connexion, elle lit uniquement la configuration et les
registres deja presents dans cette instance HA.
"""
from __future__ import annotations

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN


class AlexEntityMapStudioConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Gere la configuration d'Alex EntityMap Studio."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        """Etape unique : confirmation, instance unique forcee."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(title="Alex EntityMap Studio", data={})

        return self.async_show_form(step_id="user")
