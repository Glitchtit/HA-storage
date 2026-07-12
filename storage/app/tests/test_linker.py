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

client = TestClient(app)


def _unit_id(abbrev="kpl"):
    for u in client.get("/api/units").json():
        if u["abbreviation"] == abbrev:
            return u["id"]
    raise AssertionError(f"unit {abbrev} missing")


class TestSchema:
    def test_products_have_pack_count_and_staple(self):
        r = client.post("/api/products", json={
            "name": "schema-probe", "unit_id": _unit_id(), "staple": True, "pack_count": 6,
        })
        assert r.status_code == 201
        body = r.json()
        assert body["staple"] is True or body["staple"] == 1
        assert float(body["pack_count"]) == 6.0

    def test_link_proposals_table_exists(self):
        conn = get_connection()
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "link_proposals" in names


import linker


def _mk(name, parent_id=None, active=True):
    r = client.post("/api/products", json={
        "name": name, "unit_id": _unit_id(), "parent_id": parent_id, "active": active})
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestApplyLink:
    def test_sets_parent_and_history(self):
        conn = get_connection()
        cat = _mk("lohi-l1")
        sku = _mk("Pirkka savulohi 200g l1")
        linker.apply_link(conn, sku, cat, note="test")
        row = conn.execute("SELECT parent_id FROM products WHERE id = ?", (sku,)).fetchone()
        assert row["parent_id"] == cat
        ev = conn.execute(
            "SELECT * FROM stock_history WHERE product_id = ? AND event_type = 'link'",
            (sku,)).fetchone()
        assert ev is not None


class TestLinkProducts:
    def test_exact_normalized_match_no_ai(self, monkeypatch):
        def boom(prompt, conn, **k):
            raise AssertionError("AI must not be called for exact matches")
        monkeypatch.setattr("linker.call_ai_json", boom)
        conn = get_connection()
        cat = _mk("Cheddar-l2")
        dup = _mk("cheddar-l2")  # same name, case-insensitive
        res = linker.link_products(conn, [dup])
        assert dup in res["linked"]

    def test_ai_high_confidence_autolinks(self, monkeypatch):
        conn = get_connection()
        cat = _mk("lohi-l3")
        sku = _mk("Pirkka savulohifileepala 200g ASC l3")

        def fake(prompt, conn_, **k):
            return [{"product_id": sku, "parent_id": cat, "confidence": "high"}]
        monkeypatch.setattr("linker.call_ai_json", fake)
        res = linker.link_products(conn, [sku])
        assert sku in res["linked"]
        row = conn.execute("SELECT parent_id FROM products WHERE id = ?", (sku,)).fetchone()
        assert row["parent_id"] == cat

    def test_ai_medium_confidence_queues_proposal(self, monkeypatch):
        conn = get_connection()
        cat = _mk("voi-l4")
        sku = _mk("Valio Oivariini 350g l4")

        def fake(prompt, conn_, **k):
            return [{"product_id": sku, "parent_id": cat, "confidence": "medium"}]
        monkeypatch.setattr("linker.call_ai_json", fake)
        res = linker.link_products(conn, [sku])
        assert sku in res["proposed"]
        row = conn.execute(
            "SELECT * FROM link_proposals WHERE product_id = ? AND status = 'pending'",
            (sku,)).fetchone()
        assert row["proposed_parent_id"] == cat
        # product NOT linked yet
        assert conn.execute("SELECT parent_id FROM products WHERE id = ?",
                            (sku,)).fetchone()["parent_id"] is None

    def test_rejected_pair_never_reproposed(self, monkeypatch):
        conn = get_connection()
        cat = _mk("margariini-l5")
        sku = _mk("Voi-tuote l5")
        conn.execute(
            "INSERT INTO link_proposals (product_id, proposed_parent_id, confidence, status) "
            "VALUES (?, ?, 'medium', 'rejected')", (sku, cat))
        conn.commit()

        def fake(prompt, conn_, **k):
            return [{"product_id": sku, "parent_id": cat, "confidence": "high"}]
        monkeypatch.setattr("linker.call_ai_json", fake)
        res = linker.link_products(conn, [sku])
        assert sku not in res["linked"] and sku not in res["proposed"]

    def test_ai_offline_degrades(self, monkeypatch):
        conn = get_connection()
        sku = _mk("Tuntematon tuote 123g l6")

        def boom(prompt, conn_, **k):
            raise ValueError("AI offline")
        monkeypatch.setattr("linker.call_ai_json", boom)
        res = linker.link_products(conn, [sku])
        assert sku in res["unmatched"]


class TestRunReconcile:
    def test_sweep_links_and_backfills(self, monkeypatch):
        conn = get_connection()
        cat = _mk("kerma-l7")
        sku = _mk("Testikerma 2dl l7")
        # simulate a pre-parser product: wipe its conversion to test backfill
        conn.execute("DELETE FROM unit_conversions WHERE product_id = ?", (sku,))
        conn.commit()

        def fake(prompt, conn_, **k):
            return [{"product_id": sku, "parent_id": cat, "confidence": "high"}]
        monkeypatch.setattr("linker.call_ai_json", fake)
        res = linker.run_reconcile(conn)
        assert res["linked"] >= 1
        assert conn.execute(
            "SELECT 1 FROM unit_conversions WHERE product_id = ?", (sku,)).fetchone()


class TestLinksRouter:
    def _proposal(self, sku_name, cat_name):
        conn = get_connection()
        cat = _mk(cat_name)
        sku = _mk(sku_name)
        conn.execute(
            "INSERT INTO link_proposals (product_id, proposed_parent_id, confidence) "
            "VALUES (?, ?, 'medium')", (sku, cat))
        conn.commit()
        row = conn.execute(
            "SELECT id FROM link_proposals WHERE product_id = ?", (sku,)).fetchone()
        return row["id"], sku, cat

    def test_list_pending(self):
        prop_id, sku, cat = self._proposal("router-sku-1", "router-cat-1")
        items = client.get("/api/link-proposals").json()
        mine = [i for i in items if i["id"] == prop_id]
        assert mine and mine[0]["product_name"] == "router-sku-1"

    def test_accept_links(self):
        prop_id, sku, cat = self._proposal("router-sku-2", "router-cat-2")
        r = client.post(f"/api/link-proposals/{prop_id}/accept")
        assert r.status_code == 200
        conn = get_connection()
        assert conn.execute("SELECT parent_id FROM products WHERE id = ?",
                            (sku,)).fetchone()["parent_id"] == cat
        assert conn.execute("SELECT status FROM link_proposals WHERE id = ?",
                            (prop_id,)).fetchone()["status"] == "accepted"

    def test_reject_remembers(self):
        prop_id, sku, cat = self._proposal("router-sku-3", "router-cat-3")
        r = client.post(f"/api/link-proposals/{prop_id}/reject")
        assert r.status_code == 200
        conn = get_connection()
        assert conn.execute("SELECT status FROM link_proposals WHERE id = ?",
                            (prop_id,)).fetchone()["status"] == "rejected"
        assert conn.execute("SELECT parent_id FROM products WHERE id = ?",
                            (sku,)).fetchone()["parent_id"] is None

    def test_reconcile_endpoint(self, monkeypatch):
        monkeypatch.setattr("linker.call_ai_json", lambda p, c, **k: [])
        r = client.post("/api/products/reconcile")
        assert r.status_code == 200
        assert "linked" in r.json()
