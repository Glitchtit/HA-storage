"""The resolver's single AI matching call (AI always faked)."""
from __future__ import annotations

import json
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
import ingredient_resolver

client = TestClient(app)


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _names_in(prompt):
    """The JSON list of names the resolver asked about."""
    start = prompt.index("Names to match (JSON list):") + len("Names to match (JSON list):")
    return json.loads(prompt[start:prompt.index("\n\nCatalog concepts")].strip())


class _FakeAi:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def __call__(self, prompt, conn, **kw):
        self.prompts.append(prompt)
        return self.answer(prompt) if callable(self.answer) else self.answer


def _resolve(items, ai):
    return ingredient_resolver.resolve(get_connection(), items, ai=ai)["items"]


def test_high_confidence_match_binds_and_is_remembered():
    cream = _mk("Kerma x1", unit="dl", kind="concept")
    ai = _FakeAi([{"name": "grädde x1", "id": cream, "confidence": "high"}])
    [hit] = _resolve([{"name": "grädde x1", "amount": 2, "unit_text": "dl"}], ai)
    assert (hit["product_id"], hit["created"]) == (cream, False)
    sources = {a["alias"]: a["source"] for a in concepts.aliases_of(get_connection(), cream)}
    assert sources["grädde x1"] == "ai"
    [again] = _resolve([{"name": "grädde x1", "amount": 1, "unit_text": "dl"}], ai)
    assert again["product_id"] == cream and len(ai.prompts) == 1


def test_only_unknown_names_are_sent_and_medium_creates_a_concept():
    flour = _mk("Vehnäjauho x2", unit="dl", kind="concept")
    ai = _FakeAi([{"name": "dinkelmjöl x2", "id": flour, "confidence": "medium"}])
    hits = _resolve([{"name": "vehnäjauho x2", "amount": 3, "unit_text": "dl"},
                     {"name": "dinkelmjöl x2", "amount": 1, "unit_text": "dl"}], ai)
    assert _names_in(ai.prompts[0]) == ["dinkelmjöl x2"]
    assert hits[0]["product_id"] == flour
    assert hits[1]["created"] is True and hits[1]["product_id"] != flour


def test_garbage_answers_are_ignored():
    sku = _mk("Valio maito x3 1l")
    answers = (
        lambda name: {"name": name},                                          # not a list
        lambda name: [{"name": name, "id": sku, "confidence": "high"}],       # a SKU
        lambda name: [{"name": name, "id": 99999999, "confidence": "high"}],  # unknown id
        lambda name: [{"name": name, "id": "abc", "confidence": "high"}],     # not an id
        lambda name: [{"id": "x", "confidence": "high"}, "nonsense", None],
    )
    for i, answer in enumerate(answers):
        name = f"maito x3 {i}"
        ai = _FakeAi(answer(name))
        [hit] = _resolve([{"name": name, "amount": 1}], ai)
        assert len(ai.prompts) == 1
        assert hit["created"] is True and hit["product_id"] != sku


def test_ai_failure_still_imports():
    def boom(prompt):
        raise ValueError("AI down")
    ai = _FakeAi(boom)
    [hit] = _resolve([{"name": "kardemumma x4", "amount": 1, "unit_text": "tl"}], ai)
    assert len(ai.prompts) == 1 and hit["created"] is True


def test_a_slow_ai_call_never_duplicates_a_concept():
    """Import A asks the AI; while it waits, import B creates the concept.
    A must then bind to B's concept instead of creating a second one."""
    a_asking, b_done = threading.Event(), threading.Event()

    def slow(prompt):
        a_asking.set()
        b_done.wait(5)
        return []

    out = {}
    ai_a = _FakeAi(slow)
    a = threading.Thread(target=lambda: out.setdefault(
        "a", _resolve([{"name": "tattari x5", "amount": 1, "unit_text": "dl"}], ai_a)))
    a.start()
    assert a_asking.wait(2), "import A never asked the AI"
    out["b"] = _resolve([{"name": "tattari x5", "amount": 2, "unit_text": "dl"}], None)
    b_done.set()
    a.join()
    assert len(ai_a.prompts) == 1
    assert out["a"][0]["product_id"] == out["b"][0]["product_id"]
    assert out["a"][0]["created"] is False
    n = get_connection().execute(
        "SELECT COUNT(*) AS n FROM products WHERE name = 'Tattari x5'").fetchone()["n"]
    assert n == 1


def test_endpoint_uses_ai_when_configured(monkeypatch):
    cream = _mk("Kerma x6", unit="dl", kind="concept")
    monkeypatch.setattr("routers.products._ai_configured", lambda conn: True)
    monkeypatch.setattr("ai_client.call_ai_json", _FakeAi(
        [{"name": "cream x6", "id": cream, "confidence": "high"}]))
    r = client.post("/api/ingredients/resolve", json={"items": [{"name": "cream x6", "amount": 1}]})
    assert r.json()["items"][0]["product_id"] == cream
