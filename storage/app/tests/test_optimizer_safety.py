"""AI optimize is non-destructive: it enriches products but never rewrites the
tree, never deletes recipe ingredients, and merges multi-packs through
merge.py so nothing that referenced them is lost."""
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

import optimizer

client = TestClient(app)


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _fake_ai(details=None):
    """Category prompts get 'Juomat'; detail prompts get `details` or nothing."""
    def fake(prompt, conn, **kw):
        ids = [line.split(":")[0].strip() for line in prompt.split("Products:\n")[-1].splitlines() if ":" in line]
        if '"category"' in prompt and "location_id" not in prompt:
            return {i: {"category": "Juomat"} for i in ids}
        return details or {}
    return fake


class TestFullRunIsNonDestructive:
    def test_tree_flags_and_recipes_untouched(self, monkeypatch):
        monkeypatch.setattr("optimizer.call_ai_json", _fake_ai())
        monkeypatch.setattr("linker.call_ai_json", lambda p, c, **k: [])
        cat = _mk("virvoitusjuoma-o1", kind="concept", active=False)
        sku = _mk("Pepsi Max 1,5l o1", parent_id=cat)
        rid = client.post("/api/recipes", json={"name": "Booli o1", "ingredients": [
            {"product_id": cat, "amount": 1, "unit_id": _u("l")},
            {"product_id": cat, "amount": 2, "unit_id": _u("dl")},
        ]}).json()["id"]
        conn = get_connection()
        before = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]

        optimizer.run_optimize(conn, product_ids=None)

        assert conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"] == before
        row = conn.execute("SELECT parent_id, product_group_id FROM products WHERE id = ?", (sku,)).fetchone()
        assert row["parent_id"] == cat and row["product_group_id"] is not None
        concept = conn.execute("SELECT active, kind FROM products WHERE id = ?", (cat,)).fetchone()
        assert concept["active"] == 0 and concept["kind"] == "concept"
        assert len(client.get(f"/api/recipes/{rid}").json()["ingredients"]) == 2

class TestMultipackMergeKeepsReferences:
    def test_six_pack_merged_into_base(self, monkeypatch):
        base = _mk("Sprite zero 330ml o3")
        multi = _mk("Sprite zero 6x330ml o3")
        client.post("/api/stock/add", json={"product_id": multi, "amount": 1})
        client.post("/api/barcodes", json={"product_id": multi, "barcode": f"64{multi:011d}"})
        rid = client.post("/api/recipes", json={"name": "Drinkki o3", "ingredients": [
            {"product_id": multi, "amount": 1, "unit_id": _u("kpl")}]}).json()["id"]
        row = client.post("/api/shopping-list", json={"product_id": multi, "amount": 1}).json()
        details = {str(multi): {"pack_size": 6, "pack_unit": "kpl", "base_product_name": "Sprite zero 330ml o3"}}
        monkeypatch.setattr("optimizer.call_ai_json", _fake_ai(details))

        optimizer.run_optimize(get_connection(), product_ids=[multi], link=False)

        assert client.get(f"/api/products/{multi}").status_code == 404
        detail = client.get(f"/api/products/{base}").json()
        assert detail["stock_amount"] == 6
        assert detail["barcodes"][0]["pack_size"] == 6
        assert client.get(f"/api/recipes/{rid}").json()["ingredients"][0]["product_id"] == base
        moved = next(i for i in client.get("/api/shopping-list").json() if i["id"] == row["id"])
        assert moved["product_id"] == base


class TestMultipackSafety:
    def _pack(self, pid, base_name, size=6):
        return {str(pid): {"pack_size": size, "pack_unit": "kpl", "base_product_name": base_name}}

    def test_multipack_without_base_is_left_alone(self, monkeypatch):
        """No single-unit SKU to merge into: the pack stays its own product and
        a second run changes nothing (it used to be renamed and its stock
        multiplied on every run)."""
        multi = _mk("Sprite zero 6x0,33l o5")
        client.post("/api/stock/add", json={"product_id": multi, "amount": 1})
        client.post("/api/barcodes", json={"product_id": multi, "barcode": f"64{multi:011d}"})
        monkeypatch.setattr("optimizer.call_ai_json", _fake_ai(self._pack(multi, "Sprite zero 0,33l o5")))

        for _ in range(2):
            optimizer.run_optimize(get_connection(), product_ids=[multi], link=False)

        detail = client.get(f"/api/products/{multi}").json()
        assert detail["name"] == "Sprite zero 6x0,33l o5"
        assert detail["stock_amount"] == 1
        assert detail["barcodes"][0]["pack_size"] == 1

    def test_base_name_matching_a_concept_is_ignored(self, monkeypatch):
        concept = _mk("Limsa o6", kind="concept")
        multi = _mk("Pirkka limsa 6x0,33l o6")
        client.post("/api/stock/add", json={"product_id": multi, "amount": 1})
        monkeypatch.setattr("optimizer.call_ai_json", _fake_ai(self._pack(multi, "Limsa o6")))

        optimizer.run_optimize(get_connection(), product_ids=[multi], link=False)

        detail = client.get(f"/api/products/{multi}").json()
        assert detail["name"] == "Pirkka limsa 6x0,33l o6" and detail["stock_amount"] == 1
        assert client.get(f"/api/products/{concept}").json()["kind"] == "concept"

    def test_failed_merge_keeps_other_products_updates(self, monkeypatch):
        target = max(l["id"] for l in client.get("/api/locations").json())
        plain = _mk("Valio maito 1l o7")
        _mk("Fanta 0,33l o7")
        multi = _mk("Fanta 6x0,33l o7")
        client.post("/api/stock/add", json={"product_id": multi, "amount": 1})
        details = {str(plain): {"location_id": target, "best_before_days": 10},
                   **self._pack(multi, "Fanta 0,33l o7")}
        monkeypatch.setattr("optimizer.call_ai_json", _fake_ai(details))

        def boom(*a, **k):
            raise ValueError("merge refused")
        monkeypatch.setattr("optimizer.merge_products", boom)

        optimizer.run_optimize(get_connection(), product_ids=[plain, multi], link=False)

        p = client.get(f"/api/products/{plain}").json()
        assert p["location_id"] == target
        assert p["default_best_before_days"] == 10
        assert p["product_group_id"] is not None
        assert client.get(f"/api/products/{multi}").json()["stock_amount"] == 1

    def test_pack_count_above_cap_is_not_a_multipack(self, monkeypatch):
        base = _mk("Pumpulipuikko o8")
        box = _mk("Pumpulipuikko 200kpl o8")
        monkeypatch.setattr("optimizer.call_ai_json", _fake_ai(self._pack(box, "Pumpulipuikko o8", size=200)))

        optimizer.run_optimize(get_connection(), product_ids=[box], link=False)

        assert client.get(f"/api/products/{box}").status_code == 200
        assert client.get(f"/api/products/{base}").status_code == 200


class TestConfigErrorStopsTheRun:
    def test_config_error_is_not_swallowed(self, monkeypatch):
        import pytest
        from ai_client import AiConfigError

        def dead(prompt, conn, **kw):
            raise AiConfigError("Gemini 404: model not found")
        monkeypatch.setattr("optimizer.call_ai_json", dead)
        pid = _mk("Jaffa 1,5l o2")
        with pytest.raises(AiConfigError):
            optimizer.run_optimize(get_connection(), product_ids=[pid], link=False)
        with pytest.raises(AiConfigError):
            optimizer.run_expiry_fix(get_connection(), product_ids=[pid])


class TestLinkStep:
    def test_link_flag_controls_linker(self, monkeypatch):
        monkeypatch.setattr("optimizer.call_ai_json", _fake_ai())
        calls = []
        monkeypatch.setattr("linker.link_products",
                            lambda conn, ids, **k: calls.append(list(ids)) or {"linked": [], "proposed": [], "unmatched": ids})
        pid = _mk("Fanta 1,5l o4")
        optimizer.run_optimize(get_connection(), product_ids=[pid], link=False)
        assert calls == []
        optimizer.run_optimize(get_connection(), product_ids=[pid])
        assert calls == [[pid]]


class TestLocationFollowsStock:
    def test_stock_added_before_location_moves_with_it(self, monkeypatch):
        locs = {l["name"]: l["id"] for l in client.get("/api/locations").json()}
        target = max(locs.values())  # not the fallback (lowest id)
        pid = _mk("Spagetti 1kg o9")
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1})
        conn = get_connection()
        fallback = conn.execute("SELECT location_id FROM stock WHERE product_id = ?", (pid,)).fetchone()["location_id"]
        assert fallback == min(locs.values())
        monkeypatch.setattr("optimizer.call_ai_json", _fake_ai({str(pid): {"location_id": target}}))
        optimizer.run_optimize(conn, product_ids=[pid], link=False)
        assert conn.execute("SELECT location_id FROM stock WHERE product_id = ?", (pid,)).fetchone()["location_id"] == target
