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

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError  # noqa: E402

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
    down = False

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        return _Response([{"id": 3, "name": "Freezer", "description": "Pakastin"}])

    async def post(self, url, json=None):
        if _Client.down:
            raise httpx.ConnectError("Storage is restarting")
        _Client.posts.append(json)
        if url.endswith("/undo"):
            return _Response({"product": "Pepsi Max", "restored": 3}, _Client.consume_status)
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
    _Client.posts, _Client.consume_status, _Client.down = [], 200, False
    monkeypatch.setattr(services_mod.httpx, "AsyncClient", _Client)

    async def _refresh():
        return None

    coordinator = SimpleNamespace(addon_url="http://storage:8100", async_request_refresh=_refresh)
    monkeypatch.setattr(services_mod, "_coordinators", lambda hass: [coordinator])
    hass = SimpleNamespace(services=_Services(), data={})
    services_mod.async_register_services(hass)
    handler = hass.services.handlers[services_mod.SERVICE_CONSUME_STOCK]
    handler.undo = hass.services.handlers[services_mod.SERVICE_UNDO_CONSUME]
    return handler


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


async def test_storage_down_is_an_error_the_assistant_can_read(consume):
    _Client.down = True
    with pytest.raises(HomeAssistantError, match="Storage did not consume"):
        await consume(SimpleNamespace(data={"product_id": 728, "amount": 1}))


async def test_no_storage_set_up_is_never_a_silent_success(consume, monkeypatch):
    monkeypatch.setattr(services_mod, "_coordinators", lambda hass: [])
    with pytest.raises(HomeAssistantError, match="not set up"):
        await consume(SimpleNamespace(data={"product_id": 728, "amount": 1}))


@pytest.mark.parametrize("amount", [0, -2])
def test_an_amount_of_zero_or_less_is_refused_with_a_reason(amount):
    with pytest.raises(services_mod.vol.Invalid, match="do not consume"):
        services_mod._CONSUME_STOCK_SCHEMA({"product_id": 728, "amount": amount})


async def test_undo_puts_back_the_latest_consume(consume):
    result = await consume.undo(SimpleNamespace(data={"product_id": 728}))
    assert result == {"product": "Pepsi Max", "restored": 3}
    assert _Client.posts == [{"product_id": 728}]


async def test_undo_with_nothing_to_undo_says_so(consume):
    _Client.consume_status = 404
    with pytest.raises(ServiceValidationError, match="nothing to undo"):
        await consume.undo(SimpleNamespace(data={}))


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


@pytest.fixture
def undo_add(monkeypatch):
    """undo_add_stock with lot 5 (2 of Valio maito) bought `minutes_ago`."""
    from datetime import datetime, timedelta, timezone

    state = {"minutes_ago": 5, "left": 2, "deleted": []}

    async def fake_get(coord, path):
        if path == "/api/stock/entries":
            return [{"id": 5, "product_id": 7, "product_name": "Valio maito 1l", "amount": state["left"]}]
        bought = datetime.now(timezone.utc) - timedelta(minutes=state["minutes_ago"])
        return [{"id": 99, "event_type": "purchase", "stock_id": 5, "amount": 2,
                 "created_at": bought.strftime("%Y-%m-%d %H:%M:%S")}]

    class _DeleteClient(_Client):
        async def delete(self, url):
            state["deleted"].append(url.removeprefix("http://storage:8100"))
            return _Response(None, 204)

    async def _refresh():
        return None

    monkeypatch.setattr(services_mod, "_get", fake_get)
    monkeypatch.setattr(services_mod.httpx, "AsyncClient", _DeleteClient)
    monkeypatch.setattr(
        services_mod, "_coordinators",
        lambda hass: [SimpleNamespace(addon_url="http://storage:8100", async_request_refresh=_refresh)],
    )
    hass = SimpleNamespace(services=_Services(), data={})
    services_mod.async_register_services(hass)
    return hass.services.handlers[services_mod.SERVICE_UNDO_ADD_STOCK], state


async def test_undo_add_removes_the_lot_and_its_purchase(undo_add):
    handler, state = undo_add
    result = await handler(SimpleNamespace(data={"stock_id": 5}))
    assert state["deleted"] == ["/api/stock/5", "/api/history/99"]
    assert result == {"removed": "Valio maito 1l", "amount": 2}


@pytest.mark.parametrize(("minutes_ago", "left", "reason"), [(90, 2, "last hour"), (5, 1, "already used")])
async def test_undo_add_refuses_old_or_used_stock(undo_add, minutes_ago, left, reason):
    handler, state = undo_add
    state["minutes_ago"], state["left"] = minutes_ago, left
    with pytest.raises(ServiceValidationError, match=reason):
        await handler(SimpleNamespace(data={"stock_id": 5}))
    assert state["deleted"] == []
