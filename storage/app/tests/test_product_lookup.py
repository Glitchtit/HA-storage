"""Name lookup for voice adds (spec §5.1) and renames reaching shopping rows."""
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


def _mk(name, **extra):
    kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
    r = client.post("/api/products", json={"name": name, "unit_id": kpl, **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _lookup(name):
    r = client.get("/api/products/lookup", params={"name": name})
    assert r.status_code == 200, r.text
    product = r.json()["product"]
    return product and product["id"]


class TestLookup:
    def test_an_alias_finds_its_concept(self):
        concept = _mk("Kananmuna l1", kind="concept")
        assert client.post(f"/api/products/{concept}/aliases", json={"alias": "munat l1"}).status_code == 201
        assert _lookup("Munat l1") == concept

    def test_a_concept_name_matches_whatever_the_case_and_accents(self):
        concept = _mk("Crème fraîche l2", kind="concept")
        assert _lookup("CREME FRAICHE l2") == concept

    def test_concepts_win_over_a_sku_with_the_same_name(self):
        _mk("Maito l3")
        concept = _mk("Maito l3", kind="concept")
        assert _lookup("maito l3") == concept

    def test_a_unique_product_name_matches_when_no_concept_does(self):
        sku = _mk("Pirkka kevytmaito l4")
        assert _lookup("pirkka kevytmaito l4") == sku

    def test_inactive_products_and_unknown_names_find_nothing(self):
        _mk("Lopetettu tuote l5", active=False)
        assert _lookup("lopetettu tuote l5") is None
        assert _lookup("ei tällaista l5") is None


def test_renaming_a_product_renames_its_shopping_rows():
    pid = _mk("vanha nimi l6", kind="concept")
    row = client.post("/api/shopping-list", json={"product_id": pid, "amount": 1}).json()["id"]
    assert client.put(f"/api/products/{pid}", json={"name": "Uusi nimi l6"}).status_code == 200
    item = next(i for i in client.get("/api/shopping-list").json() if i["id"] == row)
    assert item["ha_item_name"] == "Uusi nimi l6"
