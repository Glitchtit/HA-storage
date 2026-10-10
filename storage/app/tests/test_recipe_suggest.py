"""Recipe list badges and stock-based suggestions (spec §4.10), plus the
ingredient edit the recipe fix sheet uses."""
from __future__ import annotations

import datetime as dt
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


def _concept(name, stock=0.0, best_before=None):
    """A concept with one SKU under it holding `stock` packages."""
    concept = client.post("/api/products", json={"name": name, "unit_id": _kpl(), "kind": "concept"}).json()["id"]
    sku = client.post("/api/products", json={"name": f"{name} pkt", "unit_id": _kpl(),
                                             "parent_id": concept}).json()["id"]
    if stock:
        body = {"product_id": sku, "amount": stock}
        if best_before:
            body["best_before_date"] = best_before
        assert client.post("/api/stock/add", json=body).status_code == 201
    return concept


def _recipe(name, *needs):
    """needs: (concept id, pieces needed)"""
    ingredients = [{"product_id": pid, "amount": amount, "unit_id": _kpl()} for pid, amount in needs]
    r = client.post("/api/recipes", json={"name": name, "ingredients": ingredients})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _suggested(ids, **params):
    return [s for s in client.get("/api/recipes/suggest", params=params).json() if s["id"] in ids]


class TestListBadges:
    def test_each_recipe_counts_its_missing_and_partial_ingredients(self):
        have = _concept("Riittää r1", stock=2)
        some = _concept("Vähän r1", stock=1)
        none = _concept("Ei mitään r1")
        rid = _recipe("Badge r1", (have, 1), (some, 3), (none, 1))
        item = next(r for r in client.get("/api/recipes").json() if r["id"] == rid)
        assert (item["ingredient_count"], item["missing_count"], item["yellow_count"]) == (3, 1, 1)


class TestSuggest:
    def test_only_cookable_recipes_come_back_greenest_first(self):
        have = _concept("Riittää r2", stock=5)
        some = _concept("Vähän r2", stock=1)
        none = _concept("Ei mitään r2")
        partial = _recipe("A osittain r2", (have, 1), (some, 2))
        ready = _recipe("B valmis r2", (have, 1))
        blocked = _recipe("C puuttuu r2", (have, 1), (none, 1))
        empty = _recipe("D tyhjä r2")
        got = _suggested({partial, ready, blocked, empty})
        assert [s["id"] for s in got] == [ready, partial]
        assert (got[1]["missing_count"], got[1]["yellow_count"]) == (0, 1)

    def test_stock_expiring_under_a_concept_puts_the_recipe_first(self):
        soon = (dt.date.today() + dt.timedelta(days=2)).isoformat()
        expiring = _concept("Kerma loppuu r3", stock=1, best_before=soon)
        fresh = _concept("Pasta säilyy r3", stock=3)
        uses = _recipe("Z käyttää r3", (expiring, 1))
        other = _recipe("A ei käytä r3", (fresh, 1))
        got = _suggested({uses, other}, expiring_within_days=3)
        assert [s["id"] for s in got] == [uses, other]
        assert (got[0]["uses_expiring"], got[0]["expiring_products"]) == (True, ["Kerma loppuu r3"])
        assert (got[1]["uses_expiring"], got[1]["expiring_products"]) == (False, [])

    def test_without_a_window_nothing_counts_as_expiring(self):
        soon = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        expiring = _concept("Jogurtti loppuu r4", stock=1, best_before=soon)
        rid = _recipe("Jogurttikulho r4", (expiring, 1))
        assert _suggested({rid})[0]["uses_expiring"] is False


class TestIngredientEdit:
    def _ingredient(self, rid):
        return client.get(f"/api/recipes/{rid}").json()["ingredients"][0]

    def test_saving_a_fix_binds_the_new_product_and_clears_review(self):
        old = _concept("Väärä r5")
        new = _concept("Oikea r5", stock=1)
        rid = client.post("/api/recipes", json={"name": "Korjattava r5", "ingredients": [
            {"product_id": old, "amount": 1, "unit_id": _kpl(), "needs_review": True}]}).json()["id"]
        iid = self._ingredient(rid)["id"]
        r = client.put(f"/api/recipes/{rid}/ingredients/{iid}", json={
            "product_id": new, "amount": 2, "unit_id": _kpl(), "specificity": "strict", "needs_review": False})
        assert r.status_code == 200
        ing = self._ingredient(rid)
        assert (ing["product_id"], ing["amount"], ing["specificity"], ing["needs_review"]) == (new, 2, "strict", False)

    def test_unknown_products_units_and_specificities_are_rejected(self):
        pid = _concept("Validoitava r6")
        rid = _recipe("Validointi r6", (pid, 1))
        iid = self._ingredient(rid)["id"]
        url = f"/api/recipes/{rid}/ingredients/{iid}"
        assert client.put(url, json={"product_id": 99999999}).status_code == 400
        assert client.put(url, json={"unit_id": 99999999}).status_code == 400
        assert client.put(url, json={"specificity": "maybe"}).status_code == 400
