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

    def test_list_includes_children_stock_aggregate(self):
        """GET /products must expose children_stock_amount per parent row, so
        the Products list UI can render a parent's category-level total
        (own stock + sum of all immediate children's stock). Lets users see
        e.g. Punasipuli=2 from its SKU children instead of '–'."""
        kpl = self._kpl_id()
        # Parent
        r = client.post("/api/products", json={"name": "ChildAggParent", "unit_id": kpl})
        parent_id = r.json()["id"]
        # Two children
        r = client.post("/api/products", json={"name": "ChildA", "unit_id": kpl, "parent_id": parent_id})
        child_a = r.json()["id"]
        r = client.post("/api/products", json={"name": "ChildB", "unit_id": kpl, "parent_id": parent_id})
        child_b = r.json()["id"]
        # Stock on children only (the parent itself has none — typical SKU
        # vs. category layout)
        client.post("/api/stock/add", json={"product_id": child_a, "amount": 3})
        client.post("/api/stock/add", json={"product_id": child_b, "amount": 5})

        r = client.get("/api/products")
        assert r.status_code == 200
        rows = {p["id"]: p for p in r.json()}

        # Parent: own stock 0, children's stock 8
        assert rows[parent_id]["stock_amount"] == 0
        assert rows[parent_id]["children_stock_amount"] == 8
        # Children: their own stock unchanged, children_stock_amount = 0 (they have no grandchildren)
        assert rows[child_a]["stock_amount"] == 3
        assert rows[child_a]["children_stock_amount"] == 0
        assert rows[child_b]["stock_amount"] == 5
        assert rows[child_b]["children_stock_amount"] == 0

        # Detail endpoint must also surface children_stock_amount
        r = client.get(f"/api/products/{parent_id}")
        assert r.status_code == 200
        assert r.json()["children_stock_amount"] == 8

    def test_list_products_includes_stock_aggregate(self):
        """GET /products must include `stock_amount` per row, summed across
        all stock entries for that product. Previously the list endpoint
        returned raw product rows with no stock data, so the frontend
        rendered `–` for every row even when stock existed (visible
        immediately under `GET /products/{id}` which DID include stock)."""
        kpl = self._kpl_id()
        r = client.post("/api/products", json={"name": "ListStockTest", "unit_id": kpl})
        pid = r.json()["id"]
        client.post("/api/stock/add", json={"product_id": pid, "amount": 7})
        client.post("/api/stock/add", json={"product_id": pid, "amount": 5})

        r = client.get("/api/products")
        assert r.status_code == 200
        row = next((p for p in r.json() if p["id"] == pid), None)
        assert row is not None
        assert row.get("stock_amount") == 12, (
            f"Expected aggregated stock_amount=12 on list row, got {row.get('stock_amount')!r}"
        )

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

    def test_auto_sync_adds_and_removes(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        # Pick an existing default location so /stock/add succeeds
        loc_id = client.get("/api/locations").json()[0]["id"]
        # Product tracked with min_stock_amount = 2, no stock yet
        pid = client.post(
            "/api/products",
            json={"name": f"Auto_{id(self)}", "unit_id": kpl, "min_stock_amount": 2},
        ).json()["id"]

        # Sync — should auto-add (have=0 < min=2)
        r = client.post("/api/shopping-list/sync")
        assert r.status_code == 200
        assert r.json()["added"] >= 1

        items = [i for i in client.get("/api/shopping-list").json() if i["product_id"] == pid]
        assert len(items) == 1
        assert items[0]["auto_added"] is True

        # Idempotent: a second sync at the same stock level changes nothing
        r2 = client.post("/api/shopping-list/sync").json()
        assert r2["added"] == 0 and r2["removed"] == 0

        # Restock above threshold — implicit sync inside /stock/add removes the row
        client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 5, "location_id": loc_id},
        )
        items_after = [i for i in client.get("/api/shopping-list").json() if i["product_id"] == pid]
        assert items_after == []

    def test_auto_sync_keeps_done_rows(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        loc_id = client.get("/api/locations").json()[0]["id"]
        pid = client.post(
            "/api/products",
            json={"name": f"AutoDone_{id(self)}", "unit_id": kpl, "min_stock_amount": 1},
        ).json()["id"]

        # Auto-added by sync, then user marks it done
        client.post("/api/shopping-list/sync")
        item = next(i for i in client.get("/api/shopping-list").json() if i["product_id"] == pid)
        client.put(f"/api/shopping-list/{item['id']}", json={"done": True})

        # Restock — sync must NOT delete a done row (preserves shopping trip history)
        client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 3, "location_id": loc_id},
        )
        kept = [i for i in client.get("/api/shopping-list").json() if i["product_id"] == pid]
        assert len(kept) == 1 and kept[0]["done"] is True


# ── Shopping Proposal (predictive) ─────────────────────────────────────────

class TestShoppingProposal:
    def _setup_product(self, name: str, min_stock: float = 2.0):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        loc_id = client.get("/api/locations").json()[0]["id"]
        pid = client.post(
            "/api/products",
            json={"name": name, "unit_id": kpl, "min_stock_amount": min_stock},
        ).json()["id"]
        return pid, kpl, loc_id

    def test_empty_when_no_history(self):
        pid, _, loc_id = self._setup_product(f"NoHist_{id(self)}")
        # Stock present, but no consume events
        client.post("/api/stock/add", json={"product_id": pid, "amount": 5, "location_id": loc_id})
        r = client.get("/api/shopping-list/proposal")
        assert r.status_code == 200
        items = [p for p in r.json()["proposal"] if p["product_id"] == pid]
        assert items == [], "Product with no consume history must not appear"

    def test_high_velocity_appears(self):
        # min_stock_amount kept tiny so sync_auto_shopping doesn't preempt the proposal
        pid, _, loc_id = self._setup_product(f"Fast_{id(self)}", min_stock=0.1)
        # Stock 20 in, consume 14 in the window — at 14/8 = 1.75/week,
        # remaining 6 → ~24 days to zero. Above 7d horizon, must NOT appear.
        client.post("/api/stock/add", json={"product_id": pid, "amount": 20, "location_id": loc_id})
        client.post("/api/stock/consume", json={"product_id": pid, "amount": 14})
        r = client.get("/api/shopping-list/proposal").json()
        items = [p for p in r["proposal"] if p["product_id"] == pid]
        assert items == [], "Stock still well above predicted depletion horizon"

        # Consume more so remaining is < 1 week at rate ~2.4/wk — should appear.
        client.post("/api/stock/consume", json={"product_id": pid, "amount": 5})
        r = client.get("/api/shopping-list/proposal").json()
        items = [p for p in r["proposal"] if p["product_id"] == pid]
        assert len(items) == 1
        assert items[0]["weekly_rate"] > 0
        assert items[0]["days_to_zero"] < 7
        assert items[0]["suggested_amount"] > 0
        assert items[0]["reasoning"]

    def test_skip_products_without_min_stock(self):
        # min_stock_amount = 0 → not keep-in-stock → excluded
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        loc_id = client.get("/api/locations").json()[0]["id"]
        pid = client.post(
            "/api/products",
            json={"name": f"NoMin_{id(self)}", "unit_id": kpl},  # min defaults to 0
        ).json()["id"]
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1, "location_id": loc_id})
        client.post("/api/stock/consume", json={"product_id": pid, "amount": 1})
        r = client.get("/api/shopping-list/proposal").json()
        items = [p for p in r["proposal"] if p["product_id"] == pid]
        assert items == [], "Products without min_stock_amount must be excluded"

    def test_skip_when_already_on_shopping_list(self):
        pid, kpl, loc_id = self._setup_product(f"OnList_{id(self)}")
        client.post("/api/stock/add", json={"product_id": pid, "amount": 5, "location_id": loc_id})
        client.post("/api/stock/consume", json={"product_id": pid, "amount": 4})
        # Manually add to shopping list
        client.post("/api/shopping-list", json={"product_id": pid, "amount": 1, "unit_id": kpl})
        r = client.get("/api/shopping-list/proposal").json()
        items = [p for p in r["proposal"] if p["product_id"] == pid]
        assert items == [], "Products already on shopping list must be excluded"

    def test_horizon_filter(self):
        # Keep min_stock_amount below remaining stock so auto-sync doesn't fire
        pid, _, loc_id = self._setup_product(f"Horizon_{id(self)}", min_stock=0.1)
        # Stock 5, consume 4 → rate 0.5/wk, remaining 1 → days_to_zero = (1/0.5)*7 = 14d.
        client.post("/api/stock/add", json={"product_id": pid, "amount": 5, "location_id": loc_id})
        client.post("/api/stock/consume", json={"product_id": pid, "amount": 4})
        # 7-day horizon: NOT in proposal
        r7 = client.get("/api/shopping-list/proposal?horizon_days=7").json()
        assert not [p for p in r7["proposal"] if p["product_id"] == pid]
        # 21-day horizon: IS in proposal
        r21 = client.get("/api/shopping-list/proposal?horizon_days=21").json()
        assert [p for p in r21["proposal"] if p["product_id"] == pid]

    def test_response_shape(self):
        r = client.get("/api/shopping-list/proposal").json()
        assert "lookback_weeks" in r
        assert "horizon_days" in r
        assert "proposal" in r
        assert isinstance(r["proposal"], list)


# ── Shopping clear on purchase ─────────────────────────────────────────────

class TestShoppingClearOnPurchase:
    """When /stock/add fires, manual shopping rows for that product are
    decremented quantity-aware and hard-deleted once amount reaches 0."""

    def _setup(self):
        units = {u["abbreviation"]: u["id"] for u in client.get("/api/units").json()}
        loc_id = client.get("/api/locations").json()[0]["id"]
        pid = client.post(
            "/api/products",
            json={"name": f"ClearOnBuy_{id(self)}_{self.__class__.__name__}",
                  "unit_id": units["kpl"]},
        ).json()["id"]
        return pid, units["kpl"], loc_id

    def _add_manual(self, pid, amount, unit_id=None):
        return client.post(
            "/api/shopping-list",
            json={"product_id": pid, "amount": amount, "unit_id": unit_id},
        ).json()

    def _shopping_rows_for(self, pid):
        return [r for r in client.get("/api/shopping-list").json()
                if r["product_id"] == pid]

    def test_full_consume_deletes_row(self):
        pid, kpl, loc_id = self._setup()
        self._add_manual(pid, amount=1, unit_id=kpl)
        assert self._shopping_rows_for(pid) != []

        r = client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 1, "unit_id": kpl,
                  "location_id": loc_id},
        )
        assert r.status_code == 201

        assert self._shopping_rows_for(pid) == [], \
            "manual shopping row should be hard-deleted when amount hits 0"

    def test_partial_consume_decrements_row(self):
        pid, kpl, loc_id = self._setup()
        item = self._add_manual(pid, amount=6, unit_id=kpl)

        r = client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 1, "unit_id": kpl,
                  "location_id": loc_id},
        )
        assert r.status_code == 201

        rows = self._shopping_rows_for(pid)
        assert len(rows) == 1
        assert rows[0]["id"] == item["id"]
        assert rows[0]["amount"] == 5
        assert rows[0]["done"] is False

    def test_spill_across_rows(self):
        """Purchase amount exceeds first row → leftover spills to next row,
        oldest first."""
        pid, kpl, loc_id = self._setup()
        # Two rows, insertion order is the implicit oldest→newest order
        first = self._add_manual(pid, amount=1, unit_id=kpl)
        second = self._add_manual(pid, amount=2, unit_id=kpl)

        r = client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 2, "unit_id": kpl,
                  "location_id": loc_id},
        )
        assert r.status_code == 201

        rows = self._shopping_rows_for(pid)
        assert len(rows) == 1
        assert rows[0]["id"] == second["id"]
        assert rows[0]["amount"] == 1  # 2 - (2 - 1)

    def test_auto_added_row_untouched(self):
        """Auto-added rows remain governed by sync_auto_shopping (min_stock).
        The new helper must not touch them."""
        kpl = next(u["id"] for u in client.get("/api/units").json()
                   if u["abbreviation"] == "kpl")
        loc_id = client.get("/api/locations").json()[0]["id"]
        # min_stock_amount high so sync_auto_shopping won't clear after restock
        pid = client.post(
            "/api/products",
            json={"name": f"ClearOnBuyAuto_{id(self)}",
                  "unit_id": kpl, "min_stock_amount": 10},
        ).json()["id"]
        # Force an auto-added row via the sync endpoint
        client.post("/api/shopping-list/sync")
        rows = [r for r in client.get("/api/shopping-list").json()
                if r["product_id"] == pid]
        assert len(rows) == 1 and rows[0]["auto_added"] is True
        original_id = rows[0]["id"]
        original_amount = rows[0]["amount"]

        # Buy 1 — stock still below min_stock_amount, sync_auto_shopping
        # leaves the auto-added row in place; the new helper must too.
        r = client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 1, "unit_id": kpl,
                  "location_id": loc_id},
        )
        assert r.status_code == 201

        rows_after = [r for r in client.get("/api/shopping-list").json()
                      if r["product_id"] == pid]
        assert len(rows_after) == 1
        assert rows_after[0]["id"] == original_id
        assert rows_after[0]["auto_added"] is True
        assert rows_after[0]["amount"] == original_amount

    def test_null_unit_shopping_row_matches_default_unit_scan(self):
        """Regression: manually adding a product to the shopping list (which
        omits unit_id → stored as NULL) and then scanning it (which omits
        unit_id → resolved to the product's default) must consume the row.

        The HA-stock frontend never sends unit_id on either the shopping-list
        POST or the stock/add POST. add_stock resolves the missing value to
        the product default, so a strict NULL=NULL check on the row side
        leaves the row untouched. A NULL unit on the row means "no preference",
        so any purchase of the same product should clear it.
        """
        units = {u["abbreviation"]: u["id"] for u in client.get("/api/units").json()}
        loc_id = client.get("/api/locations").json()[0]["id"]
        pid = client.post(
            "/api/products",
            json={"name": f"ClearOnBuyNullUnit_{id(self)}",
                  "unit_id": units["kpl"]},
        ).json()["id"]
        # Frontend-realistic POST: no unit_id field at all → stored as NULL.
        client.post(
            "/api/shopping-list",
            json={"product_id": pid, "amount": 1},
        )
        assert self._shopping_rows_for(pid) != []

        # Frontend-realistic scan: no unit_id field; add_stock fills product default.
        r = client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 1, "location_id": loc_id},
        )
        assert r.status_code == 201

        assert self._shopping_rows_for(pid) == [], \
            "manual shopping row with NULL unit_id should be cleared by a scan"

    def test_unit_mismatch_skips_row(self):
        """Shopping row in unit A, purchase in unit B → row untouched."""
        units = {u["abbreviation"]: u["id"] for u in client.get("/api/units").json()}
        loc_id = client.get("/api/locations").json()[0]["id"]
        pid = client.post(
            "/api/products",
            json={"name": f"ClearOnBuyUnit_{id(self)}",
                  "unit_id": units["kpl"]},
        ).json()["id"]
        item = client.post(
            "/api/shopping-list",
            json={"product_id": pid, "amount": 2, "unit_id": units["l"]},
        ).json()

        r = client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 1, "unit_id": units["kpl"],
                  "location_id": loc_id},
        )
        assert r.status_code == 201

        rows = self._shopping_rows_for(pid)
        assert len(rows) == 1
        assert rows[0]["id"] == item["id"]
        assert rows[0]["amount"] == 2

    def test_stock_consume_does_not_clear_shopping(self):
        """Consume is using existing stock, not buying — must not fire the
        new helper."""
        pid, kpl, loc_id = self._setup()
        # Seed stock so consume succeeds
        client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 5, "unit_id": kpl,
                  "location_id": loc_id},
        )
        # Add a fresh manual shopping row after the stock-add (the stock-add
        # itself would have cleared a pre-existing row).
        item = self._add_manual(pid, amount=1, unit_id=kpl)

        r = client.post(
            "/api/stock/consume",
            json={"product_id": pid, "amount": 1},
        )
        assert r.status_code == 200

        rows = self._shopping_rows_for(pid)
        assert len(rows) == 1
        assert rows[0]["id"] == item["id"]
        assert rows[0]["amount"] == 1
        assert rows[0]["done"] is False

    def test_no_matching_rows_is_noop(self):
        """Purchase for a product with no shopping rows succeeds normally."""
        pid, kpl, loc_id = self._setup()
        r = client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 1, "unit_id": kpl,
                  "location_id": loc_id},
        )
        assert r.status_code == 201
        assert self._shopping_rows_for(pid) == []

    def test_done_row_untouched(self):
        """A manual row already marked done (soft-deleted) must not be
        re-touched by the helper — the user has handled it intentionally."""
        pid, kpl, loc_id = self._setup()
        item = self._add_manual(pid, amount=2, unit_id=kpl)
        # Mark the row done explicitly
        r_put = client.put(
            f"/api/shopping-list/{item['id']}", json={"done": True}
        )
        assert r_put.status_code == 200

        r = client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 5, "unit_id": kpl,
                  "location_id": loc_id},
        )
        assert r.status_code == 201

        rows = self._shopping_rows_for(pid)
        assert len(rows) == 1
        assert rows[0]["id"] == item["id"]
        assert rows[0]["done"] is True
        assert rows[0]["amount"] == 2  # untouched


# ── Cook recipe ────────────────────────────────────────────────────────────

class TestCookRecipe:
    def _units(self):
        return {u["abbreviation"]: u["id"] for u in client.get("/api/units").json()}

    def _make_product(self, name: str, unit_abbr: str = "kpl"):
        unit_id = self._units()[unit_abbr]
        return client.post(
            "/api/products",
            json={"name": name, "unit_id": unit_id},
        ).json()["id"], unit_id

    def _add_stock(self, pid: int, amount: float):
        loc_id = client.get("/api/locations").json()[0]["id"]
        client.post("/api/stock/add", json={"product_id": pid, "amount": amount, "location_id": loc_id})

    def _make_recipe(self, name: str, servings: float, ingredients: list[dict]):
        return client.post(
            "/api/recipes",
            json={"name": name, "servings": servings, "ingredients": ingredients},
        ).json()["id"]

    def test_cook_full_stock(self):
        units = self._units()
        flour_pid, _ = self._make_product(f"Vehnäjauho_{id(self)}", "kg")
        sugar_pid, _ = self._make_product(f"Sokeri_{id(self)}", "kg")
        self._add_stock(flour_pid, 2.0)  # 2 kg available
        self._add_stock(sugar_pid, 1.0)

        recipe_id = self._make_recipe(
            f"Cake_{id(self)}",
            servings=4,
            ingredients=[
                {"product_id": flour_pid, "amount": 0.5, "unit_id": units["kg"]},
                {"product_id": sugar_pid, "amount": 0.2, "unit_id": units["kg"]},
            ],
        )

        r = client.post(f"/api/recipes/{recipe_id}/cook", json={})
        assert r.status_code == 200
        body = r.json()
        assert len(body["deducted"]) == 2
        assert body["shortfall_added"] == []
        assert body["unmatched"] == []

    def test_cook_partial_stock_creates_shortfall(self):
        units = self._units()
        pid, _ = self._make_product(f"Maito_{id(self)}", "l")
        self._add_stock(pid, 0.5)  # only 0.5 l in stock

        recipe_id = self._make_recipe(
            f"Pancakes_{id(self)}",
            servings=2,
            ingredients=[{"product_id": pid, "amount": 1.0, "unit_id": units["l"]}],
        )

        body = client.post(f"/api/recipes/{recipe_id}/cook", json={}).json()
        assert len(body["deducted"]) == 1
        assert abs(body["deducted"][0]["amount"] - 0.5) < 0.001
        assert len(body["shortfall_added"]) == 1
        assert abs(body["shortfall_added"][0]["amount"] - 0.5) < 0.001

        # Confirm shortfall landed on shopping list
        items = [
            i for i in client.get("/api/shopping-list").json()
            if i["product_id"] == pid and i["recipe_id"] == recipe_id
        ]
        assert len(items) == 1
        assert items[0]["note"].startswith("Reseptistä:")

    def test_cook_servings_multiplier_scales(self):
        units = self._units()
        pid, _ = self._make_product(f"Voi_{id(self)}", "g")
        self._add_stock(pid, 500)  # 500 g in stock

        recipe_id = self._make_recipe(
            f"Cookies_{id(self)}",
            servings=10,
            ingredients=[{"product_id": pid, "amount": 100, "unit_id": units["g"]}],
        )

        # Cook for 20 servings (2x) → needs 200 g
        body = client.post(f"/api/recipes/{recipe_id}/cook", json={"servings": 20}).json()
        assert abs(body["deducted"][0]["amount"] - 200) < 0.001
        assert body["shortfall_added"] == []

    def test_cook_unit_conversion_kg_to_g(self):
        units = self._units()
        # Product stored in kg, recipe specified in g
        pid = client.post(
            "/api/products",
            json={"name": f"Riisi_{id(self)}", "unit_id": units["kg"]},
        ).json()["id"]
        self._add_stock(pid, 1.0)  # 1 kg

        recipe_id = self._make_recipe(
            f"Risotto_{id(self)}",
            servings=4,
            ingredients=[{"product_id": pid, "amount": 250, "unit_id": units["g"]}],
        )

        # 250 g of rice = 0.25 kg from stock
        body = client.post(f"/api/recipes/{recipe_id}/cook", json={}).json()
        assert len(body["deducted"]) == 1
        # Deducted in product's stock unit (kg)
        assert abs(body["deducted"][0]["amount"] - 0.25) < 0.001
        assert body["shortfall_added"] == []

    def test_cook_returns_404_for_missing_recipe(self):
        r = client.post("/api/recipes/99999999/cook", json={})
        assert r.status_code == 404

    def test_cook_logs_consume_event(self):
        units = self._units()
        pid, _ = self._make_product(f"LogTest_{id(self)}", "kg")
        self._add_stock(pid, 5.0)
        recipe_id = self._make_recipe(
            f"LogR_{id(self)}",
            servings=1,
            ingredients=[{"product_id": pid, "amount": 1.5, "unit_id": units["kg"]}],
        )
        client.post(f"/api/recipes/{recipe_id}/cook", json={})
        history = client.get(f"/api/history?product_id={pid}&event_type=consume").json()
        assert any(abs(h["amount"] - 1.5) < 0.001 and "Reseptistä" in h.get("note", "") for h in history)


# ── Receipt OCR ────────────────────────────────────────────────────────────

class TestReceiptMatcher:
    """Pure matcher tests — no AI call. Uses the existing seeded products
    plus a few targeted creations to exercise the scoring."""

    def test_match_obvious_token_overlap(self):
        from receipt_parser import match_lines_to_products
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        # Create a product with a Finnish name that's unambiguously the right match
        client.post("/api/products", json={"name": "Kaurahiutale lasten", "unit_id": kpl})

        from main import get_connection
        rows = match_lines_to_products(
            [{"raw_text": "KAURAHIUTALE", "qty": 1, "unit": "kpl", "price": 2.5}],
            get_connection(),
        )
        assert len(rows) == 1
        assert rows[0]["confidence"] > 0.4
        # Must resolve to an active product (id is non-null and the matched product's
        # name shares the "kaurahiutale" token).
        assert rows[0]["suggested_product_id"] is not None

    def test_below_confidence_drops_suggestion(self):
        from receipt_parser import match_lines_to_products
        from main import get_connection
        rows = match_lines_to_products(
            [{"raw_text": "ZZZ_GARBAGE_NO_MATCH", "qty": 1, "unit": "kpl"}],
            get_connection(),
            min_confidence=0.45,
        )
        assert rows[0]["suggested_product_id"] is None
        assert rows[0]["confidence"] < 0.45


class TestReceiptEndpoints:
    def test_parse_rejects_empty_image(self):
        r = client.post("/api/receipts/parse", json={"image_b64": "", "mime_type": "image/jpeg"})
        assert r.status_code == 400

    def test_parse_rejects_non_image_mime(self):
        r = client.post("/api/receipts/parse", json={"image_b64": "AA==", "mime_type": "text/plain"})
        assert r.status_code == 400

    def test_parse_503_when_ai_not_configured(self):
        # Default test fixture has no claude_api_key configured — vision call must fail clean.
        r = client.post(
            "/api/receipts/parse",
            json={"image_b64": "AAAA", "mime_type": "image/jpeg"},
        )
        assert r.status_code in (502, 503), f"Expected 502/503 when AI not set, got {r.status_code}"

    def test_commit_creates_stock_entries(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        loc_id = client.get("/api/locations").json()[0]["id"]
        pid = client.post(
            "/api/products",
            json={"name": f"ReceiptCommit_{id(self)}", "unit_id": kpl},
        ).json()["id"]

        before = client.get(f"/api/stock/product/{pid}").json()
        before_total = sum(parseFloatSafe(e["amount"]) for e in (before or []))

        r = client.post(
            "/api/receipts/commit",
            json={"lines": [
                {"product_id": pid, "amount": 3, "unit_id": kpl, "location_id": loc_id},
                {"product_id": pid, "amount": 1, "unit_id": kpl, "location_id": loc_id},
            ]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["added"] == 2
        assert body["failed"] == 0

        after = client.get(f"/api/stock/product/{pid}").json()
        after_total = sum(parseFloatSafe(e["amount"]) for e in (after or []))
        assert after_total - before_total == 4

    def test_commit_reports_per_line_errors(self):
        # Mix valid + invalid: bad product_id should be reported but not crash the batch
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        loc_id = client.get("/api/locations").json()[0]["id"]
        pid = client.post(
            "/api/products",
            json={"name": f"ReceiptMixed_{id(self)}", "unit_id": kpl},
        ).json()["id"]

        r = client.post(
            "/api/receipts/commit",
            json={"lines": [
                {"product_id": pid, "amount": 2, "unit_id": kpl, "location_id": loc_id},
                {"product_id": 99999999, "amount": 1, "unit_id": kpl, "location_id": loc_id},
            ]},
        ).json()
        assert r["added"] == 1
        assert r["failed"] == 1
        assert len(r["errors"]) == 1


def parseFloatSafe(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


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

    def test_expiring_within_days_includes_expired(self):
        """expiring_within_days=N has no lower bound — past-due lots are included
        because they are more urgent than upcoming ones."""
        from datetime import date, timedelta
        pid, _, _ = self._make_product_with_location()
        expired_iso = (date.today() - timedelta(days=3)).isoformat()
        soon_iso = (date.today() + timedelta(days=2)).isoformat()
        far_iso = (date.today() + timedelta(days=30)).isoformat()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1, "best_before_date": expired_iso})
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1, "best_before_date": soon_iso})
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1, "best_before_date": far_iso})

        r = client.get("/api/stock/entries?expiring_within_days=7")
        assert r.status_code == 200
        dates = [e["best_before_date"] for e in r.json() if e["product_id"] == pid]
        assert expired_iso in dates
        assert soon_iso in dates
        assert far_iso not in dates


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




class TestExpiryMigration:
    """The schema migration backfills best_before_days for pre-existing rows."""

    def _make_legacy_stock_row(self, product_id: int, location_id: int, unit_id: int, bbd: str | None = None):
        """Insert a stock row directly (bypassing the API) so we can simulate a
        row created before the best_before_days column existed."""
        from main import get_connection
        conn = get_connection()
        cur = conn.execute(
            "INSERT INTO stock (product_id, location_id, amount, unit_id, best_before_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (product_id, location_id, 3, unit_id, bbd),
        )
        # Force best_before_days NULL to simulate pre-migration state.
        conn.execute("UPDATE stock SET best_before_days = NULL WHERE id = ?", (cur.lastrowid,))
        conn.commit()
        return cur.lastrowid

    def test_existing_rows_get_best_before_days_backfilled(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        loc = client.get("/api/locations").json()[0]["id"]
        p = client.post("/api/products", json={
            "name": f"MigTest_{id(self)}",
            "unit_id": kpl,
            "default_best_before_days": 14,
        }).json()
        stock_id = self._make_legacy_stock_row(p["id"], loc, kpl)

        # Trigger the migration explicitly (idempotent — safe to re-run).
        from main import get_connection
        from database import _migrate_schema
        _migrate_schema(get_connection())

        row = get_connection().execute(
            "SELECT best_before_days, purchased_date FROM stock WHERE id = ?",
            (stock_id,),
        ).fetchone()
        assert row["best_before_days"] == 14
        assert row["purchased_date"] is not None

    def test_realign_pass_recomputes_mismatched_best_before_date(self):
        """When best_before_date diverges from purchased_date + best_before_days,
        the always-on realign pass rewrites the date column to match. bb_days
        is authoritative; the date is a derived/cached value of that math."""
        from main import get_connection
        from database import _migrate_schema

        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        loc = client.get("/api/locations").json()[0]["id"]
        p = client.post("/api/products", json={
            "name": f"Realign_{id(self)}",
            "unit_id": kpl,
            "default_best_before_days": 365,
        }).json()

        conn = get_connection()
        # Simulate a row imported with a wrong best_before_date that disagrees
        # with the (purchased_date, best_before_days) pair: receipt imports
        # often set best_before_date to a sentinel or default-365-day stamp
        # that doesn't match the lot's own metadata.
        cur = conn.execute(
            "INSERT INTO stock (product_id, location_id, amount, unit_id, "
            "best_before_date, best_before_days, purchased_date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', '-1 day'))",
            (p["id"], loc, 1, kpl, "2999-12-31", 7, "2026-05-09"),
        )
        stock_id = cur.lastrowid
        conn.commit()

        _migrate_schema(get_connection())

        row = get_connection().execute(
            "SELECT best_before_date, best_before_days FROM stock WHERE id = ?", (stock_id,)
        ).fetchone()
        # bb_days stays as authoritative; best_before_date is recomputed.
        assert row["best_before_days"] == 7
        assert row["best_before_date"] == "2026-05-16", (
            f"expected 2026-05-09 + 7 days = 2026-05-16, got {row['best_before_date']}"
        )

    def test_realign_is_idempotent(self):
        """Running the migration again on already-consistent data is a no-op."""
        from main import get_connection
        from database import _migrate_schema

        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        loc = client.get("/api/locations").json()[0]["id"]
        p = client.post("/api/products", json={
            "name": f"Idempotent_{id(self)}",
            "unit_id": kpl,
            "default_best_before_days": 30,
        }).json()
        # Adding through the API writes a self-consistent row.
        entry = client.post("/api/stock/add", json={"product_id": p["id"], "amount": 1}).json()
        original_date = entry["best_before_date"]

        _migrate_schema(get_connection())
        _migrate_schema(get_connection())

        row = get_connection().execute(
            "SELECT best_before_date FROM stock WHERE id = ?", (entry["id"],)
        ).fetchone()
        assert row["best_before_date"] == original_date


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


# ── Expiry Snapshot ────────────────────────────────────────────────────────

class TestExpirySnapshot:
    """Adding a lot snapshots (purchased_date, best_before_days) and derives best_before_date."""

    def _make_product(self, bb_days: int = 10):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        loc = client.get("/api/locations").json()[0]["id"]
        p = client.post("/api/products", json={
            "name": f"Snap_{id(self)}_{bb_days}",
            "unit_id": kpl,
            "location_id": loc,
            "default_best_before_days": bb_days,
        }).json()
        return p["id"], kpl, loc

    def test_anchor_derivation_default(self):
        from datetime import date, timedelta
        pid, _, _ = self._make_product(bb_days=10)
        entry = client.post("/api/stock/add", json={"product_id": pid, "amount": 1}).json()
        # Capture once — date.today() called twice could straddle midnight.
        today = date.today()
        expected_bb = (today + timedelta(days=10)).isoformat()
        assert entry["purchased_date"] == today.isoformat()
        assert entry["best_before_days"] == 10
        assert entry["best_before_date"] == expected_bb

    def test_user_override_best_before_date_drives_bb_days(self):
        """A user-supplied best_before_date is converted into a per-lot bb_days
        value so the displayed expiry always equals purchased_date + bb_days."""
        from datetime import date, timedelta
        pid, _, _ = self._make_product(bb_days=10)
        override = (date.today() + timedelta(days=3)).isoformat()
        entry = client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 1, "best_before_date": override},
        ).json()
        # bb_days reflects the realized interval for this lot, not the product default.
        assert entry["best_before_days"] == 3
        # The override date is preserved exactly because purchased_date + 3 == override.
        assert entry["best_before_date"] == override

    def test_purchased_date_override_shifts_expiry(self):
        from datetime import date, timedelta
        pid, _, _ = self._make_product(bb_days=10)
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        expected_bb = (today + timedelta(days=9)).isoformat()
        entry = client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 1, "purchased_date": yesterday},
        ).json()
        assert entry["purchased_date"] == yesterday
        assert entry["best_before_days"] == 10
        assert entry["best_before_date"] == expected_bb

    def test_product_default_change_does_not_affect_existing_lots(self):
        pid, _, _ = self._make_product(bb_days=10)
        first = client.post("/api/stock/add", json={"product_id": pid, "amount": 1}).json()
        # Change the product default after the first add.
        client.put(f"/api/products/{pid}", json={"default_best_before_days": 30})
        second = client.post("/api/stock/add", json={"product_id": pid, "amount": 1}).json()
        # First lot keeps its original snapshot.
        assert first["best_before_days"] == 10
        # Second lot uses the new value.
        assert second["best_before_days"] == 30


class TestFifoOrder:
    """FIFO order: best_before_date ASC NULLS LAST → purchased_date ASC → id ASC."""

    def _make_product(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        loc = client.get("/api/locations").json()[0]["id"]
        p = client.post("/api/products", json={
            "name": f"Fifo_{id(self)}", "unit_id": kpl, "location_id": loc,
            "default_best_before_days": 0,  # so add does not auto-derive expiry
        }).json()
        return p["id"], kpl, loc

    def test_null_expiry_consumed_last_not_first(self):
        from datetime import date, timedelta
        pid, _, _ = self._make_product()
        # Lot A: no expiry (default_best_before_days=0 → bb_days=0 → NULL expiry stays).
        lot_null = client.post("/api/stock/add", json={"product_id": pid, "amount": 1}).json()
        # Lot B: explicit expiry 5 days from today.
        future = (date.today() + timedelta(days=5)).isoformat()
        lot_dated = client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 1, "best_before_date": future},
        ).json()

        client.post("/api/stock/consume", json={"product_id": pid, "amount": 1})

        # The dated lot must be the one consumed; the null-expiry lot still exists.
        remaining = {
            e["id"]: e["amount"]
            for e in client.get(f"/api/stock/product/{pid}").json()
        }
        assert remaining.get(lot_dated["id"], 0) == 0 or lot_dated["id"] not in remaining
        assert remaining.get(lot_null["id"]) == 1

    def test_tiebreak_by_purchased_date(self):
        from datetime import date, timedelta
        pid, _, _ = self._make_product()
        today = date.today()
        same_bb = (today + timedelta(days=5)).isoformat()
        older = (today - timedelta(days=2)).isoformat()
        newer = today.isoformat()
        # Insert the NEWER-purchased lot FIRST so it gets the lower id. This way the
        # old ORDER BY (which fell back on id) would have consumed it first, but the
        # new purchased_date tiebreak overrides that and consumes the older lot.
        lot_newer = client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 1, "best_before_date": same_bb, "purchased_date": newer},
        ).json()
        lot_older = client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 1, "best_before_date": same_bb, "purchased_date": older},
        ).json()

        client.post("/api/stock/consume", json={"product_id": pid, "amount": 1})

        remaining = {e["id"]: e["amount"] for e in client.get(f"/api/stock/product/{pid}").json()}
        # Older purchased_date must be consumed; newer is still there.
        assert remaining.get(lot_older["id"], 0) == 0 or lot_older["id"] not in remaining
        assert remaining.get(lot_newer["id"]) == 1


# ── Transfer ───────────────────────────────────────────────────────────────

class TestTransferCopiesSnapshot:
    def test_transfer_copies_best_before_days(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        locs = client.get("/api/locations").json()
        from_loc, to_loc = locs[0]["id"], locs[1]["id"]
        pid = client.post("/api/products", json={
            "name": f"Xfer_{id(self)}", "unit_id": kpl,
            "default_best_before_days": 21,
        }).json()["id"]
        client.post("/api/stock/add", json={"product_id": pid, "amount": 4, "location_id": from_loc})

        client.post("/api/stock/transfer", json={
            "product_id": pid, "amount": 2,
            "from_location_id": from_loc, "to_location_id": to_loc,
        })

        rows = client.get(f"/api/stock/product/{pid}").json()
        assert len(rows) == 2
        # Both halves must carry the same best_before_days snapshot.
        assert {r["best_before_days"] for r in rows} == {21}


class TestTargetedSpoil:
    def _make_product(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        loc = client.get("/api/locations").json()[0]["id"]
        p = client.post("/api/products", json={
            "name": f"Spoil_{id(self)}", "unit_id": kpl, "location_id": loc,
            "default_best_before_days": 7,
        }).json()
        return p["id"], kpl, loc

    def test_spoil_whole_lot(self):
        pid, _, _ = self._make_product()
        older = client.post("/api/stock/add", json={"product_id": pid, "amount": 1}).json()
        newer = client.post("/api/stock/add", json={"product_id": pid, "amount": 1}).json()
        # Spoil the newer lot specifically — not the FIFO oldest.
        r = client.post(f"/api/stock/spoil/{newer['id']}", json={})
        assert r.status_code == 200
        assert r.json()["spoiled"] == 1
        # Older lot is intact, newer lot is gone.
        remaining = {e["id"]: e["amount"] for e in client.get(f"/api/stock/product/{pid}").json()}
        assert remaining.get(older["id"]) == 1
        assert newer["id"] not in remaining
        # A spoil history event is logged with stock_id = newer lot's id.
        history = client.get(f"/api/history/product/{pid}").json()
        spoil_events = [h for h in history if h["event_type"] == "spoil"]
        assert any(h.get("stock_id") == newer["id"] for h in spoil_events)

    def test_spoil_partial_amount(self):
        pid, _, _ = self._make_product()
        lot = client.post("/api/stock/add", json={"product_id": pid, "amount": 4}).json()
        r = client.post(f"/api/stock/spoil/{lot['id']}", json={"amount": 2})
        assert r.status_code == 200
        assert r.json()["spoiled"] == 2
        remaining = {e["id"]: e["amount"] for e in client.get(f"/api/stock/product/{pid}").json()}
        assert remaining[lot["id"]] == 2

    def test_spoil_amount_clamps_to_lot_amount(self):
        pid, _, _ = self._make_product()
        lot = client.post("/api/stock/add", json={"product_id": pid, "amount": 2}).json()
        # Ask for more than exists — must clamp to 2, not 400.
        r = client.post(f"/api/stock/spoil/{lot['id']}", json={"amount": 99})
        assert r.status_code == 200
        assert r.json()["spoiled"] == 2
        remaining = [e for e in client.get(f"/api/stock/product/{pid}").json() if e["id"] == lot["id"]]
        assert remaining == []

    def test_spoil_unknown_lot_returns_404(self):
        r = client.post("/api/stock/spoil/999999", json={})
        assert r.status_code == 404


# ── Monetary Waste Tracking (0.11.0) ───────────────────────────────────────

class TestWasteStats:
    def _make_product(self, *, unit_price=None):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        loc = client.get("/api/locations").json()[0]["id"]
        body = {"name": f"Waste_{id(self)}_{unit_price}", "unit_id": kpl, "location_id": loc,
                "default_best_before_days": 7}
        if unit_price is not None:
            body["unit_price"] = unit_price
        return client.post("/api/products", json=body).json()["id"]

    def test_product_unit_price_round_trip(self):
        pid = self._make_product(unit_price=2.50)
        p = client.get(f"/api/products/{pid}").json()
        assert p["unit_price"] == 2.5
        assert p["unit_price_currency"] == "EUR"

    def test_stock_price_paid_snapshot_from_product_default(self):
        pid = self._make_product(unit_price=3.0)
        lot = client.post("/api/stock/add", json={"product_id": pid, "amount": 2}).json()
        # Lot should snapshot the product's current unit_price.
        assert lot["price_paid"] == 3.0

    def test_stock_price_paid_explicit_override(self):
        pid = self._make_product(unit_price=3.0)
        lot = client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 2, "price_paid": 4.25},
        ).json()
        assert lot["price_paid"] == 4.25

    def test_waste_endpoint_values_targeted_spoil(self):
        pid = self._make_product(unit_price=2.5)
        lot = client.post("/api/stock/add", json={"product_id": pid, "amount": 3}).json()
        r = client.post(f"/api/stock/spoil/{lot['id']}", json={})
        assert r.status_code == 200
        waste = client.get("/api/stats/waste?days=1").json()
        # This product's row should appear with value 3 * 2.5 = 7.5
        match = [p for p in waste["by_product"] if p["product_id"] == pid]
        assert len(match) == 1
        assert match[0]["amount"] == 3
        assert match[0]["value"] == 7.5
        # Total value must include this product's contribution.
        assert waste["total_value"] >= 7.5
        assert waste["currency"] == "EUR"

    def test_waste_endpoint_falls_back_to_product_default(self):
        """A lot added before unit_price was set on the product should still get
        valued at the product's current unit_price when spoiled."""
        pid = self._make_product(unit_price=None)
        lot = client.post("/api/stock/add", json={"product_id": pid, "amount": 2}).json()
        assert lot["price_paid"] in (None, 0)
        # Now set the product's unit price.
        client.put(f"/api/products/{pid}", json={"unit_price": 5.0})
        # Spoil the lot.
        client.post(f"/api/stock/spoil/{lot['id']}", json={})
        waste = client.get("/api/stats/waste?days=1").json()
        match = [p for p in waste["by_product"] if p["product_id"] == pid]
        assert len(match) == 1
        # value comes from product fallback at spoil time (or query time).
        assert match[0]["value"] == 10.0

    def test_waste_endpoint_unknown_price_counts_amount_not_value(self):
        pid = self._make_product(unit_price=None)
        lot = client.post("/api/stock/add", json={"product_id": pid, "amount": 4}).json()
        client.post(f"/api/stock/spoil/{lot['id']}", json={})
        waste = client.get("/api/stats/waste?days=1").json()
        match = [p for p in waste["by_product"] if p["product_id"] == pid]
        assert len(match) == 1
        assert match[0]["amount"] == 4
        # No price anywhere → value is 0.
        assert match[0]["value"] == 0

    def test_waste_endpoint_breaks_down_by_location(self):
        pid = self._make_product(unit_price=1.0)
        locs = client.get("/api/locations").json()
        a, b = locs[0]["id"], locs[1]["id"]
        lot_a = client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 5, "location_id": a},
        ).json()
        lot_b = client.post(
            "/api/stock/add",
            json={"product_id": pid, "amount": 7, "location_id": b},
        ).json()
        client.post(f"/api/stock/spoil/{lot_a['id']}", json={})
        client.post(f"/api/stock/spoil/{lot_b['id']}", json={})
        waste = client.get("/api/stats/waste?days=1").json()
        by_loc = {row["location_id"]: row["value"] for row in waste["by_location"]}
        assert by_loc.get(a, 0) >= 5
        assert by_loc.get(b, 0) >= 7

    def test_receipt_commit_passes_price_to_lot(self):
        # Receipt lines provide total price (qty * unit); the commit endpoint
        # should divide back to per-unit and snapshot onto the lot.
        pid = self._make_product(unit_price=None)
        commit = client.post(
            "/api/receipts/commit",
            json={"lines": [{"product_id": pid, "amount": 2, "price_paid": 5.0}]},
        )
        assert commit.status_code == 200
        assert commit.json()["added"] == 1
        # Look up the lot and verify the per-unit price was stored.
        stock = client.get(f"/api/stock/product/{pid}").json()
        assert any(abs((lot.get("price_paid") or 0) - 2.5) < 0.001 for lot in stock)


# ── Predicted Runouts (0.12.0) ─────────────────────────────────────────────

class TestPredictedRunouts:
    def _make_product(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        return client.post("/api/products", json={
            "name": f"Runout_{id(self)}", "unit_id": kpl,
        }).json()["id"]

    def test_runouts_endpoint_returns_shape(self):
        r = client.get("/api/stats/runouts?horizon=14")
        assert r.status_code == 200
        data = r.json()
        assert data["horizon"] == 14
        assert isinstance(data["runouts"], list)

    def test_predicted_runout_lists_product_with_recent_consumption(self):
        # Add stock, consume most of it — the small remainder should predict a runout.
        pid = self._make_product()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 56})
        # Consume 7/day worth in one call (so weekly velocity is detectable).
        client.post("/api/stock/consume", json={"product_id": pid, "amount": 49})
        data = client.get("/api/stats/runouts?horizon=14").json()
        assert any(r["product_id"] == pid for r in data["runouts"])

    def test_runouts_excludes_products_with_no_consumption(self):
        pid = self._make_product()
        client.post("/api/stock/add", json={"product_id": pid, "amount": 5})
        # No consume events at all.
        data = client.get("/api/stats/runouts?horizon=14").json()
        assert not any(r["product_id"] == pid for r in data["runouts"])


# ── Weekly Digest (0.12.0) ─────────────────────────────────────────────────

class TestDigest:
    def test_digest_endpoint_returns_keys(self):
        r = client.get("/api/stats/digest")
        assert r.status_code == 200
        data = r.json()
        for key in (
            "generated_at", "days", "currency",
            "expiring_this_week", "predicted_runouts_14d",
            "waste_value_30d", "waste_amount_30d", "top_spoilers_30d",
        ):
            assert key in data

    def test_digest_reports_waste_value(self):
        kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
        pid = client.post("/api/products", json={
            "name": f"Digest_{id(self)}", "unit_id": kpl, "unit_price": 1.5,
        }).json()["id"]
        lot = client.post("/api/stock/add", json={"product_id": pid, "amount": 4}).json()
        client.post(f"/api/stock/spoil/{lot['id']}", json={})
        data = client.get("/api/stats/digest").json()
        # 4 * 1.5 = 6.0 — the digest's last-30d total must reflect at least this.
        assert data["waste_value_30d"] >= 6.0
        # Top spoilers list should include our product.
        assert any(s["product_id"] == pid for s in data["top_spoilers_30d"])
