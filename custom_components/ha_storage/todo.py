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
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import StorageCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: StorageCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([StorageShoppingListTodo(coordinator, entry)])


class StorageShoppingListTodo(CoordinatorEntity, TodoListEntity):
    """The Storage shopping list, exposed as a HA todo entity.

    Read + check/uncheck + delete are supported. Adding new items from the HA
    todo UI is intentionally disabled — Storage shopping items must be tied to
    a product, so use the `ha_storage.add_to_shopping_list` service instead.
    """

    _attr_supported_features = (
        TodoListEntityFeature.UPDATE_TODO_ITEM | TodoListEntityFeature.DELETE_TODO_ITEM
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
