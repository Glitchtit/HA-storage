"""POST /api/ingredients/resolve: recipe lines -> concepts, without AI."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
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


def _resolve(*items):
    r = client.post("/api/ingredients/resolve", json={"items": list(items)})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _product(pid):
    return client.get(f"/api/products/{pid}").json()


class TestBinding:
    def test_alias_hit(self):
        kerma = _mk("Kerma i1", unit="dl", kind="concept")
        conn = get_connection()
        concepts.add_alias(conn, "grädde i1", kerma, "recipe")
        conn.commit()
        [hit] = _resolve({"name": "grädde i1", "amount": 2, "unit_text": "dl"})
        assert (hit["product_id"], hit["specificity"], hit["created"]) == (kerma, "loose", False)

    def test_normalized_name_hit(self):
        flour = _mk("Vehnäjauho i2", unit="dl", kind="concept")
        [hit] = _resolve({"name": "VEHNÄJAUHO  i2", "amount": 3.5, "unit_text": "dl"})
        assert hit["product_id"] == flour and hit["created"] is False

    def test_variant_equal_to_its_generic_binds_loose(self):
        pepper = _mk("Mustapippuri i3", unit="g", kind="concept")
        [hit] = _resolve({"name": "mustapippuri i3", "specific": "Mustapippuri i3",
                          "amount": 1, "unit_text": "tl"})
        assert (hit["product_id"], hit["specificity"], hit["created"]) == (pepper, "loose", False)
        assert client.get("/api/products", params={"parent_id": pepper}).json() == []

    def test_variant_that_is_the_generic_concepts_own_name_binds_loose(self):
        pepper = _mk("Mustapippuri i3b", unit="g", kind="concept")
        conn = get_connection()
        concepts.add_alias(conn, "pippuri i3b", pepper, "recipe")
        conn.commit()
        [hit] = _resolve({"name": "pippuri i3b", "specific": "mustapippuri i3b",
                          "amount": 1, "unit_text": "tl"})
        assert (hit["product_id"], hit["specificity"]) == (pepper, "loose")

    def test_unknown_variant_becomes_a_child_of_its_generic(self):
        group = client.post("/api/product-groups", json={"name": "Kuivatuotteet i4"}).json()
        pasta = _mk("Pasta i4", unit="g", kind="concept", product_group_id=group["id"])
        [hit] = _resolve({"name": "pasta i4", "specific": "spaghetti i4", "amount": 400, "unit_text": "g"})
        assert hit["specificity"] == "strict" and hit["created"] is True
        child = _product(hit["product_id"])
        assert (child["name"], child["kind"], child["active"], child["parent_id"]) == (
            "Spaghetti i4", "concept", True, pasta)
        assert child["product_group_id"] == group["id"]
        assert concepts.find_concept(get_connection(), "SPAGHETTI i4") == child["id"]

    def test_unknown_generic_becomes_a_top_level_concept_once(self):
        [first] = _resolve({"name": "guanciale i5", "amount": 250, "unit_text": "g"})
        [again] = _resolve({"name": "Guanciale i5", "amount": 100, "unit_text": "g"})
        made = _product(first["product_id"])
        assert first["created"] is True and again["created"] is False
        assert again["product_id"] == first["product_id"]
        assert (made["name"], made["kind"], made["parent_id"]) == ("Guanciale i5", "concept", None)
        assert made["unit_id"] == _u("g")

    def test_skus_never_bind(self):
        sku = _mk("Lime i6")
        [hit] = _resolve({"name": "lime i6", "amount": 1})
        assert hit["product_id"] != sku and _product(hit["product_id"])["kind"] == "concept"

    def test_source_word_is_remembered(self):
        [first] = _resolve({"name": "raparperi i7", "source_name": "rabarber i7",
                            "amount": 500, "unit_text": "g"})
        [second] = _resolve({"name": "rabarber i7", "amount": 300, "unit_text": "g"})
        assert second["product_id"] == first["product_id"] and second["created"] is False

    def test_line_without_a_name_is_flagged(self):
        [hit] = _resolve({"raw": "1 st", "name": "", "specific": None, "amount": 1, "unit_text": "st"})
        assert hit["product_id"] is None and hit["needs_review"] is True


class TestUnits:
    def _one(self, amount, unit_text):
        [hit] = _resolve({"name": "sokeri i8", "amount": amount, "unit_text": unit_text})
        return hit["amount"], hit["unit"], hit["needs_review"]

    def test_aliases_and_containers(self):
        assert self._one(2, "msk") == (2, "rkl", False)
        assert self._one(1, "burk") == (1, "prk", False)
        assert self._one(1, "paketti") == (1, "pkt", False)

    def test_imperial_units_convert(self):
        assert self._one(1, "cup") == (2.4, "dl", False)

    def test_number_without_unit_counts_pieces(self):
        assert self._one(3, None) == (3, "kpl", False)

    def test_unknown_unit_word_is_kpl_and_flagged(self):
        assert self._one(2, "näve") == (2, "kpl", True)

    def test_no_amount_means_to_taste(self):
        assert self._one(None, None) == (0, "rs", False)
        assert self._one(None, "tl") == (0, "tl", False)

    def test_unit_ids_match_the_catalog(self):
        [hit] = _resolve({"name": "sokeri i8", "amount": 1, "unit_text": "dl"})
        assert hit["unit_id"] == _u("dl")


def test_parallel_imports_create_one_concept():
    import ingredient_resolver

    results, errors = [], []

    def worker():
        try:
            results.append(ingredient_resolver.resolve(
                get_connection(), [{"name": "kvinoa i9", "amount": 1, "unit_text": "dl"}]))
        except Exception as exc:  # pragma: no cover - surfaced by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len({r["items"][0]["product_id"] for r in results}) == 1
    n = get_connection().execute(
        "SELECT COUNT(*) AS n FROM products WHERE name = 'Kvinoa i9'").fetchone()["n"]
    assert n == 1
