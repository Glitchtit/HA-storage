"""Storage – DataUpdateCoordinator polling the add-on REST API."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import httpx
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_EXPIRING_WITHIN_DAYS

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=5)


class StorageCoordinator(DataUpdateCoordinator):
    """Fetch aggregated state from the Storage add-on."""

    def __init__(
        self,
        hass: HomeAssistant,
        addon_url: str,
        expiring_within_days: int = DEFAULT_EXPIRING_WITHIN_DAYS,
    ) -> None:
        super().__init__(hass, _LOGGER, name="Storage", update_interval=SCAN_INTERVAL)
        self.addon_url = addon_url.rstrip("/")
        self.expiring_within_days = expiring_within_days

    async def _async_update_data(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Reconcile auto-added shopping rows against current stock
                # before fetching the list, so the snapshot we expose to HA
                # reflects the latest min_stock thresholds.
                try:
                    await client.post(f"{self.addon_url}/api/shopping-list/sync")
                except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
                    _LOGGER.debug("Shopping auto-sync skipped: %s", exc)

                results = await asyncio.gather(
                    client.get(f"{self.addon_url}/api/health"),
                    client.get(f"{self.addon_url}/api/products"),
                    client.get(f"{self.addon_url}/api/stock"),
                    client.get(
                        f"{self.addon_url}/api/stock/entries",
                        params={"expiring_within_days": self.expiring_within_days},
                    ),
                    client.get(f"{self.addon_url}/api/stock/entries", params={"expired": "true"}),
                    client.get(f"{self.addon_url}/api/shopping-list"),
                    client.get(f"{self.addon_url}/api/barcode-queue"),
                    client.get(f"{self.addon_url}/api/ai/optimize"),
                    client.get(f"{self.addon_url}/api/stats/digest"),
                    return_exceptions=True,
                )
                # The digest endpoint is optional — addons older than 0.12.0 don't
                # have it. Don't fail the whole refresh on its absence; just leave
                # the digest-derived sensors at their defaults.
                digest_result = results[-1]
                if isinstance(digest_result, Exception):
                    _LOGGER.debug("Digest fetch failed (older add-on?): %s", digest_result)
                    digest_data = None
                    results = results[:-1] + (None,)
                else:
                    digest_data = (
                        digest_result.json() if digest_result.status_code == 200 else None
                    )

                for r in results[:-1]:
                    if isinstance(r, Exception):
                        raise r

                health, products, stock, expiring, expired, shopping, barcodes, optimize, _ = results

                products_data = products.json() if products.status_code == 200 else []
                stock_data = stock.json() if stock.status_code == 200 else []
                expiring_data = expiring.json() if expiring.status_code == 200 else []
                expired_data = expired.json() if expired.status_code == 200 else []
                shopping_data = shopping.json() if shopping.status_code == 200 else []
                barcodes_data = barcodes.json() if barcodes.status_code == 200 else []
                optimize_data = (
                    optimize.json()
                    if optimize.status_code == 200
                    else {"status": "idle", "task_id": None}
                )

                low_stock = sum(
                    1
                    for s in stock_data
                    if s.get("min_stock_amount", 0) > 0
                    and s.get("amount", 0) <= s.get("min_stock_amount", 0)
                )
                shopping_pending = sum(1 for i in shopping_data if not i.get("done"))

                return {
                    "health": health.json() if health.status_code == 200 else {},
                    "products": products_data,
                    "stock": stock_data,
                    "expiring": expiring_data,
                    "expired": expired_data,
                    "shopping": shopping_data,
                    "barcodes": barcodes_data,
                    "optimize": optimize_data,
                    "low_stock_count": low_stock,
                    "shopping_pending_count": shopping_pending,
                    "digest": digest_data,
                }
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            raise UpdateFailed(
                f"Cannot connect to Storage add-on at {self.addon_url}. "
                f"Check the URL in the integration settings. Error: {exc}"
            ) from exc
        except Exception as exc:
            raise UpdateFailed(f"Error fetching storage data: {exc}") from exc
