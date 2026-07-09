"""Tests for the store registry and per-store availability endpoints."""

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


def _make_product(name: str) -> int:
    units = client.get("/api/units").json()
    kpl_id = next(u["id"] for u in units if u["abbreviation"] == "kpl")
    r = client.post("/api/products", json={"name": name, "unit_id": kpl_id})
    assert r.status_code == 201
    return r.json()["id"]


class TestStores:
    def test_upsert_creates_store(self):
        r = client.put("/api/stores/N110", json={"name": "K-Citymarket Kupittaa"})
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == "N110"
        assert data["name"] == "K-Citymarket Kupittaa"

    def test_upsert_updates_name(self):
        client.put("/api/stores/K532", json={"name": "K532"})
        r = client.put("/api/stores/K532", json={"name": "K-Market Testila"})
        assert r.status_code == 200
        assert r.json()["name"] == "K-Market Testila"

    def test_list_stores(self):
        client.put("/api/stores/N137", json={"name": "K-Citymarket Länsikeskus"})
        r = client.get("/api/stores")
        assert r.status_code == 200
        ids = [s["id"] for s in r.json()]
        assert "N137" in ids


class TestAvailability:
    def test_set_and_read_availability(self):
        pid = _make_product("Availtuote 1")
        client.put("/api/stores/N110", json={"name": "K-Citymarket Kupittaa"})
        r = client.put(
            f"/api/products/{pid}/availability",
            json=[{"store_id": "N110", "available": True, "price": 2.35}],
        )
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["store_id"] == "N110"
        assert rows[0]["name"] == "K-Citymarket Kupittaa"
        assert rows[0]["available"] is True
        assert rows[0]["price"] == 2.35
        assert rows[0]["price_currency"] == "EUR"
        assert rows[0]["checked_at"]

    def test_unknown_store_auto_created_with_id_as_name(self):
        pid = _make_product("Availtuote 2")
        r = client.put(
            f"/api/products/{pid}/availability",
            json=[{"store_id": "N999", "available": False}],
        )
        assert r.status_code == 200
        assert r.json()[0]["name"] == "N999"
        assert r.json()[0]["available"] is False
        assert r.json()[0]["price"] is None

    def test_upsert_overwrites_existing_row(self):
        pid = _make_product("Availtuote 3")
        client.put(f"/api/products/{pid}/availability",
                   json=[{"store_id": "N110", "available": True, "price": 1.00}])
        r = client.put(f"/api/products/{pid}/availability",
                       json=[{"store_id": "N110", "available": False}])
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["available"] is False
        assert rows[0]["price"] is None

    def test_partial_write_leaves_other_stores_untouched(self):
        pid = _make_product("Availtuote 4")
        client.put(f"/api/products/{pid}/availability",
                   json=[{"store_id": "N110", "available": True, "price": 2.0},
                         {"store_id": "K532", "available": True, "price": 2.1}])
        r = client.put(f"/api/products/{pid}/availability",
                       json=[{"store_id": "N110", "available": False}])
        rows = {row["store_id"]: row for row in r.json()}
        assert rows["N110"]["available"] is False
        assert rows["K532"]["available"] is True   # untouched
        assert rows["K532"]["price"] == 2.1

    def test_availability_product_404(self):
        r = client.put("/api/products/999999/availability",
                       json=[{"store_id": "N110", "available": True}])
        assert r.status_code == 404

    def test_cascade_on_product_delete(self):
        pid = _make_product("Availtuote 5")
        client.put(f"/api/products/{pid}/availability",
                   json=[{"store_id": "N110", "available": True}])
        client.delete(f"/api/products/{pid}")
        # Recreate a product and confirm no stale rows leak into its response.
        pid2 = _make_product("Availtuote 6")
        r = client.put(f"/api/products/{pid2}/availability",
                       json=[{"store_id": "K532", "available": True}])
        assert [row["store_id"] for row in r.json()] == ["K532"]
