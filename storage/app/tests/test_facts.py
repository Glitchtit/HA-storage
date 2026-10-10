"""Catalog: effective facts, inheritance, legacy rows, role-based dimensions."""
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

from facts import Catalog
from quantity import COUNT, PACKAGE

client = TestClient(app)


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestFacts:
    def test_density_inherited_from_concept(self):
        cat = _mk("vehnäjauho-f1", unit="dl", kind="concept", density_g_ml=0.6)
        sku = _mk("Myllyn Paras vehnäjauho 2kg f1", parent_id=cat)
        f = Catalog(get_connection()).facts(sku)
        assert f.density_g_ml == 0.6
        assert f.content_amount == 2.0 and f.content_unit.abbreviation == "kg"

    def test_own_density_beats_inherited(self):
        cat = _mk("sokeri-f2", unit="dl", kind="concept", density_g_ml=0.85)
        sku = _mk("Tomusokeri 500g f2", parent_id=cat, density_g_ml=0.56)
        assert Catalog(get_connection()).facts(sku).density_g_ml == 0.56

    def test_content_not_inherited(self):
        cat = _mk("kerma-f3", unit="dl", kind="concept", content_amount=2,
                  content_unit_id=_u("dl"))
        sku = _mk("Kerma ilman kokoa f3", parent_id=cat)
        assert Catalog(get_connection()).facts(sku).content_amount is None

    def test_piece_weight_derived_from_carton(self):
        sku = _mk("Munat 10kpl/580g f4")
        f = Catalog(get_connection()).facts(sku)
        assert f.piece_weight_g == 58.0 and f.piece_weight_exact is True

    def test_legacy_rows_folded(self):
        conn = get_connection()
        sku = _mk("Legacy sku f5", unit="kpl")
        conn.execute(
            "INSERT INTO unit_conversions (from_unit_id, to_unit_id, factor, product_id) "
            "VALUES (?, ?, 65, ?)", (_u("kpl"), _u("g"), sku))
        conn.execute(
            "INSERT INTO unit_conversions (from_unit_id, to_unit_id, factor, product_id) "
            "VALUES (?, ?, 1.67, ?)", (_u("kg"), _u("l"), sku))
        conn.execute(
            "INSERT INTO unit_conversions (from_unit_id, to_unit_id, factor, product_id) "
            "VALUES (?, ?, 10309.0, ?)", (_u("ml"), _u("g"), sku))  # hallucinated
        conn.commit()
        f = Catalog(conn).facts(sku)
        assert f.content_amount == 65 and f.content_unit.abbreviation == "g"
        assert abs(f.density_g_ml - 1 / 1.67) < 1e-6
        assert f.piece_weight_g is None  # a SKU's kpl row is content, not piece weight

    def test_legacy_concept_row_is_piece_weight(self):
        conn = get_connection()
        cat = _mk("valkosipuli-f6", unit="kpl", kind="concept")
        conn.execute(
            "INSERT INTO unit_conversions (from_unit_id, to_unit_id, factor, product_id) "
            "VALUES (?, ?, 50, ?)", (_u("kpl"), _u("g"), cat))
        conn.commit()
        assert Catalog(conn).facts(cat).piece_weight_g == 50


class TestTree:
    def test_cycle_terminates(self):
        conn = get_connection()
        a = _mk("cycle-a-f7", kind="concept")
        b = _mk("cycle-b-f7", kind="concept", parent_id=a)
        conn.execute("UPDATE products SET parent_id = ? WHERE id = ?", (b, a))  # bad legacy data
        conn.commit()
        cat = Catalog(conn)
        assert set(cat.descendants(a)) == {b}
        assert cat.ancestors(a) == [b]
        conn.execute("UPDATE products SET parent_id = NULL WHERE id = ?", (a,))
        conn.commit()

    def test_stock_dim_by_role(self):
        cat_id = _mk("kananmuna-f8", kind="concept")
        sku = _mk("Munat 6kpl f8", parent_id=cat_id)
        cat = Catalog(get_connection())
        kpl = cat.unit_by_abbr("kpl")
        assert cat.stock_dim(sku, kpl) == PACKAGE
        assert cat.stock_dim(cat_id, kpl) == COUNT

    def test_unit_without_dimension_is_vague(self):
        r = client.post("/api/units", json={"name": "Kahmalo", "abbreviation": "kahm-f9"})
        unit = Catalog(get_connection()).unit(r.json()["id"])
        assert unit.dimension == "vague" and unit.base_factor is None

    def test_lots_fifo_across_products(self):
        a = _mk("fifo-a-f10")
        b = _mk("fifo-b-f10")
        client.post("/api/stock/add", json={"product_id": a, "amount": 1, "best_before_date": "2031-05-01"})
        client.post("/api/stock/add", json={"product_id": b, "amount": 1, "best_before_date": "2031-01-01"})
        lots = Catalog(get_connection()).lots([a, b])
        assert [l["product_id"] for l in lots] == [b, a]
