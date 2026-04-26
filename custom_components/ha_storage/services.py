"""Storage – HA service handlers."""

from __future__ import annotations

import logging

import httpx
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import StorageCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_ADD_TO_SHOPPING_LIST = "add_to_shopping_list"
SERVICE_CONSUME_STOCK = "consume_stock"
SERVICE_RUN_OPTIMIZE = "run_optimize"

_ADD_TO_SHOPPING_SCHEMA = vol.Schema(
    {
        vol.Required("product_id"): vol.Coerce(int),
        vol.Optional("amount", default=1): vol.Coerce(float),
        vol.Optional("unit_id"): vol.Any(None, vol.Coerce(int)),
        vol.Optional("note", default=""): cv.string,
    }
)

_CONSUME_STOCK_SCHEMA = vol.Schema(
    {
        vol.Required("product_id"): vol.Coerce(int),
        vol.Required("amount"): vol.Coerce(float),
    }
)

_RUN_OPTIMIZE_SCHEMA = vol.Schema(
    {
        vol.Optional("product_ids"): vol.All(cv.ensure_list, [vol.Coerce(int)]),
        vol.Optional("fresh_seed", default=False): cv.boolean,
    }
)


def _coordinators(hass: HomeAssistant) -> list[StorageCoordinator]:
    return [v for k, v in hass.data.get(DOMAIN, {}).items() if isinstance(v, StorageCoordinator)]


async def _post(coordinator: StorageCoordinator, path: str, payload: dict) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{coordinator.addon_url}{path}", json=payload)
        resp.raise_for_status()


def async_register_services(hass: HomeAssistant) -> None:
    """Register integration services. Idempotent."""
    if hass.services.has_service(DOMAIN, SERVICE_ADD_TO_SHOPPING_LIST):
        return

    async def handle_add(call: ServiceCall) -> None:
        payload = {k: v for k, v in call.data.items() if v is not None}
        for coord in _coordinators(hass):
            await _post(coord, "/api/shopping-list", payload)
            await coord.async_request_refresh()

    async def handle_consume(call: ServiceCall) -> None:
        payload = {"product_id": call.data["product_id"], "amount": call.data["amount"]}
        for coord in _coordinators(hass):
            await _post(coord, "/api/stock/consume", payload)
            await coord.async_request_refresh()

    async def handle_optimize(call: ServiceCall) -> None:
        payload: dict = {}
        if "product_ids" in call.data:
            payload["product_ids"] = call.data["product_ids"]
        if call.data.get("fresh_seed"):
            payload["fresh_seed"] = True
        for coord in _coordinators(hass):
            await _post(coord, "/api/ai/optimize", payload)
            await coord.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_ADD_TO_SHOPPING_LIST, handle_add, schema=_ADD_TO_SHOPPING_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CONSUME_STOCK, handle_consume, schema=_CONSUME_STOCK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RUN_OPTIMIZE, handle_optimize, schema=_RUN_OPTIMIZE_SCHEMA
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    for svc in (SERVICE_ADD_TO_SHOPPING_LIST, SERVICE_CONSUME_STOCK, SERVICE_RUN_OPTIMIZE):
        if hass.services.has_service(DOMAIN, svc):
            hass.services.async_remove(DOMAIN, svc)
