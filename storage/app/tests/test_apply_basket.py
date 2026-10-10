"""Finishing a trip re-applies basket items whose scan cleared nothing, since
the linker may have placed a new product under a concept since (spec §4.8)."""
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


def _kpl():
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")


def _mk(name, **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _kpl(), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _rows(product_id):
    return [i for i in client.get("/api/shopping-list").json() if i["product_id"] == product_id]


def _stock_total(product_id):
    return sum(e["amount"] for e in client.get(f"/api/stock/product/{product_id}").json())


def test_a_product_linked_after_its_scan_clears_the_concept_row():
    concept = _mk("Ruokakerma b1", kind="concept")
    row = client.post("/api/shopping-list", json={"product_id": concept, "amount": 1}).json()["id"]
    sku = _mk("Uusi ruokakerma 2 dl b1")
    assert client.post("/api/stock/add", json={"product_id": sku, "amount": 1}).json()["cleared_rows"] == []
    assert client.put(f"/api/products/{sku}", json={"parent_id": concept}).status_code == 200
    r = client.post("/api/shopping-list/apply-basket", json={"basket": [{"product_id": sku, "amount": 1}]})
    assert r.status_code == 200
    assert r.json() == {"cleared_rows": [{"id": row, "product_id": concept, "removed": True, "amount": 0}],
                        "unmatched": []}
    assert _rows(concept) == []


def test_items_that_still_clear_nothing_come_back_unmatched():
    sku = _mk("Heräteostos b2")
    r = client.post("/api/shopping-list/apply-basket", json={"basket": [{"product_id": sku, "amount": 2}]})
    assert r.json() == {"cleared_rows": [], "unmatched": [{"product_id": sku, "amount": 2, "unit_id": None}]}


def test_applying_the_basket_adds_no_stock():
    concept = _mk("Kurkku b3", kind="concept")
    sku = _mk("Kurkku kpl b3", parent_id=concept)
    client.post("/api/shopping-list", json={"product_id": concept, "amount": 3})
    before = _stock_total(sku)
    client.post("/api/shopping-list/apply-basket", json={"basket": [{"product_id": sku, "amount": 1}]})
    assert _stock_total(sku) == before
    assert _rows(concept)[0]["amount"] == 2
