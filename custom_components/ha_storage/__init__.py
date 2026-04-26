"""Storage – HA custom integration setup."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ADDON_URL,
    CONF_EXPIRING_WITHIN_DAYS,
    DEFAULT_EXPIRING_WITHIN_DAYS,
    DOMAIN,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL,
)
from .coordinator import StorageCoordinator
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "todo"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    addon_url = entry.data.get(CONF_ADDON_URL)
    expiring_within_days = int(
        entry.data.get(CONF_EXPIRING_WITHIN_DAYS, DEFAULT_EXPIRING_WITHIN_DAYS)
    )
    coordinator = StorageCoordinator(hass, addon_url, expiring_within_days)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    if not hass.data[DOMAIN].get("panel_registered"):
        try:
            hass.components.frontend.async_register_built_in_panel(
                "iframe",
                PANEL_TITLE,
                PANEL_ICON,
                PANEL_URL,
                {"url": addon_url},
                require_admin=False,
            )
            hass.data[DOMAIN]["panel_registered"] = True
        except Exception as exc:
            _LOGGER.warning("Could not register Storage panel: %s", exc)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not any(k for k in hass.data[DOMAIN] if k != "panel_registered"):
            async_unregister_services(hass)
    return unload_ok
