"""Setting up the integration registers no sidebar panel of its own.

The Storage add-on's ingress panel is the UI. The integration's old iframe
panel pointed at the add-on's internal address, which a browser cannot reach,
and the call it used no longer exists in Home Assistant 2026.10.
Needs the `homeassistant` package (skipped in a bare pytest environment).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import ha_storage  # noqa: E402
from ha_storage.const import DOMAIN  # noqa: E402


class _Coordinator:
    def __init__(self, hass, addon_url, expiring_within_days):
        self.addon_url = addon_url

    async def async_config_entry_first_refresh(self):
        return None


async def test_setup_registers_no_panel_and_logs_no_panel_warning(monkeypatch, caplog):
    monkeypatch.setattr(ha_storage, "StorageCoordinator", _Coordinator)
    monkeypatch.setattr(ha_storage, "async_register_services", lambda hass: None)

    async def _forward(entry, platforms):
        return None

    hass = SimpleNamespace(data={}, config_entries=SimpleNamespace(async_forward_entry_setups=_forward))
    await ha_storage.async_setup(hass, {})
    entry = SimpleNamespace(data={"addon_url": "http://0a9ed235-ha-storage:8099"}, entry_id="e1")
    with caplog.at_level(logging.WARNING):
        assert await ha_storage.async_setup_entry(hass, entry) is True
    assert "panel" not in caplog.text.lower()
    assert list(hass.data[DOMAIN]) == ["e1"]
