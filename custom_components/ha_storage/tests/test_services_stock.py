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


async def test_spoiled_is_passed_on_as_waste(consume):
    await consume(SimpleNamespace(data={"product_id": 728, "amount": 1, "spoiled": True}))
    assert _Client.posts == [{"product_id": 728, "amount": 1, "spoiled": True}]


@pytest.fixture
def add_stock(monkeypatch):
    """The add_stock handler with Storage's answers faked; yields (handler, posts)."""
    answers = {
        "/api/products": [
            {"id": 7, "name": "Valio maito 1l", "kind": "sku"},
            {"id": 8, "name": "Maito", "kind": "concept"},
        ],
        "/api/locations": [{"id": 1, "name": "Fridge", "description": "Jääkaappi"}],
        "/api/stats/top-purchased?days=365&limit=100": [],
    }
    posts: list = []

    async def fake_get(coord, path):
        return answers[path]

    async def fake_post(coord, path, payload):
        posts.append((path, payload))
        return {"best_before_date": "2026-11-20", "cleared_rows": [{"id": 1}]}

    async def _refresh():
        return None

    monkeypatch.setattr(services_mod, "_get", fake_get)
    monkeypatch.setattr(services_mod, "_post", fake_post)
    monkeypatch.setattr(
        services_mod, "_coordinators", lambda hass: [SimpleNamespace(async_request_refresh=_refresh)]
    )
    hass = SimpleNamespace(services=_Services(), data={})
    services_mod.async_register_services(hass)
    return hass.services.handlers[services_mod.SERVICE_ADD_STOCK], posts


async def test_a_bought_product_goes_into_stock(add_stock):
    from datetime import date

    handler, posts = add_stock
    result = await handler(
        SimpleNamespace(
            data={"name": "valio maito 1l", "amount": 2, "location": "fridge", "best_before": date(2026, 11, 20)}
        )
    )
    assert posts == [
        ("/api/stock/add", {"product_id": 7, "amount": 2, "location_id": 1, "best_before_date": "2026-11-20"})
    ]
    assert result["added"] == "Valio maito 1l"
    assert result["taken_off_shopping_list"] is True


async def test_a_vague_name_adds_nothing_and_lists_the_products(add_stock):
    # "maito" is a generic concept: stock needs the real product, so the agent gets to pick.
    handler, posts = add_stock
    with pytest.raises(ServiceValidationError, match="Valio maito 1l"):
        await handler(SimpleNamespace(data={"name": "maito", "amount": 1}))
    assert posts == []
