"""Swapping a row's product keeps the requirement (spec §4.8); the stepper
can turn a requirement into a package count."""
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

client = TestClient(app)


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _add(product_id, amount=1, unit=None, **extra):
    body = {"product_id": product_id, "amount": amount, **extra}
    if unit:
        body["unit_id"] = _u(unit)
    r = client.post("/api/shopping-list", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _row(row_id):
    return next(i for i in client.get("/api/shopping-list").json() if i["id"] == row_id)


def _swap(row_id, product_id):
    return client.post(f"/api/shopping-list/{row_id}/swap", json={"product_id": product_id})


class TestSwap:
    def test_a_sku_under_the_concept_becomes_the_preferred_product(self):
        concept = _mk("Kerma s1", unit="dl", kind="concept")
        chosen = _mk("Laktoositon kerma 2 dl s1", parent_id=concept)
        row = _add(concept, 3, unit="dl", note="Reseptistä: A")
        r = _swap(row, chosen)
        assert r.status_code == 200
        item = r.json()
        assert (item["product_id"], item["preferred_product_id"], item["amount"], item["unit_id"],
                item["note"]) == (concept, chosen, 3, _u("dl"), "Reseptistä: A")
        assert item["display"]["buy_product_id"] == chosen

    def test_swapping_back_to_the_concept_clears_the_preference(self):
        concept = _mk("Kahvi s2", kind="concept")
        chosen = _mk("Juhlakahvi s2", parent_id=concept)
        row = _add(concept)
        _swap(row, chosen)
        assert _swap(row, concept).json()["preferred_product_id"] is None

    def test_another_product_takes_over_the_row_and_its_attribution(self):
        old = _mk("Tomaattimurska s3")
        new = _mk("Tomaattipyree s3")
        recipe = client.post("/api/recipes", json={"name": "Kastike s3"}).json()["id"]
        bundle = client.post("/api/bundles", json={"name": "Setti s3", "items": []}).json()["id"]
        row = _add(old, 2, note="Reseptistä: Kastike s3", recipe_id=recipe)
        conn = get_connection()
        conn.execute("UPDATE shopping_list SET bundle_id = ? WHERE id = ?", (bundle, row))
        conn.commit()
        item = _swap(row, new).json()
        assert (item["product_id"], item["amount"], item["unit_id"], item["note"], item["recipe_id"],
                item["bundle_id"], item["ha_item_name"], item["preferred_product_id"]) == (
            new, 2, None, "Reseptistä: Kastike s3", recipe, bundle, "Tomaattipyree s3", None)

    def test_pieces_become_packages_on_a_sku(self):
        concept = _mk("Sitruuna s4", kind="concept")
        sku = _mk("Sitruunat 4 kpl s4", pack_count=4)
        row = _add(concept, 3, unit="kpl", note="Reseptistä: B")
        item = _swap(row, sku).json()
        assert (item["product_id"], item["amount"], item["unit_id"]) == (sku, 1, None)

    def test_packages_stay_packages_on_a_concept(self):
        concept = _mk("Maito s5", kind="concept")
        sku = _mk("Kevytmaito 1 l s5", parent_id=concept)
        row = _add(sku, 2, unit="kpl")
        item = _swap(row, concept).json()
        assert (item["product_id"], item["amount"], item["unit_id"]) == (concept, 2, None)

    def test_minimum_stock_rows_cannot_move_to_another_product(self):
        concept = _mk("Vessapaperi s6", kind="concept", min_stock_amount=1)
        sku = _mk("Vessapaperi 8 rll s6", parent_id=concept)
        other = _mk("Talouspaperi s6")
        client.post("/api/shopping-list/sync")
        row = next(i for i in client.get("/api/shopping-list").json()
                   if i["product_id"] == concept and i["auto_added"])["id"]
        assert _swap(row, other).status_code == 409
        assert _swap(row, sku).json()["preferred_product_id"] == sku

    def test_unknown_row_or_product(self):
        sku = _mk("Olematon s7")
        assert _swap(99999999, sku).status_code == 404
        assert _swap(_add(sku), 99999999).status_code == 400


class TestUpdate:
    def test_the_stepper_turns_a_requirement_into_packages(self):
        concept = _mk("Ruokakerma s8", unit="dl", kind="concept")
        row = _add(concept, 3, unit="dl", note="Reseptistä: C")
        r = client.put(f"/api/shopping-list/{row}", json={"amount": 2, "unit_id": None})
        assert r.status_code == 200
        item = r.json()
        assert (item["amount"], item["unit_id"], item["display"]["qty_text"],
                item["display"]["unit_dimension"]) == (2, None, "2", "package")

    def test_zero_or_negative_amounts_are_rejected(self):
        row = _add(_mk("Nolla s9"))
        assert client.put(f"/api/shopping-list/{row}", json={"amount": 0}).status_code == 422
        assert client.put(f"/api/shopping-list/{row}", json={"amount": -1}).status_code == 422

    def test_a_null_note_is_stored_as_empty_text(self):
        row = _add(_mk("Muistiinpano s10"), note="poista")
        assert client.put(f"/api/shopping-list/{row}", json={"note": None}).status_code == 200
        assert _row(row)["note"] == ""

    def test_unknown_units_are_rejected(self):
        row = _add(_mk("Yksikötön s11"))
        assert client.put(f"/api/shopping-list/{row}", json={"unit_id": 99999999}).status_code == 400
