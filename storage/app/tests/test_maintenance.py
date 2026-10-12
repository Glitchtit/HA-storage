"""Correcting data left by older versions: a lot can be edited in place, a
store can be removed, and the phase-1 conversion backup is dropped once."""
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


def _lot(product_id, **extra):
    r = client.post("/api/stock/add", json={"product_id": product_id, "amount": 1,
                                            "purchased_date": "2026-07-10", **extra})
    assert r.status_code == 201, r.text
    conn = get_connection()
    return conn.execute("SELECT id FROM stock WHERE product_id = ? ORDER BY id DESC", (product_id,)).fetchone()["id"]


def _history(product_id):
    conn = get_connection()
    return conn.execute("SELECT COUNT(*) AS n FROM stock_history WHERE product_id = ?", (product_id,)).fetchone()["n"]


class TestEditLot:
    def test_moves_a_lot_to_another_product_keeping_its_dates_and_price(self):
        concept = _mk("WC-paperi m1", kind="concept")
        sku = _mk("Lotus wc-paperi 16rl m1", parent_id=concept)
        lot = _lot(concept, price_paid=7.5, best_before_date="2027-01-01")
        before = _history(sku)
        r = client.put(f"/api/stock/{lot}", json={"product_id": sku})
        assert r.status_code == 200, r.text
        e = r.json()
        assert (e["product_id"], e["purchased_date"], e["best_before_date"], e["price_paid"]) == (
            sku, "2026-07-10", "2027-01-01", 7.5)
        assert _history(sku) == before  # a correction is not a purchase

    def test_fixes_a_lot_stored_in_the_wrong_unit(self):
        sku = _mk("Talouspaperi 4rl m2")
        lot = _lot(sku, unit_id=_u("g"))
        r = client.put(f"/api/stock/{lot}", json={"unit_id": _u("kpl")})
        assert r.status_code == 200, r.text
        assert r.json()["unit_id"] == _u("kpl")

    def test_clears_a_placeholder_best_before_date(self):
        sku = _mk("Tomaattimurska 400 g m3", default_best_before_days=0)
        lot = _lot(sku, best_before_date="2999-12-31")
        r = client.put(f"/api/stock/{lot}", json={"best_before_date": None})
        assert r.status_code == 200, r.text
        assert (r.json()["best_before_date"], r.json()["best_before_days"]) == (None, None)

    def test_a_new_best_before_date_resets_the_days(self):
        sku = _mk("Jogurtti m4")
        lot = _lot(sku)
        r = client.put(f"/api/stock/{lot}", json={"best_before_date": "2026-07-20"})
        assert (r.json()["best_before_date"], r.json()["best_before_days"]) == ("2026-07-20", 10)

    def test_corrects_the_amount_when_rolls_were_counted_as_packs(self):
        concept = _mk("Talouspaperi m7", kind="concept")
        sku = _mk("Serla talouspaperi 4rl m7", parent_id=concept, pack_count=4)
        lot = _lot(concept, amount=4)
        r = client.put(f"/api/stock/{lot}", json={"product_id": sku, "amount": 1})
        assert r.status_code == 200, r.text
        assert (r.json()["product_id"], r.json()["amount"]) == (sku, 1)

    def test_opened_never_exceeds_the_corrected_amount_and_zero_is_refused(self):
        sku = _mk("Kahvi m8")
        lot = _lot(sku, amount=3)
        client.post("/api/stock/open", json={"product_id": sku, "amount": 2})
        r = client.put(f"/api/stock/{lot}", json={"amount": 1})
        assert (r.json()["amount"], r.json()["amount_opened"]) == (1, 1)
        assert client.put(f"/api/stock/{lot}", json={"amount": 0}).status_code == 422

    def test_unknown_lot_product_or_unit_is_refused(self):
        sku = _mk("Maito m5")
        lot = _lot(sku)
        assert client.put("/api/stock/999999", json={"unit_id": _u("kpl")}).status_code == 404
        assert client.put(f"/api/stock/{lot}", json={"product_id": 999999}).status_code == 404
        assert client.put(f"/api/stock/{lot}", json={"unit_id": 999999}).status_code == 404
        assert client.put(f"/api/stock/{lot}", json={"location_id": 999999}).status_code == 404


class TestDeleteStore:
    def test_removes_the_store_and_its_availability(self):
        sku = _mk("Maissi m6")
        assert client.put("/api/stores/test-store-m6", json={"name": "Testi"}).status_code == 200
        conn = get_connection()
        conn.execute("INSERT INTO product_availability (product_id, store_id, available) VALUES (?, 'test-store-m6', 1)", (sku,))
        conn.commit()
        assert client.delete("/api/stores/test-store-m6").status_code == 204
        assert "test-store-m6" not in {s["id"] for s in client.get("/api/stores").json()}
        assert not conn.execute("SELECT 1 FROM product_availability WHERE store_id = 'test-store-m6'").fetchone()

    def test_unknown_store_is_404(self):
        assert client.delete("/api/stores/no-such-store").status_code == 404


class TestDropConversionBackup:
    def test_the_phase1_backup_table_is_dropped_once(self):
        import database
        conn = get_connection()
        conn.execute("CREATE TABLE IF NOT EXISTS unit_conversions_backup_qty1 (id INTEGER)")
        conn.execute("DELETE FROM _meta WHERE key = 'drop_qty1_backup_v1'")
        conn.commit()
        database.drop_old_backups(conn)
        names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "unit_conversions_backup_qty1" not in names
        assert "recipe_ingredients_backup" in names  # the re-import undo log stays
        assert conn.execute("SELECT 1 FROM _meta WHERE key = 'drop_qty1_backup_v1'").fetchone()
