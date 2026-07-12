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


class TestEnsureConversions:
    def test_creates_kpl_to_g_conversion_on_create(self):
        kpl = _unit_id("kpl")
        r = client.post("/api/products", json={"name": "Testituote 250g", "unit_id": kpl})
        pid = r.json()["id"]
        convs = [c for c in client.get("/api/conversions").json() if c.get("product_id") == pid]
        assert len(convs) == 1
        assert convs[0]["factor"] == 250.0

    def test_sets_pack_count(self):
        kpl = _unit_id("kpl")
        r = client.post("/api/products", json={"name": "Munia 6kpl/348g", "unit_id": kpl})
        assert float(r.json()["pack_count"]) == 6.0

    def test_idempotent(self):
        from main import get_connection
        kpl = _unit_id("kpl")
        pid = client.post("/api/products", json={"name": "Idem 500g", "unit_id": kpl}).json()["id"]
        conn = get_connection()
        assert pack_size.ensure_pack_conversions(conn, pid) is False  # already written at create
