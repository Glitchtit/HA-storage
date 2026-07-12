"""Tests for the recipe availability endpoint.

Port of HA-recipes' `_get_recipe_detail` status semantics, but computed
server-side against Storage's own tree/pack/conversion tables so HA-recipes
(and any other consumer) can call one endpoint instead of re-deriving stock
math client-side.
"""

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


def _unit_id(abbrev):
    for u in client.get("/api/units").json():
        if u["abbreviation"] == abbrev:
            return u["id"]
    raise AssertionError(abbrev)


def _mk(name, unit="kpl", parent_id=None, staple=False, pack_count=None):
    r = client.post("/api/products", json={
        "name": name, "unit_id": _unit_id(unit), "parent_id": parent_id,
        "staple": staple, "pack_count": pack_count})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _recipe(ingredients):
    r = client.post("/api/recipes", json={
        "name": "avail-test", "description": "", "servings": 1,
        "ingredients": ingredients})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _stock(pid, amount):
    r = client.post("/api/stock/add", json={"product_id": pid, "amount": amount})
    assert r.status_code in (200, 201), r.text


def _avail(recipe_id):
    r = client.get(f"/api/recipes/{recipe_id}/availability")
    assert r.status_code == 200, r.text
    return {i["ingredient_id"]: i for i in r.json()["ingredients"]}


class TestAvailability:
    def test_grandchild_stock_satisfies_variant_binding(self):
        # Juusto -> cheddar -> SKU with pack conversion 1 kpl = 150 g
        cat = _mk("Juusto-a1", unit="g")
        variant = _mk("cheddar-a1", unit="g", parent_id=cat)
        sku = _mk("Valio cheddar juustoraaste 150g a1", parent_id=variant)
        _stock(sku, 1)
        rid = _recipe([{"product_id": variant, "amount": 150,
                        "unit_id": _unit_id("g"), "specificity": "strict"}])
        ing = list(_avail(rid).values())[0]
        assert ing["status"] == "green"

    def test_pack_count_satisfies_kpl_need(self):
        # eggs: 1 package of 10 in stock, recipe needs 2 kpl
        cat = _mk("Kananmuna-a2")
        sku = _mk("Munapakkaus a2", parent_id=cat, pack_count=10)
        _stock(sku, 1)
        rid = _recipe([{"product_id": cat, "amount": 2,
                        "unit_id": _unit_id("kpl"), "specificity": "loose"}])
        assert list(_avail(rid).values())[0]["status"] == "green"

    def test_conversion_via_pack_size(self):
        # babypinaatti: SKU auto-parsed 65g pack, need 30 g
        cat = _mk("babypinaatti-a3", unit="g")
        sku = _mk("Pirkka babypinaatti 65g a3", parent_id=cat)  # Task 4 creates 1 kpl = 65 g
        _stock(sku, 1)
        rid = _recipe([{"product_id": cat, "amount": 30,
                        "unit_id": _unit_id("g"), "specificity": "loose"}])
        assert list(_avail(rid).values())[0]["status"] == "green"

    def test_staple_always_green(self):
        pid = _mk("vesi-a4", staple=True)
        rid = _recipe([{"product_id": pid, "amount": 2,
                        "unit_id": _unit_id("rkl"), "specificity": "loose"}])
        assert list(_avail(rid).values())[0]["status"] == "green"

    def test_no_stock_is_red(self):
        pid = _mk("Parsakaali-a5", unit="g")
        rid = _recipe([{"product_id": pid, "amount": 150,
                        "unit_id": _unit_id("g"), "specificity": "loose"}])
        assert list(_avail(rid).values())[0]["status"] == "red"

    def test_to_taste_yellow_without_stock(self):
        pid = _mk("Sahrami-a6", unit="g")
        rid = _recipe([{"product_id": pid, "amount": 0,
                        "unit_id": _unit_id("g"), "specificity": "loose"}])
        assert list(_avail(rid).values())[0]["status"] == "yellow"

    def test_unconvertible_stock_is_yellow(self):
        # stock in kpl, need in dl, no conversion for this product
        pid = _mk("Mysteeri-a7")
        _stock(pid, 1)
        rid = _recipe([{"product_id": pid, "amount": 3,
                        "unit_id": _unit_id("dl"), "specificity": "loose"}])
        assert list(_avail(rid).values())[0]["status"] == "yellow"
