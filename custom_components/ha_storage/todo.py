"""Storage – Todo entity backed by the shopping list."""

from __future__ import annotations

import logging

import httpx
from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import StorageCoordinator
from .resolver import describe_add_failure, resolve_product_by_name

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: StorageCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([StorageShoppingListTodo(coordinator, entry)])


class ShoppingListAddError(intent.IntentHandleError, ServiceValidationError):
    """A shopping-list add that failed because the name didn't resolve.

    Subclasses both so the message reaches every caller: HA's intent layer
    passes IntentErrors through to the Assist/LLM agent (any other exception is
    masked as "Error handling HassListAddItem"), and the REST API returns a
    ServiceValidationError as HTTP 400 with the message.
    """


class StorageShoppingListTodo(CoordinatorEntity, TodoListEntity):
    """The Storage shopping list, exposed as a HA todo entity.

    Read + check/uncheck + delete + create are supported. Shopping items must
    tie to a Storage product, so create resolves the free-text summary to an
    existing product with the same resolver as the
    `ha_storage.add_to_shopping_list_by_name` service. Ambiguous or unknown
    names raise ShoppingListAddError with a readable message instead of adding
    a product-less row. Callers that need a structured result should still use
    the by-name service.
    """

    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
    )

    def __init__(self, coordinator: StorageCoordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_shopping_list"
        self._attr_name = "Storage Shopping List"
        self._attr_icon = "mdi:cart"

    @property
    def todo_items(self) -> list[TodoItem]:
        items: list[TodoItem] = []
        product_names = {p["id"]: p["name"] for p in self.coordinator.data.get("products", [])}
        for it in self.coordinator.data.get("shopping", []):
            name = product_names.get(it["product_id"], it.get("ha_item_name") or "Unknown")
            amount = it.get("amount", 1)
            summary = f"{name} ×{amount:g}" if amount and amount != 1 else name
            status = TodoItemStatus.COMPLETED if it.get("done") else TodoItemStatus.NEEDS_ACTION
            items.append(
                TodoItem(
                    uid=str(it["id"]),
                    summary=summary,
                    status=status,
                    description=it.get("note") or None,
                )
            )
        return items

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Add an item to the shopping list by resolving its text to a product."""
        name = (item.summary or "").strip()
        if not name:
            raise ShoppingListAddError("The shopping list item needs a product name.")

        products = self.coordinator.data.get("products") if self.coordinator.data else None
        try:
            if products is None:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(f"{self.coordinator.addon_url}/api/products")
                    resp.raise_for_status()
                    products = resp.json()

            status, result = resolve_product_by_name(name, products)
            if status != "added":
                history = await self._async_purchase_history()
                raise ShoppingListAddError(
                    describe_add_failure(
                        name, status, result if status == "ambiguous" else None, history
                    )
                )

            payload: dict = {"product_id": result["id"], "amount": 1}
            if item.description:
                payload["note"] = item.description
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.coordinator.addon_url}/api/shopping-list", json=payload
                )
                resp.raise_for_status()
        except HomeAssistantError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(
                f"Failed to add '{name}' to the Storage shopping list: {exc}"
            ) from exc
        await self.coordinator.async_request_refresh()

    async def _async_purchase_history(self) -> list[dict]:
        """Past year's most-purchased products, or [] if unavailable."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.coordinator.addon_url}/api/stats/top-purchased",
                    params={"days": 365, "limit": 100},
                )
                resp.raise_for_status()
                return resp.json()
        except (httpx.HTTPError, OSError, ValueError) as err:
            _LOGGER.debug("Purchase history unavailable: %s", err)
            return []

    async def async_update_todo_item(self, item: TodoItem) -> None:
        done = item.status == TodoItemStatus.COMPLETED
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.put(
                    f"{self.coordinator.addon_url}/api/shopping-list/{item.uid}",
                    json={"done": done},
                )
            await self.coordinator.async_request_refresh()
        except Exception as exc:
            _LOGGER.warning("Failed to update shopping item %s: %s", item.uid, exc)

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                for uid in uids:
                    await client.delete(
                        f"{self.coordinator.addon_url}/api/shopping-list/{uid}"
                    )
            await self.coordinator.async_request_refresh()
        except Exception as exc:
            _LOGGER.warning("Failed to delete shopping items %s: %s", uids, exc)
