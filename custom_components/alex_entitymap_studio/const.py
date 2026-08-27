"""Constantes pour Alex EntityMap Studio."""

DOMAIN = "alex_entitymap_studio"

PANEL_URL_PATH = "alex-entitymap-studio"
PANEL_TITLE = "Alex EntityMap Studio"
PANEL_ICON = "mdi:sitemap"

# Domaines dont l'attribut last_triggered (plutot que last_changed) est le
# meilleur indicateur de "derniere utilisation" -- last_changed y refleterait
# des transitions d'etat internes (running/idle) qui ne correspondent pas
# forcement au moment ou l'utilisateur/une automatisation l'a reellement
# declenche.
LAST_TRIGGERED_DOMAINS = ("automation", "script")

# Emplacements par defaut a scanner pour les automatisations/scripts. Un
# utilisateur avec une organisation de fichiers differente (splits
# personnalises via !include_dir_*) peut en ajouter d'autres via la config
# de l'integration.
DEFAULT_CONFIG_FILES = ("automations.yaml", "scripts.yaml", "configuration.yaml")

# Emplacement des dashboards geres par l'interface (stockage HA natif).
LOVELACE_STORAGE_GLOB = ".storage/lovelace*"
