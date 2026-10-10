"""A purchase clears shopping rows of the bought SKU and of every concept
above it, converting the purchase into each row's unit (spec §4.8)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "products"), exist_ok=True)
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "recipes"), exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _add(product_id, amount=1, unit=None, note=""):
    body = {"product_id": product_id, "amount": amount, "note": note}
    if unit:
        body["unit_id"] = _u(unit)
    r = client.post("/api/shopping-list", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _buy(product_id, amount=1, unit=None):
    body = {"product_id": product_id, "amount": amount}
    if unit:
        body["unit_id"] = _u(unit)
    r = client.post("/api/stock/add", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _amount(row_id):
    """The row's amount, or None once it is gone."""
    row = next((i for i in client.get("/api/shopping-list").json() if i["id"] == row_id), None)
    return None if row is None else row["amount"]


class TestTreeAwareClearing:
    def test_buying_a_sku_clears_its_concept_row(self):
        concept = _mk("Maito c1", kind="concept")
        sku = _mk("Kevytmaito 1 l c1", parent_id=concept)
        row = _add(concept, 2)
        result = _buy(sku)
        assert result["cleared_rows"] == [
            {"id": row, "product_id": concept, "removed": False, "amount": 1}]
        assert _amount(row) == 1

    def test_own_rows_go_first_then_the_nearest_concept_then_the_oldest(self):
        top = _mk("Juusto c2", kind="concept")
        mid = _mk("Kypsytetty juusto c2", kind="concept", parent_id=top)
        sku = _mk("Parmesaani 200 g c2", parent_id=mid)
        top_row = _add(top)
        mid_row = _add(mid)
        own_row = _add(sku)
        result = _buy(sku, 2)
        assert [c["id"] for c in result["cleared_rows"]] == [own_row, mid_row]
        assert (_amount(own_row), _amount(mid_row), _amount(top_row)) == (None, None, 1)

    def test_purchase_is_converted_into_the_row_unit(self):
        concept = _mk("Kerma c3", unit="dl", kind="concept")
        sku = _mk("Vispikerma 2 dl c3", parent_id=concept, content_amount=2, content_unit_id=_u("dl"))
        row = _add(concept, 3, unit="dl", note="Reseptistä: A")
        _buy(sku)
        assert _amount(row) == 1
        _buy(sku)
        assert _amount(row) is None

    def test_a_bag_of_flour_covers_a_dl_row_through_density(self):
        concept = _mk("Vehnäjauho c4", unit="dl", kind="concept", density_g_ml=0.6)
        sku = _mk("Vehnäjauho 2 kg c4", parent_id=concept, content_amount=2, content_unit_id=_u("kg"))
        row = _add(concept, 5, unit="dl", note="Reseptistä: B")
        _buy(sku)
        assert _amount(row) is None

    def test_a_mass_row_without_facts_is_satisfied_whole_by_one_purchase(self):
        concept = _mk("Juustoraaste c5", unit="g", kind="concept")
        sku = _mk("Juustoraaste pussi c5", parent_id=concept)
        first = _add(concept, 200, unit="g", note="Reseptistä: C")
        second = _add(concept, 150, unit="g", note="Reseptistä: D")
        _buy(sku)
        assert (_amount(first), _amount(second)) == (None, 150)

    def test_a_package_counts_as_one_piece_without_a_pack_size(self):
        concept = _mk("Sitruuna c6", kind="concept")
        sku = _mk("Sitruuna kpl c6", parent_id=concept)
        row = _add(concept, 3, unit="kpl", note="Reseptistä: E")
        _buy(sku)
        assert _amount(row) == 2

    def test_pack_size_counts_pieces(self):
        concept = _mk("Kananmuna c7", kind="concept")
        sku = _mk("Kananmunat 10 kpl c7", parent_id=concept, pack_count=10)
        row = _add(concept, 12, unit="kpl", note="Reseptistä: F")
        _buy(sku)
        assert _amount(row) == 2

    def test_a_pinned_concept_is_only_cleared_by_buying_it_exactly(self):
        concept = _mk("Kahvi c8", kind="concept")
        sku = _mk("Arkikahvi c8", parent_id=concept)
        row = _add(concept)
        assert client.put(f"/api/shopping-list/{row}", json={"pinned": True}).status_code == 200
        assert _buy(sku)["cleared_rows"] == []
        assert _amount(row) == 1

    def test_a_pinned_sku_row_still_clears_on_its_own_purchase(self):
        sku = _mk("Erikoisolut c9")
        row = _add(sku)
        client.put(f"/api/shopping-list/{row}", json={"pinned": True})
        _buy(sku)
        assert _amount(row) is None

    def test_done_concept_rows_are_left_alone(self):
        concept = _mk("Voi c10", kind="concept")
        sku = _mk("Voi 500 g c10", parent_id=concept)
        row = _add(concept)
        client.put(f"/api/shopping-list/{row}", json={"done": True})
        _buy(sku)
        assert _amount(row) == 1

    def test_unrelated_rows_are_not_touched(self):
        concept = _mk("Sokeri c11", kind="concept")
        other = _mk("Suola c11", kind="concept")
        sku = _mk("Sokeri 1 kg c11", parent_id=concept)
        row = _add(other)
        assert _buy(sku)["cleared_rows"] == []
        assert _amount(row) == 1


    def test_simultaneous_purchases_all_count(self):
        # Every request has its own connection: purchases clearing one row at
        # the same moment must not start from the same amount and overwrite
        # each other. Three rounds, so a lucky interleaving cannot hide a race.
        for n in range(3):
            concept = _mk(f"Samaan aikaan c14 {n}", kind="concept")
            sku = _mk(f"Samaan aikaan pkt c14 {n}", parent_id=concept)
            row = _add(concept, 8)
            threads = [threading.Thread(target=_buy, args=(sku,)) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert _amount(row) is None


class TestReceiptClearing:
    def test_receipt_lines_clear_rows(self):
        concept = _mk("Jogurtti c12", kind="concept")
        sku = _mk("Maustamaton jogurtti c12", parent_id=concept)
        row = _add(concept, 2)
        r = client.post("/api/receipts/commit", json={"lines": [{"product_id": sku, "amount": 2}]})
        assert r.status_code == 200 and r.json()["added"] == 1
        assert r.json()["cleared_rows"] == [
            {"id": row, "product_id": concept, "removed": True, "amount": 0}]
        assert _amount(row) is None

    def test_receipt_updates_minimum_stock_rows_at_once(self):
        sku = _mk("Kahvisuodatin c13", min_stock_amount=2)
        client.post("/api/shopping-list/sync")
        assert any(i["product_id"] == sku and i["auto_added"] for i in client.get("/api/shopping-list").json())
        client.post("/api/receipts/commit", json={"lines": [{"product_id": sku, "amount": 2}]})
        assert not any(i["product_id"] == sku for i in client.get("/api/shopping-list").json())


def _broken_sync(conn):
    raise sqlite3.OperationalError("database is locked")


class TestSideEffectsNeverFailAPurchase:
    # Spec §7: a purchase never fails because of a list side effect.
    def test_a_failing_minimum_stock_sync_never_fails_the_purchase(self, monkeypatch):
        concept = _mk("Kaurahiutale c15", kind="concept")
        sku = _mk("Kaurahiutaleet 1 kg c15", parent_id=concept)
        row = _add(concept)
        monkeypatch.setattr("routers.stock.sync_auto_shopping", _broken_sync)
        assert [c["id"] for c in _buy(sku)["cleared_rows"]] == [row]
        assert _amount(row) is None

    def test_a_failing_sync_never_hides_what_a_receipt_cleared(self, monkeypatch):
        concept = _mk("Rusina c16", kind="concept")
        sku = _mk("Rusinat 250 g c16", parent_id=concept)
        row = _add(concept)
        monkeypatch.setattr("routers.receipts.sync_auto_shopping", _broken_sync)
        r = client.post("/api/receipts/commit", json={"lines": [{"product_id": sku, "amount": 1}]})
        assert r.status_code == 200
        assert [c["id"] for c in r.json()["cleared_rows"]] == [row]
        assert _amount(row) is None


def _correct(product_id, amount=1):
    r = client.post("/api/stock/correct-purchase", json={"product_id": product_id, "amount": amount})
    assert r.status_code == 200, r.text
    return r.json()


def _row(row_id):
    return next((i for i in client.get("/api/shopping-list").json() if i["id"] == row_id), None)


class TestUndoingAMisScan:
    # Swiping a scan back (correct-purchase) puts back the list rows it took.
    def test_a_corrected_scan_puts_back_the_row_it_removed(self):
        concept = _mk("Kookosmaito c17", kind="concept")
        sku = _mk("Kookosmaito 400 ml c17", parent_id=concept)
        recipe = client.post("/api/recipes", json={"name": "Curry c17"}).json()["id"]
        row = client.post("/api/shopping-list", json={
            "product_id": concept, "amount": 1, "note": "Reseptistä: Curry c17", "recipe_id": recipe}).json()["id"]
        _buy(sku)
        assert _amount(row) is None
        assert _correct(sku)["restored_rows"] == [{"id": row, "product_id": concept, "amount": 1}]
        item = _row(row)
        assert (item["amount"], item["note"], item["recipe_id"], item["done"]) == (1, "Reseptistä: Curry c17", recipe, False)

    def test_a_row_the_scan_reduced_gets_its_amount_back(self):
        concept = _mk("Kerma c18", unit="dl", kind="concept")
        sku = _mk("Vispikerma 2 dl c18", parent_id=concept, content_amount=2, content_unit_id=_u("dl"))
        row = _add(concept, 3, unit="dl", note="Reseptistä: B")
        _buy(sku)
        assert _amount(row) == 1
        _correct(sku)
        assert _amount(row) == 3

    def test_correcting_part_of_a_multipack_puts_back_that_part(self):
        concept = _mk("Jogurtti c19", kind="concept")
        sku = _mk("Jogurtti 6-pack c19", parent_id=concept)
        row = _add(concept, 6)
        _buy(sku, 6)
        assert _amount(row) is None
        _correct(sku, 2)
        assert _amount(row) == 2
        _correct(sku, 1)
        assert _amount(row) == 3

    def test_rows_removed_by_hand_after_the_scan_stay_removed(self):
        concept = _mk("Persilja c20", kind="concept")
        sku = _mk("Persilja ruukku c20", parent_id=concept)
        row = _add(concept, 3)
        _buy(sku)
        assert client.delete(f"/api/shopping-list/{row}").status_code == 204
        assert _correct(sku)["restored_rows"] == []
        assert _row(row) is None

    def test_ticked_rows_are_left_alone(self):
        concept = _mk("Tilli c21", kind="concept")
        sku = _mk("Tilli nippu c21", parent_id=concept)
        row = _add(concept, 3)
        _buy(sku)
        client.put(f"/api/shopping-list/{row}", json={"done": True})
        _correct(sku)
        assert (_row(row)["amount"], _row(row)["done"]) == (2, True)
