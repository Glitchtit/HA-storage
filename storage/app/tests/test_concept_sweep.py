"""Whole-catalog concept sweep (spec §6 steps 9-10) and the unit repair."""
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

import concept_jobs

client = TestClient(app)


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _get(pid):
    return client.get(f"/api/products/{pid}")


class _Inline:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, **_):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


def _sweep_ai(ids):
    """Answers each kind of sweep prompt for the products in `ids`."""
    def fake(prompt, conn, **kw):
        if "have no products under them" in prompt:
            return [{"id": ids["dup"], "relation": "same", "target_id": ids["spag"],
                     "confidence": "high"}]
        if "ARE the concept" in prompt:
            return [{"concept_id": ids["jam"], "product_id": ids["jam_sku"], "confidence": "high"}]
        if "aisle group" in prompt:
            return [{"id": ids["jam"], "group_id": ids["group"]}]
        if "kitchen facts" in prompt:
            return [{"id": ids["jam"], "density_g_ml": 0.8}]
        return []
    return fake


def _fixture(tag):
    ids = {"group": client.post("/api/product-groups", json={"name": f"Leivonta {tag}"}).json()["id"]}
    ids["spag"] = _mk(f"Spagetti {tag}", unit="g", kind="concept")
    _mk(f"Pirkka spagetti {tag} 1kg", parent_id=ids["spag"])
    ids["dup"] = _mk(f"Spaghetti {tag}", unit="g", kind="concept")
    ids["jam"] = _mk(f"Hillosokeri {tag}", unit="dl", kind="concept")
    ids["jam_sku"] = _mk(f"Dansukker hillosokeri {tag} 1kg")
    ids["recipe"] = client.post("/api/recipes", json={"name": f"Hillo {tag}", "ingredients": [
        {"product_id": ids["jam"], "amount": 1, "unit_id": _u("dl")},
        {"product_id": ids["dup"], "amount": 200, "unit_id": _u("g")}]}).json()["id"]
    return ids


def test_sweep_places_adopts_groups_and_estimates(monkeypatch):
    ids = _fixture("s1")
    stats = concept_jobs.run_sweep(get_connection(), _sweep_ai(ids))
    assert stats["place"]["merged"] >= 1 and stats["adopt"]["adopted"] >= 1
    assert _get(ids["dup"]).status_code == 404
    jam = _get(ids["jam"]).json()
    assert jam["product_group_id"] == ids["group"] and jam["density_g_ml"] == 0.8
    assert _get(ids["jam_sku"]).json()["parent_id"] == ids["jam"]
    ings = client.get(f"/api/recipes/{ids['recipe']}").json()["ingredients"]
    assert [i["product_id"] for i in ings] == [ids["jam"], ids["spag"]]


def test_sweep_endpoint_runs_in_the_background_and_reports(monkeypatch):
    ids = _fixture("s2")
    monkeypatch.setattr("routers.products._ai_configured", lambda conn: True)
    monkeypatch.setattr("ai_client.call_ai_json", _sweep_ai(ids))
    monkeypatch.setattr("concept_jobs.threading.Thread", _Inline)
    assert client.post("/api/concepts/sweep").json() == {"started": True}
    status = client.get("/api/concepts/sweep").json()
    assert status["running"] is False and status["error"] is None
    assert status["result"]["adopt"]["adopted"] >= 1
    flag = get_connection().execute(
        "SELECT value FROM _meta WHERE key = 'concepts_sweep_v1'").fetchone()
    assert flag is not None


def test_sweep_without_ai_is_skipped_and_retried_later(monkeypatch):
    conn = get_connection()
    conn.execute("DELETE FROM _meta WHERE key = 'concepts_sweep_v1'")
    conn.commit()
    monkeypatch.setattr("routers.products._ai_configured", lambda conn: False)
    monkeypatch.setattr("concept_jobs.threading.Thread", _Inline)
    assert concept_jobs.start_sweep_if_due() is True
    assert client.get("/api/concepts/sweep").json()["result"] == {"skipped": "no AI provider"}
    assert conn.execute(
        "SELECT 1 FROM _meta WHERE key = 'concepts_sweep_v1'").fetchone() is None


def test_sweep_is_not_due_once_done(monkeypatch):
    conn = get_connection()
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('concepts_sweep_v1', '{}')")
    conn.commit()
    monkeypatch.setattr("concept_jobs.threading.Thread", _Inline)
    assert concept_jobs.start_sweep_if_due() is False


def test_repair_units_endpoint_follows_recipe_usage():
    cream = _mk("Ruokakerma s5", unit="g", kind="concept")
    client.post("/api/recipes", json={"name": "Kastike s5", "ingredients": [
        {"product_id": cream, "amount": 2, "unit_id": _u("dl")},
        {"product_id": cream, "amount": 1, "unit_id": _u("dl")}]})
    r = client.post("/api/concepts/repair-units")
    assert r.status_code == 200 and r.json()["changed"] >= 1
    assert _get(cream).json()["unit_id"] == _u("dl")


def test_isolated_twins_merge_once_one_has_adopted_its_products():
    """Two childless twins cannot target each other in the first placement
    pass; the sweep places again after adoption, when one of them has products."""
    kept = _mk("Spagetti s7", unit="g", kind="concept")
    twin = _mk("Spaghetti s7", unit="g", kind="concept")
    sku = _mk("Pirkka spagetti s7 1kg")
    rid = client.post("/api/recipes", json={"name": "Pasta s7", "ingredients": [
        {"product_id": twin, "amount": 400, "unit_id": _u("g")}]}).json()["id"]

    def fake(prompt, conn, **kw):
        if "have no products under them" in prompt:
            return [{"id": twin, "relation": "same", "target_id": kept, "confidence": "high"}]
        if "ARE the concept" in prompt:
            return [{"concept_id": kept, "product_id": sku, "confidence": "high"}]
        return []

    concept_jobs.run_sweep(get_connection(), fake)
    assert _get(twin).status_code == 404
    assert _get(sku).json()["parent_id"] == kept
    assert client.get(f"/api/recipes/{rid}").json()["ingredients"][0]["product_id"] == kept
