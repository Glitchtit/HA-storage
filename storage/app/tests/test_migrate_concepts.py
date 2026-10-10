"""Concept identity migration on a fixture DB shaped like the live catalog
after phase 1 (2026-10): 266 inactive concepts in "Group master", recipe
stubs with lower-case names, twin concepts, a same-name child stub, and an
18-pack that phase 1 classified as a concept because things were linked
under it."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import concepts
import migrate_concepts
from database import get_db, init_db


@pytest.fixture
def live(tmp_path):
    conn = get_db(tmp_path / "live.db")
    init_db(conn)  # fresh schema; migration flags already set on the empty DB
    u = {r["abbreviation"]: r["id"] for r in conn.execute("SELECT id, abbreviation FROM units")}
    g = {name: conn.execute("INSERT INTO product_groups (name) VALUES (?)", (name,)).lastrowid
         for name in ("Group master", "Maitotuotteet", "Leivonta", "Virvoitusjuomat", "Mausteet")}

    def product(name, unit="kpl", kind="sku", parent=None, group=None, active=1):
        return conn.execute(
            "INSERT INTO products (name, unit_id, kind, parent_id, product_group_id, active) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, u[unit], kind, parent, group, active),
        ).lastrowid

    def concept(name, unit, parent=None):
        return product(name, unit, "concept", parent, g["Group master"], 0)

    ids = {}
    ids["kerma"] = concept("kerma", "dl")
    ids["valio_kerma"] = product("Valio kerma 2dl", parent=ids["kerma"], group=g["Maitotuotteet"])
    ids["arla_kerma"] = product("Arla kerma 2dl", parent=ids["kerma"], group=g["Maitotuotteet"])
    ids["kermajauhe"] = product("Kermajauhe 100g", parent=ids["kerma"], group=g["Leivonta"])
    ids["creme"] = concept("crème fraîche", "dl", parent=ids["kerma"])
    ids["vesi"] = concept("vesi", "dl")
    ids["odd_sku"] = product("Pirkka vesi 1,5l", group=g["Group master"])
    ids["kola"] = product("Kolajuoma", "dl", "concept", group=g["Virvoitusjuomat"])
    ids["pepsi"] = product("Pepsi Max virvoitusjuoma 0,33l 18-pack", "dl", "concept",
                           parent=ids["kola"], group=g["Virvoitusjuomat"])
    ids["fanta"] = product("Fanta Hami Melon 0,5l", parent=ids["pepsi"], group=g["Virvoitusjuomat"])
    ids["kaneli_a"] = concept("Kaneli", "g")
    ids["kaneli_b"] = concept("Kaneli", "g")
    ids["meira"] = product("Meira kaneli 30g", parent=ids["kaneli_b"], group=g["Mausteet"])
    ids["pippuri"] = concept("Mustapippuri", "g")
    ids["pippuri_stub"] = concept("mustapippuri", "g", parent=ids["pippuri"])
    ids["sokeri"] = concept("Sokeri", "dl")
    rid = conn.execute("INSERT INTO recipes (name) VALUES ('Fixture')").lastrowid
    for pid, note, spec in (
        (ids["pippuri_stub"], "jauhettu — mustapippuri", "strict"),
        (ids["sokeri"], "hieno — sokerit", "loose"),
        (ids["creme"], "kermaviili — creme fraiche lite", "strict"),
    ):
        conn.execute(
            "INSERT INTO recipe_ingredients (recipe_id, product_id, amount, unit_id, note, specificity) "
            "VALUES (?, ?, 1, ?, ?, ?)", (rid, pid, u["dl"], note, spec))
    conn.commit()
    yield conn, g, ids
    conn.close()


def _row(conn, pid):
    return conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()


def test_mislabelled_pack_becomes_a_sku_and_lets_go_of_its_children(live):
    conn, g, ids = live
    migrate_concepts.run(conn, force=True)
    pepsi = _row(conn, ids["pepsi"])
    assert pepsi["kind"] == "sku" and pepsi["parent_id"] == ids["kola"]
    assert pepsi["content_amount"] == 0.33
    assert _row(conn, ids["fanta"])["parent_id"] is None


def test_concepts_become_visible_with_real_groups(live):
    conn, g, ids = live
    migrate_concepts.run(conn, force=True)
    kerma = _row(conn, ids["kerma"])
    assert (kerma["name"], kerma["active"], kerma["product_group_id"]) == (
        "Kerma", 1, g["Maitotuotteet"])
    creme = _row(conn, ids["creme"])
    assert (creme["name"], creme["active"], creme["product_group_id"]) == (
        "Crème fraîche", 1, g["Maitotuotteet"])
    assert _row(conn, ids["vesi"])["product_group_id"] is None
    assert _row(conn, ids["odd_sku"])["product_group_id"] is None
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM product_groups WHERE name = 'Group master'").fetchone()["n"] == 0


def test_aliases_come_from_names_and_loose_recipe_notes(live):
    conn, g, ids = live
    migrate_concepts.run(conn, force=True)
    assert concepts.find_concept(conn, "kerma") == ids["kerma"]
    assert concepts.find_concept(conn, "sokerit") == ids["sokeri"]
    assert concepts.find_concept(conn, "creme fraiche lite") is None  # strict rows teach nothing


def test_twins_and_same_name_children_merge(live):
    conn, g, ids = live
    migrate_concepts.run(conn, force=True)
    assert _row(conn, ids["kaneli_a"]) is None
    assert _row(conn, ids["meira"])["parent_id"] == ids["kaneli_b"]
    assert _row(conn, ids["pippuri_stub"]) is None
    ing = conn.execute(
        "SELECT product_id FROM recipe_ingredients WHERE note = 'jauhettu — mustapippuri'").fetchone()
    assert ing["product_id"] == ids["pippuri"]


def test_flags_make_it_run_once(live):
    conn, g, ids = live
    conn.execute("DELETE FROM _meta WHERE key LIKE 'concepts_%'")  # init_db set them on the empty DB
    conn.commit()
    first = migrate_concepts.run(conn)
    assert set(first) == {"concepts_demote_v1", "concepts_visible_v1",
                          "concepts_aliases_v1", "concepts_dedup_v1"}
    assert migrate_concepts.run(conn) == {}
