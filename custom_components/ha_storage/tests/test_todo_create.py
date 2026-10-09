"""Tests for StorageShoppingListTodo.async_create_todo_item.

Needs the `homeassistant` package (skipped in a bare pytest environment). The
add-on HTTP API is faked by swapping the module's httpx.AsyncClient.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

# Import the integration as a package so its relative imports resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from homeassistant.components.todo import (  # noqa: E402
    TodoItem,
    TodoItemStatus,
    TodoListEntityFeature,
)
from homeassistant.exceptions import ServiceValidationError  # noqa: E402
from homeassistant.helpers import intent  # noqa: E402

from ha_storage import todo as todo_mod  # noqa: E402

PRODUCTS = [
    {"id": 1, "name": "Valio Eila laktoositon kevytmaitojuoma 1l"},
    {"id": 2, "name": "Palmolive Naturals nestesaippua 500ml Olive Milk täyttöpussi"},
    {"id": 3, "name": "Stratos Milky Joy suklaapatukka 35g"},
    {"id": 4, "name": "Kaurajuoma"},
]


class _FakeResponse:
    def __init__(self, payload=None):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


HISTORY = [
    {"product_id": 1, "product_name": PRODUCTS[0]["name"], "total_amount": 15.0, "event_count": 15},
]


class _FakeClient:
    posts: list[tuple[str, dict]] = []
    gets: list[str] = []
    history_fails = False

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        _FakeClient.gets.append(url)
        if _FakeClient.history_fails:
            raise todo_mod.httpx.ConnectError("down")
        return _FakeResponse(HISTORY)

    async def post(self, url, json=None):
        _FakeClient.posts.append((url, json))
        return _FakeResponse({"id": 99})


@pytest.fixture
def entity(monkeypatch):
    _FakeClient.posts = []
    _FakeClient.gets = []
    _FakeClient.history_fails = False
    monkeypatch.setattr(todo_mod.httpx, "AsyncClient", _FakeClient)
    refreshed = []

    async def _refresh():
        refreshed.append(True)

    coordinator = SimpleNamespace(
        data={"products": PRODUCTS, "shopping": []},
        addon_url="http://storage:8100",
        async_request_refresh=_refresh,
    )
    ent = todo_mod.StorageShoppingListTodo(coordinator, SimpleNamespace(entry_id="e1"))
    ent.refreshed = refreshed
    return ent


def test_advertises_create(entity):
    assert entity.supported_features & TodoListEntityFeature.CREATE_TODO_ITEM


async def test_create_resolved_name_posts_product(entity):
    await entity.async_create_todo_item(
        TodoItem(summary="Kaurajuoma", status=TodoItemStatus.NEEDS_ACTION)
    )
    assert _FakeClient.posts == [
        ("http://storage:8100/api/shopping-list", {"product_id": 4, "amount": 1})
    ]
    assert entity.refreshed


async def test_create_ambiguous_raises_readable_error(entity):
    with pytest.raises(todo_mod.ShoppingListAddError) as err:
        await entity.async_create_todo_item(
            TodoItem(summary="Milk", status=TodoItemStatus.NEEDS_ACTION)
        )
    # Must survive HA's intent layer (IntentError bubbles, others are masked)
    # and the REST API (ServiceValidationError -> HTTP 400 with the message).
    assert isinstance(err.value, intent.IntentHandleError)
    assert isinstance(err.value, ServiceValidationError)
    assert "Milk" in str(err.value)
    assert "Stratos Milky Joy" in str(err.value)
    assert _FakeClient.posts == []


async def test_create_unknown_raises_readable_error(entity):
    with pytest.raises(todo_mod.ShoppingListAddError) as err:
        await entity.async_create_todo_item(
            TodoItem(summary="Dragon fruit", status=TodoItemStatus.NEEDS_ACTION)
        )
    assert "Dragon fruit" in str(err.value)
    assert _FakeClient.posts == []


async def test_unknown_name_offers_usual_purchases(entity):
    with pytest.raises(todo_mod.ShoppingListAddError) as err:
        await entity.async_create_todo_item(
            TodoItem(summary="Dragon fruit", status=TodoItemStatus.NEEDS_ACTION)
        )
    assert _FakeClient.gets == ["http://storage:8100/api/stats/top-purchased"]
    assert "Valio Eila laktoositon kevytmaitojuoma 1l (15×)" in str(err.value)


async def test_history_fetch_failure_still_gives_readable_error(entity):
    _FakeClient.history_fails = True
    with pytest.raises(todo_mod.ShoppingListAddError) as err:
        await entity.async_create_todo_item(
            TodoItem(summary="Dragon fruit", status=TodoItemStatus.NEEDS_ACTION)
        )
    assert "Dragon fruit" in str(err.value)
