"""Tests for quick-add shopping bundles."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Set up test env before importing app (same pattern as test_api.py)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "products"), exist_ok=True)
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "recipes"), exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def _kpl_id() -> int:
    units = client.get("/api/units").json()
    return next(u["id"] for u in units if u["abbreviation"] == "kpl")


def _make_product(name: str) -> int:
    r = client.post("/api/products", json={"name": name, "unit_id": _kpl_id()})
    assert r.status_code == 201
    return r.json()["id"]


class TestBundleSchema:
    def test_bundle_tables_and_column_exist(self):
        import sqlite3
        from main import get_connection
        conn = get_connection()
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "bundles" in tables
        assert "bundle_items" in tables
        sl_cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(shopping_list)").fetchall()}
        assert "bundle_id" in sl_cols

    def test_migration_is_idempotent(self):
        from database import _migrate_schema
        from main import get_connection
        conn = get_connection()
        _migrate_schema(conn)  # second run must not raise
        _migrate_schema(conn)  # third run must not raise


class TestBundleCrud:
    def test_create_and_get_bundle(self):
        p1 = _make_product("Tortillat")
        p2 = _make_product("Jauheliha")
        r = client.post("/api/bundles", json={
            "name": "Taco night", "emoji": "🌮",
            "items": [{"product_id": p1}, {"product_id": p2}],
        })
        assert r.status_code == 201
        b = r.json()
        assert b["name"] == "Taco night"
        assert b["emoji"] == "🌮"
        assert len(b["items"]) == 2
        names = {i["product_name"] for i in b["items"]}
        assert names == {"Tortillat", "Jauheliha"}

        listed = client.get("/api/bundles").json()
        row = next(x for x in listed if x["id"] == b["id"])
        assert row["item_count"] == 2

    def test_create_rejects_unknown_product(self):
        r = client.post("/api/bundles", json={
            "name": "Bad", "items": [{"product_id": 999999}],
        })
        assert r.status_code == 400

    def test_update_replaces_items(self):
        p1 = _make_product("Salsa")
        p2 = _make_product("Guacamole")
        b = client.post("/api/bundles", json={
            "name": "Dippi-ilta", "items": [{"product_id": p1}],
        }).json()
        r = client.put(f"/api/bundles/{b['id']}", json={
            "name": "Dippi-ilta 2", "items": [{"product_id": p2}],
        })
        assert r.status_code == 200
        updated = r.json()
        assert updated["name"] == "Dippi-ilta 2"
        assert [i["product_id"] for i in updated["items"]] == [p2]

    def test_update_without_items_keeps_items(self):
        p1 = _make_product("Nachot")
        b = client.post("/api/bundles", json={
            "name": "Nacho-ilta", "items": [{"product_id": p1}],
        }).json()
        r = client.put(f"/api/bundles/{b['id']}", json={"emoji": "🧀"})
        assert r.status_code == 200
        assert r.json()["emoji"] == "🧀"
        assert len(r.json()["items"]) == 1

    def test_delete_bundle(self):
        b = client.post("/api/bundles", json={"name": "Poistuva"}).json()
        assert client.delete(f"/api/bundles/{b['id']}").status_code == 204
        assert client.get(f"/api/bundles/{b['id']}").status_code == 404

    def test_get_detail_reports_stock_and_on_list(self):
        pid = _make_product("Ruisleipä")
        # put 2 in stock
        locs = client.get("/api/locations").json()
        client.post("/api/stock/add", json={
            "product_id": pid, "location_id": locs[0]["id"],
            "amount": 2, "unit_id": _kpl_id(),
        })
        # and put it on the shopping list
        client.post("/api/shopping-list", json={"product_id": pid})
        b = client.post("/api/bundles", json={
            "name": "Aamupala", "items": [{"product_id": pid}],
        }).json()
        item = client.get(f"/api/bundles/{b['id']}").json()["items"][0]
        assert item["stock_amount"] == 2
        assert item["on_list"] is True

    def test_update_rejects_unknown_product_before_mutation(self):
        # Verify validation runs BEFORE mutation: if items contain unknown product,
        # 400 is raised and the name is NOT changed (catches transaction leak).
        p1 = _make_product("Kaali")
        b = client.post("/api/bundles", json={
            "name": "Keitto", "items": [{"product_id": p1}],
        }).json()
        # Try to update name AND add unknown product
        r = client.put(f"/api/bundles/{b['id']}", json={
            "name": "Uusi nimi", "items": [{"product_id": 999999}],
        })
        assert r.status_code == 400
        # Verify name was NOT changed (mutation was rejected before commit)
        updated = client.get(f"/api/bundles/{b['id']}").json()
        assert updated["name"] == "Keitto"
