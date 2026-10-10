"""Dedup sweep: concepts whose normalized names or aliases collide merge."""
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

import concepts

client = TestClient(app)


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _exists(pid):
    return client.get(f"/api/products/{pid}").status_code == 200


def _recipe_using(*pids):
    r = client.post("/api/recipes", json={"name": f"Resepti {pids}", "ingredients": [
        {"product_id": pid, "amount": 1, "unit_id": _u("dl")} for pid in pids]})
    return r.json()["id"]


def _ingredient_products(rid):
    return [i["product_id"] for i in client.get(f"/api/recipes/{rid}").json()["ingredients"]]


def test_the_more_used_twin_survives():
    a = _mk("Kaneli d1", unit="g", kind="concept")
    b = _mk("kaneli  d1", unit="g", kind="concept")
    rid = _recipe_using(b, b)
    merged = concepts.dedup_concepts(get_connection())
    assert (a, b) in merged
    assert not _exists(a) and _ingredient_products(rid) == [b, b]
    assert concepts.find_concept(get_connection(), "Kaneli d1") == b


def test_same_name_child_merges_into_its_parent():
    parent = _mk("Mustapippuri d2", unit="g", kind="concept")
    child = _mk("mustapippuri d2", unit="g", kind="concept", parent_id=parent)
    rid = _recipe_using(child)
    concepts.dedup_concepts(get_connection())
    assert not _exists(child) and _ingredient_products(rid) == [parent]


def test_survivor_inherits_the_parent_of_its_twin():
    fish = _mk("Lohi d3", unit="g", kind="concept")
    top = _mk("Kirjolohifilee d3", unit="g", kind="concept")
    _mk("Pirkka kirjolohifilee d3 400g", parent_id=top)
    _recipe_using(top)
    under = _mk("kirjolohifilee d3", unit="g", kind="concept", parent_id=fish)
    concepts.dedup_concepts(get_connection())
    assert not _exists(under)
    assert client.get(f"/api/products/{top}").json()["parent_id"] == fish


def test_alias_collision_merges():
    """A former concept's name (a 'merge' alias) that reappears as a new
    concept is the same twin again."""
    cream = _mk("Kerma d4", unit="dl", kind="concept")
    _recipe_using(cream)
    conn = get_connection()
    concepts.add_alias(conn, "grädde d4", cream, "merge")
    conn.commit()
    swedish = _mk("Grädde d4", unit="dl", kind="concept")
    concepts.dedup_concepts(conn)
    assert not _exists(swedish) and _exists(cream)


def test_diacritics_and_case_collide():
    a = _mk("Crème fraîche d5", unit="dl", kind="concept")
    b = _mk("creme fraiche d5", unit="dl", kind="concept")
    _recipe_using(a)
    concepts.dedup_concepts(get_connection())
    assert _exists(a) and not _exists(b)


def test_twin_holding_stock_is_left_alone():
    a = _mk("Talouspaperi d6", unit="kpl", kind="concept")
    _mk("Lotus talouspaperi d6 4rl", parent_id=a)
    b = _mk("Talouspaperi  d6", unit="kpl", kind="concept")
    client.post("/api/stock/add", json={"product_id": b, "amount": 3})
    merged = concepts.dedup_concepts(get_connection())
    assert _exists(a) and _exists(b)
    assert all(b not in pair for pair in merged)


def test_skus_are_never_merged():
    a = _mk("Valio maito d7 1l")
    b = _mk("Valio maito d7 1l")
    concepts.dedup_concepts(get_connection())
    assert _exists(a) and _exists(b)


def test_reconcile_runs_the_dedup(monkeypatch):
    monkeypatch.setattr("linker.call_ai_json", lambda p, c, **k: [])
    a = _mk("Sinappi d8", unit="g", kind="concept")
    b = _mk("SINAPPI d8", unit="g", kind="concept")
    _recipe_using(a)
    r = client.post("/api/products/reconcile")
    assert r.status_code == 200 and r.json()["merged"] >= 1
    assert _exists(a) and not _exists(b)


def test_recipe_ai_and_manual_aliases_never_merge_concepts():
    """Review finding: an alias learned from a recipe line, the AI or a manual
    edit binds a word; it is no proof that two concepts are the same."""
    kerma = _mk("Kerma d9", unit="dl", kind="concept")
    kuohu = _mk("Kuohukerma d9", unit="dl", kind="concept")
    _recipe_using(kerma)
    conn = get_connection()
    for source in ("manual", "recipe", "ai"):
        conn.execute(
            "INSERT OR REPLACE INTO product_aliases (alias_norm, product_id, source) VALUES (?, ?, ?)",
            ("kerma d9", kuohu, source))
        conn.commit()
        concepts.dedup_concepts(conn)
        assert _exists(kerma) and _exists(kuohu), source
