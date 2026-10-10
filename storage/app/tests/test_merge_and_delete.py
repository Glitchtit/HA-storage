"""Product merge and the delete guard: no silent loss of recipe data."""
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
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestMerge:
    def test_moves_everything_and_keeps_duplicate_ingredients(self):
        keep = _mk("kaneli-m1", unit="g", kind="concept")
        dup = _mk("kaneli-m1 dup", unit="g", kind="concept", density_g_ml=0.56)
        child = _mk("Meira kaneli 30g m1", parent_id=dup)
        rid = client.post("/api/recipes", json={"name": "Pulla m1", "ingredients": [
            {"product_id": dup, "amount": 2, "unit_id": _u("tl")},
            {"product_id": dup, "amount": 1, "unit_id": _u("tl")},
        ]}).json()["id"]
        row = client.post("/api/shopping-list", json={"product_id": dup, "amount": 1}).json()
        r = client.post(f"/api/products/{dup}/merge", json={"target_id": keep})
        assert r.status_code == 200 and r.json()["density_g_ml"] == 0.56
        ings = client.get(f"/api/recipes/{rid}").json()["ingredients"]
        assert [i["product_id"] for i in ings] == [keep, keep]
        assert client.get(f"/api/products/{child}").json()["parent_id"] == keep
        moved = next(i for i in client.get("/api/shopping-list").json() if i["id"] == row["id"])
        assert moved["product_id"] == keep and moved["ha_item_name"] == "kaneli-m1"
        assert client.get(f"/api/products/{dup}").status_code == 404

    def test_bundle_items_deduplicated(self):
        a = _mk("maito-m2", unit="dl", kind="concept")
        b = _mk("maito-m2 dup", unit="dl", kind="concept")
        bundle = client.post("/api/bundles", json={"name": "Aamu m2", "items": [
            {"product_id": a}, {"product_id": b}]}).json()
        client.post(f"/api/products/{b}/merge", json={"target_id": a})
        items = client.get(f"/api/bundles/{bundle['id']}").json()["items"]
        assert [i["product_id"] for i in items] == [a]

    def test_sku_merge_moves_lots_and_barcodes(self):
        a = _mk("Pirkka spagetti 1kg m3")
        b = _mk("Pirkka spagetti 1kg m3 dup")
        client.post("/api/stock/add", json={"product_id": b, "amount": 2})
        client.post("/api/barcodes", json={"product_id": b, "barcode": f"64{b:011d}"})
        client.post(f"/api/products/{b}/merge", json={"target_id": a})
        detail = client.get(f"/api/products/{a}").json()
        assert detail["stock_amount"] == 2 and len(detail["barcodes"]) == 1

    def test_refusals(self):
        cat = _mk("juusto-m4", unit="g", kind="concept")
        variant = _mk("cheddar-m4", unit="g", kind="concept", parent_id=cat)
        sku = _mk("Cheddar 150g m4", parent_id=variant)
        client.post("/api/stock/add", json={"product_id": sku, "amount": 1})
        other_sku = _mk("Gouda 150g m4")
        assert client.post(f"/api/products/{cat}/merge", json={"target_id": cat}).status_code == 400
        assert client.post(f"/api/products/{cat}/merge", json={"target_id": variant}).status_code == 400
        assert client.post(f"/api/products/{variant}/merge", json={"target_id": other_sku}).status_code == 400
        assert client.post(f"/api/products/{sku}/merge", json={"target_id": cat}).status_code == 400
        assert client.post(f"/api/products/{cat}/merge", json={"target_id": 999999}).status_code == 404


class TestDeleteGuard:
    def test_recipe_reference_blocks_delete(self):
        pid = _mk("raparperi-g1", unit="g", kind="concept")
        rid = client.post("/api/recipes", json={"name": "Piiras g1", "ingredients": [
            {"product_id": pid, "amount": 500, "unit_id": _u("g")}]}).json()["id"]
        r = client.delete(f"/api/products/{pid}")
        assert r.status_code == 409 and "Merge" in r.json()["detail"]
        assert len(client.get(f"/api/recipes/{rid}").json()["ingredients"]) == 1

    def test_bundle_reference_blocks_delete(self):
        pid = _mk("tortilla-g2")
        client.post("/api/bundles", json={"name": "Taco g2", "items": [{"product_id": pid}]})
        assert client.delete(f"/api/products/{pid}").status_code == 409

    def test_force_deletes_anyway(self):
        pid = _mk("vanha-g3", unit="g", kind="concept")
        rid = client.post("/api/recipes", json={"name": "Vanha g3", "ingredients": [
            {"product_id": pid, "amount": 1, "unit_id": _u("g")}]}).json()["id"]
        assert client.delete(f"/api/products/{pid}", params={"force": True}).status_code == 204
        assert client.get(f"/api/recipes/{rid}").json()["ingredients"] == []

    def test_unreferenced_delete_still_works(self):
        pid = _mk("irrallinen-g4")
        assert client.delete(f"/api/products/{pid}").status_code == 204
