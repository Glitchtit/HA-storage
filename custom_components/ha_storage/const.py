"""Storage – HA custom integration constants."""

DOMAIN = "ha_storage"

CONF_ADDON_URL = "addon_url"
CONF_EXPIRING_WITHIN_DAYS = "expiring_within_days"

DEFAULT_ADDON_URL = "http://homeassistant.local:8099"
DEFAULT_EXPIRING_WITHIN_DAYS = 7

ADDON_SLUG = "ha_storage"
SUPERVISOR_ADDON_API = f"http://supervisor/addons/{ADDON_SLUG}/info"

PANEL_TITLE = "Storage"
PANEL_ICON = "mdi:database"
PANEL_URL = "ha-storage"
