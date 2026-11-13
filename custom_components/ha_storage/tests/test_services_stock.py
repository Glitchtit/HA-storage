"""ha_storage.consume_stock narrows to a named location and explains a refusal.

Needs the `homeassistant` package (skipped in a bare pytest environment).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip("homeassistant")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from homeassistant.exceptions import ServiceValidationError  # noqa: E402

from ha_storage import services as services_mod  # noqa: E402


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload, self.status_code = payload, status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("refused", request=None, response=self)

    def json(self):
        return self._payload


class _Client:
    posts: list[dict] = []
    consume_status = 200

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        return _Response([{"id": 3, "name": "Freezer", "description": "Pakastin"}])

    async def post(self, url, json=None):
        _Client.posts.append(json)
        return _Response({"consumed": json["amount"], "remaining_to_consume": 0}, _Client.consume_status)


class _Services:
    def __init__(self):
        self.handlers = {}

    def has_service(self, domain, name):
        return False

    def async_register(self, domain, name, handler, schema=None, supports_response=None):
        self.handlers[name] = handler


@pytest.fixture
def consume(monkeypatch):
    _Client.posts, _Client.consume_status = [], 200
    monkeypatch.setattr(services_mod.httpx, "AsyncClient", _Client)

    async def _refresh():
        return None

    coordinator = SimpleNamespace(addon_url="http://storage:8100", async_request_refresh=_refresh)
    monkeypatch.setattr(services_mod, "_coordinators", lambda hass: [coordinator])
    hass = SimpleNamespace(services=_Services(), data={})
    services_mod.async_register_services(hass)
    return hass.services.handlers[services_mod.SERVICE_CONSUME_STOCK]


async def test_a_location_name_becomes_its_id(consume):
    result = await consume(SimpleNamespace(data={"product_id": 728, "amount": 4, "location": "freezer"}))
    assert result == {"consumed": 4, "remaining_to_consume": 0}
    assert _Client.posts == [{"product_id": 728, "amount": 4, "location_id": 3}]


async def test_an_unknown_location_consumes_nothing(consume):
    with pytest.raises(ServiceValidationError, match="Unknown location 'garage'"):
        await consume(SimpleNamespace(data={"product_id": 728, "amount": 4, "location": "garage"}))
    assert _Client.posts == []


async def test_nothing_in_stock_is_a_readable_error(consume):
    _Client.consume_status = 400
    with pytest.raises(ServiceValidationError, match="not in stock in freezer"):
        await consume(SimpleNamespace(data={"product_id": 728, "amount": 1, "location": "freezer"}))
