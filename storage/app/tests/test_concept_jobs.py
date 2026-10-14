"""Background AI follow-ups for concepts: place, adopt, aisle group, facts.
AI is always faked; each fake answers by recognising its prompt."""
from __future__ import annotations

import json
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

import concept_jobs
import concepts

client = TestClient(app)

ADOPT, PLACE, GROUPS, FACTS, GATHER = ("ARE the concept", "have no products under them",
                                       "aisle group", "kitchen facts", "can use it")


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _get(pid):
    return client.get(f"/api/products/{pid}").json()


def _group(name):
    return client.post("/api/product-groups", json={"name": name}).json()["id"]


class _Ai:
    """Fake call_ai_json. `answers` maps a prompt marker to a reply (or a
    function of the prompt). Unrecognised prompts get an empty list."""

    def __init__(self, **answers):
        self.answers = {{"adopt": ADOPT, "place": PLACE, "groups": GROUPS, "facts": FACTS,
                         "gather": GATHER}[k]: v
                        for k, v in answers.items()}
        self.prompts = []

    def __call__(self, prompt, conn, **kw):
        self.prompts.append(prompt)
        for marker, answer in self.answers.items():
            if marker in prompt:
                return answer(prompt) if callable(answer) else answer
        return []


def _payload(prompt):
    """The first JSON value after the line ending in '(JSON):'."""
    start = prompt.index("(JSON):") + len("(JSON):")
    return json.JSONDecoder().raw_decode(prompt[start:].lstrip())[0]


class TestAdopt:
    def test_high_adopts_medium_proposes_and_other_concepts_keep_their_skus(self):
        sugar = _mk("Sokeri j1", unit="dl", kind="concept")
        jam_sugar = _mk("Hillosokeri j1", unit="dl", kind="concept", parent_id=sugar)
        loose_sku = _mk("Dansukker hillosokeri j1 1kg")
        sibling_sku = _mk("Pirkka hillosokeri j1 500g", parent_id=sugar)
        other = _mk("Säilöntäaineet j1", unit="g", kind="concept")
        taken_sku = _mk("Meira hillosokeri j1 2kg", parent_id=other)
        maybe_sku = _mk("Hillosokeri-pussi j1")
        ai = _Ai(adopt=[
            {"concept_id": jam_sugar, "product_id": loose_sku, "confidence": "high"},
            {"concept_id": jam_sugar, "product_id": sibling_sku, "confidence": "high"},
            {"concept_id": jam_sugar, "product_id": taken_sku, "confidence": "high"},
            {"concept_id": jam_sugar, "product_id": maybe_sku, "confidence": "medium"},
        ])
        res = concept_jobs.adopt(get_connection(), [jam_sugar], ai)
        assert res == {"adopted": 2, "proposed": 1}
        assert _get(loose_sku)["parent_id"] == jam_sugar
        assert _get(sibling_sku)["parent_id"] == jam_sugar
        assert _get(taken_sku)["parent_id"] == other
        proposals = client.get("/api/link-proposals").json()
        assert any(p["product_id"] == maybe_sku and p["proposed_parent_id"] == jam_sugar
                   for p in proposals)
        notes = [e["note"] for e in client.get(f"/api/history?product_id={loose_sku}").json()]
        assert "adopted by Hillosokeri j1" in notes

    def test_candidates_share_a_word_or_a_parent(self):
        pasta = _mk("Pasta j2", unit="g", kind="concept")
        bucatini = _mk("Bucatini j2", unit="g", kind="concept", parent_id=pasta)
        sibling = _mk("Rummo penne j2 500g", parent_id=pasta)
        named = _mk("Barilla bucatini j2 1kg")
        _mk("Pirkka ketsuppi j2")
        ai = _Ai(adopt=[])
        concept_jobs.adopt(get_connection(), [bucatini], ai)
        [batch] = _payload(ai.prompts[0])
        assert {c["id"] for c in batch["candidates"]} == {sibling, named}


    def test_short_names_match_whole_words(self):
        """Review finding: 'Voi'-like names (3 letters) got no candidates."""
        tea = _mk("Tee j11", unit="kpl", kind="concept")
        bags = _mk("Lipton tee j11 20pss")
        cup = _mk("Teekuppi j11")
        ai = _Ai(adopt=[])
        concept_jobs.adopt(get_connection(), [tea], ai)
        [batch] = _payload(ai.prompts[0])
        ids = {c["id"] for c in batch["candidates"]}
        assert bags in ids and cup not in ids


    def test_inflected_words_still_find_the_product(self):
        """The recipe word is often inflected differently from the package:
        "fileesuikaleet" / "hunajamarinoidut" vs "fileesuikale hunajamarinoitu"."""
        strips = _mk("Hunajamarinoidut broilerin fileesuikaleet k1", unit="g", kind="concept")
        sku = _mk("Pirkka kanan fileesuikale hunajamarinoitu k1 450g")
        _mk("Pirkka hunajakakku k1 350g")
        ai = _Ai(adopt=[])
        concept_jobs.adopt(get_connection(), [strips], ai)
        [batch] = _payload(ai.prompts[0])
        ids = {c["id"] for c in batch["candidates"]}
        assert sku in ids


class TestPlace:
    def test_same_merges_kind_of_reparents_medium_proposes(self):
        spag = _mk("Spagetti j3", unit="g", kind="concept")
        _mk("Pirkka spagetti j3 1kg", parent_id=spag)
        dup = _mk("Spaghetti j3", unit="g", kind="concept")
        tomato = _mk("Tomaatti j3", unit="g", kind="concept")
        _mk("Kotimainen tomaatti j3", parent_id=tomato)
        cherry = _mk("Terttutomaatti j3", unit="g", kind="concept")
        lime = _mk("Lime j3", unit="kpl", kind="concept")
        citrus = _mk("Sitrushedelmä j3", unit="kpl", kind="concept")
        _mk("Sitruuna j3 1kpl", parent_id=citrus)
        rid = client.post("/api/recipes", json={"name": "Pasta j3", "ingredients": [
            {"product_id": dup, "amount": 400, "unit_id": _u("g")}]}).json()["id"]
        ai = _Ai(place=[
            {"id": dup, "relation": "same", "target_id": spag, "confidence": "high"},
            {"id": cherry, "relation": "kind_of", "target_id": tomato, "confidence": "high"},
            {"id": lime, "relation": "kind_of", "target_id": citrus, "confidence": "medium"},
        ])
        res = concept_jobs.place(get_connection(), [dup, cherry, lime], ai)
        assert res == {"merged": 1, "reparented": 1, "proposed": 1}
        assert client.get(f"/api/products/{dup}").status_code == 404
        assert client.get(f"/api/recipes/{rid}").json()["ingredients"][0]["product_id"] == spag
        assert concepts.find_concept(get_connection(), "spaghetti j3") == spag
        assert _get(cherry)["parent_id"] == tomato
        assert _get(lime)["parent_id"] is None

    def test_targets_must_be_existing_concepts_it_offered(self):
        """Review finding: a target the prompt never listed as an existing
        concept (here: another concept being placed) is ignored."""
        juice = _mk("Sitruunamehu j9", unit="dl", kind="concept")
        lemon = _mk("Sitruuna j9", unit="kpl", kind="concept")
        ai = _Ai(place=[{"id": juice, "relation": "same", "target_id": lemon, "confidence": "high"}])
        res = concept_jobs.place(get_connection(), [juice, lemon], ai)
        assert res["merged"] == 0
        assert client.get(f"/api/products/{juice}").status_code == 200

    def test_same_needs_similar_names(self):
        """Review finding: an off-by-one id must not merge unrelated concepts;
        a 'same' merge needs names that look alike."""
        cheese = _mk("Juusto j10", unit="g", kind="concept")
        mince = _mk("Jauheliha j10", unit="g", kind="concept")
        _mk("Snellman jauheliha j10 400g", parent_id=mince)
        ai = _Ai(place=[{"id": cheese, "relation": "same", "target_id": mince, "confidence": "high"}])
        res = concept_jobs.place(get_connection(), [cheese], ai)
        assert res["merged"] == 0
        assert client.get(f"/api/products/{cheese}").status_code == 200

    def test_only_concepts_without_descendants_are_asked(self):
        busy = _mk("Kahvi j4", unit="g", kind="concept")
        _mk("Juhla Mokka j4 500g", parent_id=busy)
        lonely = _mk("Kardemumma j4", unit="g", kind="concept")
        ai = _Ai(place=[])
        concept_jobs.place(get_connection(), [busy, lonely], ai)
        assert [c["id"] for c in _payload(ai.prompts[0])] == [lonely]


class TestGather:
    def test_high_reparents_medium_proposes_and_parented_concepts_stay(self):
        rice = _mk("Riisi j12", unit="dl", kind="concept")
        basmati = _mk("Basmatiriisi j12", unit="g", kind="concept")
        jasmine = _mk("Jasmiiniriisi j12", unit="g", kind="concept")
        porridge = _mk("Puuroriisi j12", unit="g", kind="concept")
        grains = _mk("Viljat j12", unit="g", kind="concept")
        parented = _mk("Pitkäjyväriisi j12", unit="g", kind="concept", parent_id=grains)
        ai = _Ai(gather=[
            {"concept_id": rice, "candidate_id": basmati, "confidence": "high"},
            {"concept_id": rice, "candidate_id": jasmine, "confidence": "medium"},
            {"concept_id": rice, "candidate_id": porridge, "confidence": "low"},
            {"concept_id": rice, "candidate_id": parented, "confidence": "high"},
        ])
        res = concept_jobs.gather(get_connection(), [rice], ai)
        assert res == {"gathered": 1, "proposed": 1}
        assert _get(basmati)["parent_id"] == rice
        assert _get(jasmine)["parent_id"] is None
        assert _get(porridge)["parent_id"] is None
        assert _get(parented)["parent_id"] == grains
        proposals = client.get("/api/link-proposals").json()
        assert any(p["product_id"] == jasmine and p["proposed_parent_id"] == rice
                   for p in proposals)
        notes = [e["note"] for e in client.get(f"/api/history?product_id={basmati}").json()]
        assert "gathered under Riisi j12" in notes

    def test_candidates_are_top_level_compounds_ending_in_the_name(self):
        rice = _mk("Riisi j13", unit="dl", kind="concept")
        basmati = _mk("Basmatiriisi j13", unit="g", kind="concept")
        long_grain = _mk("Pitkäjyväinen riisi j13", unit="g", kind="concept")
        _mk("Riisivälipala j13", unit="kpl", kind="concept")
        _mk("Villiriisi j13", unit="g", kind="concept", parent_id=rice)
        _mk("Pirkka basmatiriisi j13 1kg")
        ai = _Ai(gather=[])
        concept_jobs.gather(get_connection(), [rice], ai)
        [batch] = _payload(ai.prompts[0])
        assert batch["concept_id"] == rice
        # The test database is shared; other tests' rice concepts are fair candidates too.
        assert {c["id"] for c in batch["candidates"] if "j13" in c["name"]} == {basmati, long_grain}

    def test_no_candidates_means_no_ai_call(self):
        lonely = _mk("Mustakumina j14", unit="g", kind="concept")
        ai = _Ai(gather=[])
        assert concept_jobs.gather(get_connection(), [lonely], ai) == {"gathered": 0, "proposed": 0}
        assert ai.prompts == []


class TestGroups:
    def test_majority_of_descendants_then_ai(self):
        dairy, baking = _group("Maitotuotteet j5"), _group("Leivonta j5")
        cream = _mk("Kerma j5", unit="dl", kind="concept")
        _mk("Valio kerma j5", parent_id=cream, product_group_id=dairy)
        _mk("Arla kerma j5", parent_id=cream, product_group_id=dairy)
        _mk("Kermajauhe j5", parent_id=cream, product_group_id=baking)
        yeast = _mk("Hiiva j5", unit="g", kind="concept")
        ai = _Ai(groups=lambda prompt: [{"id": yeast, "group_id": baking}])
        assert concept_jobs.assign_groups(get_connection(), [cream, yeast], ai) == 2
        assert _get(cream)["product_group_id"] == dairy
        assert _get(yeast)["product_group_id"] == baking
        assert [c["id"] for c in _payload(ai.prompts[0])] == [yeast]

    def test_ai_cannot_invent_a_group(self):
        salt = _mk("Suola j6", unit="g", kind="concept")
        ai = _Ai(groups=[{"id": salt, "group_id": 99999999}])
        assert concept_jobs.assign_groups(get_connection(), [salt], ai) == 0
        assert _get(salt)["product_group_id"] is None


class TestFacts:
    def test_fills_missing_plausible_facts_only(self):
        flour = _mk("Vehnäjauho j7", unit="dl", kind="concept")
        egg = _mk("Kananmuna j7", unit="kpl", kind="concept")
        butter = _mk("Voi j7", unit="g", kind="concept", density_g_ml=0.91)
        odd = _mk("Kaakao j7", unit="dl", kind="concept")
        ai = _Ai(facts=[
            {"id": flour, "density_g_ml": 0.55, "piece_weight_g": None},
            {"id": egg, "density_g_ml": None, "piece_weight_g": 60},
            {"id": butter, "density_g_ml": 0.5},
            {"id": odd, "density_g_ml": 50},
        ])
        n = concept_jobs.estimate_facts(
            get_connection(), [(flour, "volume"), (egg, "count"), (butter, "mass"), (odd, "volume")], ai)
        assert n == 2
        assert _get(flour)["density_g_ml"] == 0.55
        assert _get(flour)["facts_meta"]["density_g_ml"] == "ai"
        assert _get(egg)["piece_weight_g"] == 60
        assert _get(butter)["density_g_ml"] == 0.91
        assert _get(odd)["density_g_ml"] is None
        asked = {c["id"] for c in _payload(ai.prompts[0])}
        assert butter not in asked


def test_resolve_endpoint_runs_the_follow_ups(monkeypatch):
    class _Inline:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, **_):
            self.target, self.args, self.kwargs = target, args, kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    sku = _mk("Kotimaista guanciale j8 150g")
    ai = _Ai(adopt=lambda prompt: [
        {"concept_id": c["concept_id"], "product_id": sku, "confidence": "high"}
        for c in _payload(prompt)])
    monkeypatch.setattr("routers.products._ai_configured", lambda conn: True)
    monkeypatch.setattr("ai_client.call_ai_json", ai)
    monkeypatch.setattr("concept_jobs.threading.Thread", _Inline)
    r = client.post("/api/ingredients/resolve",
                    json={"items": [{"name": "guanciale j8", "amount": 150, "unit_text": "g"}]})
    concept = r.json()["items"][0]["product_id"]
    assert _get(sku)["parent_id"] == concept


def test_import_of_a_new_generic_gathers_its_existing_variants(monkeypatch):
    class _Inline:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, **_):
            self.target, self.args, self.kwargs = target, args, kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    basmati = _mk("Basmatiriisi j15", unit="g", kind="concept")
    ai = _Ai(gather=lambda prompt: [
        {"concept_id": b["concept_id"], "candidate_id": c["id"], "confidence": "high"}
        for b in _payload(prompt) for c in b["candidates"]])
    monkeypatch.setattr("routers.products._ai_configured", lambda conn: True)
    monkeypatch.setattr("ai_client.call_ai_json", ai)
    monkeypatch.setattr("concept_jobs.threading.Thread", _Inline)
    r = client.post("/api/ingredients/resolve",
                    json={"items": [{"name": "riisi j15", "amount": 3.5, "unit_text": "dl"}]})
    rice = r.json()["items"][0]["product_id"]
    assert rice != basmati
    assert _get(basmati)["parent_id"] == rice


def test_import_bound_to_an_existing_concept_adopts_its_skus_from_the_parent(monkeypatch):
    """A SKU sitting on the generic ("Kerma") moves down to the existing
    variant ("Ruokakerma") a recipe binds to, not only for new concepts."""
    class _Inline:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, **_):
            self.target, self.args, self.kwargs = target, args, kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    cream = _mk("Kerma k2", unit="dl", kind="concept")
    cooking = _mk("Ruokakerma k2", unit="dl", kind="concept", parent_id=cream)
    _mk("Pirkka ruokakerma k2 1dl", parent_id=cooking)
    sku = _mk("Valio ruokakerma k2 2dl", parent_id=cream)
    ai = _Ai(adopt=lambda prompt: [
        {"concept_id": b["concept_id"], "product_id": c["id"], "confidence": "high"}
        for b in _payload(prompt) for c in b["candidates"] if "ruokakerma" in c["name"]])
    monkeypatch.setattr("routers.products._ai_configured", lambda conn: True)
    monkeypatch.setattr("ai_client.call_ai_json", ai)
    monkeypatch.setattr("concept_jobs.threading.Thread", _Inline)
    r = client.post("/api/ingredients/resolve",
                    json={"items": [{"name": "ruokakerma k2", "amount": 2, "unit_text": "dl"}]})
    assert r.json()["items"][0]["product_id"] == cooking
    assert _get(sku)["parent_id"] == cooking
