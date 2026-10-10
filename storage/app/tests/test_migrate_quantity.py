"""Quantity migration on a fixture DB shaped like the live catalog (2026-10).

Live shapes reproduced: optimizer categories stored in grams (the 'Kappale'
lookup bug), recipe stubs whose unit is a spoon, legacy "1 kpl = X" pack rows,
six density rows per product including a hallucinated 10 309 g/ml one.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import migrate_quantity
from database import get_db, init_db


@pytest.fixture
def legacy(tmp_path):
    conn = get_db(tmp_path / "legacy.db")
    init_db(conn)  # fresh schema; migration flags already set on the empty DB
    u = {r["abbreviation"]: r["id"] for r in conn.execute("SELECT id, abbreviation FROM units")}
    gm = conn.execute("INSERT INTO product_groups (name) VALUES ('Group master')").lastrowid

    def product(name, unit, parent=None, group=None, description=""):
        return conn.execute(
            "INSERT INTO products (name, unit_id, parent_id, product_group_id, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, u[unit], parent, group, description),
        ).lastrowid

    def conv(pid, src, dst, factor):
        conn.execute(
            "INSERT INTO unit_conversions (from_unit_id, to_unit_id, factor, product_id) "
            "VALUES (?, ?, ?, ?)", (u[src], u[dst], factor, pid))

    ids = {}
    ids["flour_cat"] = product("Vehnäjauho", "g", group=gm)
    ids["flour_sku"] = product("Myllyn Paras Erikoisvehnäjauho 2kg", "kpl", parent=ids["flour_cat"])
    conv(ids["flour_sku"], "kpl", "kg", 2)
    conv(ids["flour_sku"], "kg", "l", 1.67)
    conv(ids["flour_sku"], "g", "dl", 0.0167)
    conv(ids["flour_sku"], "ml", "g", 10309.0)  # hallucinated, must be ignored
    ids["egg_cat"] = product("Kananmuna", "g", group=gm)
    ids["egg_sku"] = product("Munat 10kpl/580g", "kpl", parent=ids["egg_cat"])
    conv(ids["egg_sku"], "kpl", "g", 580)
    ids["cheese_cat"] = product("Juusto", "g", group=gm)
    ids["cheese_sku"] = product("Valio cheddar 150g", "kpl", parent=ids["cheese_cat"])
    conv(ids["cheese_sku"], "kpl", "g", 150)
    ids["stub"] = product("raparperi", "rkl", description="Auto-created by recipe scraper")
    ids["garlic"] = product("Valkosipuli", "g", group=gm)
    conv(ids["garlic"], "kpl", "g", 50)
    ids["pepsi"] = product("Pepsi Max 1,5l", "kpl")
    rid = conn.execute("INSERT INTO recipes (name) VALUES ('Fixture')").lastrowid
    for pid, amount, unit in (
        (ids["flour_cat"], 3.5, "dl"), (ids["egg_cat"], 2, "kpl"), (ids["stub"], 500, "g"),
    ):
        conn.execute(
            "INSERT INTO recipe_ingredients (recipe_id, product_id, amount, unit_id) VALUES (?, ?, ?, ?)",
            (rid, pid, amount, u[unit]))
    conn.commit()
    yield conn, u, ids
    conn.close()


def _row(conn, pid):
    return conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()


def test_kind_classification(legacy):
    conn, u, ids = legacy
    migrate_quantity.run(conn, force=True)
    for key in ("flour_cat", "egg_cat", "cheese_cat", "stub", "garlic"):
        assert _row(conn, ids[key])["kind"] == "concept", key
    for key in ("flour_sku", "egg_sku", "cheese_sku", "pepsi"):
        assert _row(conn, ids[key])["kind"] == "sku", key


def test_content_from_rows_and_names(legacy):
    conn, u, ids = legacy
    migrate_quantity.run(conn, force=True)
    flour = _row(conn, ids["flour_sku"])
    assert flour["content_amount"] == 2 and flour["content_unit_id"] == u["kg"]
    eggs = _row(conn, ids["egg_sku"])
    assert eggs["content_amount"] == 580 and eggs["pack_count"] == 10
    pepsi = _row(conn, ids["pepsi"])
    assert pepsi["content_amount"] == 1.5 and pepsi["content_unit_id"] == u["l"]
    assert _row(conn, ids["garlic"])["piece_weight_g"] == 50
    assert '"content": "name"' in flour["facts_meta"]


def test_density_collapsed_and_lifted(legacy):
    conn, u, ids = legacy
    migrate_quantity.run(conn, force=True)
    sku_density = _row(conn, ids["flour_sku"])["density_g_ml"]
    assert abs(sku_density - 0.5988) < 0.001
    assert _row(conn, ids["flour_cat"])["density_g_ml"] == sku_density
    left = conn.execute(
        "SELECT COUNT(*) AS n FROM unit_conversions WHERE product_id IS NOT NULL").fetchone()["n"]
    assert left == 0
    backed_up = conn.execute(
        "SELECT COUNT(*) AS n FROM unit_conversions_backup_qty1").fetchone()["n"]
    assert backed_up == 7


def test_concept_units_repaired(legacy):
    conn, u, ids = legacy
    migrate_quantity.run(conn, force=True)
    assert _row(conn, ids["flour_cat"])["unit_id"] == u["dl"]
    assert _row(conn, ids["egg_cat"])["unit_id"] == u["kpl"]
    assert _row(conn, ids["stub"])["unit_id"] == u["g"]
    assert _row(conn, ids["cheese_cat"])["unit_id"] == u["g"]  # mass content, g already right


def test_global_conversions_untouched(legacy):
    conn, u, ids = legacy
    before = conn.execute(
        "SELECT COUNT(*) AS n FROM unit_conversions WHERE product_id IS NULL").fetchone()["n"]
    migrate_quantity.run(conn, force=True)
    after = conn.execute(
        "SELECT COUNT(*) AS n FROM unit_conversions WHERE product_id IS NULL").fetchone()["n"]
    assert before == after == 12


def test_idempotent(legacy):
    conn, u, ids = legacy
    first = migrate_quantity.run(conn, force=True)
    assert first["qty_kind_v1"] == 5
    assert migrate_quantity.run(conn) == {}
    again = migrate_quantity.run(conn, force=True)
    assert again["qty_kind_v1"] == 0 and again["qty_content_v1"] == 0
    assert again["qty_density_v1"] == 0 and again["qty_concept_units_v1"] == 0
