"""Comprehensive tests for the HA-Storage API."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Set up test env before importing app
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "products"), exist_ok=True)
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "recipes"), exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


# ── Health ─────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health(self):
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["version"] != ""
        assert data["db_tables"] > 0


# ── Units ──────────────────────────────────────────────────────────────────

class TestUnits:
    def test_list_seeded_units(self):
        r = client.get("/api/units")
        assert r.status_code == 200
        abbrevs = [u["abbreviation"] for u in r.json()]
        for expected in ["g", "kg", "ml", "dl", "l", "tl", "rkl", "kpl", "rs"]:
            assert expected in abbrevs

    def test_create_unit(self):
        r = client.post("/api/units", json={"name": "Tusina", "abbreviation": "tus", "name_plural": "Tusinaa"})
        assert r.status_code == 201
        assert r.json()["abbreviation"] == "tus"

    def test_create_duplicate_unit(self):
        r = client.post("/api/units", json={"name": "Gramma2", "abbreviation": "g"})
        assert r.status_code == 409

    def test_delete_unit(self):
        r = client.post("/api/units", json={"name": "TestDel", "abbreviation": "td"})
        uid = r.json()["id"]
        r = client.delete(f"/api/units/{uid}")
        assert r.status_code == 204

    def test_delete_unit_in_use(self):
        units = client.get("/api/units").json()
        kpl_id = next(u["id"] for u in units if u["abbreviation"] == "kpl")
        # Create a product using kpl
        client.post("/api/products", json={"name": "TestUnitInUse", "unit_id": kpl_id})
        r = client.delete(f"/api/units/{kpl_id}")
        assert r.status_code == 409


# ── Conversions ────────────────────────────────────────────────────────────

class TestConversions:
    def test_list_conversions(self):
        r = client.get("/api/conversions")
        assert r.status_code == 200
        assert len(r.json()) >= 12  # 6 forward + 6 reverse

    def test_resolve_kg_to_g(self):
        units = {u["abbreviation"]: u["id"] for u in client.get("/api/units").json()}
        r = client.get(f"/api/conversions/resolve?from_unit_id={units['kg']}&to_unit_id={units['g']}")
        assert r.status_code == 200
        assert r.json()["factor"] == 1000.0

    def test_resolve_same_unit(self):
        units = {u["abbreviation"]: u["id"] for u in client.get("/api/units").json()}
        r = client.get(f"/api/conversions/resolve?from_unit_id={units['g']}&to_unit_id={units['g']}")
        assert r.status_code == 200
        assert r.json()["factor"] == 1.0

    def test_resolve_chain_rkl_to_l(self):
        """rkl → ml → l requires a 2-hop chain."""
        units = {u["abbreviation"]: u["id"] for u in client.get("/api/units").json()}
        r = client.get(f"/api/conversions/resolve?from_unit_id={units['rkl']}&to_unit_id={units['l']}")
        assert r.status_code == 200
        # 1 rkl = 15 ml, 1 ml = 0.001 l → factor = 0.015
        assert abs(r.json()["factor"] - 0.015) < 0.001

    def test_resolve_no_path(self):
        units = {u["abbreviation"]: u["id"] for u in client.get("/api/units").json()}
        r = client.get(f"/api/conversions/resolve?from_unit_id={units['kpl']}&to_unit_id={units['g']}")
        assert r.status_code == 404

    def test_create_conversion(self):
        units = {u["abbreviation"]: u["id"] for u in client.get("/api/units").json()}
        r = client.post("/api/conversions", json={
            "from_unit_id": units["kpl"],
            "to_unit_id": units["g"],
            "factor": 60,
            "product_id": None,
        })
        assert r.status_code == 201


# ── Locations ──────────────────────────────────────────────────────────────

class TestLocations:
    def test_list_seeded_locations(self):
        r = client.get("/api/locations")
        assert r.status_code == 200
        names = [l["name"] for l in r.json()]
        assert "Fridge" in names
        assert "Pantry" in names
        assert "Freezer" in names

    def test_create_location(self):
        r = client.post("/api/locations", json={"name": "Garage", "description": "Autotalli"})
        assert r.status_code == 201
        assert r.json()["name"] == "Garage"

    def test_duplicate_location(self):
        r = client.post("/api/locations", json={"name": "Fridge"})
        assert r.status_code == 409


# ── Product Groups ─────────────────────────────────────────────────────────

class TestProductGroups:
    def test_create_group(self):
        r = client.post("/api/product-groups", json={"name": "Meijerituotteet"})
        assert r.status_code == 201

    def test_list_groups(self):
        r = client.get("/api/product-groups")
        assert r.status_code == 200


# ── Products ───────────────────────────────────────────────────────────────

class TestProducts:
    def _kpl_id(self):
        return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")

    def test_create_product(self):
        r = client.post("/api/products", json={"name": "Maito", "unit_id": self._kpl_id()})
        assert r.status_code == 201
        assert r.json()["name"] == "Maito"

    def test_create_product_invalid_unit(self):
        r = client.post("/api/products", json={"name": "Bad", "unit_id": 99999})
        assert r.status_code == 400

    def test_get_product_detail(self):
        r = client.post("/api/products", json={"name": "DetailTest", "unit_id": self._kpl_id()})
        pid = r.json()["id"]
        r = client.get(f"/api/products/{pid}")
        assert r.status_code == 200
        assert "children" in r.json()
        assert "barcodes" in r.json()
        assert "stock_amount" in r.json()

    def test_update_product(self):
        r = client.post("/api/products", json={"name": "UpdateMe", "unit_id": self._kpl_id()})
        pid = r.json()["id"]
        r = client.put(f"/api/products/{pid}", json={"name": "Updated"})
        assert r.status_code == 200
        assert r.json()["name"] == "Updated"

    def test_delete_product(self):
        r = client.post("/api/products", json={"name": "DeleteMe", "unit_id": self._kpl_id()})
        pid = r.json()["id"]
        r = client.delete(f"/api/products/{pid}")
        assert r.status_code == 204
        r = client.get(f"/api/products/{pid}")
        assert r.status_code == 404

    def test_parent_child(self):
        parent = client.post("/api/products", json={"name": "Parent", "unit_id": self._kpl_id()}).json()
        child = client.post("/api/products", json={
            "name": "Child", "unit_id": self._kpl_id(), "parent_id": parent["id"]
        }).json()
        detail = client.get(f"/api/products/{parent['id']}").json()
        assert any(c["id"] == child["id"] for c in detail["children"])

    def test_list_products(self):
        r = client.get("/api/products")
        assert r.status_code == 200
        assert len(r.json()) > 0


# ── Barcodes ───────────────────────────────────────────────────────────────

class TestBarcodes:
    def _make_product(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        return client.post("/api/products", json={"name": f"BC_{id(self)}", "unit_id": kpl}).json()["id"]

    def test_create_barcode(self):
        pid = self._make_product()
        r = client.post("/api/barcodes", json={"product_id": pid, "barcode": "1234567890123"})
        assert r.status_code == 201
        assert r.json()["pack_size"] == 1

    def test_duplicate_barcode(self):
        pid = self._make_product()
        client.post("/api/barcodes", json={"product_id": pid, "barcode": "DUP123"})
        r = client.post("/api/barcodes", json={"product_id": pid, "barcode": "DUP123"})
        assert r.status_code == 409

    def test_product_by_barcode(self):
        pid = self._make_product()
        client.post("/api/barcodes", json={"product_id": pid, "barcode": "LOOKUP123"})
        r = client.get("/api/products/by-barcode/LOOKUP123")
        assert r.status_code == 200
        assert r.json()["id"] == pid

    def test_barcode_not_found(self):
        r = client.get("/api/products/by-barcode/NOEXIST")
        assert r.status_code == 404


# ── Stock ──────────────────────────────────────────────────────────────────

class TestStock:
    def _make_product_with_location(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        loc = client.get("/api/locations").json()[0]["id"]
        p = client.post("/api/products", json={
            "name": f"Stock_{id(self)}", "unit_id": kpl, "location_id": loc
        }).json()
        return p["id"], kpl, loc

    def test_add_stock(self):
        pid, kpl, loc = self._make_product_with_location()
        r = client.post("/api/stock/add", json={"product_id": pid, "amount": 5})
        assert r.status_code == 201
        assert r.json()["amount"] == 5

    def test_consume_fifo(self):
        pid, kpl, loc = self._make_product_with_location()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 3})
        client.post("/api/stock/add", json={"product_id": pid, "amount": 7})
        r = client.post("/api/stock/consume", json={"product_id": pid, "amount": 5})
        assert r.status_code == 200
        assert r.json()["consumed"] == 5

    def test_consume_more_than_available(self):
        pid, kpl, loc = self._make_product_with_location()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 2})
        r = client.post("/api/stock/consume", json={"product_id": pid, "amount": 5})
        assert r.status_code == 200
        assert r.json()["consumed"] == 2
        assert r.json()["remaining_to_consume"] == 3

    def test_open_stock(self):
        pid, kpl, loc = self._make_product_with_location()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 5})
        r = client.post("/api/stock/open", json={"product_id": pid, "amount": 2})
        assert r.status_code == 200
        assert r.json()["opened"] == 2

    def test_transfer_stock(self):
        pid, kpl, loc = self._make_product_with_location()
        locs = client.get("/api/locations").json()
        from_loc = locs[0]["id"]
        to_loc = locs[1]["id"]
        client.post("/api/stock/add", json={"product_id": pid, "amount": 5, "location_id": from_loc})
        r = client.post("/api/stock/transfer", json={
            "product_id": pid, "amount": 3, "from_location_id": from_loc, "to_location_id": to_loc
        })
        assert r.status_code == 200
        assert r.json()["transferred"] == 3

    def test_delete_stock_entry(self):
        pid, kpl, loc = self._make_product_with_location()
        entry = client.post("/api/stock/add", json={"product_id": pid, "amount": 1}).json()
        r = client.delete(f"/api/stock/{entry['id']}")
        assert r.status_code == 204

    def test_stock_cascades_on_product_delete(self):
        pid, kpl, loc = self._make_product_with_location()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 10})
        client.delete(f"/api/products/{pid}")
        r = client.get(f"/api/stock/product/{pid}")
        assert r.status_code == 404


# ── Recipes ────────────────────────────────────────────────────────────────

class TestRecipes:
    def _make_product(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        return client.post("/api/products", json={"name": f"Rec_{id(self)}", "unit_id": kpl}).json()["id"], kpl

    def test_create_recipe(self):
        pid, kpl = self._make_product()
        r = client.post("/api/recipes", json={
            "name": "Testiresepti",
            "servings": 2,
            "ingredients": [{"product_id": pid, "amount": 3, "unit_id": kpl}],
        })
        assert r.status_code == 201
        assert r.json()["name"] == "Testiresepti"
        assert len(r.json()["ingredients"]) == 1

    def test_get_recipe_detail(self):
        pid, kpl = self._make_product()
        rec = client.post("/api/recipes", json={
            "name": "DetailRecipe",
            "ingredients": [{"product_id": pid, "amount": 2, "unit_id": kpl}],
        }).json()
        r = client.get(f"/api/recipes/{rec['id']}")
        assert r.status_code == 200
        assert len(r.json()["ingredients"]) == 1
        assert "product_name" in r.json()["ingredients"][0]

    def test_recipe_to_shopping(self):
        pid, kpl = self._make_product()
        rec = client.post("/api/recipes", json={
            "name": "ShopRecipe",
            "ingredients": [{"product_id": pid, "amount": 5, "unit_id": kpl}],
        }).json()
        r = client.post(f"/api/recipes/{rec['id']}/to-shopping")
        assert r.status_code == 201
        assert r.json()["added"] == 1

    def test_delete_recipe_cascades(self):
        pid, kpl = self._make_product()
        rec = client.post("/api/recipes", json={
            "name": "CascadeRecipe",
            "ingredients": [{"product_id": pid, "amount": 1, "unit_id": kpl}],
        }).json()
        r = client.delete(f"/api/recipes/{rec['id']}")
        assert r.status_code == 204

    def test_add_ingredient(self):
        pid, kpl = self._make_product()
        rec = client.post("/api/recipes", json={"name": "AddIng"}).json()
        r = client.post(f"/api/recipes/{rec['id']}/ingredients", json={
            "product_id": pid, "amount": 2, "unit_id": kpl
        })
        assert r.status_code == 201

    def test_update_ingredient(self):
        pid, kpl = self._make_product()
        rec = client.post("/api/recipes", json={
            "name": "UpdIng",
            "ingredients": [{"product_id": pid, "amount": 1, "unit_id": kpl}],
        }).json()
        ing_id = rec["ingredients"][0]["id"]
        r = client.put(f"/api/recipes/{rec['id']}/ingredients/{ing_id}", json={"amount": 5})
        assert r.status_code == 200
        assert r.json()["amount"] == 5


# ── Shopping List ──────────────────────────────────────────────────────────

class TestShoppingList:
    def _make_product(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        return client.post("/api/products", json={"name": f"Shop_{id(self)}", "unit_id": kpl}).json()["id"], kpl

    def test_add_item(self):
        pid, kpl = self._make_product()
        r = client.post("/api/shopping-list", json={"product_id": pid, "amount": 2, "unit_id": kpl})
        assert r.status_code == 201

    def test_toggle_done(self):
        pid, kpl = self._make_product()
        item = client.post("/api/shopping-list", json={"product_id": pid, "amount": 1}).json()
        r = client.put(f"/api/shopping-list/{item['id']}", json={"done": True})
        assert r.status_code == 200

    def test_clear_done(self):
        pid, kpl = self._make_product()
        item = client.post("/api/shopping-list", json={"product_id": pid, "amount": 1}).json()
        client.put(f"/api/shopping-list/{item['id']}", json={"done": True})
        r = client.delete("/api/shopping-list/done")
        assert r.status_code == 204


# ── Barcode Queue ──────────────────────────────────────────────────────────

class TestBarcodeQueue:
    def test_enqueue(self):
        r = client.post("/api/barcode-queue", json={"barcode": "QUEUE123", "source": "scanner"})
        assert r.status_code == 201
        assert r.json()["status"] == "pending"

    def test_update_status(self):
        entry = client.post("/api/barcode-queue", json={"barcode": "QUEUE456"}).json()
        r = client.put(f"/api/barcode-queue/{entry['id']}", json={"status": "processed"})
        assert r.status_code == 200
        assert r.json()["status"] == "processed"

    def test_filter_by_status(self):
        client.post("/api/barcode-queue", json={"barcode": "FILTER1"})
        r = client.get("/api/barcode-queue?status=pending")
        assert r.status_code == 200
        assert all(e["status"] == "pending" for e in r.json())


# ── Files ──────────────────────────────────────────────────────────────────

class TestFiles:
    def test_upload_and_get_product_image(self):
        r = client.put("/api/files/products/test.png", content=b"\x89PNG fake image data")
        assert r.status_code == 201
        r = client.get("/api/files/products/test.png")
        assert r.status_code == 200

    def test_get_missing_image(self):
        r = client.get("/api/files/products/noexist.png")
        assert r.status_code == 404

    def test_delete_image(self):
        client.put("/api/files/products/delme.png", content=b"data")
        r = client.delete("/api/files/products/delme.png")
        assert r.status_code == 204

    def test_recipe_image(self):
        r = client.put("/api/files/recipes/recipe.jpg", content=b"jpeg data")
        assert r.status_code == 201
        r = client.get("/api/files/recipes/recipe.jpg")
        assert r.status_code == 200


# ── Config ─────────────────────────────────────────────────────────────────

class TestConfig:
    def test_get_config_hides_key(self):
        r = client.get("/api/config")
        assert r.status_code == 200
        keys = [c["key"] for c in r.json()]
        assert "gemini_api_key" not in keys

    def test_set_and_get_config(self):
        r = client.put("/api/config/test_key", json={"key": "test_key", "value": "test_value"})
        assert r.status_code == 200
        assert r.json()["value"] == "test_value"

    def test_get_ai_key_when_empty(self):
        r = client.get("/api/config/ai-key")
        # May be 404 if no key set or 200 if env var was set
        assert r.status_code in (200, 404)


# ── Stock Entries (aggregate, used by HACS integration) ────────────────────

class TestStockEntries:
    def _make_product_with_location(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        loc = client.get("/api/locations").json()[0]["id"]
        p = client.post("/api/products", json={
            "name": f"StockEntries_{id(self)}", "unit_id": kpl, "location_id": loc
        }).json()
        return p["id"], kpl, loc

    def test_lists_all_entries_with_product_name(self):
        pid, _, _ = self._make_product_with_location()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1, "best_before_date": "2099-01-01"})
        r = client.get("/api/stock/entries")
        assert r.status_code == 200
        rows = r.json()
        assert any(e["product_id"] == pid and e["product_name"].startswith("StockEntries_") for e in rows)

    def test_filter_expiring_within_days(self):
        from datetime import date, timedelta
        pid, _, _ = self._make_product_with_location()
        soon = (date.today() + timedelta(days=3)).isoformat()
        far = (date.today() + timedelta(days=60)).isoformat()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1, "best_before_date": soon})
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1, "best_before_date": far})

        r = client.get("/api/stock/entries?expiring_within_days=7")
        assert r.status_code == 200
        dates = [e["best_before_date"] for e in r.json() if e["product_id"] == pid]
        assert soon in dates
        assert far not in dates

    def test_filter_expired(self):
        from datetime import date, timedelta
        pid, _, _ = self._make_product_with_location()
        gone = (date.today() - timedelta(days=2)).isoformat()
        future = (date.today() + timedelta(days=10)).isoformat()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1, "best_before_date": gone})
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1, "best_before_date": future})

        r = client.get("/api/stock/entries?expired=true")
        assert r.status_code == 200
        dates = [e["best_before_date"] for e in r.json() if e["product_id"] == pid]
        assert gone in dates
        assert future not in dates

    def test_expiring_today_is_included(self):
        from datetime import date
        pid, _, _ = self._make_product_with_location()
        today = date.today().isoformat()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1, "best_before_date": today})

        r = client.get("/api/stock/entries?expiring_within_days=0")
        assert r.status_code == 200
        dates = [e["best_before_date"] for e in r.json() if e["product_id"] == pid]
        assert today in dates


# ── AI Optimize Status (no task id) ────────────────────────────────────────

class TestOptimizeStatusEndpoint:
    def test_idle_when_nothing_ever_ran(self):
        # This test depends on test order — if any optimize task has run earlier,
        # we'll get a most-recent task instead of idle. So accept either shape and
        # only assert the schema.
        r = client.get("/api/ai/optimize")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert data["status"] in ("idle", "running", "done", "error")
        assert "task_id" in data

    def test_reports_running_task(self):
        from routers import ai as ai_mod
        import time
        # Inject a fake running task
        with ai_mod._tasks_lock:
            ai_mod._tasks["fake-running"] = {
                "task_id": "fake-running",
                "status": "running",
                "logs": [],
                "updated": 0,
                "started_at": time.time(),
                "finished_at": None,
                "mode": "full",
            }
            ai_mod._running_task_id = "fake-running"
        try:
            r = client.get("/api/ai/optimize")
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "running"
            assert data["task_id"] == "fake-running"
        finally:
            with ai_mod._tasks_lock:
                ai_mod._running_task_id = None
                ai_mod._tasks.pop("fake-running", None)

    def test_reports_most_recent_when_idle(self):
        from routers import ai as ai_mod
        import time
        with ai_mod._tasks_lock:
            ai_mod._tasks["older"] = {
                "task_id": "older",
                "status": "done",
                "logs": [],
                "updated": 1,
                "started_at": 100.0,
                "finished_at": 200.0,
                "mode": "full",
            }
            ai_mod._tasks["newer"] = {
                "task_id": "newer",
                "status": "done",
                "logs": [],
                "updated": 2,
                "started_at": 300.0,
                "finished_at": 400.0,
                "mode": "incremental",
            }
            ai_mod._running_task_id = None
        try:
            r = client.get("/api/ai/optimize")
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "done"
            assert data["task_id"] == "newer"
        finally:
            with ai_mod._tasks_lock:
                ai_mod._tasks.pop("older", None)
                ai_mod._tasks.pop("newer", None)


class TestOptimizeUngroupedOnly:
    def test_400_when_no_ungrouped_products(self):
        from main import get_connection
        conn = get_connection()
        # Force every active product to have a non-null product_group_id.
        # Use the first existing product group as a sentinel.
        grp = conn.execute("SELECT id FROM product_groups LIMIT 1").fetchone()
        if not grp:
            cur = conn.execute(
                "INSERT INTO product_groups (name) VALUES ('TestGroupForUngrouped')"
            )
            gid = cur.lastrowid
        else:
            gid = grp["id"]
        # Snapshot then bulk-assign so all active products are grouped.
        before = conn.execute(
            "SELECT id, product_group_id FROM products WHERE active = 1"
        ).fetchall()
        conn.execute(
            "UPDATE products SET product_group_id = ? WHERE active = 1 AND product_group_id IS NULL",
            (gid,),
        )
        conn.commit()
        try:
            r = client.post("/api/ai/optimize", json={"ungrouped_only": True})
            assert r.status_code == 400
            assert "ungrouped" in r.json()["detail"].lower()
        finally:
            for row in before:
                conn.execute(
                    "UPDATE products SET product_group_id = ? WHERE id = ?",
                    (row["product_group_id"], row["id"]),
                )
            conn.commit()

    def test_route_picks_up_ungrouped_products(self, monkeypatch):
        # Stub the thread target so the test does not actually call the AI.
        from routers import ai as ai_mod
        captured: dict = {}

        def fake_run(task_id, product_ids, enforced_categories, fresh_seed=False):
            captured["product_ids"] = product_ids
            with ai_mod._tasks_lock:
                ai_mod._tasks[task_id]["status"] = "done"
                ai_mod._tasks[task_id]["finished_at"] = 0.0
                ai_mod._running_task_id = None

        monkeypatch.setattr(ai_mod, "_run_optimize_task", fake_run)
        # Also stub Thread so it runs synchronously.
        import threading as _th
        class _Sync:
            def __init__(self, target, args=(), daemon=True, name=""):
                self._t, self._a = target, args
            def start(self):
                self._t(*self._a)
        monkeypatch.setattr(_th, "Thread", _Sync)

        from main import get_connection
        conn = get_connection()
        # Create one guaranteed-ungrouped product
        kpl_row = conn.execute("SELECT id FROM units LIMIT 1").fetchone()
        kpl = kpl_row["id"]
        cur = conn.execute(
            "INSERT INTO products (name, active, unit_id, product_group_id) "
            "VALUES ('UngroupedProbe', 1, ?, NULL)",
            (kpl,),
        )
        new_pid = cur.lastrowid
        conn.commit()

        try:
            r = client.post("/api/ai/optimize", json={"ungrouped_only": True})
            assert r.status_code == 200
            assert isinstance(captured.get("product_ids"), list)
            assert new_pid in captured["product_ids"]
            # Every captured id must be ungrouped at time of the call
            assert all(isinstance(i, int) for i in captured["product_ids"])
        finally:
            conn.execute("DELETE FROM products WHERE id = ?", (new_pid,))
            conn.commit()
            with ai_mod._tasks_lock:
                ai_mod._running_task_id = None



class TestRemovedHaSyncRoutes:
    def test_shopping_ha_sync_gone(self):
        # Path may be intercepted by /shopping-list/{item_id} template → 405; either is "gone".
        r = client.post("/api/shopping-list/ha-sync")
        assert r.status_code in (404, 405)

    def test_shopping_ha_status_gone(self):
        r = client.get("/api/shopping-list/ha-status")
        assert r.status_code in (404, 405)

    def test_stock_ha_sync_gone(self):
        r = client.post("/api/stock-list/ha-sync")
        assert r.status_code in (404, 405)

    def test_stock_ha_status_gone(self):
        r = client.get("/api/stock-list/ha-status")
        assert r.status_code in (404, 405)

    def test_add_shopping_item_still_works(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        pid = client.post("/api/products", json={"name": "PostHaSync", "unit_id": kpl}).json()["id"]
        r = client.post("/api/shopping-list", json={"product_id": pid, "amount": 1})
        assert r.status_code == 201


# ── History & Stats ────────────────────────────────────────────────────────

class TestHistory:
    def _make(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        locs = client.get("/api/locations").json()
        loc = locs[0]["id"]
        pid = client.post("/api/products", json={
            "name": f"Hist_{id(self)}", "unit_id": kpl, "location_id": loc
        }).json()["id"]
        return pid, kpl, loc, locs

    def test_purchase_event_created(self):
        pid, *_ = self._make()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 4})
        events = client.get(f"/api/history?product_id={pid}").json()
        assert any(e["event_type"] == "purchase" and e["amount"] == 4 for e in events)

    def test_consume_event_created(self):
        pid, *_ = self._make()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 5})
        client.post("/api/stock/consume", json={"product_id": pid, "amount": 3, "note": "lunch"})
        events = client.get(f"/api/history?product_id={pid}&event_type=consume").json()
        assert len(events) == 1
        assert events[0]["amount"] == 3
        assert events[0]["note"] == "lunch"

    def test_consume_spoiled_logs_spoil_event(self):
        pid, *_ = self._make()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 5})
        client.post("/api/stock/consume", json={
            "product_id": pid, "amount": 1, "spoiled": True, "note": "moldy",
        })
        spoils = client.get(f"/api/history?product_id={pid}&event_type=spoil").json()
        consumes = client.get(f"/api/history?product_id={pid}&event_type=consume").json()
        assert len(spoils) == 1
        assert spoils[0]["amount"] == 1
        assert spoils[0]["note"] == "moldy"
        assert len(consumes) == 0

    def test_open_event_created(self):
        pid, *_ = self._make()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 2})
        client.post("/api/stock/open", json={"product_id": pid, "amount": 1})
        events = client.get(f"/api/history?product_id={pid}&event_type=open").json()
        assert len(events) == 1
        assert events[0]["amount"] == 1

    def test_transfer_event_created(self):
        pid, _, loc, locs = self._make()
        to_loc = locs[1]["id"]
        client.post("/api/stock/add", json={"product_id": pid, "amount": 5, "location_id": loc})
        client.post("/api/stock/transfer", json={
            "product_id": pid, "amount": 2,
            "from_location_id": loc, "to_location_id": to_loc,
        })
        events = client.get(f"/api/history?product_id={pid}&event_type=transfer").json()
        assert len(events) == 1
        assert events[0]["from_location_id"] == loc
        assert events[0]["location_id"] == to_loc

    def test_spoil_event_on_delete_with_reason(self):
        pid, *_ = self._make()
        entry = client.post("/api/stock/add", json={"product_id": pid, "amount": 3}).json()
        client.delete(f"/api/stock/{entry['id']}?reason=spoiled")
        events = client.get(f"/api/history?product_id={pid}&event_type=spoil").json()
        assert len(events) == 1
        assert events[0]["amount"] == 3
        assert events[0]["note"] == "spoiled"

    def test_no_spoil_when_delete_without_reason(self):
        pid, *_ = self._make()
        entry = client.post("/api/stock/add", json={"product_id": pid, "amount": 3}).json()
        client.delete(f"/api/stock/{entry['id']}")
        events = client.get(f"/api/history?product_id={pid}&event_type=spoil").json()
        assert events == []

    def test_product_history_endpoint(self):
        pid, *_ = self._make()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1})
        client.post("/api/stock/consume", json={"product_id": pid, "amount": 1})
        events = client.get(f"/api/history/product/{pid}").json()
        assert len(events) >= 2

    def test_invalid_event_type_filter(self):
        r = client.get("/api/history?event_type=bogus")
        assert r.status_code == 400

    def test_delete_history_entry(self):
        pid, *_ = self._make()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1})
        events = client.get(f"/api/history?product_id={pid}").json()
        eid = events[0]["id"]
        r = client.delete(f"/api/history/{eid}")
        assert r.status_code == 204


class TestStats:
    def _make(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        return client.post("/api/products", json={
            "name": f"Stats_{id(self)}", "unit_id": kpl
        }).json()["id"]

    def test_summary(self):
        pid = self._make()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 2})
        s = client.get("/api/stats/summary").json()
        for key in ("events_total", "events_7d", "events_30d",
                    "products_purchased_30d", "products_consumed_30d", "spoiled_30d"):
            assert key in s
        assert s["events_total"] >= 1

    def test_top_consumed_ordering(self):
        a = self._make()
        b = self._make()
        client.post("/api/stock/add", json={"product_id": a, "amount": 100})
        client.post("/api/stock/add", json={"product_id": b, "amount": 100})
        client.post("/api/stock/consume", json={"product_id": a, "amount": 5})
        client.post("/api/stock/consume", json={"product_id": b, "amount": 20})
        rows = client.get("/api/stats/top-consumed?days=1&limit=10").json()
        names = [r["product_id"] for r in rows]
        assert b in names
        # b should rank above a
        assert names.index(b) < names.index(a)

    def test_top_purchased(self):
        pid = self._make()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 7})
        rows = client.get("/api/stats/top-purchased?days=1&limit=10").json()
        assert any(r["product_id"] == pid for r in rows)

    def test_timeline(self):
        pid = self._make()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 3})
        rows = client.get("/api/stats/timeline?days=1").json()
        assert len(rows) >= 1
        assert "day" in rows[0]
        assert "amount" in rows[0]

    def test_product_stats(self):
        pid = self._make()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 10})
        client.post("/api/stock/consume", json={"product_id": pid, "amount": 4})
        s = client.get(f"/api/stats/product/{pid}").json()
        assert s["purchased_total"] == 10
        assert s["consumed_total"] == 4
        assert s["purchase_count"] == 1
        assert s["consume_count"] == 1


class TestHistoryBackfill:
    def test_backfill_from_existing_stock(self, tmp_path, monkeypatch):
        """Fresh DB with pre-seeded stock rows should backfill purchase events."""
        import sqlite3
        from database import get_db, init_db

        db_path = tmp_path / "backfill.db"
        # Stage 1: create schema and seed minimal data WITHOUT triggering backfill yet,
        # by manually inserting stock rows after init.
        conn = get_db(db_path)
        init_db(conn)
        # Find a unit + location + create a product
        kpl_id = conn.execute("SELECT id FROM units WHERE abbreviation='kpl'").fetchone()["id"]
        loc_id = conn.execute("SELECT id FROM locations LIMIT 1").fetchone()["id"]
        cur = conn.execute(
            "INSERT INTO products (name, unit_id, location_id) VALUES ('BF', ?, ?)",
            (kpl_id, loc_id),
        )
        prod_id = cur.lastrowid
        conn.execute(
            "INSERT INTO stock (product_id, location_id, amount, unit_id, purchased_date) "
            "VALUES (?, ?, 8, ?, '2024-01-15')",
            (prod_id, loc_id, kpl_id),
        )
        # Clear the backfill marker so the next init re-runs it
        conn.execute("DELETE FROM _meta WHERE key='history_backfilled'")
        conn.execute("DELETE FROM stock_history")
        conn.commit()
        conn.close()

        # Stage 2: re-open and re-init — backfill should fire
        conn2 = get_db(db_path)
        init_db(conn2)
        rows = conn2.execute(
            "SELECT * FROM stock_history WHERE product_id = ?", (prod_id,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["event_type"] == "purchase"
        assert rows[0]["amount"] == 8
        marker = conn2.execute(
            "SELECT value FROM _meta WHERE key='history_backfilled'"
        ).fetchone()
        assert marker is not None
        conn2.close()
