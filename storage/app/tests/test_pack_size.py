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
from main import app, get_connection
import pack_size

client = TestClient(app)


def _unit_id(abbrev="kpl"):
    for u in client.get("/api/units").json():
        if u["abbreviation"] == abbrev:
            return u["id"]
    raise AssertionError(f"unit {abbrev} missing")


class TestParse:
    def test_grams(self):
        assert pack_size.parse_pack_size("Pirkka babypinaatti 65g") == {
            "amount": 65.0, "unit": "g", "count": None}

    def test_count_and_weight(self):
        p = pack_size.parse_pack_size("Pirkka Luomu kananmunia 10kpl/580g")
        assert p["count"] == 10 and p["amount"] == 580.0 and p["unit"] == "g"

    def test_comma_decimal_litres(self):
        p = pack_size.parse_pack_size("Valio Keittiön kuohukerma 3,3 dl laktoositon")
        assert p["amount"] == 3.3 and p["unit"] == "dl"

    def test_nbsp_and_kg(self):
        p = pack_size.parse_pack_size("Myllyn Paras Erikoisvehnäjauho 1\xa0kg")
        assert p["amount"] == 1.0 and p["unit"] == "kg"

    def test_quality_class_is_not_count(self):
        p = pack_size.parse_pack_size("Pirkka suomalainen parsakaali 300g 1lk")
        assert p["count"] is None and p["amount"] == 300.0 and p["unit"] == "g"

    def test_no_size(self):
        assert pack_size.parse_pack_size("lohi") == {"amount": None, "unit": None, "count": None}

    def test_rolls(self):
        assert pack_size.parse_pack_size("Lotus Emilia talouspyyhe 4rl valk")["count"] == 4


class TestParseMultipack:
    def test_multipack_total_content(self):
        p = pack_size.parse_pack_size("Pepsi Max 6x0,33l")
        assert p["count"] == 6 and p["unit"] == "l" and abs(p["amount"] - 1.98) < 1e-9

    def test_two_by_grams(self):
        p = pack_size.parse_pack_size("Tonnikala 2 x 200g")
        assert p == {"amount": 400.0, "unit": "g", "count": 2}


class TestEnsurePackFacts:
    def _product(self, pid):
        return client.get(f"/api/products/{pid}").json()

    def test_content_set_on_create(self):
        r = client.post("/api/products", json={"name": "Testituote 250g", "unit_id": _unit_id("kpl")})
        body = r.json()
        assert body["content_amount"] == 250.0
        assert body["content_unit_id"] == _unit_id("g")
        assert body["facts_meta"]["content"] == "name"
        convs = [c for c in client.get("/api/conversions").json() if c.get("product_id") == body["id"]]
        assert convs == []  # facts live on the product, not in unit_conversions

    def test_sets_pack_count(self):
        r = client.post("/api/products", json={"name": "Munia 6kpl/348g", "unit_id": _unit_id("kpl")})
        body = r.json()
        assert float(body["pack_count"]) == 6.0 and body["content_amount"] == 348.0

    def test_idempotent(self):
        pid = client.post("/api/products", json={"name": "Idem 500g", "unit_id": _unit_id("kpl")}).json()["id"]
        assert pack_size.ensure_pack_facts(get_connection(), pid) is False

    def test_concepts_are_skipped(self):
        r = client.post("/api/products", json={
            "name": "Konsepti 500g", "unit_id": _unit_id("g"), "kind": "concept"})
        assert r.json()["content_amount"] is None

    def test_manual_content_never_overwritten(self):
        units = {u["abbreviation"]: u["id"] for u in client.get("/api/units").json()}
        r = client.post("/api/products", json={
            "name": "Kahvi 500g", "unit_id": units["kpl"],
            "content_amount": 450, "content_unit_id": units["g"]})
        body = r.json()
        assert body["content_amount"] == 450 and body["facts_meta"]["content"] == "manual"
