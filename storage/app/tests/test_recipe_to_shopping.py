"""POST /recipes/{id}/to-shopping on the quantity engine."""
from __future__ import annotations

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
from main import app

client = TestClient(app)


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _recipe(name, ingredients, servings=4):
    r = client.post("/api/recipes", json={"name": name, "servings": servings, "ingredients": [
        {"product_id": pid, "amount": amount, "unit_id": _u(unit), "specificity": spec}
        for pid, amount, unit, spec in ingredients]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _push(rid, **body):
    r = client.post(f"/api/recipes/{rid}/to-shopping", json=body or None)
    assert r.status_code == 201, r.text
    return r.json()


def _rows(pid):
    return [i for i in client.get("/api/shopping-list").json() if i["product_id"] == pid and not i["done"]]


class TestToShopping:
    def test_loose_need_lands_on_concept_not_parent(self):
        top = _mk("Makeutusaineet-s1", unit="g", kind="concept")
        sugar = _mk("sokeri-s1", unit="dl", kind="concept", parent_id=top)
        rid = _recipe("Pulla s1", [(sugar, 1, "dl", "loose")])
        assert _push(rid)["added"] == 1
        assert _rows(top) == []
        rows = _rows(sugar)
        assert len(rows) == 1 and rows[0]["amount"] == 1 and rows[0]["unit_id"] == _u("dl")
        assert rows[0]["note"] == "From recipe: Pulla s1" and rows[0]["recipe_id"] == rid

    def test_strict_need_lands_on_variant(self):
        cat = _mk("juusto-s2", unit="g", kind="concept")
        variant = _mk("cheddar-s2", unit="g", kind="concept", parent_id=cat)
        rid = _recipe("Piiras s2", [(variant, 150, "g", "strict")])
        _push(rid)
        assert len(_rows(variant)) == 1 and _rows(cat) == []

    def test_missing_adds_only_the_shortfall(self):
        cat = _mk("kerma-s3", unit="dl", kind="concept")
        sku = _mk("Kuohukerma 2dl s3", parent_id=cat)
        client.post("/api/stock/add", json={"product_id": sku, "amount": 1})
        rid = _recipe("Kastike s3", [(cat, 3, "dl", "loose")])
        _push(rid)
        rows = _rows(cat)
        assert len(rows) == 1 and abs(rows[0]["amount"] - 1) < 1e-9

    def test_nothing_added_when_stock_covers(self):
        cat = _mk("vehnäjauho-s4", unit="dl", kind="concept", density_g_ml=0.6)
        sku = _mk("Vehnäjauho 2kg s4", parent_id=cat)
        client.post("/api/stock/add", json={"product_id": sku, "amount": 1})
        rid = _recipe("Leipä s4", [(cat, 5, "dl", "loose")])
        assert _push(rid)["added"] == 0

    def test_missing_and_opened_adds_need_beyond_unopened(self):
        cat = _mk("kerma-s5", unit="dl", kind="concept")
        sku = _mk("Kerma 2dl s5", parent_id=cat)
        client.post("/api/stock/add", json={"product_id": sku, "amount": 1})
        client.post("/api/stock/open", json={"product_id": sku, "amount": 1})
        rid = _recipe("Keitto s5", [(cat, 1.5, "dl", "loose")])
        assert _push(rid, mode="missing")["added"] == 0
        assert _push(rid, mode="missing_and_opened")["added"] == 1
        assert abs(_rows(cat)[0]["amount"] - 1.5) < 1e-9

    def test_all_adds_full_need_even_in_stock(self):
        cat = _mk("maito-s6", unit="dl", kind="concept")
        sku = _mk("Maito 1l s6", parent_id=cat)
        client.post("/api/stock/add", json={"product_id": sku, "amount": 3})
        rid = _recipe("Puuro s6", [(cat, 4, "dl", "loose")])
        _push(rid, mode="all")
        assert _rows(cat)[0]["amount"] == 4

    def test_staple_never_added(self):
        water = _mk("vesi-s7", unit="dl", kind="concept", staple=True)
        rid = _recipe("Keitto s7", [(water, 5, "dl", "loose")])
        assert _push(rid, mode="all")["added"] == 0

    def test_to_taste_adds_one_package_only_when_none_in_stock(self):
        salt = _mk("suola-s8", unit="g", kind="concept")
        rid = _recipe("Pasta s8", [(salt, 0, "g", "loose")])
        _push(rid)
        rows = _rows(salt)
        assert len(rows) == 1 and rows[0]["amount"] == 1 and rows[0]["unit_id"] is None
        pepper = _mk("pippuri-s8", unit="g", kind="concept")
        sku = _mk("Mustapippuri 32g s8", parent_id=pepper)
        client.post("/api/stock/add", json={"product_id": sku, "amount": 1})
        rid2 = _recipe("Pasta2 s8", [(pepper, 0, "g", "loose")])
        assert _push(rid2)["added"] == 0

    def test_two_recipes_merge_into_one_row(self):
        cat = _mk("vehnäjauho-s9", unit="dl", kind="concept")
        r1 = _recipe("Pulla s9", [(cat, 3, "dl", "loose")])
        r2 = _recipe("Kastike s9", [(cat, 2, "rkl", "loose")])
        _push(r1)
        result = _push(r2)
        assert result["merged"] == 1 and result["created"] == 0
        rows = _rows(cat)
        assert len(rows) == 1
        assert abs(rows[0]["amount"] - 3.3) < 1e-9  # 3 dl + 2 rkl (0.3 dl)
        assert rows[0]["note"] == "From recipe: Pulla s9, Kastike s9"

    def test_pressing_twice_does_not_double(self):
        cat = _mk("kerma-s10", unit="dl", kind="concept")
        rid = _recipe("Kastike s10", [(cat, 2, "dl", "loose")])
        _push(rid)
        assert _push(rid)["added"] == 0
        assert _rows(cat)[0]["amount"] == 2

    def test_count_need_on_sku_becomes_whole_packages(self):
        potato_concept = _mk("peruna-s11", unit="kpl", kind="concept", piece_weight_g=150)
        bag = _mk("Peruna Annabelle 1kg s11", parent_id=potato_concept)
        rid = _recipe("Muusi s11", [(bag, 4, "kpl", "loose")])
        _push(rid)
        rows = _rows(bag)
        assert len(rows) == 1 and rows[0]["amount"] == 1 and rows[0]["unit_id"] is None

    def test_servings_scale_the_need(self):
        cat = _mk("riisi-s12", unit="dl", kind="concept")
        rid = _recipe("Riisi s12", [(cat, 2, "dl", "loose")], servings=4)
        _push(rid, servings=8)
        assert _rows(cat)[0]["amount"] == 4

    def test_unknown_amount_stock_is_not_guessed(self):
        cat = _mk("mysteeri-s13", unit="dl", kind="concept")
        sku = _mk("Mysteeri ilman kokoa s13", parent_id=cat)
        client.post("/api/stock/add", json={"product_id": sku, "amount": 1})
        rid = _recipe("Mysteeri s13", [(cat, 2, "dl", "loose")])
        assert _push(rid)["added"] == 0

    def test_invalid_mode_rejected(self):
        cat = _mk("x-s14", unit="dl", kind="concept")
        rid = _recipe("X s14", [(cat, 1, "dl", "loose")])
        r = client.post(f"/api/recipes/{rid}/to-shopping", json={"mode": "everything"})
        assert r.status_code == 400

    def test_same_product_twice_adds_both_needs(self):
        butter = _mk("voi-s15", unit="g", kind="concept")
        rid = _recipe("Pulla s15", [(butter, 100, "g", "loose"), (butter, 20, "g", "loose")])
        assert _push(rid)["added"] == 2
        rows = _rows(butter)
        assert len(rows) == 1 and abs(rows[0]["amount"] - 120) < 1e-9

    def test_same_product_twice_shares_the_stock(self):
        butter = _mk("voi-s16", unit="g", kind="concept")
        sku = _mk("Voi 100g s16", parent_id=butter)
        client.post("/api/stock/add", json={"product_id": sku, "amount": 1})
        rid = _recipe("Pulla s16", [(butter, 100, "g", "loose"), (butter, 20, "g", "loose")])
        _push(rid)
        rows = _rows(butter)
        assert len(rows) == 1 and abs(rows[0]["amount"] - 20) < 1e-9

    def test_same_product_twice_to_taste_adds_one_package(self):
        salt = _mk("suola-s17", unit="g", kind="concept")
        rid = _recipe("Keitto s17", [(salt, 0, "g", "loose"), (salt, 0, "g", "loose")])
        _push(rid)
        rows = _rows(salt)
        assert len(rows) == 1 and rows[0]["amount"] == 1

    def test_unexpected_error_rolls_back_rows(self, monkeypatch):
        import routers.recipes as recipes_router
        from main import get_connection
        cream = _mk("kerma-s18", unit="dl", kind="concept")
        milk = _mk("maito-s18", unit="dl", kind="concept")
        rid = _recipe("Kastike s18", [(cream, 2, "dl", "loose"), (milk, 3, "dl", "loose")])
        real = recipes_router.add_recipe_need
        calls = []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("list write failed")
            return real(*args, **kwargs)
        monkeypatch.setattr(recipes_router, "add_recipe_need", flaky)
        with pytest.raises(RuntimeError):
            client.post(f"/api/recipes/{rid}/to-shopping", json={"mode": "missing"})
        get_connection().commit()  # what the next unrelated request would do
        assert _rows(cream) == [] and _rows(milk) == []


class TestLegacyRecipeNote:
    """Rows written before the English UI carry "Reseptistä: "; they are
    still recipe notes."""

    def test_note_names_recognise_both_prefixes(self):
        from recipe_rows import recipe_note_names
        assert recipe_note_names("From recipe: A, B") == ["A", "B"]
        assert recipe_note_names("Reseptistä: A, B") == ["A", "B"]
        assert recipe_note_names("osta tarjouksesta") is None
        assert recipe_note_names(None) is None

    def test_merge_into_legacy_row_rewrites_prefix(self):
        cat = _mk("kerma-s11", unit="dl", kind="concept")
        r1 = _recipe("Pulla s11", [(cat, 3, "dl", "loose")])
        r2 = _recipe("Kastike s11", [(cat, 2, "dl", "loose")])
        r = client.post("/api/shopping-list", json={
            "product_id": cat, "amount": 3, "unit_id": _u("dl"),
            "note": "Reseptistä: Pulla s11", "recipe_id": r1})
        assert r.status_code == 201, r.text
        result = _push(r2)
        assert result["merged"] == 1 and result["created"] == 0
        [row] = _rows(cat)
        assert row["amount"] == 5
        assert row["note"] == "From recipe: Pulla s11, Kastike s11"

    def test_legacy_note_counts_as_already_listed(self):
        cat = _mk("maito-s12", unit="dl", kind="concept")
        other = _recipe("Muu s12", [(cat, 1, "dl", "loose")])
        rid = _recipe("Kastike s12", [(cat, 2, "dl", "loose")])
        client.post("/api/shopping-list", json={
            "product_id": cat, "amount": 2, "unit_id": _u("dl"),
            "note": "Reseptistä: Kastike s12", "recipe_id": other})
        assert _push(rid)["added"] == 0
        assert [r["amount"] for r in _rows(cat)] == [2]
