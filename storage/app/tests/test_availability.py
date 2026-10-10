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

    def test_pack_count_not_applied_to_non_piece_unit_match(self):
        # A gram lot is read as grams: pack_count=10 must NOT multiply it.
        # 100 g of a 500 g need is partial stock -> yellow, never green.
        cat = _mk("packcount-guard-a2b", unit="g")
        sku = _mk("Guarded sku a2b 100g", unit="g", parent_id=cat, pack_count=10)
        _stock(sku, 100)
        rid = _recipe([{"product_id": cat, "amount": 500,
                        "unit_id": _unit_id("g"), "specificity": "loose"}])
        ing = list(_avail(rid).values())[0]
        assert ing["status"] == "yellow" and ing["available"] == 100

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

    # ── Two branches the brief's 7 tests don't exercise: "to taste" green,
    # and the opened/low-piece-count downgrade of an otherwise-green row. ──

    def test_to_taste_green_with_stock(self):
        pid = _mk("Muskottipahka-a8", unit="g")
        _stock(pid, 5)
        rid = _recipe([{"product_id": pid, "amount": 0,
                        "unit_id": _unit_id("g"), "specificity": "loose"}])
        assert list(_avail(rid).values())[0]["status"] == "green"

    def test_opened_single_pack_downgrades_to_yellow(self):
        # Enough stock to cover the need, but it's a single opened pack —
        # can't be sure the remaining amount still meets `needed`.
        pid = _mk("Voi-a9")
        _stock(pid, 1)
        r = client.post("/api/stock/open", json={"product_id": pid, "amount": 1})
        assert r.status_code == 200, r.text
        rid = _recipe([{"product_id": pid, "amount": 1,
                        "unit_id": _unit_id("kpl"), "specificity": "loose"}])
        assert list(_avail(rid).values())[0]["status"] == "yellow"


def _concept(name, unit, **facts):
    r = client.post("/api/products", json={
        "name": name, "unit_id": _unit_id(unit), "kind": "concept", **facts})
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestQuantityAwareAvailability:
    def test_egg_carton_counts_pieces(self):
        cat = _concept("kananmuna-b1", "kpl")
        sku = _mk("Munat 10kpl/580g b1", parent_id=cat)
        _stock(sku, 1)
        rid = _recipe([{"product_id": cat, "amount": 6, "unit_id": _unit_id("kpl")}])
        ing = list(_avail(rid).values())[0]
        assert ing["status"] == "green" and ing["available"] == 10 and ing["approx"] is False

    def test_potato_bag_uses_concept_piece_weight(self):
        cat = _concept("peruna-b2", "kpl", piece_weight_g=150)
        sku = _mk("Peruna Annabelle 1kg b2", parent_id=cat)
        _stock(sku, 1)
        rid = _recipe([{"product_id": cat, "amount": 4, "unit_id": _unit_id("kpl")}])
        ing = list(_avail(rid).values())[0]
        assert ing["status"] == "green" and ing["approx"] is True
        assert abs(ing["available"] - 6.667) < 0.01

    def test_flour_dl_against_kg_bag_via_concept_density(self):
        cat = _concept("vehnäjauho-b3", "dl", density_g_ml=0.6)
        sku = _mk("Myllyn Paras vehnäjauho 2kg b3", parent_id=cat)
        _stock(sku, 1)
        rid = _recipe([{"product_id": cat, "amount": 3.5, "unit_id": _unit_id("dl")}])
        ing = list(_avail(rid).values())[0]
        assert ing["status"] == "green" and abs(ing["available"] - 33.333) < 0.01

    def test_partial_cream_is_yellow_with_amount(self):
        cat = _concept("kerma-b4", "dl")
        sku = _mk("Kuohukerma 2dl b4", parent_id=cat)
        _stock(sku, 1)
        rid = _recipe([{"product_id": cat, "amount": 3, "unit_id": _unit_id("dl")}])
        ing = list(_avail(rid).values())[0]
        assert ing["status"] == "yellow" and ing["available"] == 2

    def test_small_need_from_big_opened_bag_is_green(self):
        cat = _concept("sokeri-b5", "dl", density_g_ml=0.85)
        sku = _mk("Sokeri 1kg b5", parent_id=cat)
        _stock(sku, 1)
        client.post("/api/stock/open", json={"product_id": sku, "amount": 1})
        rid = _recipe([{"product_id": cat, "amount": 1, "unit_id": _unit_id("dl")}])
        assert list(_avail(rid).values())[0]["status"] == "green"

    def test_large_need_from_opened_pack_is_yellow(self):
        cat = _concept("kerma-b6", "dl")
        sku = _mk("Kerma 2dl b6", parent_id=cat)
        _stock(sku, 1)
        client.post("/api/stock/open", json={"product_id": sku, "amount": 1})
        rid = _recipe([{"product_id": cat, "amount": 1.5, "unit_id": _unit_id("dl")}])
        assert list(_avail(rid).values())[0]["status"] == "yellow"

    def test_lot_unit_not_product_unit_is_used(self):
        # Legacy drift: product unit g, lot recorded in kg.
        pid = _mk("drift-b7", unit="g")
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1, "unit_id": _unit_id("kg")})
        rid = _recipe([{"product_id": pid, "amount": 500, "unit_id": _unit_id("g")}])
        ing = list(_avail(rid).values())[0]
        assert ing["status"] == "green" and ing["available"] == 1000

    def test_custom_unit_without_dimension_is_unknown_not_error(self):
        unit = client.post("/api/units", json={"name": "Kourallinen", "abbreviation": "kour-b8"}).json()
        pid = _mk("kourallinen-b8", unit="kpl")
        _stock(pid, 2)
        rid = _recipe([{"product_id": pid, "amount": 1, "unit_id": unit["id"]}])
        ing = list(_avail(rid).values())[0]
        assert ing["status"] == "green"  # vague need with stock reads as to-taste

    def test_opened_count_above_amount_is_clamped(self):
        from main import get_connection
        cat = _concept("kerma-b9", "dl")
        sku = _mk("Kerma 2dl b9", parent_id=cat)
        _stock(sku, 1)
        conn = get_connection()
        conn.execute("UPDATE stock SET amount_opened = 5 WHERE product_id = ?", (sku,))
        conn.commit()
        rid = _recipe([{"product_id": cat, "amount": 0.5, "unit_id": _unit_id("dl")}])
        ing = list(_avail(rid).values())[0]
        assert ing["available_unopened"] == 0 and ing["status"] == "green"

    def test_recipe_detail_reports_converted_stock(self):
        cat = _concept("vehnäjauho-b10", "dl", density_g_ml=0.6)
        sku = _mk("Vehnäjauho 1kg b10", parent_id=cat)
        _stock(sku, 1)
        rid = _recipe([{"product_id": cat, "amount": 2, "unit_id": _unit_id("dl")}])
        ing = client.get(f"/api/recipes/{rid}").json()["ingredients"][0]
        assert abs(ing["stock_amount"] - 16.667) < 0.01
        assert ing["stock_unit_id"] == _unit_id("dl") and ing["status"] == "green"
