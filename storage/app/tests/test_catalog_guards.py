"""Catalog writes are serialized, and concepts stay out of SKU-only paths."""
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

import linker
import optimizer
import tree
from locks import CATALOG_LOCK

client = TestClient(app)


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_parent_changes_hold_the_catalog_lock(monkeypatch):
    held = []
    real = tree.prepare_parent

    def spy(conn, product_id, parent_id):
        held.append(CATALOG_LOCK._is_owned())
        return real(conn, product_id, parent_id)

    monkeypatch.setattr("tree.prepare_parent", spy)
    cat = _mk("Jauhot g1", unit="dl", kind="concept")
    sku = _mk("Pirkka vehnäjauho g1 2kg", parent_id=cat)
    other = _mk("Leivonta g1", unit="dl", kind="concept")
    client.put(f"/api/products/{cat}", json={"parent_id": other})
    loose = _mk("Myllyn Paras ruisjauho g1")
    linker.apply_link(get_connection(), loose, cat)
    assert sku and len(held) == 6 and all(held)  # four creates, one update, one link


def test_receipt_matcher_suggests_skus_only():
    from receipt_parser import match_lines_to_products

    concept = _mk("Banaani g2", kind="concept")
    sku = _mk("Chiquita banaani g2 1kg")
    [line] = match_lines_to_products([{"raw_text": "BANAANI G2"}], get_connection())
    assert line.get("suggested_product_id") in (sku, None)


def test_optimize_gives_a_concept_its_aisle_group_and_nothing_else(monkeypatch):
    loc = client.post("/api/locations", json={"name": "Kuivakaappi g3"}).json()["id"]
    concept = _mk("Hiiva g3", unit="g", kind="concept")

    def fake(prompt, conn, **kw):
        if '"category"' in prompt and "location_id" not in prompt:
            return {str(concept): {"category": "Leivonta g3"}}
        return {str(concept): {"location_id": loc, "best_before_days": 5, "pack_size": 6,
                               "base_product_name": "Hiivapala g3"}}

    monkeypatch.setattr("optimizer.call_ai_json", fake)
    optimizer.run_optimize(get_connection(), product_ids=[concept], link=False)
    after = client.get(f"/api/products/{concept}").json()
    groups = {g["id"]: g["name"] for g in client.get("/api/product-groups").json()}
    assert groups[after["product_group_id"]] == "Leivonta g3"
    assert after["name"] == "Hiiva g3" and after["location_id"] is None
    assert after["default_best_before_days"] == 60
