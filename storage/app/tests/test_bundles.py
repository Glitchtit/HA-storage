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
