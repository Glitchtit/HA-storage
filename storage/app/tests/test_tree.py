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
import tree

client = TestClient(app)


def _unit_id():
    return client.get("/api/units").json()[0]["id"]


def _mk(name, parent_id=None):
    r = client.post("/api/products", json={"name": name, "unit_id": _unit_id(), "parent_id": parent_id})
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestTreeHelpers:
    def test_descendants_recursive(self):
        cat = _mk("Juusto-t1")
        variant = _mk("cheddar-t1", parent_id=cat)
        sku = _mk("Valio cheddar-t1", parent_id=variant)
        conn = get_connection()
        assert set(tree.descendant_ids(conn, cat)) == {variant, sku}
        assert tree.descendant_ids(conn, sku) == []

    def test_ancestors_recursive(self):
        cat = _mk("kerma-t1")
        variant = _mk("vispikerma-t1", parent_id=cat)
        sku = _mk("Valio kuohukerma-t1", parent_id=variant)
        conn = get_connection()
        assert set(tree.ancestor_ids(conn, sku)) == {variant, cat}


class TestCycleGuard:
    def test_self_parent_rejected(self):
        pid = _mk("selfp-t1")
        r = client.put(f"/api/products/{pid}", json={"parent_id": pid})
        assert r.status_code == 400

    def test_cycle_rejected(self):
        a = _mk("cycle-a")
        b = _mk("cycle-b", parent_id=a)
        r = client.put(f"/api/products/{a}", json={"parent_id": b})
        assert r.status_code == 400

    def test_missing_parent_rejected_on_create(self):
        r = client.post("/api/products", json={"name": "orphan-t1", "unit_id": _unit_id(), "parent_id": 999999})
        assert r.status_code == 400

    def test_valid_deep_parent_accepted(self):
        a = _mk("deep-a")
        b = _mk("deep-b", parent_id=a)
        c = _mk("deep-c")
        r = client.put(f"/api/products/{c}", json={"parent_id": b})
        assert r.status_code == 200


class TestRecursiveAggregation:
    def _stock_add(self, pid, amount=1):
        r = client.post("/api/stock/add", json={"product_id": pid, "amount": amount})
        assert r.status_code in (200, 201), r.text

    def test_list_children_stock_spans_grandchildren(self):
        cat = _mk("agg-cat")
        variant = _mk("agg-variant", parent_id=cat)
        sku = _mk("agg-sku", parent_id=variant)
        self._stock_add(sku, 3)
        rows = client.get("/api/products", params={"active_only": "false"}).json()
        by_id = {p["id"]: p for p in rows}
        assert by_id[cat]["children_stock_amount"] == 3
        assert by_id[variant]["children_stock_amount"] == 3

    def test_get_product_child_stock_recursive(self):
        cat = _mk("agg2-cat")
        variant = _mk("agg2-variant", parent_id=cat)
        sku = _mk("agg2-sku", parent_id=variant)
        self._stock_add(sku, 2)
        detail = client.get(f"/api/products/{cat}").json()
        assert detail["children_stock_amount"] == 2


class TestParentsAreConcepts:
    """Parents are concepts: a plain product becomes one when it gets its
    first child; a product holding stock or barcodes is a real SKU and can
    never become a parent."""

    def _kind(self, pid):
        return client.get(f"/api/products/{pid}").json()["kind"]

    def test_plain_product_promoted_on_first_child(self):
        cat = _mk("promote-cat-t2")
        assert self._kind(cat) == "sku"
        _mk("promote-sku-t2", parent_id=cat)
        assert self._kind(cat) == "concept"

    def test_stocked_product_rejected_as_parent(self):
        sku = _mk("stocked-sku-t3")
        client.post("/api/stock/add", json={"product_id": sku, "amount": 1})
        r = client.post("/api/products", json={
            "name": "child-t3", "unit_id": _unit_id(), "parent_id": sku})
        assert r.status_code == 400
        assert self._kind(sku) == "sku"

    def test_barcoded_product_rejected_as_parent_on_update(self):
        sku = _mk("barcoded-sku-t4")
        client.post("/api/barcodes", json={"product_id": sku, "barcode": f"64{sku:011d}"})
        other = _mk("other-t4")
        r = client.put(f"/api/products/{other}", json={"parent_id": sku})
        assert r.status_code == 400
        assert self._kind(sku) == "sku"

    def test_concept_parent_with_stock_still_allowed(self):
        # Legacy concepts that hold old lots keep accepting children.
        cat = client.post("/api/products", json={
            "name": "legacy-cat-t5", "unit_id": _unit_id(), "kind": "concept"}).json()["id"]
        client.post("/api/stock/add", json={"product_id": cat, "amount": 2})
        r = client.post("/api/products", json={
            "name": "legacy-child-t5", "unit_id": _unit_id(), "parent_id": cat})
        assert r.status_code == 201
