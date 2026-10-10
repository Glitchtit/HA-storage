"""Cook deducts from the SKUs actually under each ingredient's product."""
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


def _lots(pid):
    return client.get(f"/api/stock/product/{pid}").json()


def _cook(ingredients, servings=4):
    rid = client.post("/api/recipes", json={"name": f"cook-{ingredients[0][0]}", "servings": servings,
        "ingredients": [{"product_id": p, "amount": a, "unit_id": _u(u)} for p, a, u in ingredients]}).json()["id"]
    r = client.post(f"/api/recipes/{rid}/cook", json={})
    assert r.status_code == 200, r.text
    return rid, r.json()


class TestCookSubtree:
    def test_deducts_from_child_skus_fifo(self):
        cat = _mk("maito-c1", unit="dl", kind="concept")
        old = _mk("Maito 1l vanha c1", parent_id=cat)
        new = _mk("Maito 1l uusi c1", parent_id=cat)
        client.post("/api/stock/add", json={"product_id": new, "amount": 1, "best_before_date": "2031-06-01"})
        client.post("/api/stock/add", json={"product_id": old, "amount": 1, "best_before_date": "2031-01-01"})
        _, body = _cook([(cat, 5, "dl")])
        assert body["shortfall_added"] == [] and body["unmatched"] == []
        assert [d["product_id"] for d in body["deducted"]] == [old]
        assert abs(body["deducted"][0]["amount"] - 0.5) < 1e-6
        assert _lots(new)[0]["amount"] == 1

    def test_fraction_of_bag_marks_it_opened(self):
        cat = _mk("vehnäjauho-c2", unit="dl", kind="concept", density_g_ml=0.6)
        sku = _mk("Vehnäjauho 2kg c2", parent_id=cat)
        client.post("/api/stock/add", json={"product_id": sku, "amount": 1})
        _, body = _cook([(cat, 3.5, "dl")])
        lot = _lots(sku)[0]
        assert abs(lot["amount"] - 0.895) < 1e-6
        assert lot["amount_opened"] > 0
        assert abs(body["deducted"][0]["amount"] - 0.105) < 1e-6

    def test_spills_across_skus_then_lists_shortfall(self):
        cat = _mk("kerma-c3", unit="dl", kind="concept")
        a = _mk("Kerma 2dl c3a", parent_id=cat)
        b = _mk("Kerma 2dl c3b", parent_id=cat)
        client.post("/api/stock/add", json={"product_id": a, "amount": 1, "best_before_date": "2031-01-01"})
        client.post("/api/stock/add", json={"product_id": b, "amount": 1, "best_before_date": "2031-02-01"})
        rid, body = _cook([(cat, 5, "dl")])
        assert {d["product_id"] for d in body["deducted"]} == {a, b}
        assert abs(body["shortfall_added"][0]["amount"] - 1) < 1e-6
        rows = [i for i in client.get("/api/shopping-list").json()
                if i["product_id"] == cat and i["recipe_id"] == rid]
        assert len(rows) == 1 and rows[0]["unit_id"] == _u("dl")

    def test_same_product_twice_uses_updated_stock(self):
        butter = _mk("voi-c4", unit="g", kind="concept")
        sku = _mk("Voi 500g c4", parent_id=butter)
        client.post("/api/stock/add", json={"product_id": sku, "amount": 1})
        _, body = _cook([(butter, 300, "g"), (butter, 300, "g")])
        assert abs(sum(d["amount"] for d in body["deducted"]) - 1.0) < 1e-6
        assert abs(body["shortfall_added"][0]["amount"] - 100) < 1e-6
        assert _lots(sku) == []

    def test_unconvertible_stock_is_reported_not_deducted(self):
        cat = _mk("mysteeri-c5", unit="dl", kind="concept")
        sku = _mk("Mysteeri ilman kokoa c5", parent_id=cat)
        client.post("/api/stock/add", json={"product_id": sku, "amount": 1})
        _, body = _cook([(cat, 2, "dl")])
        assert body["deducted"] == [] and body["shortfall_added"] == []
        assert body["unmatched"][0]["reason"] == "no_unit_conversion"
        assert _lots(sku)[0]["amount"] == 1

    def test_staple_and_to_taste_skipped(self):
        water = _mk("vesi-c6", unit="dl", kind="concept", staple=True)
        salt = _mk("suola-c6", unit="g", kind="concept")
        _, body = _cook([(water, 3, "dl"), (salt, 0, "g")])
        assert body["deducted"] == [] and body["shortfall_added"] == [] and body["unmatched"] == []

    def test_shortfall_not_doubled_when_recipe_already_listed(self):
        cat = _mk("kerma-c7", unit="dl", kind="concept")
        rid = client.post("/api/recipes", json={"name": "Kastike c7", "servings": 4, "ingredients": [
            {"product_id": cat, "amount": 2, "unit_id": _u("dl")}]}).json()["id"]
        client.post(f"/api/recipes/{rid}/to-shopping")
        client.post(f"/api/recipes/{rid}/cook", json={})
        rows = [i for i in client.get("/api/shopping-list").json() if i["product_id"] == cat and not i["done"]]
        assert len(rows) == 1 and rows[0]["amount"] == 2
