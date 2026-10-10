"""Aliases survive merges, and the alias API is the manual escape hatch."""
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

import concepts

client = TestClient(app)


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestMergeKeepsAliases:
    def test_aliases_move_and_source_name_becomes_alias(self):
        keep = _mk("Kaneli a1", unit="g", kind="concept")
        dup = _mk("Kanel a1", unit="g", kind="concept")
        conn = get_connection()
        concepts.add_alias(conn, "cinnamon a1", dup, "recipe")
        conn.commit()
        assert client.post(f"/api/products/{dup}/merge", json={"target_id": keep}).status_code == 200
        aliases = {a["alias"] for a in client.get(f"/api/products/{keep}/aliases").json()}
        assert {"cinnamon a1", "kanel a1"} <= aliases
        assert concepts.find_concept(conn, "Kanel a1") == keep

    def test_concept_target_takes_no_package_facts(self):
        concept = _mk("Kerma a2", unit="dl", kind="concept")
        sku = _mk("Valio kerma a2 2dl", unit="kpl", density_g_ml=1.0, pack_count=1, unit_price=1.5)
        conn = get_connection()
        conn.execute(
            "UPDATE products SET content_amount = 2, content_unit_id = ? WHERE id = ?", (_u("dl"), sku)
        )
        conn.commit()
        assert client.post(f"/api/products/{sku}/merge", json={"target_id": concept}).status_code == 200
        merged = client.get(f"/api/products/{concept}").json()
        assert merged["content_amount"] is None and merged["pack_count"] is None
        assert merged["unit_price"] is None
        assert merged["density_g_ml"] == 1.0


class TestAliasApi:
    def test_add_list_and_delete(self):
        pid = _mk("Vehnäjauho a3", unit="dl", kind="concept")
        r = client.post(f"/api/products/{pid}/aliases", json={"alias": "Vetemjöl a3"})
        assert r.status_code == 201 and r.json() == {"alias": "vetemjöl a3", "source": "manual"}
        assert [a["alias"] for a in client.get(f"/api/products/{pid}/aliases").json()] == ["vetemjöl a3"]
        assert client.delete(f"/api/products/{pid}/aliases/vetemjöl a3").status_code == 204
        assert client.get(f"/api/products/{pid}/aliases").json() == []

    def test_manual_alias_takes_over_an_existing_mapping(self):
        wrong = _mk("Vispikerma a4", unit="dl", kind="concept")
        right = _mk("Kerma a4", unit="dl", kind="concept")
        conn = get_connection()
        concepts.add_alias(conn, "grädde a4", wrong, "ai")
        conn.commit()
        assert client.post(f"/api/products/{right}/aliases", json={"alias": "grädde a4"}).status_code == 201
        assert concepts.find_concept(conn, "grädde a4") == right

    def test_rejects_skus_blank_aliases_and_unknown_products(self):
        sku = _mk("Valio maito a5 1l")
        concept = _mk("Maito a5", unit="dl", kind="concept")
        assert client.post(f"/api/products/{sku}/aliases", json={"alias": "mjölk a5"}).status_code == 400
        assert client.post(f"/api/products/{concept}/aliases", json={"alias": " ,. "}).status_code == 400
        assert client.post("/api/products/99999999/aliases", json={"alias": "x a5"}).status_code == 404
        assert client.delete(f"/api/products/{concept}/aliases/nope a5").status_code == 404
