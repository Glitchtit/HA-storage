"""Concept identity basics: name normalization, aliases and concept lookup."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "products"), exist_ok=True)
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "recipes"), exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from main import app, get_connection

import concepts
from concepts import norm

client = TestClient(app)


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestNorm:
    @pytest.mark.parametrize("raw, expected", [
        ("Crème fraîche", "creme fraiche"),
        ("creme-fraiche", "creme fraiche"),
        ("  Vehnä\xa0JAUHO ", "vehnä jauho"),
        ("Pähkinä‑hedelmäsekoitus", "pähkinä hedelmäsekoitus"),
        ("Öljy, (rypsi)", "öljy rypsi"),
        ("jalapeño_chili", "jalapeno chili"),
        ("", ""),
        (None, ""),
    ])
    def test_cases(self, raw, expected):
        assert norm(raw) == expected


class TestAliases:
    def test_first_mapping_wins(self):
        a = _mk("Kerma c1", unit="dl", kind="concept")
        b = _mk("Vispikerma c1", unit="dl", kind="concept")
        conn = get_connection()
        assert concepts.add_alias(conn, "grädde c1", a, "recipe") is True
        assert concepts.add_alias(conn, "GRÄDDE  c1", b, "recipe") is False
        conn.commit()
        assert concepts.find_concept(conn, "grädde c1") == a
        assert [x["alias"] for x in concepts.aliases_of(conn, a)] == ["grädde c1"]

    def test_alias_beats_name_and_skus_never_match(self):
        concept = _mk("Maito c2", unit="dl", kind="concept")
        other = _mk("Mjölk c2", unit="dl", kind="concept")
        _mk("Valio maito c2 1l")
        conn = get_connection()
        concepts.add_alias(conn, "mjölk c2", concept, "recipe")
        conn.commit()
        assert concepts.find_concept(conn, "Mjölk c2") == concept
        assert concepts.find_concept(conn, "mjolk-c2") is None  # ö is kept, so no match
        assert concepts.find_concept(conn, "valio maito c2 1l") is None
        assert other != concept

    def test_exact_normalized_name_lowest_id_wins(self):
        first = _mk("Kaneli c3", unit="g", kind="concept")
        _mk("kaneli c3", unit="g", kind="concept")
        assert concepts.find_concept(get_connection(), "KANELI c3") == first

    def test_aliases_follow_a_deleted_product(self):
        pid = _mk("Tilli c4", unit="g", kind="concept")
        conn = get_connection()
        concepts.add_alias(conn, "dill c4", pid, "recipe")
        conn.commit()
        assert client.delete(f"/api/products/{pid}").status_code == 204
        assert concepts.find_concept(conn, "dill c4") is None
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM product_aliases WHERE product_id = ?", (pid,)
        ).fetchone()["n"] == 0


def test_schema_has_phase2_columns():
    conn = get_connection()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(recipe_ingredients)").fetchall()}
    assert {"raw_text", "needs_review"} <= cols
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    assert {"product_aliases", "recipe_ingredients_backup"} <= tables


def test_catalog_lock_is_reentrant():
    from locks import CATALOG_LOCK
    with CATALOG_LOCK:
        with CATALOG_LOCK:
            assert CATALOG_LOCK._is_owned()
