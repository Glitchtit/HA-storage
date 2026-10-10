"""Ingredient raw text / review flag, and atomic ingredient replacement."""
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


def _recipe(name, ingredients):
    r = client.post("/api/recipes", json={"name": name, "ingredients": ingredients})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_raw_text_and_review_flag_round_trip():
    pid = _mk("Kerma r1", unit="dl", kind="concept")
    rid = _recipe("Kastike r1", [{"product_id": pid, "amount": 2, "unit_id": _u("dl"),
                                  "raw_text": "2 dl grädde", "needs_review": True}])
    ing = client.get(f"/api/recipes/{rid}").json()["ingredients"][0]
    assert ing["raw_text"] == "2 dl grädde" and ing["needs_review"] is True
    row = client.get(f"/api/recipes/{rid}/availability").json()["ingredients"][0]
    assert row["raw_text"] == "2 dl grädde" and row["needs_review"] is True and row["note"] == ""


def test_old_style_ingredient_defaults():
    pid = _mk("Suola r2", unit="g", kind="concept")
    rid = _recipe("Puuro r2", [{"product_id": pid, "amount": 1, "unit_id": _u("tl")}])
    ing = client.get(f"/api/recipes/{rid}").json()["ingredients"][0]
    assert ing["raw_text"] is None and ing["needs_review"] is False


def test_replace_keeps_the_recipe_and_backs_up_old_rows():
    voi = _mk("Voi r3", unit="g", kind="concept")
    marg = _mk("Margariini r3", unit="g", kind="concept")
    rid = _recipe("Pulla r3", [{"product_id": voi, "amount": 100, "unit_id": _u("g"),
                                "note": "pehmeä — voi"}])
    client.put(f"/api/recipes/{rid}", json={"picture_filename": "pulla_r3.jpg"})
    r = client.put(f"/api/recipes/{rid}/ingredients", json={"ingredients": [
        {"product_id": marg, "amount": 50, "unit_id": _u("g"), "raw_text": "50 g margarin",
         "specificity": "strict"},
        {"product_id": voi, "amount": 1, "unit_id": _u("rkl")},
    ]})
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["id"] == rid and detail["picture_filename"] == "pulla_r3.jpg"
    assert [(i["product_id"], i["amount"], i["sort_order"]) for i in detail["ingredients"]] == [
        (marg, 50, 0), (voi, 1, 1)]
    assert detail["ingredients"][0]["specificity"] == "strict"
    backup = get_connection().execute(
        "SELECT * FROM recipe_ingredients_backup WHERE recipe_id = ?", (rid,)
    ).fetchall()
    assert len(backup) == 1
    assert (backup[0]["product_id"], backup[0]["product_name"], backup[0]["note"]) == (
        voi, "Voi r3", "pehmeä — voi")


def test_replace_with_an_unknown_product_changes_nothing():
    pid = _mk("Sokeri r4", unit="dl", kind="concept")
    rid = _recipe("Kakku r4", [{"product_id": pid, "amount": 2, "unit_id": _u("dl")}])
    r = client.put(f"/api/recipes/{rid}/ingredients", json={"ingredients": [
        {"product_id": 99999999, "amount": 1, "unit_id": _u("dl")}]})
    assert r.status_code == 400
    assert [i["product_id"] for i in client.get(f"/api/recipes/{rid}").json()["ingredients"]] == [pid]
    assert get_connection().execute(
        "SELECT COUNT(*) AS n FROM recipe_ingredients_backup WHERE recipe_id = ?", (rid,)
    ).fetchone()["n"] == 0


def test_replace_unknown_recipe_is_404():
    assert client.put("/api/recipes/99999999/ingredients", json={"ingredients": []}).status_code == 404
