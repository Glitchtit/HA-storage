"""GET /shopping-list rows carry display.qty_text and display.unit_dimension."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "products"), exist_ok=True)
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "recipes"), exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from main import app, get_connection

client = TestClient(app)


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    return client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra}).json()["id"]


def _row(row_id):
    return next(i for i in client.get("/api/shopping-list").json() if i["id"] == row_id)


class TestDisplay:
    def test_plain_row_is_package_count(self):
        sku = _mk("Sprite zero 330ml d1")
        r = client.post("/api/shopping-list", json={"product_id": sku, "amount": 2})
        assert r.json()["display"] == {"qty_text": "2", "unit_abbr": None, "unit_dimension": "package"}

    def test_recipe_volume_row(self):
        cat = _mk("vehnäjauho-d2", unit="dl", kind="concept")
        r = client.post("/api/shopping-list", json={
            "product_id": cat, "amount": 3.5, "unit_id": _u("dl"), "note": "Reseptistä: X"})
        assert _row(r.json()["id"])["display"] == {
            "qty_text": "3,5 dl", "unit_abbr": "dl", "unit_dimension": "volume"}

    def test_kpl_on_sku_reads_as_packages_on_concept_as_pieces(self):
        sku = _mk("Munat 10kpl d3")
        cat = _mk("kananmuna-d3", kind="concept")
        a = client.post("/api/shopping-list", json={"product_id": sku, "amount": 1, "unit_id": _u("kpl")}).json()
        b = client.post("/api/shopping-list", json={"product_id": cat, "amount": 2, "unit_id": _u("kpl")}).json()
        assert a["display"]["unit_dimension"] == "package"
        assert b["display"] == {"qty_text": "2 kpl", "unit_abbr": "kpl", "unit_dimension": "count"}

    def test_missing_unit_renders_bare_number(self):
        sku = _mk("Orpo yksikkö d4")
        row = client.post("/api/shopping-list", json={"product_id": sku, "amount": 2}).json()
        conn = get_connection()
        conn.commit()  # PRAGMA foreign_keys is a no-op inside a transaction
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("UPDATE shopping_list SET unit_id = 999999 WHERE id = ?", (row["id"],))
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        assert _row(row["id"])["display"] == {"qty_text": "2", "unit_abbr": None, "unit_dimension": "vague"}

    def test_update_returns_display(self):
        sku = _mk("Päivitettävä d5")
        row = client.post("/api/shopping-list", json={"product_id": sku, "amount": 1}).json()
        r = client.put(f"/api/shopping-list/{row['id']}", json={"amount": 3})
        assert r.json()["display"]["qty_text"] == "3"
