"""Storage – Sensor entities for HA."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import StorageCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: StorageCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            StorageProductsTotalSensor(coordinator, entry),
            StorageLowStockSensor(coordinator, entry),
            StorageExpiringSoonSensor(coordinator, entry),
            StorageExpiredSensor(coordinator, entry),
            StorageShoppingPendingSensor(coordinator, entry),
            StorageBarcodeQueueSensor(coordinator, entry),
            StorageOptimizeStatusSensor(coordinator, entry),
        ]
    )


class _Base(CoordinatorEntity, SensorEntity):
    _key: str = ""
    _name: str = ""
    _icon: str | None = None
    _unit: str | None = None

    def __init__(self, coordinator: StorageCoordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{self._key}"
        self._attr_name = self._name
        if self._icon:
            self._attr_icon = self._icon
        if self._unit:
            self._attr_native_unit_of_measurement = self._unit


class StorageProductsTotalSensor(_Base):
    _key = "products_total"
    _name = "Storage Products Total"
    _icon = "mdi:package-variant"

    @property
    def native_value(self):
        return len(self.coordinator.data.get("products", []))


class StorageLowStockSensor(_Base):
    _key = "low_stock"
    _name = "Storage Low Stock"
    _icon = "mdi:alert-decagram"

    @property
    def native_value(self):
        return self.coordinator.data.get("low_stock_count", 0)


class StorageExpiringSoonSensor(_Base):
    _key = "expiring_soon"
    _name = "Storage Expiring Soon"
    _icon = "mdi:clock-alert"

    @property
    def native_value(self):
        return len(self.coordinator.data.get("expiring", []))

    @property
    def extra_state_attributes(self):
        return {"days": self.coordinator.expiring_within_days}


class StorageExpiredSensor(_Base):
    _key = "expired"
    _name = "Storage Expired"
    _icon = "mdi:calendar-remove"

    @property
    def native_value(self):
        return len(self.coordinator.data.get("expired", []))


class StorageShoppingPendingSensor(_Base):
    _key = "shopping_pending"
    _name = "Storage Shopping Pending"
    _icon = "mdi:cart-outline"

    @property
    def native_value(self):
        return self.coordinator.data.get("shopping_pending_count", 0)


class StorageBarcodeQueueSensor(_Base):
    _key = "barcode_queue"
    _name = "Storage Barcode Queue"
    _icon = "mdi:barcode-scan"

    @property
    def native_value(self):
        return len(self.coordinator.data.get("barcodes", []))


class StorageOptimizeStatusSensor(_Base):
    _key = "optimize_status"
    _name = "Storage Optimize Status"
    _icon = "mdi:auto-fix"

    @property
    def native_value(self):
        return self.coordinator.data.get("optimize", {}).get("status", "idle")

    @property
    def extra_state_attributes(self):
        opt = self.coordinator.data.get("optimize", {})
        return {
            "task_id": opt.get("task_id"),
            "started_at": opt.get("started_at"),
            "finished_at": opt.get("finished_at"),
            "updated": opt.get("updated"),
            "mode": opt.get("mode"),
        }
