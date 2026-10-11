"""Finding a store product for a concept on the shopping list that has none
(spec 2026-10-11 §5). The scraper and the AI are always faked."""
from __future__ import annotations

import json
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
import sku_finder

client = TestClient(app)


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _add(product_id, amount=1):
    r = client.post("/api/shopping-list", json={"product_id": product_id, "amount": amount})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _barcode(product_id, ean):
    r = client.post("/api/barcodes", json={"product_id": product_id, "barcode": ean})
    assert r.status_code in (200, 201), r.text


def _hit(name, ean):
    return {"name": name, "ean": ean, "description": "", "image_url": ""}


class _Scraper:
    """Fake scraper: hits per query; add_product creates the product through
    Storage's API like the real one."""

    def __init__(self, hits=None, fail=None):
        self.hits, self.fail = hits or {}, fail
        self.searched, self.added = [], []

    def search(self, query):
        self.searched.append(query)
        if self.fail == "search":
            raise sku_finder.ScraperError("unreachable")
        return list(self.hits.get(query, []))

    def add_product(self, hit, *, parent_id, product_group_id):
        if self.fail == "add":
            raise sku_finder.ScraperError("timed out")
        pid = _mk(hit["name"], parent_id=parent_id)
        _barcode(pid, hit["ean"])
        self.added.append((hit["ean"], parent_id, product_group_id))
        return pid


def _ai(index=0, confidence="high", prompts=None):
    def call(prompt, conn):
        if prompts is not None:
            prompts.append(prompt)
        return {"index": index, "confidence": confidence}
    return call


def _parent(pid):
    return client.get(f"/api/products/{pid}").json()["parent_id"]


class TestPending:
    def test_pending_selects_open_concept_rows_without_variants(self):
        bare = _mk("Juustokreemi p1", kind="concept")
        stocked = _mk("Kerma p1", kind="concept")
        _mk("Arkikerma p1", parent_id=stocked)
        done_concept = _mk("Rahka p1", kind="concept")
        sku = _mk("Maito p1")
        _add(bare)
        _add(stocked)
        _add(sku)
        row = _add(done_concept)
        assert client.put(f"/api/shopping-list/{row}", json={"done": True}).status_code == 200
        todo = sku_finder.pending(get_connection())
        assert bare in todo
        assert not {stocked, done_concept, sku} & set(todo)

    def test_pending_counts_inactive_variants_as_none(self):
        concept = _mk("Kerma p2", kind="concept")
        gone = _mk("Poistunut kerma p2", parent_id=concept)
        assert client.put(f"/api/products/{gone}", json={"active": False}).status_code == 200
        _add(concept)
        assert concept in sku_finder.pending(get_connection())

    def test_final_records_and_fresh_errors_are_skipped_old_errors_retried(self):
        conn = get_connection()
        final, fresh, old, spent = (_mk(f"Käsite p3{i}", kind="concept") for i in "abcd")
        for c in (final, fresh, old, spent):
            _add(c)
        sku_finder.record(conn, final, {"status": "no_match"})
        sku_finder.record(conn, fresh, {"status": "error", "detail": "busy"})
        sku_finder.record(conn, old, {"status": "error", "detail": "busy"})
        sku_finder.record(conn, spent, {"status": "error"})
        conn.execute("UPDATE sku_searches SET last_attempt_at = datetime('now', '-31 minutes') "
                     "WHERE product_id IN (?, ?)", (old, spent))
        conn.execute("UPDATE sku_searches SET attempts = 5 WHERE product_id = ?", (spent,))
        conn.commit()
        todo = sku_finder.pending(conn)
        assert old in todo
        assert not {final, fresh, spent} & set(todo)

    def test_record_counts_attempts(self):
        conn = get_connection()
        concept = _mk("Käsite p4", kind="concept")
        sku_finder.record(conn, concept, {"status": "error", "detail": "busy"})
        sku_finder.record(conn, concept, {"status": "found", "found_product_id": 5, "detail": "X"})
        row = conn.execute("SELECT * FROM sku_searches WHERE product_id = ?", (concept,)).fetchone()
        assert (row["status"], row["attempts"], row["found_product_id"]) == ("found", 2, 5)


class TestFindFor:
    def test_found_product_lands_under_the_concept_and_becomes_the_pick(self):
        concept = _mk("Juustokreemi f1", kind="concept")
        row = _add(concept)
        prompts = []
        scraper = _Scraper({"juustokreemi f1": [_hit("Valio juustokreemi f1 200g", "9000000000011")]})
        res = sku_finder.find_for(get_connection(), concept, scraper, _ai(prompts=prompts))
        assert res["status"] == "found"
        assert _parent(res["found_product_id"]) == concept
        assert "Juustokreemi f1" in prompts[0] and "Valio juustokreemi f1 200g" in prompts[0]
        d = next(i for i in client.get("/api/shopping-list").json() if i["id"] == row)["display"]
        assert d["buy_product_name"] == "Valio juustokreemi f1 200g"

    def test_aliases_are_searched_when_the_name_finds_nothing(self):
        conn = get_connection()
        concept = _mk("Crème fraîche f2", kind="concept")
        concepts.add_alias(conn, "ranskankerma f2", concept, "test")
        conn.commit()
        scraper = _Scraper({"ranskankerma f2": [_hit("Ranskankerma f2 2dl", "9000000000028")]})
        res = sku_finder.find_for(conn, concept, scraper, _ai())
        assert scraper.searched[0] == "creme fraiche f2"
        assert res["status"] == "found"

    def test_no_hits(self):
        concept = _mk("Käsite f3", kind="concept")
        res = sku_finder.find_for(get_connection(), concept, _Scraper(), _ai())
        assert res["status"] == "no_hits"

    def test_unsure_or_invalid_answers_are_no_match(self):
        for i, (index, conf) in enumerate([(None, "high"), (0, "medium"), (7, "high")]):
            concept = _mk(f"Käsite f4{i}", kind="concept")
            scraper = _Scraper({f"käsite f4{i}": [_hit(f"Tuote f4{i}", f"90000000004{i}")]})
            res = sku_finder.find_for(get_connection(), concept, scraper, _ai(index, conf))
            assert res["status"] == "no_match"
            assert scraper.added == []

    def test_existing_unparented_product_is_moved_under_the_concept(self):
        concept = _mk("Käsite f5", kind="concept")
        loose = _mk("Irrallinen tuote f5")
        _barcode(loose, "9000000000059")
        scraper = _Scraper({"käsite f5": [_hit("Irrallinen tuote f5", "9000000000059")]})
        res = sku_finder.find_for(get_connection(), concept, scraper, _ai())
        assert (res["status"], res["found_product_id"]) == ("linked", loose)
        assert _parent(loose) == concept and scraper.added == []

    def test_existing_product_under_another_concept_stays(self):
        concept = _mk("Käsite f6", kind="concept")
        other = _mk("Toinen käsite f6", kind="concept")
        taken = _mk("Varattu tuote f6", parent_id=other)
        _barcode(taken, "9000000000066")
        scraper = _Scraper({"käsite f6": [_hit("Varattu tuote f6", "9000000000066")]})
        res = sku_finder.find_for(get_connection(), concept, scraper, _ai())
        assert res["status"] == "exists_elsewhere"
        assert _parent(taken) == other

    def test_scraper_and_ai_failures_are_errors_that_change_nothing(self):
        concept = _mk("Käsite f7", kind="concept")
        hits = {"käsite f7": [_hit("Tuote f7", "9000000000073")]}
        res = sku_finder.find_for(get_connection(), concept, _Scraper(hits, fail="search"), _ai())
        assert (res["status"], res["stop"]) == ("error", True)
        res = sku_finder.find_for(get_connection(), concept, _Scraper(hits, fail="add"), _ai())
        assert (res["status"], res["stop"]) == ("error", True)

        def broken(prompt, conn):
            raise RuntimeError("quota")
        res = sku_finder.find_for(get_connection(), concept, _Scraper(hits), broken)
        assert (res["status"], res["stop"]) == ("error", False)
        assert client.get("/api/products/by-barcode/9000000000073").status_code == 404


import httpx  # noqa: E402


def _transport(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestScraperClient:
    def test_search_polls_the_task_until_done(self):
        calls = []

        def handler(request):
            calls.append((request.method, request.url.path))
            if request.method == "POST":
                assert json.loads(request.content) == {"query": "juustokreemi", "max_products": 20}
                return httpx.Response(200, json={"task_id": "7", "status": "running"})
            if len(calls) < 3:
                return httpx.Response(200, json={"status": "running"})
            return httpx.Response(200, json={"status": "done", "success": True,
                                             "products": [_hit("Valio", "1")]})
        c = sku_finder.ScraperClient("http://s/api/scraper", http=_transport(handler), poll=0)
        assert c.search("juustokreemi") == [_hit("Valio", "1")]
        assert calls[0] == ("POST", "/api/scraper/search")
        assert calls[-1] == ("GET", "/api/scraper/task/7")

    def test_busy_failed_and_unreachable_raise(self):
        busy = sku_finder.ScraperClient("http://s/api/scraper", poll=0, http=_transport(
            lambda r: httpx.Response(409, json={"error": "busy"})))
        failed = sku_finder.ScraperClient("http://s/api/scraper", poll=0, http=_transport(
            lambda r: httpx.Response(200, json={"task_id": "1", "status": "running"})
            if r.method == "POST" else httpx.Response(200, json={"status": "done", "success": False,
                                                                 "error": "boom"})))

        def down(request):
            raise httpx.ConnectError("refused")
        unreachable = sku_finder.ScraperClient("http://s/api/scraper", poll=0, http=_transport(down))
        for c in (busy, failed, unreachable):
            with pytest.raises(sku_finder.ScraperError):
                c.search("x")

    def test_add_product_sends_the_parent_and_returns_the_new_id(self):
        sent = []

        def handler(request):
            if request.method == "POST":
                sent.append(json.loads(request.content))
                return httpx.Response(200, json={"task_id": "2", "status": "running"})
            return httpx.Response(200, json={"status": "done", "success": True, "added_ids": [91]})
        c = sku_finder.ScraperClient("http://s/api/scraper", http=_transport(handler), poll=0)
        assert c.add_product(_hit("Valio", "1"), parent_id=5, product_group_id=3) == 91
        assert sent == [{"products": [{**_hit("Valio", "1"), "parent_id": 5, "product_group_id": 3}]}]


class TestRun:
    def test_run_records_each_concept_once(self):
        conn = get_connection()
        a = _mk("Käsite r1a", kind="concept")
        b = _mk("Käsite r1b", kind="concept")
        _add(a)
        _add(b)
        scraper = _Scraper({"käsite r1a": [_hit("Tuote r1a", "9000000000103")]})
        counts = sku_finder.run_pending(conn, scraper, _ai())
        assert counts["found"] >= 1 and counts["no_hits"] >= 1
        assert not {a, b} & set(sku_finder.pending(conn))
        assert scraper.searched.count("käsite r1a") == 1

    def test_run_stops_at_the_first_scraper_error(self):
        conn = get_connection()
        _add(_mk("Käsite r2a", kind="concept"))
        _add(_mk("Käsite r2b", kind="concept"))
        scraper = _Scraper(fail="search")
        assert sku_finder.run_pending(conn, scraper, _ai()) == {"error": 1}
        assert len(scraper.searched) == 1

    def test_start_needs_an_ai_provider(self):
        assert sku_finder.start() is False

    def test_start_is_single_flight(self, monkeypatch):
        import threading
        import time
        import routers.products

        gate, ran = threading.Event(), []

        def job():
            ran.append(1)
            gate.wait(5)
            sku_finder._finish({"done": 1})
        monkeypatch.setattr(routers.products, "_ai_configured", lambda conn: True)
        monkeypatch.setattr(sku_finder, "_job", job)
        assert sku_finder.start() is True
        assert sku_finder.start() is False
        gate.set()
        for _ in range(100):
            if not sku_finder.status(get_connection())["running"]:
                break
            time.sleep(0.02)
        assert ran == [1]
        assert sku_finder.status(get_connection())["last_run"]["done"] == 1


class TestEndpointsAndTriggers:
    def test_status_lists_records(self):
        concept = _mk("Käsite e1", kind="concept")
        sku_finder.record(get_connection(), concept, {"status": "no_hits"})
        body = client.get("/api/shopping-list/find-products").json()
        assert body["running"] is False
        assert any(r["product_id"] == concept and r["name"] == "Käsite e1" for r in body["records"])
        assert client.post("/api/shopping-list/find-products").json() == {"started": False}

    def test_adding_a_concept_row_or_a_recipe_starts_a_run(self, monkeypatch):
        started = []
        monkeypatch.setattr(sku_finder, "start", lambda: started.append(1) or True)
        concept = _mk("Käsite e2", kind="concept")
        _add(_mk("Tuote e2"))
        assert started == []
        _add(concept)
        assert started == [1]
        r = client.post("/api/recipes", json={"name": "Resepti e2", "servings": 4, "ingredients": [
            {"product_id": concept, "amount": 1, "unit_id": _u("kpl"), "specificity": "loose"}]})
        assert r.status_code == 201, r.text
        pushed = client.post(f"/api/recipes/{r.json()['id']}/to-shopping", json={"mode": "all"})
        assert pushed.status_code in (200, 201), pushed.text
        assert started == [1, 1]

    def test_the_periodic_sync_starts_a_run(self, monkeypatch):
        import main
        started = []
        monkeypatch.setattr(sku_finder, "start", lambda: started.append(1) or True)
        main.periodic_sync_once()
        assert started == [1]
