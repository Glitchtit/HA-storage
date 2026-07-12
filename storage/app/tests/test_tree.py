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
