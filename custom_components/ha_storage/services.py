"""Storage – HA service handlers."""

from __future__ import annotations

import logging

import httpx
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import StorageCoordinator
from .resolver import resolve_product_by_name

_LOGGER = logging.getLogger(__name__)

SERVICE_ADD_TO_SHOPPING_LIST = "add_to_shopping_list"
SERVICE_ADD_TO_SHOPPING_LIST_BY_NAME = "add_to_shopping_list_by_name"
SERVICE_CONSUME_STOCK = "consume_stock"
SERVICE_RUN_OPTIMIZE = "run_optimize"
SERVICE_GET_WEEKLY_DIGEST = "get_weekly_digest"

_ADD_TO_SHOPPING_SCHEMA = vol.Schema(
    {
        vol.Required("product_id"): vol.Coerce(int),
        vol.Optional("amount", default=1): vol.Coerce(float),
        vol.Optional("unit_id"): vol.Any(None, vol.Coerce(int)),
        vol.Optional("note", default=""): cv.string,
    }
)

_ADD_BY_NAME_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("amount", default=1): vol.Coerce(float),
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

    async def handle_add_by_name(call: ServiceCall) -> dict:
        """Resolve a product name to an existing Storage product and add it.

        Fetches products from the coordinator cache when available (else
        GET /api/products), resolves the name with the pure resolver, and on a
        single match reuses the standard /api/shopping-list add path. Never
        auto-creates products: returns "ambiguous" or "not_found" so the caller
        can chain scraper.search_products + scraper.add_product and retry.
        """
        coords = _coordinators(hass)
        if not coords:
            return {"status": "not_found", "candidates": []}
        coord = coords[0]

        products = coord.data.get("products") if coord.data else None
        if products is None:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(f"{coord.addon_url}/api/products")
                    resp.raise_for_status()
                    products = resp.json()
            except (httpx.HTTPError, OSError) as err:
                _LOGGER.warning(
                    "add_to_shopping_list_by_name: product fetch failed: %s", err
                )
                return {"status": "error", "message": "Storage products could not be fetched"}

        status, result = resolve_product_by_name(call.data["name"], products)

        if status == "ambiguous":
            return {"status": "ambiguous", "candidates": result}
        if status == "not_found":
            return {"status": "not_found", "candidates": []}

        payload = {
            "product_id": result["id"],
            "amount": call.data["amount"],
        }
        if call.data.get("note"):
            payload["note"] = call.data["note"]
        await _post(coord, "/api/shopping-list", payload)
        await coord.async_request_refresh()
        return {"status": "added", "product": result}

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

    async def handle_digest(call: ServiceCall) -> dict:
        """Return the weekly digest for the first configured coordinator.

        Multi-coordinator setups are rare; if more than one is registered, the
        first one wins. Users wanting per-instance digests can identify which
        coordinator's data they want via the standard HA target selector at the
        automation level (out of scope for this service).
        """
        coords = _coordinators(hass)
        if not coords:
            return {"error": "no_coordinator"}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{coords[0].addon_url}/api/stats/digest")
            if resp.status_code != 200:
                return {"error": f"http_{resp.status_code}"}
            return resp.json()

    hass.services.async_register(
        DOMAIN, SERVICE_ADD_TO_SHOPPING_LIST, handle_add, schema=_ADD_TO_SHOPPING_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_TO_SHOPPING_LIST_BY_NAME,
        handle_add_by_name,
        schema=_ADD_BY_NAME_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CONSUME_STOCK, handle_consume, schema=_CONSUME_STOCK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RUN_OPTIMIZE, handle_optimize, schema=_RUN_OPTIMIZE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_WEEKLY_DIGEST,
        handle_digest,
        supports_response=SupportsResponse.ONLY,
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    for svc in (
        SERVICE_ADD_TO_SHOPPING_LIST,
        SERVICE_ADD_TO_SHOPPING_LIST_BY_NAME,
        SERVICE_CONSUME_STOCK,
        SERVICE_RUN_OPTIMIZE,
        SERVICE_GET_WEEKLY_DIGEST,
    ):
        if hass.services.has_service(DOMAIN, svc):
            hass.services.async_remove(DOMAIN, svc)
