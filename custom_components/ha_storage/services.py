"""Storage – HA service handlers."""

from __future__ import annotations

import logging
from datetime import timedelta

import httpx
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util
from homeassistant.util.ssl import get_default_context

from .const import DOMAIN
from .coordinator import StorageCoordinator
from .lookup import async_lookup_product
from .resolver import (
    describe_add_failure,
    find_stock,
    match_location,
    resolve_product_by_name,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_ADD_TO_SHOPPING_LIST = "add_to_shopping_list"
SERVICE_ADD_TO_SHOPPING_LIST_BY_NAME = "add_to_shopping_list_by_name"
SERVICE_ADD_STOCK = "add_stock"
SERVICE_CONSUME_STOCK = "consume_stock"
SERVICE_FIND_STOCK = "find_stock"
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
        vol.Optional("location"): cv.string,
        vol.Optional("spoiled", default=False): cv.boolean,
    }
)

_ADD_STOCK_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("amount", default=1): vol.All(
            vol.Coerce(float), vol.Range(min=0, min_included=False)
        ),
        vol.Optional("location"): cv.string,
        vol.Optional("best_before"): cv.date,
    }
)

_FIND_STOCK_SCHEMA = vol.Schema(
    {
        vol.Optional("name"): cv.string,
        vol.Optional("location"): cv.string,
        vol.Optional("expiring_within_days"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=365)
        ),
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


async def _post(coordinator: StorageCoordinator, path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(verify=get_default_context(), timeout=15) as client:
        resp = await client.post(f"{coordinator.addon_url}{path}", json=payload)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:  # an empty body
            return {}


async def _get(coordinator: StorageCoordinator, path: str) -> list[dict]:
    """GET a Storage list. A failure is a HomeAssistantError, the only kind a
    voice assistant is told about instead of losing the whole reply."""
    try:
        async with httpx.AsyncClient(verify=get_default_context(), timeout=15) as client:
            resp = await client.get(f"{coordinator.addon_url}{path}")
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, OSError, ValueError) as err:
        raise HomeAssistantError(f"Storage could not be reached: {err}") from err


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

        Asks Storage's name lookup first (aliases, then concept names, so
        "maito" adds the concept and the list shows the usual SKU as its buy
        hint). Otherwise fetches products from the coordinator cache when
        available (else GET /api/products), resolves the name with the pure
        resolver, and on a single match reuses the standard /api/shopping-list
        add path. Never auto-creates products: returns "ambiguous" or
        "not_found" so the caller can chain scraper.search_products +
        scraper.add_product and retry.
        """
        coords = _coordinators(hass)
        if not coords:
            return {"status": "not_found", "candidates": []}
        coord = coords[0]

        result = await async_lookup_product(coord.addon_url, call.data["name"])
        if result is None:
            products = coord.data.get("products") if coord.data else None
            if products is None:
                try:
                    async with httpx.AsyncClient(verify=get_default_context(), timeout=15) as client:
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

    async def handle_consume(call: ServiceCall) -> dict:
        payload = {"product_id": call.data["product_id"], "amount": call.data["amount"]}
        if call.data.get("spoiled"):
            payload["spoiled"] = True  # counted as waste, not as eaten
        where = call.data.get("location")
        result: dict = {}
        for coord in _coordinators(hass):
            if where:
                loc = match_location(where, await _get(coord, "/api/locations"))
                if loc is None:
                    raise ServiceValidationError(f"Unknown location '{where}'.")
                # shortcut: Storage before 0.29.3 ignores location_id and takes the
                # oldest anywhere; check the health version if old add-ons matter.
                payload["location_id"] = loc["id"]
            try:
                result = await _post(coord, "/api/stock/consume", payload)
            except httpx.HTTPStatusError as err:
                if err.response.status_code != 400:
                    raise
                # Storage's 400: there was nothing of it to take.
                raise ServiceValidationError(
                    f"Nothing consumed: product {payload['product_id']} is not in"
                    f" stock{f' in {where}' if where else ''}."
                ) from err
            await coord.async_request_refresh()
        return result

    async def handle_find_stock(call: ServiceCall) -> dict:
        coords = _coordinators(hass)
        if not coords:
            return {"error": "Storage is not set up."}
        days = call.data.get("expiring_within_days")
        return find_stock(
            await _get(coords[0], "/api/stock/entries"),
            await _get(coords[0], "/api/locations"),
            call.data.get("name"),
            call.data.get("location"),
            None if days is None else (dt_util.now().date() + timedelta(days=days)).isoformat(),
        )

    async def handle_add_stock(call: ServiceCall) -> dict:
        """Put a bought product into stock, found by name the way a shopping add is."""
        coords = _coordinators(hass)
        if not coords:
            raise HomeAssistantError("Storage is not set up.")
        coord = coords[0]
        name = call.data["name"]
        # Stock sits on real products, never on a generic concept ("Maito").
        skus = [p for p in await _get(coord, "/api/products") if p.get("kind") != "concept"]
        status, match = resolve_product_by_name(name, skus)
        if status != "added":
            try:
                history = await _get(coord, "/api/stats/top-purchased?days=365&limit=100")
            except HomeAssistantError:
                history = []
            raise ServiceValidationError(describe_add_failure(name, status, match, history))

        payload = {"product_id": match["id"], "amount": call.data["amount"]}
        where = call.data.get("location")
        if where:
            locations = await _get(coord, "/api/locations")
            loc = match_location(where, locations)
            if loc is None:
                raise ServiceValidationError(
                    f"Unknown location '{where}'. Locations:"
                    f" {', '.join(l['name'] for l in locations)}."
                )
            payload["location_id"] = loc["id"]
        if call.data.get("best_before"):
            payload["best_before_date"] = call.data["best_before"].isoformat()
        try:
            lot = await _post(coord, "/api/stock/add", payload)
        except (httpx.HTTPError, OSError) as err:
            raise HomeAssistantError(f"Storage did not add the stock: {err}") from err
        await coord.async_request_refresh()
        return {
            "added": match["name"],
            "amount": payload["amount"],
            "best_before": lot.get("best_before_date"),
            "taken_off_shopping_list": bool(lot.get("cleared_rows")),
        }

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
        async with httpx.AsyncClient(verify=get_default_context(), timeout=15) as client:
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
        DOMAIN,
        SERVICE_CONSUME_STOCK,
        handle_consume,
        schema=_CONSUME_STOCK_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_STOCK,
        handle_add_stock,
        schema=_ADD_STOCK_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_FIND_STOCK,
        handle_find_stock,
        schema=_FIND_STOCK_SCHEMA,
        supports_response=SupportsResponse.ONLY,
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
        SERVICE_ADD_STOCK,
        SERVICE_CONSUME_STOCK,
        SERVICE_FIND_STOCK,
        SERVICE_RUN_OPTIMIZE,
        SERVICE_GET_WEEKLY_DIGEST,
    ):
        if hass.services.has_service(DOMAIN, svc):
            hass.services.async_remove(DOMAIN, svc)
