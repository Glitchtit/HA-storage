"""Schema, unit catalog and product role/fact fields (quantity overhaul phase 1)."""
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


def _units():
    return {u["abbreviation"]: u for u in client.get("/api/units").json()}


class TestUnitCatalog:
    def test_seeded_units_have_dimensions(self):
        units = _units()
        assert units["g"]["dimension"] == "mass" and units["g"]["base_factor"] == 1.0
        assert units["dl"]["dimension"] == "volume" and units["dl"]["base_factor"] == 100.0
        assert units["kpl"]["dimension"] == "count"
        assert units["rs"]["dimension"] == "vague" and units["rs"]["base_factor"] is None

    def test_new_package_units_installed(self):
        units = _units()
        for abbr in ("pkt", "prk", "tlk", "pss", "rasia", "nippu", "ruukku"):
            assert units[abbr]["dimension"] == "package", abbr
        assert units["cl"]["base_factor"] == 10.0
        assert units["mm"]["dimension"] == "volume"

    def test_catalog_is_idempotent(self):
        from database import _ensure_unit_catalog
        before = len(_units())
        _ensure_unit_catalog(get_connection())
        assert len(_units()) == before

    def test_create_unit_rejects_unknown_dimension(self):
        r = client.post("/api/units", json={
            "name": "Kauha", "abbreviation": "kauha-x", "dimension": "ladle"})
        assert r.status_code == 400

    def test_custom_unit_without_dimension_allowed(self):
        r = client.post("/api/units", json={"name": "Kourallinen", "abbreviation": "kour-x"})
        assert r.status_code == 201 and r.json()["dimension"] is None


class TestResolveUnitText:
    def test_swedish_tablespoon(self):
        r = client.get("/api/units/resolve", params={"text": "msk"})
        assert r.status_code == 200
        body = r.json()
        assert body["abbreviation"] == "rkl" and body["multiplier"] == 1.0
        assert body["unit_id"] == _units()["rkl"]["id"]

    def test_cup_converts_to_dl(self):
        body = client.get("/api/units/resolve", params={"text": "cups"}).json()
        assert body["abbreviation"] == "dl" and body["multiplier"] == 2.4

    def test_unknown_text_404(self):
        r = client.get("/api/units/resolve", params={"text": "handful"})
        assert r.status_code == 404


class TestProductKindAndFacts:
    def test_default_kind_is_sku(self):
        r = client.post("/api/products", json={"name": "kind-default-q1", "unit_id": _units()["kpl"]["id"]})
        assert r.status_code == 201 and r.json()["kind"] == "sku"

    def test_explicit_concept(self):
        r = client.post("/api/products", json={
            "name": "kind-concept-q2", "unit_id": _units()["dl"]["id"], "kind": "concept"})
        assert r.json()["kind"] == "concept"

    def test_invalid_kind_rejected(self):
        r = client.post("/api/products", json={
            "name": "kind-bad-q3", "unit_id": _units()["kpl"]["id"], "kind": "category"})
        assert r.status_code == 400

    def test_group_master_no_longer_implies_concept(self):
        conn = get_connection()
        gm = conn.execute("SELECT id FROM product_groups WHERE name = 'Group master'").fetchone()
        gid = gm["id"] if gm else conn.execute(
            "INSERT INTO product_groups (name) VALUES ('Group master')").lastrowid
        conn.commit()
        r = client.post("/api/products", json={
            "name": "gm-stub-q4", "unit_id": _units()["g"]["id"], "product_group_id": gid})
        assert r.json()["kind"] == "sku"

    def test_manual_facts_get_manual_provenance(self):
        units = _units()
        r = client.post("/api/products", json={
            "name": "facts-q5", "unit_id": units["kpl"]["id"],
            "content_amount": 2, "content_unit_id": units["kg"]["id"], "density_g_ml": 0.6})
        body = r.json()
        assert body["content_amount"] == 2 and body["content_unit_id"] == units["kg"]["id"]
        assert body["facts_meta"] == {"content": "manual", "density_g_ml": "manual"}

    def test_unchanged_fact_keeps_provenance(self):
        units = _units()
        pid = client.post("/api/products", json={
            "name": "Kerma 2dl q6", "unit_id": units["kpl"]["id"]}).json()["id"]
        conn = get_connection()
        conn.execute(
            "UPDATE products SET content_amount = 2, content_unit_id = ?, facts_meta = ? WHERE id = ?",
            (units["dl"]["id"], '{"content": "name"}', pid))
        conn.commit()
        # The admin form re-sends every field on save; equal values stay automatic.
        r = client.put(f"/api/products/{pid}", json={
            "name": "Kerma 2dl q6", "content_amount": 2, "content_unit_id": units["dl"]["id"]})
        assert r.json()["facts_meta"] == {"content": "name"}
        r = client.put(f"/api/products/{pid}", json={"content_amount": 2.5})
        assert r.json()["facts_meta"] == {"content": "manual"}

    def test_cleared_fact_drops_provenance(self):
        units = _units()
        pid = client.post("/api/products", json={
            "name": "facts-q7", "unit_id": units["kpl"]["id"], "density_g_ml": 1.0}).json()["id"]
        r = client.put(f"/api/products/{pid}", json={"density_g_ml": None})
        assert r.json()["density_g_ml"] is None and r.json()["facts_meta"] == {}

    def test_bad_content_unit_rejected(self):
        r = client.post("/api/products", json={
            "name": "facts-q8", "unit_id": _units()["kpl"]["id"], "content_unit_id": 999999})
        assert r.status_code == 400

    def test_concept_with_children_cannot_become_sku(self):
        units = _units()
        cat = client.post("/api/products", json={
            "name": "kind-cat-q9", "unit_id": units["dl"]["id"], "kind": "concept"}).json()["id"]
        client.post("/api/products", json={
            "name": "kind-sku-q9 1l", "unit_id": units["kpl"]["id"], "parent_id": cat})
        r = client.put(f"/api/products/{cat}", json={"kind": "sku"})
        assert r.status_code == 400
