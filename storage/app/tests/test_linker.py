import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "products"), exist_ok=True)
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "recipes"), exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from main import app, get_connection

client = TestClient(app)


def _unit_id(abbrev="kpl"):
    for u in client.get("/api/units").json():
        if u["abbreviation"] == abbrev:
            return u["id"]
    raise AssertionError(f"unit {abbrev} missing")


class TestSchema:
    def test_products_have_pack_count_and_staple(self):
        r = client.post("/api/products", json={
            "name": "schema-probe", "unit_id": _unit_id(), "staple": True, "pack_count": 6,
        })
        assert r.status_code == 201
        body = r.json()
        assert body["staple"] is True or body["staple"] == 1
        assert float(body["pack_count"]) == 6.0

    def test_link_proposals_table_exists(self):
        conn = get_connection()
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "link_proposals" in names
