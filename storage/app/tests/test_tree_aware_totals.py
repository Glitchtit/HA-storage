"""Min-stock sync, bundle stock and product subtree totals read the whole
subtree in the product's own unit."""
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
from main import app

client = TestClient(app)


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _auto_rows(pid):
    return [i for i in client.get("/api/shopping-list").json()
            if i["product_id"] == pid and i["auto_added"] and not i["done"]]


class TestMinStockOnConcepts:
    def test_concept_min_stock_counts_child_skus(self):
        milk = _mk("maito-t1", unit="dl", kind="concept", min_stock_amount=10)
        sku = _mk("Maito 1l t1", parent_id=milk)
        client.post("/api/shopping-list/sync")
        assert len(_auto_rows(milk)) == 1
        client.post("/api/stock/add", json={"product_id": sku, "amount": 1})  # 10 dl
        assert _auto_rows(milk) == []

    def test_sku_min_stock_counts_packages_not_pieces(self):
        eggs = _mk("Munat 10kpl/580g t2", min_stock_amount=1)
        client.post("/api/stock/add", json={"product_id": eggs, "amount": 1})
        client.post("/api/shopping-list/sync")
        assert _auto_rows(eggs) == []  # one carton meets min 1, not "10 pieces"


class TestBundleStock:
    def test_concept_item_sees_child_stock(self):
        milk = _mk("maito-t3", unit="dl", kind="concept")
        sku = _mk("Maito 1l t3", parent_id=milk)
        client.post("/api/stock/add", json={"product_id": sku, "amount": 2})
        bundle = client.post("/api/bundles", json={"name": "Aamiainen t3", "items": [{"product_id": milk}]}).json()
        detail = client.get(f"/api/bundles/{bundle['id']}").json()
        assert detail["items"][0]["stock_amount"] == 20


class TestSubtreeTotals:
    def test_concept_total_in_own_unit(self):
        flour = _mk("vehnäjauho-t4", unit="dl", kind="concept", density_g_ml=0.6)
        sku = _mk("Vehnäjauho 2kg t4", parent_id=flour)
        client.post("/api/stock/add", json={"product_id": sku, "amount": 1})
        row = next(p for p in client.get("/api/products", params={"active_only": False}).json()
                   if p["id"] == flour)
        assert abs(row["subtree_amount"] - 33.333) < 0.01
        assert row["subtree_unit_id"] == _u("dl") and row["subtree_approx"] is True
        detail = client.get(f"/api/products/{flour}").json()
        assert abs(detail["subtree_amount"] - 33.333) < 0.01

    def test_leaf_has_no_subtree_total(self):
        sku = _mk("Yksittäinen t5")
        row = client.get(f"/api/products/{sku}").json()
        assert row["subtree_amount"] is None
