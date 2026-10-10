"""ha_storage.add_to_shopping_list_by_name asks Storage's name lookup first.

Needs the `homeassistant` package (skipped in a bare pytest environment).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ha_storage import services as services_mod  # noqa: E402

PRODUCTS = [{"id": 4, "name": "Kevytmaito 1 l", "kind": "sku"},
            {"id": 9, "name": "Kaurajuoma", "kind": "sku"}]


class _FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    posts: list[tuple[str, dict]] = []
    lookup: dict | None = None
    lookup_status = 200  # Storage before 0.27.0 answers 422 ("lookup" is not a product id)

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        assert url.endswith("/api/products/lookup")
        return _FakeResponse({"product": _FakeClient.lookup}, _FakeClient.lookup_status)

    async def post(self, url, json=None):
        _FakeClient.posts.append((url, json))
        return _FakeResponse({"id": 1})


class _Services:
    def __init__(self):
        self.handlers = {}

    def has_service(self, domain, name):
        return False

    def async_register(self, domain, name, handler, schema=None, supports_response=None):
        self.handlers[name] = handler


@pytest.fixture
def add_by_name(monkeypatch):
    _FakeClient.posts = []
    _FakeClient.lookup = None
    _FakeClient.lookup_status = 200
    monkeypatch.setattr(services_mod.httpx, "AsyncClient", _FakeClient)

    async def _refresh():
        return None

    coordinator = SimpleNamespace(data={"products": PRODUCTS}, addon_url="http://storage:8100",
                                  async_request_refresh=_refresh)
    monkeypatch.setattr(services_mod, "_coordinators", lambda hass: [coordinator])
    hass = SimpleNamespace(services=_Services(), data={})
    services_mod.async_register_services(hass)
    return hass.services.handlers[services_mod.SERVICE_ADD_TO_SHOPPING_LIST_BY_NAME]


def _call(name):
    return SimpleNamespace(data={"name": name, "amount": 1, "note": ""})


async def test_storage_lookup_wins(add_by_name):
    _FakeClient.lookup = {"id": 21, "name": "Maito", "kind": "concept"}
    result = await add_by_name(_call("maito"))
    assert result == {"status": "added", "product": {"id": 21, "name": "Maito"}}
    assert _FakeClient.posts == [("http://storage:8100/api/shopping-list", {"product_id": 21, "amount": 1})]


async def test_without_a_lookup_hit_names_are_matched_as_before(add_by_name):
    result = await add_by_name(_call("kaurajuoma"))
    assert result == {"status": "added", "product": {"id": 9, "name": "Kaurajuoma"}}


async def test_an_older_storage_without_the_lookup_falls_back_to_names(add_by_name):
    _FakeClient.lookup_status = 422
    result = await add_by_name(_call("kaurajuoma"))
    assert result == {"status": "added", "product": {"id": 9, "name": "Kaurajuoma"}}
