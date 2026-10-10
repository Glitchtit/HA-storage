"""Storage's own name lookup for by-name adds (aliases and concepts first)."""

from __future__ import annotations

import logging

import httpx
from homeassistant.util.ssl import get_default_context

_LOGGER = logging.getLogger(__name__)


async def async_lookup_product(addon_url: str, name: str) -> dict | None:
    """{id, name} of the product Storage binds `name` to — a concept by alias
    or name, else one product with exactly that name — or None when nothing
    matches or the add-on predates GET /api/products/lookup (Storage 0.27.0)."""
    try:
        async with httpx.AsyncClient(verify=get_default_context(), timeout=10) as client:
            resp = await client.get(f"{addon_url}/api/products/lookup", params={"name": name})
            if resp.status_code >= 400:
                return None
            product = resp.json().get("product")
    except (httpx.HTTPError, OSError, ValueError) as err:
        _LOGGER.debug("Product lookup for %r unavailable: %s", name, err)
        return None
    if not product:
        return None
    return {"id": product["id"], "name": product["name"]}
