"""'prefer' specificity: the bound product is the preferred variant, its
parent is the acceptable fallback. A substitute counts as in stock but is
reported, shopping asks for the preferred variant, and cooking uses it first."""
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


def _stock(pid, amount, best_before=None):
    body = {"product_id": pid, "amount": amount}
    if best_before:
        body["best_before_date"] = best_before
    r = client.post("/api/stock/add", json=body)
    assert r.status_code in (200, 201), r.text


def _cream(tag):
    """Ruokakerma -> 'Ruokakerma kolme juustoa', one SKU under each."""
    plain = _mk(f"Ruokakerma {tag}", kind="concept")
    flavoured = _mk(f"Ruokakerma kolme juustoa {tag}", kind="concept", parent_id=plain)
    plain_sku = _mk(f"Pirkka ruokakerma {tag} 2dl", parent_id=plain)
    flav_sku = _mk(f"Valio ruokakerma kolme juustoa {tag} 2,5dl", parent_id=flavoured)
    return plain, flavoured, plain_sku, flav_sku


def _recipe(name, pid, amount=2, specificity="prefer"):
    r = client.post("/api/recipes", json={"name": name, "servings": 4, "ingredients": [
        {"product_id": pid, "amount": amount, "unit_id": _u("kpl"), "specificity": specificity}]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _avail(rid):
    [ing] = client.get(f"/api/recipes/{rid}/availability").json()["ingredients"]
    return ing


def _detail(rid):
    [ing] = client.get(f"/api/recipes/{rid}").json()["ingredients"]
    return ing


class TestStatus:
    def test_preferred_in_stock_is_a_clean_match(self):
        plain, flav, _, flav_sku = _cream("p1")
        _stock(flav_sku, 2)
        ing = _avail(_recipe("Vuoka p1", flav))
        assert (ing["status"], ing["substitute"]) == ("green", False)
        assert ing["specificity"] == "prefer"
        assert ing["preferred_name"] == "Ruokakerma kolme juustoa p1"
        assert ing["fallback_name"] == "Ruokakerma p1"

    def test_plain_substitute_counts_but_is_marked(self):
        plain, flav, plain_sku, _ = _cream("p2")
        _stock(plain_sku, 2)
        ing = _avail(_recipe("Vuoka p2", flav))
        assert (ing["status"], ing["substitute"], ing["available"]) == ("green", True, 2)

    def test_mixed_stock_is_green_and_marked(self):
        plain, flav, plain_sku, flav_sku = _cream("p3")
        _stock(plain_sku, 1)
        _stock(flav_sku, 1)
        ing = _avail(_recipe("Vuoka p3", flav))
        assert (ing["status"], ing["substitute"]) == ("green", True)

    def test_too_little_substitute_is_yellow(self):
        plain, flav, plain_sku, _ = _cream("p4")
        _stock(plain_sku, 1)
        ing = _avail(_recipe("Vuoka p4", flav))
        assert (ing["status"], ing["substitute"]) == ("yellow", True)

    def test_nothing_is_red_and_not_a_substitute(self):
        plain, flav, _, _ = _cream("p5")
        ing = _avail(_recipe("Vuoka p5", flav))
        assert (ing["status"], ing["substitute"]) == ("red", False)

    def test_strict_on_the_same_variant_ignores_the_substitute(self):
        plain, flav, plain_sku, _ = _cream("p6")
        _stock(plain_sku, 2)
        ing = _avail(_recipe("Vuoka p6", flav, specificity="strict"))
        assert (ing["status"], ing["substitute"]) == ("red", False)

    def test_prefer_on_a_top_level_concept_has_no_fallback(self):
        top = _mk("Sahrami p7", kind="concept")
        ing = _avail(_recipe("Riisi p7", top))
        assert (ing["status"], ing["substitute"], ing["fallback_name"]) == ("red", False, None)

    def test_recipe_detail_and_list_use_the_fallback(self):
        plain, flav, plain_sku, _ = _cream("p8")
        _stock(plain_sku, 2)
        rid = _recipe("Vuoka p8", flav)
        ing = _detail(rid)
        assert (ing["status"], ing["substitute"], ing["fallback_name"]) == ("green", True, "Ruokakerma p8")
        summary = next(r for r in client.get("/api/recipes").json() if r["id"] == rid)
        assert summary["missing_count"] == 0


class TestApi:
    def test_prefer_is_accepted_and_bogus_rejected(self):
        plain, flav, _, _ = _cream("p9")
        rid = _recipe("Vuoka p9", flav, specificity="loose")
        iid = _detail(rid)["id"]
        r = client.put(f"/api/recipes/{rid}/ingredients/{iid}", json={"specificity": "prefer"})
        assert r.status_code == 200 and r.json()["specificity"] == "prefer"
        r = client.put(f"/api/recipes/{rid}/ingredients/{iid}", json={"specificity": "maybe"})
        assert r.status_code == 400


class TestShoppingAndCook:
    def test_substitute_in_stock_adds_nothing(self):
        plain, flav, plain_sku, _ = _cream("p10")
        _stock(plain_sku, 2)
        rid = _recipe("Vuoka p10", flav)
        r = client.post(f"/api/recipes/{rid}/to-shopping", json=None)
        assert r.json()["added"] == 0

    def test_shortfall_lands_on_the_preferred_variant(self):
        plain, flav, _, _ = _cream("p11")
        rid = _recipe("Vuoka p11", flav)
        client.post(f"/api/recipes/{rid}/to-shopping", json=None)
        rows = [i for i in client.get("/api/shopping-list").json() if not i["done"]]
        assert any(i["product_id"] == flav and i["amount"] == 2 for i in rows)
        assert not any(i["product_id"] == plain for i in rows)

    def test_cook_uses_preferred_lots_first(self):
        plain, flav, plain_sku, flav_sku = _cream("p12")
        # The plain pack expires first; it would go first without the preference.
        _stock(plain_sku, 2, "2031-01-01")
        _stock(flav_sku, 1, "2031-06-01")
        rid = _recipe("Vuoka p12", flav)
        body = client.post(f"/api/recipes/{rid}/cook", json={}).json()
        taken = {d["product_id"]: d["amount"] for d in body["deducted"]}
        assert taken == {flav_sku: 1, plain_sku: 1}
        assert body["shortfall_added"] == []


class TestResolve:
    def _resolve(self, *items):
        r = client.post("/api/ingredients/resolve", json={"items": list(items)})
        assert r.status_code == 200, r.text
        return r.json()["items"]

    def test_preferred_is_created_under_the_bound_concept(self):
        cream = _mk("Kerma p13", unit="dl", kind="concept")
        cooking = _mk("Ruokakerma p13", unit="dl", kind="concept", parent_id=cream)
        [hit] = self._resolve({"name": "kerma p13", "specific": "ruokakerma p13",
                               "preferred": "ruokakerma kolme juustoa p13",
                               "amount": 2, "unit_text": "prk"})
        assert hit["specificity"] == "prefer" and hit["created"] is True
        pref = client.get(f"/api/products/{hit['product_id']}").json()
        assert pref["kind"] == "concept" and pref["parent_id"] == cooking
        assert pref["name"].lower() == "ruokakerma kolme juustoa p13"

    def test_existing_preferred_is_reused(self):
        cheese = _mk("Juusto p14", unit="g", kind="concept")
        cheddar = _mk("Cheddar p14", unit="g", kind="concept", parent_id=cheese)
        grated = _mk("Cheddarraaste p14", unit="g", kind="concept", parent_id=cheddar)
        [hit] = self._resolve({"name": "juusto p14", "specific": "cheddar p14",
                               "preferred": "cheddarraaste p14", "amount": 75, "unit_text": "g"})
        assert (hit["product_id"], hit["specificity"], hit["created"]) == (grated, "prefer", False)

    def test_preferred_equal_to_the_binding_stays_as_before(self):
        cream = _mk("Kerma p15", unit="dl", kind="concept")
        whip = _mk("Vispikerma p15", unit="dl", kind="concept", parent_id=cream)
        [hit] = self._resolve({"name": "kerma p15", "specific": "vispikerma p15",
                               "preferred": "Vispikerma p15", "amount": 2, "unit_text": "dl"})
        assert (hit["product_id"], hit["specificity"]) == (whip, "strict")

    def test_no_preferred_binds_as_before(self):
        rice = _mk("Riisi p16", unit="dl", kind="concept")
        [hit] = self._resolve({"name": "riisi p16", "amount": 3.5, "unit_text": "dl"})
        assert (hit["product_id"], hit["specificity"]) == (rice, "loose")
