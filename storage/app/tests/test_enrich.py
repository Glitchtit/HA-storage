"""Enrichment places and fills in products without losing anything: it fills
empty fields, never overwrites what a person set, picks only from the groups
and locations that exist, and merges a multi-pack only into an existing
single-unit product."""
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

import pytest
from fastapi.testclient import TestClient
from main import app, get_connection

import enrich
from ai_client import AiConfigError, AiTruncated

client = TestClient(app)


@pytest.fixture(autouse=True)
def _linker_answers_nothing(monkeypatch):
    """The linker makes its own AI call; without a key it would fail and
    stop the run. Tests that care about linking override this."""
    monkeypatch.setattr("linker.call_ai_json", lambda p, c, **k: [])


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _get(pid):
    return client.get(f"/api/products/{pid}").json()


def _group(name):
    enrich.ensure_groups(get_connection(), [name])
    return next(g["id"] for g in client.get("/api/product-groups").json() if g["name"] == name)


def _locations():
    return sorted(l["id"] for l in client.get("/api/locations").json())


def _asked(prompt):
    """The products an attribute prompt asks about."""
    body = prompt.split("clearly wrong:\n")[1].split("\n\nReturn a JSON object")[0]
    return json.loads(body)


def _ai(answers=None, *, default=None, calls=None):
    """Fake AI for the attribute prompt: one row per product, `answers[id]`
    laid over `default`. (The linker makes its own call through
    linker.call_ai_json, which fails without a key and links nothing.)"""
    answers, default = answers or {}, default or {}

    def fake(prompt, conn, **kw):
        if calls is not None:
            calls.append("attributes")
        return [{"id": p["id"], "group_id": None, "location_id": None, "best_before_days": None,
                 **default, **answers.get(p["id"], {})} for p in _asked(prompt)]
    return fake


class TestFullRunIsNonDestructive:
    def test_tree_flags_and_recipes_untouched(self, monkeypatch):
        monkeypatch.setattr("linker.call_ai_json", lambda p, c, **k: [])
        gid = _group("Juomat e1")
        cat = _mk("virvoitusjuoma-e1", kind="concept", active=False)
        sku = _mk("Pepsi Max 1,5l e1", parent_id=cat)
        rid = client.post("/api/recipes", json={"name": "Booli e1", "ingredients": [
            {"product_id": cat, "amount": 1, "unit_id": _u("l")},
            {"product_id": cat, "amount": 2, "unit_id": _u("dl")},
        ]}).json()["id"]
        conn = get_connection()

        enrich.run(conn, _ai(default={"group_id": gid}))

        row = conn.execute("SELECT parent_id, product_group_id FROM products WHERE id = ?", (sku,)).fetchone()
        assert row["parent_id"] == cat and row["product_group_id"] == gid
        concept = conn.execute("SELECT active, kind FROM products WHERE id = ?", (cat,)).fetchone()
        assert concept["active"] == 0 and concept["kind"] == "concept"
        assert len(client.get(f"/api/recipes/{rid}").json()["ingredients"]) == 2

    def test_a_refused_request_stops_the_run(self):
        def dead(prompt, conn, **kw):
            raise AiConfigError("Claude refused the request (HTTP 404: model)")
        pid = _mk("Jaffa 1,5l e2")
        with pytest.raises(AiConfigError):
            enrich.run(get_connection(), dead, product_ids=[pid])
        with pytest.raises(AiConfigError):
            enrich.run(get_connection(), dead, product_ids=[pid], fields=("shelf_life",))


class TestMultipacks:
    def _pack(self, name, stock=1):
        pid = _mk(name)
        client.post("/api/stock/add", json={"product_id": pid, "amount": stock})
        client.post("/api/barcodes", json={"product_id": pid, "barcode": f"64{pid:011d}"})
        return pid

    def test_six_pack_merged_into_base(self):
        base = _mk("Sprite zero 330ml e3")
        multi = self._pack("Sprite zero 6x330ml e3")
        rid = client.post("/api/recipes", json={"name": "Drinkki e3", "ingredients": [
            {"product_id": multi, "amount": 1, "unit_id": _u("kpl")}]}).json()["id"]
        row = client.post("/api/shopping-list", json={"product_id": multi, "amount": 1}).json()

        enrich.run(get_connection(), _ai(), product_ids=[multi])

        assert client.get(f"/api/products/{multi}").status_code == 404
        detail = _get(base)
        assert detail["stock_amount"] == 6
        assert detail["barcodes"][0]["pack_size"] == 6
        assert client.get(f"/api/recipes/{rid}").json()["ingredients"][0]["product_id"] == base
        moved = next(i for i in client.get("/api/shopping-list").json() if i["id"] == row["id"])
        assert moved["product_id"] == base

    def test_multipack_without_base_is_left_alone(self):
        """No single-unit product to merge into: the pack stays its own
        product and a second run changes nothing (it used to be renamed and
        its stock multiplied on every run)."""
        multi = self._pack("Sprite zero 6x0,33l e4")
        for _ in range(2):
            enrich.run(get_connection(), _ai(), product_ids=[multi])
        detail = _get(multi)
        assert detail["name"] == "Sprite zero 6x0,33l e4"
        assert detail["stock_amount"] == 1
        assert detail["barcodes"][0]["pack_size"] == 1

    def test_a_concept_is_never_a_merge_target(self):
        concept = _mk("Limsa 0,33l e5", kind="concept")
        multi = self._pack("Limsa 6x0,33l e5")
        enrich.run(get_connection(), _ai(), product_ids=[multi])
        assert _get(multi)["stock_amount"] == 1
        assert _get(concept)["kind"] == "concept"

    def test_failed_merge_keeps_other_products_updates(self, monkeypatch):
        target = _locations()[-1]
        gid = _group("Maito e6")
        plain = _mk("Valio maito 1l e6")
        _mk("Fanta 0,33l e6")
        multi = self._pack("Fanta 6x0,33l e6")

        def boom(*a, **k):
            raise ValueError("merge refused")
        monkeypatch.setattr("enrich.merge_products", boom)

        enrich.run(get_connection(), _ai({plain: {"location_id": target, "group_id": gid,
                                                  "best_before_days": 10}}),
                   product_ids=[plain, multi])

        p = _get(plain)
        assert (p["location_id"], p["default_best_before_days"], p["product_group_id"]) == (target, 10, gid)
        assert _get(multi)["stock_amount"] == 1

    def test_package_contents_are_not_a_multipack(self):
        base = _mk("Teepussi 2g e7")
        box = self._pack("Teepussi 100x2g e7")
        enrich.run(get_connection(), _ai(), product_ids=[box])
        assert client.get(f"/api/products/{box}").status_code == 200
        assert _get(base)["stock_amount"] == 0

    def test_a_barcode_that_already_counts_units_is_not_multiplied_again(self):
        _mk("Jaffa 0,33l e8")
        multi = _mk("Jaffa 6x0,33l e8")
        client.post("/api/barcodes", json={"product_id": multi, "barcode": f"64{multi:011d}", "pack_size": 6})
        client.post("/api/stock/add", json={"product_id": multi, "amount": 6})
        enrich.run(get_connection(), _ai(), product_ids=[multi])
        assert _get(multi)["stock_amount"] == 6


class TestWhatGetsWritten:
    def test_empty_fields_are_filled_and_set_ones_kept(self):
        old_loc, new_loc = _locations()[0], _locations()[-1]
        gid = _group("Kuivatuotteet e9")
        pid = _mk("Spagetti 1kg e9", location_id=old_loc)

        enrich.enrich(get_connection(), [pid], _ai(default={
            "group_id": gid, "location_id": new_loc, "best_before_days": 1095}), link=False)

        p = _get(pid)
        assert p["location_id"] == old_loc  # was set: a fill leaves it
        assert (p["product_group_id"], p["default_best_before_days"]) == (gid, 1095)

    def test_what_a_person_set_survives_a_recheck_and_ai_values_do_not(self):
        hand, other = _locations()[0], _locations()[-1]
        g1, g2 = _group("Leipä e10"), _group("Leivonnaiset e10")
        pid = _mk("Ruisleipä e10")
        conn = get_connection()
        enrich.enrich(conn, [pid], _ai(default={"group_id": g1, "location_id": other,
                                                "best_before_days": 7}), link=False)
        r = client.put(f"/api/products/{pid}", json={"location_id": hand})
        assert r.status_code == 200

        enrich.run(conn, _ai(default={"group_id": g2, "location_id": other, "best_before_days": 9}),
                   product_ids=[pid])

        p = _get(pid)
        assert p["location_id"] == hand            # set by hand: kept
        assert p["product_group_id"] == g2         # set by the AI: re-checked
        assert p["default_best_before_days"] == 9

    def test_only_groups_and_locations_that_exist_are_used(self):
        pid = _mk("Outo tuote e11")
        before = len(client.get("/api/product-groups").json())
        enrich.run(get_connection(), _ai(default={"group_id": 987654, "location_id": 987654,
                                                  "best_before_days": 30}), product_ids=[pid])
        p = _get(pid)
        assert p["product_group_id"] is None and p["location_id"] is None
        assert p["default_best_before_days"] == 30
        assert len(client.get("/api/product-groups").json()) == before

    def test_stock_added_before_location_moves_with_it(self):
        target = _locations()[-1]
        pid = _mk("Makaroni 1kg e12")
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1})
        conn = get_connection()
        fallback = conn.execute("SELECT location_id FROM stock WHERE product_id = ?", (pid,)).fetchone()["location_id"]
        assert fallback == _locations()[0]
        enrich.run(conn, _ai({pid: {"location_id": target}}), product_ids=[pid])
        assert conn.execute("SELECT location_id FROM stock WHERE product_id = ?", (pid,)).fetchone()["location_id"] == target

    def test_placement_moves_only_stock_in_the_fallback_location(self):
        locs = _locations()
        pid = _mk("Ruisleipa 500g e12")
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1, "location_id": locs[1]})
        client.post("/api/stock/add", json={"product_id": pid, "amount": 1})  # fallback
        conn = get_connection()
        enrich.run(conn, _ai({pid: {"location_id": locs[-1]}}), product_ids=[pid])
        rows = conn.execute("SELECT location_id FROM stock WHERE product_id = ? ORDER BY id", (pid,)).fetchall()
        assert [r["location_id"] for r in rows] == [locs[1], locs[-1]]  # put there on purpose: kept

    def test_shelf_life_only_touches_nothing_else(self):
        gid = _group("Mausteet e13")
        pid = _mk("Kaneli e13")
        enrich.run(get_connection(), _ai(default={"group_id": gid, "location_id": _locations()[0],
                                                  "best_before_days": 1095}),
                   product_ids=[pid], fields=("shelf_life",))
        p = _get(pid)
        assert p["default_best_before_days"] == 1095
        assert p["product_group_id"] is None and p["location_id"] is None

    def test_the_prompt_shows_what_is_set_so_a_recheck_can_keep_it(self):
        gid = _group("Juomat e25")
        loc = _locations()[0]
        fresh, known = _mk("Mehu e25"), _mk("Limu e25", location_id=loc)
        conn = get_connection()
        enrich.enrich(conn, [known], _ai(default={"group_id": gid, "best_before_days": 270}), link=False)
        seen = []

        def fake(prompt, conn_, **kw):
            seen.extend(_asked(prompt))
            return []
        enrich.run(conn, fake, product_ids=[fresh, known])
        by_id = {p["id"]: p for p in seen}
        assert by_id[fresh] == {"id": fresh, "name": "Mehu e25"}  # 60 is only the placeholder
        assert by_id[known] == {"id": known, "name": "Limu e25", "group": "Juomat e25",
                                "location": next(l["name"] for l in client.get("/api/locations").json()
                                                 if l["id"] == loc), "days": 270}

    def test_dry_run_writes_nothing_and_lists_the_changes(self):
        gid = _group("Säilykkeet e14")
        pid = _mk("Tonnikala e14")
        res = enrich.run(get_connection(), _ai(default={"group_id": gid, "best_before_days": 1095}),
                         product_ids=[pid], dry_run=True)
        assert res["proposals"] == [{"id": pid, "name": "Tonnikala e14",
                                     "product_group_id": gid, "default_best_before_days": 1095}]
        p = _get(pid)
        assert p["product_group_id"] is None and p["default_best_before_days"] == 60

    def test_a_cut_off_answer_is_asked_for_in_halves(self):
        gid = _group("Naposteltavat e15")
        ids = [_mk(f"Sipsi {i} e15") for i in range(5)]
        sizes = []

        def fake(prompt, conn, **kw):
            asked = _asked(prompt)
            sizes.append(len(asked))
            if len(asked) > 2:
                raise AiTruncated("cut off")
            return [{"id": p["id"], "group_id": gid, "location_id": None, "best_before_days": 200}
                    for p in asked]

        enrich.enrich(get_connection(), ids, fake, link=False)
        assert all(_get(i)["product_group_id"] == gid for i in ids)
        assert sizes[0] == 5 and max(sizes[1:]) <= 3


class TestReviewFindings:
    def test_a_hand_edit_made_while_the_ai_answers_wins(self):
        hand, ai_loc = _locations()[0], _locations()[-1]
        gid = _group("Leipä e30")
        pid = _mk("Paahtoleipä e30", content_amount=500, content_unit_id=_u("g"))
        client.put(f"/api/products/{pid}", json={"pack_count": 20})  # marks pack_count manual

        def ai(prompt, conn, **kw):
            # The person saves the product while the AI is still answering.
            assert client.put(f"/api/products/{pid}", json={"location_id": hand}).status_code == 200
            return [{"id": pid, "group_id": gid, "location_id": ai_loc, "best_before_days": 7}]

        enrich.run(get_connection(), ai, product_ids=[pid])

        p = _get(pid)
        assert p["location_id"] == hand and p["product_group_id"] == gid
        meta = p["facts_meta"]
        meta = json.loads(meta) if isinstance(meta, str) else meta
        assert meta["location"] == "manual" and meta["pack_count"] == "manual"

    def test_one_product_the_ai_cannot_answer_for_does_not_block_the_queue(self, monkeypatch):
        monkeypatch.setattr(enrich, "DEBOUNCE_S", 0)
        conn = get_connection()
        conn.execute("UPDATE products SET ai_enriched_at = datetime('now') WHERE ai_enriched_at IS NULL")
        conn.commit()
        gid = _group("Tupakkatuotteet e31")
        bad = _mk("Savuke e31")
        good = [_mk(f"Nuuska {i} e31") for i in range(3)]

        def ai(prompt, conn_, **kw):
            asked = _asked(prompt)
            if any(p["id"] == bad for p in asked):
                raise ValueError("Gemini returned no answer (SAFETY)")
            return [{"id": p["id"], "group_id": gid, "location_id": None, "best_before_days": 365}
                    for p in asked]

        counts = enrich.run_pending(conn, ai)
        assert "failed" not in counts and enrich.pending(conn) == []
        assert all(_get(i)["product_group_id"] == gid for i in good)
        assert _get(bad)["product_group_id"] is None

    def test_an_outage_is_not_mistaken_for_a_bad_product(self, monkeypatch):
        monkeypatch.setattr(enrich, "DEBOUNCE_S", 0)
        monkeypatch.setattr(enrich, "_outage_until", 0.0)
        conn = get_connection()
        conn.execute("UPDATE products SET ai_enriched_at = datetime('now') WHERE ai_enriched_at IS NULL")
        conn.commit()
        ids = [_mk(f"Pähkinä {i} e32") for i in range(3)]
        calls = []

        def down(prompt, conn_, **kw):
            calls.append(1)
            raise ValueError("AI call failed after 4 attempts: connection refused")

        counts = enrich.run_pending(conn, down)
        assert counts["failed"] == 3 and enrich.pending(conn) == ids
        assert len(calls) == 3  # the batch, its first product, its last product

    def test_a_failed_link_call_leaves_the_product_queued(self, monkeypatch):
        monkeypatch.setattr(enrich, "DEBOUNCE_S", 0)
        conn = get_connection()
        conn.execute("UPDATE products SET ai_enriched_at = datetime('now') WHERE ai_enriched_at IS NULL")
        conn.commit()
        _mk("pähkinä-konsepti e33", kind="concept")
        pid = _mk("Cashew e33")

        def link_down(prompt, conn_, **kw):
            raise ValueError("AI call failed after 4 attempts")
        monkeypatch.setattr("linker.call_ai_json", link_down)

        counts = enrich.run_pending(conn, _ai())
        assert counts["failed"] == 1 and enrich.pending(conn) == [pid]

    def test_a_purchase_queues_a_loose_product_again(self, monkeypatch):
        started = []
        monkeypatch.setattr(enrich, "start", lambda: started.append(1))
        loose = _mk("Irtotuote e34")
        concept = _mk("kategoria-e34", kind="concept")
        placed = _mk("Sijoitettu e34", parent_id=concept)
        conn = get_connection()
        conn.execute("UPDATE products SET ai_enriched_at = datetime('now') WHERE id IN (?, ?)", (loose, placed))
        conn.commit()
        for pid in (loose, placed):
            assert client.post("/api/stock/add", json={"product_id": pid, "amount": 1}).status_code in (200, 201)
        queued = set(enrich.pending(conn))
        assert loose in queued and placed not in queued
        assert len(started) >= 2

    def test_a_fill_does_not_ask_about_products_with_nothing_missing(self):
        gid = _group("Valmiit e35")
        pid = _mk("Valmis e35", location_id=_locations()[0], product_group_id=gid,
                  default_best_before_days=30)
        calls = []
        enrich.enrich(get_connection(), [pid], _ai(calls=calls), link=False)
        assert calls == []
        assert pid not in enrich.pending(get_connection())

    def test_merging_packs_keeps_the_stock_value(self):
        base = _mk("Olut 0,33l e36")
        multi = _mk("Olut 6x0,33l e36")
        client.post("/api/barcodes", json={"product_id": multi, "barcode": f"64{multi:011d}"})
        client.post("/api/stock/add", json={"product_id": multi, "amount": 2, "price_paid": 5.40})
        enrich.run(get_connection(), _ai(), product_ids=[multi])
        lots = get_connection().execute(
            "SELECT amount, price_paid FROM stock WHERE product_id = ?", (base,)).fetchall()
        assert sum(l["amount"] for l in lots) == 12
        assert round(sum(l["amount"] * l["price_paid"] for l in lots), 2) == 10.80

    def test_fix_expiry_for_named_products_does_not_link(self, monkeypatch):
        linked = []
        monkeypatch.setattr("linker.link_products", lambda conn, ids, **k: linked.append(ids)
                            or {"linked": [], "proposed": [], "unmatched": ids, "ai_error": None})
        pid = _mk("Säilyke e37")
        enrich.run(get_connection(), _ai(default={"best_before_days": 1095}),
                   product_ids=[pid], fields=("shelf_life",))
        assert linked == [] and _get(pid)["default_best_before_days"] == 1095

    def test_emptying_house_rules_in_the_options_restores_the_default(self, monkeypatch, tmp_path):
        import database
        conn = get_connection()
        opts = tmp_path / "options.json"
        monkeypatch.setattr(database, "_OPTIONS_FILE", opts)
        opts.write_text(json.dumps({"house_rules": "Kaikki kaappiin."}))
        database.sync_from_options(conn)
        assert enrich.house_rules(conn) == "Kaikki kaappiin."
        opts.write_text(json.dumps({"house_rules": ""}))
        database.sync_from_options(conn)
        assert enrich.house_rules(conn) == enrich.DEFAULT_HOUSE_RULES


class TestLinking:
    def test_named_products_are_linked_and_a_full_run_leaves_it_to_the_sweep(self, monkeypatch):
        linked, swept = [], []
        monkeypatch.setattr("linker.link_products", lambda conn, ids, **k: linked.append(list(ids))
                            or {"linked": [], "proposed": [], "unmatched": ids, "ai_error": None})
        monkeypatch.setattr("linker.run_reconcile", lambda conn: swept.append(1)
                            or {"linked": 0, "proposed": 0})
        pid = _mk("Fanta 1,5l e16")
        enrich.run(get_connection(), _ai(), product_ids=[pid])
        assert linked == [[pid]] and swept == []
        enrich.run(get_connection(), _ai())
        assert linked == [[pid]] and swept == [1]

    def test_a_named_concept_gets_its_group_from_the_concept_steps(self):
        gid = _group("Leivonta e17")
        concept = _mk("Hiiva e17", unit="g", kind="concept")

        def fake(prompt, conn, **kw):
            assert "aisle group" in prompt and "Storage locations" not in prompt
            return [{"id": concept, "group_id": gid}]

        enrich.run(get_connection(), fake, product_ids=[concept])
        after = _get(concept)
        assert after["product_group_id"] == gid
        assert after["location_id"] is None and after["default_best_before_days"] == 60


class TestQueue:
    @pytest.fixture(autouse=True)
    def _quiet(self, monkeypatch):
        monkeypatch.setattr(enrich, "DEBOUNCE_S", 0)
        monkeypatch.setattr(enrich, "_outage_until", 0.0)
        conn = get_connection()  # other tests' products are not this queue's business
        conn.execute("UPDATE products SET ai_enriched_at = datetime('now') WHERE ai_enriched_at IS NULL")
        conn.commit()

    def test_a_burst_of_new_products_costs_two_calls(self, monkeypatch):
        gid = _group("Makeiset e18")
        ids = [_mk(f"Karkki {i} e18") for i in range(30)]
        conn = get_connection()
        assert enrich.pending(conn) == ids
        calls = []
        monkeypatch.setattr("linker.call_ai_json", lambda p, c, **k: calls.append("link") or [])
        counts = enrich.run_pending(conn, _ai(default={"group_id": gid, "best_before_days": 365},
                                              calls=calls))
        assert calls == ["link", "attributes"]
        assert counts["enriched"] == 30 and enrich.pending(conn) == []
        assert all(_get(i)["product_group_id"] == gid for i in ids)

    def test_an_outage_records_nothing_and_holds_off(self, monkeypatch):
        pid = _mk("Lakritsi e19")
        conn = get_connection()

        def down(prompt, conn_, **kw):
            raise ValueError("AI call failed after 4 attempts")

        counts = enrich.run_pending(conn, down)
        assert counts["failed"] == 1 and enrich.pending(conn) == [pid]
        monkeypatch.setattr("routers.products._autoplace_enabled", lambda conn: True)
        assert enrich.start() is False  # waiting the outage out

    def test_start_runs_the_queue_on_a_thread(self, monkeypatch):
        gid = _group("Juusto e20")
        pid = _mk("Oltermanni e20")
        monkeypatch.setattr("routers.products._autoplace_enabled", lambda conn: True)
        monkeypatch.setattr("ai_client.call_ai_json", _ai(default={"group_id": gid}))

        class _Sync:
            def __init__(self, target, daemon=True, name=""):
                self._t = target
            def start(self):
                self._t()
        monkeypatch.setattr(enrich.threading, "Thread", _Sync)

        assert enrich.start() is True
        assert _get(pid)["product_group_id"] == gid
        assert enrich.start() is False  # nothing pending

    def test_start_is_off_without_an_ai_provider_and_never_raises(self, monkeypatch):
        _mk("Salmiakki e21")
        assert enrich.start() is False  # the test environment has no key

        def broken(conn):
            raise RuntimeError("database is locked")
        monkeypatch.setattr(enrich, "pending", broken)
        monkeypatch.setattr("routers.products._autoplace_enabled", lambda conn: True)
        assert enrich.start() is False

    def test_creating_a_product_starts_the_queue(self, monkeypatch):
        started = []
        monkeypatch.setattr(enrich, "start", lambda: started.append(1))
        _mk("Suklaa e22")
        _mk("suklaa-konsepti e22", kind="concept")
        assert started == [1]


class TestSeedGroups:
    def test_a_catalog_without_groups_gets_the_aisles(self, tmp_path):
        from database import get_db, init_db

        conn = get_db(tmp_path / "fresh.db")
        init_db(conn)
        unit = conn.execute("SELECT id FROM units WHERE abbreviation = 'kpl'").fetchone()["id"]
        cur = conn.execute("INSERT INTO products (name, unit_id) VALUES ('Maito 1l', ?)", (unit,))
        conn.commit()
        names = lambda: {r["name"] for r in conn.execute("SELECT name FROM product_groups")}
        assert "Maitotuotteet" not in names()

        def fake(prompt, conn_, **kw):
            dairy = next(g["id"] for g in json.loads(
                prompt.split("null when none fits:\n")[1].split("\n\nStorage locations")[0])
                if g["name"] == "Maitotuotteet")
            return [{"id": cur.lastrowid, "group_id": dairy, "location_id": None, "best_before_days": 10}]

        enrich.enrich(conn, [cur.lastrowid], fake, link=False)
        assert set(enrich._SEED_GROUPS) <= names()
        row = conn.execute("SELECT product_group_id, ai_enriched_at FROM products").fetchone()
        assert row["product_group_id"] is not None and row["ai_enriched_at"]
        conn.close()


class TestUpgrade:
    def test_the_existing_catalog_is_not_queued(self, tmp_path):
        """Upgrading adds the column and marks every product that is already
        there as looked at: only products created from now on are enriched
        on their own."""
        from database import _migrate_schema, get_db, init_db

        conn = get_db(tmp_path / "old.db")
        init_db(conn)
        unit = conn.execute("SELECT id FROM units WHERE abbreviation = 'kpl'").fetchone()["id"]
        conn.execute("INSERT INTO products (name, unit_id) VALUES ('Vanha tuote', ?)", (unit,))
        conn.execute("ALTER TABLE products DROP COLUMN ai_enriched_at")  # as before the upgrade
        conn.execute("DELETE FROM _meta WHERE key = 'ai_enriched_backfill_v1'")
        conn.commit()

        _migrate_schema(conn)
        conn.commit()

        assert enrich.pending(conn) == []
        conn.execute("INSERT INTO products (name, unit_id) VALUES ('Uusi tuote', ?)", (unit,))
        assert len(enrich.pending(conn)) == 1
        _migrate_schema(conn)  # every later start: the new product stays queued
        assert len(enrich.pending(conn)) == 1
        conn.close()

    def test_a_start_interrupted_before_the_backfill_redoes_it(self, tmp_path):
        """The column was added (it commits on its own) but the start died
        before the stamps were committed: the next start stamps them."""
        from database import _migrate_schema, get_db, init_db

        conn = get_db(tmp_path / "half.db")
        init_db(conn)
        unit = conn.execute("SELECT id FROM units WHERE abbreviation = 'kpl'").fetchone()["id"]
        conn.execute("INSERT INTO products (name, unit_id) VALUES ('Vanha tuote', ?)", (unit,))
        conn.execute("DELETE FROM _meta WHERE key = 'ai_enriched_backfill_v1'")
        conn.commit()
        assert len(enrich.pending(conn)) == 1

        _migrate_schema(conn)
        conn.commit()
        assert enrich.pending(conn) == []
        conn.close()


class TestOptimizeEndpoints:
    class _Sync:
        def __init__(self, target, args=(), daemon=True, name=""):
            self._t, self._a = target, args
        def start(self):
            self._t(*self._a)

    def _run(self, monkeypatch, body, result=None):
        """POST /ai/optimize with the job run in place; returns (response,
        the arguments enrich.run got)."""
        from routers import ai as ai_mod
        seen = {}

        def fake_run(conn, ai, **kw):
            seen.update(kw)
            return result or {"updated": 3, "merged": 0, "proposals": [{"id": 1}]}
        monkeypatch.setattr(enrich, "run", fake_run)
        monkeypatch.setattr(ai_mod.threading, "Thread", self._Sync)
        return client.post("/api/ai/optimize", json=body), seen

    def test_missing_only_is_refused_when_nothing_is_missing(self, monkeypatch):
        monkeypatch.setattr(enrich, "missing", lambda conn: [])
        r = client.post("/api/ai/optimize", json={"ungrouped_only": True})
        assert r.status_code == 400 and "missing" in r.json()["detail"].lower()

    def test_missing_only_reaches_the_job(self, monkeypatch):
        monkeypatch.setattr(enrich, "missing", lambda conn: [1])
        r, seen = self._run(monkeypatch, {"ungrouped_only": True})
        assert r.status_code == 200
        assert seen["missing_only"] is True and seen["product_ids"] is None
        task = client.get(f"/api/ai/optimize/{r.json()['task_id']}").json()
        assert task["status"] == "done" and task["updated"] == 3 and task["mode"] == "incremental"
        assert "proposals" not in task

    def test_dry_run_returns_the_proposals(self, monkeypatch):
        r, seen = self._run(monkeypatch, {"product_ids": [1], "dry_run": True})
        assert seen["dry_run"] is True and seen["product_ids"] == [1]
        assert client.get(f"/api/ai/optimize/{r.json()['task_id']}").json()["proposals"] == [{"id": 1}]

    def test_fix_expiry_only_writes_shelf_life(self, monkeypatch):
        from routers import ai as ai_mod
        seen = {}
        monkeypatch.setattr(enrich, "run", lambda conn, ai, **kw: seen.update(kw)
                            or {"updated": 3, "merged": 0, "proposals": []})
        monkeypatch.setattr(ai_mod.threading, "Thread", self._Sync)
        r = client.post("/api/ai/fix-expiry")
        assert r.status_code == 200 and seen["fields"] == ("shelf_life",)
        task = client.get(f"/api/ai/optimize/{r.json()['task_id']}").json()
        assert (task["status"], task["updated"], task["mode"]) == ("done", 3, "expiry-fix")

    def test_a_failed_job_ends_as_error_with_the_cause(self, monkeypatch):
        from routers import ai as ai_mod

        def dead(conn, ai, **kw):
            raise AiConfigError("Claude refused the request (HTTP 404: model)")
        monkeypatch.setattr(enrich, "run", dead)
        monkeypatch.setattr(ai_mod.threading, "Thread", self._Sync)
        r = client.post("/api/ai/optimize", json={"product_ids": [1]})
        task = client.get(f"/api/ai/optimize/{r.json()['task_id']}").json()
        assert task["status"] == "error" and "HTTP 404" in task["logs"][-1]
        assert client.post("/api/ai/optimize", json={"product_ids": [1]}).status_code == 200  # lock released

    def test_saving_the_group_list_creates_the_groups(self):
        r = client.put("/api/ai/optimize/categories", json={"categories": ["Uusi ryhmä e23"]})
        assert r.status_code == 200
        assert "Uusi ryhmä e23" in {g["name"] for g in client.get("/api/product-groups").json()}


class TestAvailabilityKeepsPrice:
    def test_a_check_without_a_price_keeps_the_known_one(self):
        pid = _mk("Kahvi 500g e24")
        url = f"/api/products/{pid}/availability"
        client.put(url, json=[{"store_id": "E24", "available": True, "price": 5.49}])
        r = client.put(url, json=[{"store_id": "E24", "available": True}])
        assert r.status_code == 200
        assert [s["price"] for s in r.json() if s["store_id"] == "E24"] == [5.49]
