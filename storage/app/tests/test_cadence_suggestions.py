"""Tests for purchase-cadence shopping suggestions.

Covers ``compute_cadence_suggestions`` via the ``GET /api/shopping-list/
cadence-suggestions`` endpoint. Purchase history is seeded directly into
``stock_history`` with ``created_at`` offsets relative to ``now`` so the
±1-week window math is deterministic.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Set up test env before importing app
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "products"), exist_ok=True)
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "recipes"), exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from main import app, get_connection

client = TestClient(app)


def _unit_id(abbrev: str = "kpl") -> int:
    units = {u["abbreviation"]: u["id"] for u in client.get("/api/units").json()}
    return units[abbrev]


def _make_product(name: str, *, min_stock: float = 0) -> int:
    r = client.post("/api/products", json={
        "name": name,
        "unit_id": _unit_id(),
        "min_stock_amount": min_stock,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _add_purchase(product_id: int, *, days_ago: float, amount: float = 2) -> None:
    """Insert a raw purchase event at ``days_ago`` days before now."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO stock_history (product_id, event_type, amount, created_at) "
        "VALUES (?, 'purchase', ?, datetime('now', ?))",
        (product_id, amount, f"-{days_ago} days"),
    )
    conn.commit()


def _add_stock(product_id: int, amount: float) -> None:
    """Insert a raw stock lot (no purchase event) to set current_qty."""
    conn = get_connection()
    loc_id = conn.execute("SELECT id FROM locations LIMIT 1").fetchone()["id"]
    conn.execute(
        "INSERT INTO stock (product_id, location_id, amount, unit_id) VALUES (?, ?, ?, ?)",
        (product_id, loc_id, amount, _unit_id()),
    )
    conn.commit()


def _suggested_ids(**params) -> set[int]:
    r = client.get("/api/shopping-list/cadence-suggestions", params=params)
    assert r.status_code == 200, r.text
    return {s["product_id"] for s in r.json()["suggestions"]}


# ── Inclusion ───────────────────────────────────────────────────────────────

def test_frequently_bought_due_is_included():
    """3 buys, ~10-day rhythm, last bought 10d ago → due now → included."""
    pid = _make_product("Cadence freq due")
    for d in (30, 20, 10):
        _add_purchase(pid, days_ago=d)
    assert pid in _suggested_ids()


def test_kept_in_stock_two_purchases_due_is_included():
    """Keep-in-stock product bypasses the frequency gate (cnt 2 < min 3)."""
    pid = _make_product("Cadence kept 2buys", min_stock=3)
    for d in (7, 0):
        _add_purchase(pid, days_ago=d)
    assert pid in _suggested_ids()


# ── Exclusion ───────────────────────────────────────────────────────────────

def test_overdue_beyond_window_is_excluded():
    """~10-day rhythm, last bought 20d ago → 10d overdue → outside ±7d."""
    pid = _make_product("Cadence overdue")
    for d in (40, 30, 20):
        _add_purchase(pid, days_ago=d)
    assert pid not in _suggested_ids()


def test_recently_rebought_is_excluded():
    """A fresh purchase moves the anchor forward → expected far in future."""
    pid = _make_product("Cadence rebought")
    for d in (30, 20, 10, 1):
        _add_purchase(pid, days_ago=d)
    assert pid not in _suggested_ids()


def test_well_stocked_is_excluded():
    """Due by cadence, but current stock is at/above the keep threshold."""
    pid = _make_product("Cadence wellstocked", min_stock=5)
    for d in (14, 7, 0):
        _add_purchase(pid, days_ago=d)
    _add_stock(pid, 10)  # >= min_stock 5
    assert pid not in _suggested_ids()


def test_already_on_shopping_list_is_excluded():
    pid = _make_product("Cadence onlist")
    for d in (30, 20, 10):
        _add_purchase(pid, days_ago=d)
    r = client.post("/api/shopping-list", json={"product_id": pid, "amount": 1})
    assert r.status_code == 201, r.text
    assert pid not in _suggested_ids()


def test_single_purchase_is_excluded():
    """One purchase → no interval to compute."""
    pid = _make_product("Cadence single", min_stock=3)
    _add_purchase(pid, days_ago=0)
    assert pid not in _suggested_ids()


def test_infrequent_non_staple_is_excluded():
    """Non-staple with only 2 buys (< min_purchases) doesn't qualify."""
    pid = _make_product("Cadence infrequent")
    for d in (7, 0):
        _add_purchase(pid, days_ago=d)
    assert pid not in _suggested_ids()


# ── Response shape ────────────────────────────────────────────────────────────

def test_response_shape_and_fields():
    pid = _make_product("Cadence shape")
    for d in (30, 20, 10):
        _add_purchase(pid, days_ago=d, amount=3)
    r = client.get("/api/shopping-list/cadence-suggestions")
    assert r.status_code == 200
    body = r.json()
    assert body["lookback_days"] == 180
    assert body["window_days"] == 7
    assert body["min_purchases"] == 3
    item = next(s for s in body["suggestions"] if s["product_id"] == pid)
    assert item["purchase_count"] == 3
    assert abs(item["avg_interval_days"] - 10.0) < 0.5
    assert item["suggested_amount"] == 3.0
    assert item["is_kept"] is False
    assert "pv" in item["reasoning"]
