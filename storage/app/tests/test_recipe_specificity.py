"""Tests for recipe-ingredient specificity (strict vs loose).

Loose ingredient (default): stock aggregation includes children of the
linked parent product. Strict ingredient: only the linked product itself
counts as stock, so a sibling under the same parent does not satisfy it.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Reuse the shared DATA_DIR from test_api when both run in the same session
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "products"), exist_ok=True)
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "recipes"), exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def _kpl_id() -> int:
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")


def _make_parent_with_children(parent_name: str, child_names: list[str]) -> tuple[int, list[int]]:
    """Create one parent + N children, return (parent_id, [child_id, …])."""
    kpl = _kpl_id()
    parent = client.post("/api/products", json={"name": parent_name, "unit_id": kpl}).json()
    children: list[int] = []
    for cname in child_names:
        c = client.post("/api/products", json={
            "name": cname, "unit_id": kpl, "parent_id": parent["id"]
        }).json()
        children.append(c["id"])
    return parent["id"], children


def _add_stock(product_id: int, amount: float) -> None:
    client.post("/api/stock/add", json={"product_id": product_id, "amount": amount})


class TestSpecificityDefaults:
    def test_default_specificity_is_loose(self):
        kpl = _kpl_id()
        pid = client.post("/api/products", json={"name": "DefaultSpec", "unit_id": kpl}).json()["id"]
        rec = client.post("/api/recipes", json={
            "name": "DefaultSpecRecipe",
            "ingredients": [{"product_id": pid, "amount": 1, "unit_id": kpl}],
        }).json()
        ing = rec["ingredients"][0]
        assert ing["specificity"] == "loose"

    def test_explicit_strict_is_persisted(self):
        kpl = _kpl_id()
        pid = client.post("/api/products", json={"name": "ExplicitStrict", "unit_id": kpl}).json()["id"]
        rec = client.post("/api/recipes", json={
            "name": "ExplicitStrictRecipe",
            "ingredients": [{
                "product_id": pid, "amount": 1, "unit_id": kpl, "specificity": "strict",
            }],
        }).json()
        assert rec["ingredients"][0]["specificity"] == "strict"

    def test_invalid_specificity_falls_back_to_loose(self):
        kpl = _kpl_id()
        pid = client.post("/api/products", json={"name": "BadSpec", "unit_id": kpl}).json()["id"]
        rec = client.post("/api/recipes", json={
            "name": "BadSpecRecipe",
            "ingredients": [{
                "product_id": pid, "amount": 1, "unit_id": kpl, "specificity": "garbage",
            }],
        }).json()
        assert rec["ingredients"][0]["specificity"] == "loose"


class TestSpecificityStockAggregation:
    def test_loose_aggregates_children(self):
        parent_id, [child1_id, _child2_id] = _make_parent_with_children(
            "Juusto_loose", ["Gouda_loose", "Parmesan_loose"],
        )
        _add_stock(child1_id, 5)  # Gouda only
        kpl = _kpl_id()
        rec = client.post("/api/recipes", json={
            "name": "LooseCheeseRecipe",
            "ingredients": [{
                "product_id": parent_id, "amount": 1, "unit_id": kpl, "specificity": "loose",
            }],
        }).json()
        detail = client.get(f"/api/recipes/{rec['id']}").json()
        # Loose ingredient linked to parent should see Gouda's 5 aggregated.
        assert detail["ingredients"][0]["stock_amount"] == 5

    def test_strict_only_counts_linked_product(self):
        parent_id, [gouda_id, parmesan_id] = _make_parent_with_children(
            "Juusto_strict", ["Gouda_strict", "Parmesan_strict"],
        )
        _add_stock(gouda_id, 5)  # Only Gouda in stock, recipe needs Parmesan strictly.
        kpl = _kpl_id()
        rec = client.post("/api/recipes", json={
            "name": "StrictParmesanRecipe",
            "ingredients": [{
                "product_id": parmesan_id, "amount": 1, "unit_id": kpl, "specificity": "strict",
            }],
        }).json()
        detail = client.get(f"/api/recipes/{rec['id']}").json()
        # Strict ingredient must NOT aggregate Gouda stock.
        assert detail["ingredients"][0]["stock_amount"] == 0

    def test_strict_match_with_own_stock_is_satisfied(self):
        parent_id, [_gouda_id, parmesan_id] = _make_parent_with_children(
            "Juusto_strict_hit", ["Gouda_sh", "Parmesan_sh"],
        )
        _add_stock(parmesan_id, 3)
        kpl = _kpl_id()
        rec = client.post("/api/recipes", json={
            "name": "StrictHitRecipe",
            "ingredients": [{
                "product_id": parmesan_id, "amount": 1, "unit_id": kpl, "specificity": "strict",
            }],
        }).json()
        detail = client.get(f"/api/recipes/{rec['id']}").json()
        assert detail["ingredients"][0]["stock_amount"] == 3


class TestSpecificityUpdate:
    def test_update_ingredient_flips_specificity(self):
        kpl = _kpl_id()
        pid = client.post("/api/products", json={"name": "FlipSpec", "unit_id": kpl}).json()["id"]
        rec = client.post("/api/recipes", json={
            "name": "FlipSpecRecipe",
            "ingredients": [{"product_id": pid, "amount": 1, "unit_id": kpl}],
        }).json()
        ing_id = rec["ingredients"][0]["id"]
        r = client.put(
            f"/api/recipes/{rec['id']}/ingredients/{ing_id}",
            json={"specificity": "strict"},
        )
        assert r.status_code == 200
        assert r.json()["specificity"] == "strict"
        # Verify it survives a refetch
        detail = client.get(f"/api/recipes/{rec['id']}").json()
        assert detail["ingredients"][0]["specificity"] == "strict"
