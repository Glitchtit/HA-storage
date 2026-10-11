"""Find a retail product for a concept on the shopping list that has none
(spec 2026-10-11 §5).

pending   concepts on open shopping rows with no active SKU under them and
          no final search record (an error retries after 30 minutes, five
          times; "no hits" after a day, three times — K-Ruoka answers empty
          when it fails)
find_for  search K-Ruoka (through the scraper) by the concept's name and
          aliases, let the AI pick the hit that is the concept, then file
          it under the concept: move an existing unparented product there,
          or have the scraper create it
record    one row per concept in sku_searches

AI and scraper calls never hold CATALOG_LOCK; apply_link takes it for the
write. The scraper object is injected (ScraperClient in production).

run_pending searches every pending concept once; start() runs it on a
daemon thread with its own connection (single-flight, only with an AI
provider). The shopping-list add, recipe push and periodic sync call start().
An outage (scraper unreachable, busy or failing, AI failing) is nobody's
concept's fault: it is not recorded, the run stops and no run starts for
OUTAGE_BACKOFF_S.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

import tree
from concepts import aliases_of, norm
from facts import Catalog
from locks import CATALOG_LOCK

log = logging.getLogger(__name__)

FINAL = frozenset({"found", "linked", "no_hits", "no_match", "exists_elsewhere"})
MAX_ATTEMPTS = 5
RETRY_AFTER_MINUTES = 30
NO_HITS_ATTEMPTS = 3
NO_HITS_RETRY_HOURS = 24
OUTAGE_BACKOFF_S = 15 * 60
MAX_QUERIES = 3
MAX_HITS = 20


class ScraperError(Exception):
    """The scraper could not answer: unreachable, busy, timed out or failed."""


_PICK_PROMPT = """You keep a Finnish household's grocery catalog.
The generic ingredient below has no store product yet. Pick the store
product that IS this ingredient in an everyday form: any brand or size of
the same thing counts ("Valio Keittiön juustokreemi 200g" is "Juustokreemi"),
a different thing does not ("Juustokakku" is not "Juusto", "Kermaviili" is
not "Kerma"). When several fit, prefer the plain variant over special diets,
flavours and multipacks. When none fits, answer null.

Ingredient: {name}
Broader categories: {ancestors}
Store products (JSON): {hits_json}

Return ONLY JSON: {{"index": <index of the product or null>, "confidence": "high"|"medium"|"low"}}
"""


def _result(status: str, *, found: int | None = None, detail: str = "", stop: bool = False) -> dict:
    return {"status": status, "found_product_id": found, "detail": detail, "stop": stop}


def pending(conn: sqlite3.Connection) -> list[int]:
    """Concepts on open shopping rows that have no active SKU under them and
    are due a search."""
    rows = conn.execute(
        "SELECT DISTINCT s.product_id AS id FROM shopping_list s "
        "  JOIN products p ON p.id = s.product_id "
        "  LEFT JOIN sku_searches f ON f.product_id = p.id "
        " WHERE s.done = 0 AND p.kind = 'concept' AND p.active = 1 "
        "   AND (f.product_id IS NULL "
        "        OR (f.status = 'error' AND f.attempts < ? "
        "            AND f.last_attempt_at <= datetime('now', ?)) "
        "        OR (f.status = 'no_hits' AND f.attempts < ? "
        "            AND f.last_attempt_at <= datetime('now', ?))) "
        " ORDER BY s.product_id",
        (MAX_ATTEMPTS, f"-{RETRY_AFTER_MINUTES} minutes",
         NO_HITS_ATTEMPTS, f"-{NO_HITS_RETRY_HOURS} hours"),
    ).fetchall()
    if not rows:
        return []
    cat = Catalog(conn)
    return [r["id"] for r in rows
            if not any(cat.is_sku(d) and (cat.product(d) or {}).get("active")
                       for d in cat.descendants(r["id"]))]


def _queries(conn: sqlite3.Connection, name: str, concept_id: int) -> list[str]:
    """The name as written (store names keep their accents: "Jalapeño"), the
    name without accents ("creme fraiche"), then the aliases."""
    out: list[str] = []
    texts = [" ".join(name.casefold().split()), norm(name)]
    texts += [norm(a["alias"]) for a in aliases_of(conn, concept_id)]
    for q in texts:
        if q and q not in out:
            out.append(q)
    return out[:MAX_QUERIES]


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _by_barcode(conn: sqlite3.Connection, ean: str) -> int | None:
    row = conn.execute("SELECT product_id FROM barcodes WHERE barcode = ?", (ean,)).fetchone()
    return row["product_id"] if row else None


def _file_existing(conn: sqlite3.Connection, concept: dict, pid: int) -> dict:
    """Move an existing product under the concept when the adopt rule allows:
    an active SKU that is unparented or sits under one of its ancestors."""
    from linker import apply_link

    with CATALOG_LOCK:
        prod = conn.execute(
            "SELECT kind, active, parent_id FROM products WHERE id = ?", (pid,)
        ).fetchone()
        movable = (prod is not None and prod["kind"] == "sku" and prod["active"]
                   and (prod["parent_id"] is None
                        or prod["parent_id"] in tree.ancestor_ids(conn, concept["id"])))
        if not movable:
            return _result("exists_elsewhere", found=pid)
        try:
            apply_link(conn, pid, concept["id"], note=f"found for {concept['name']}")
        except ValueError as exc:
            conn.rollback()
            return _result("exists_elsewhere", found=pid, detail=str(exc))
    return _result("linked", found=pid)


def _pick_prompt(conn: sqlite3.Connection, concept: dict, hits: list[dict]) -> str:
    ids = tree.ancestor_ids(conn, concept["id"])
    ancestors = [r["name"] for r in conn.execute(
        f"SELECT name FROM products WHERE id IN ({','.join('?' * len(ids))})", ids
    ).fetchall()] if ids else []
    return _PICK_PROMPT.format(
        name=concept["name"], ancestors=", ".join(ancestors) or "-",
        hits_json=json.dumps([{"index": i, "name": h["name"]} for i, h in enumerate(hits)],
                             ensure_ascii=False))


def find_for(conn: sqlite3.Connection, concept_id: int, scraper: Any,
             ai: Callable[..., Any]) -> dict:
    """Search, pick and file one concept's product. Returns a result dict:
    status (FINAL or 'error'), found_product_id, detail, and stop — True when
    the scraper failed, so the rest of a run would fail the same way."""
    concept = conn.execute(
        "SELECT id, name, product_group_id FROM products WHERE id = ? AND kind = 'concept'",
        (concept_id,),
    ).fetchone()
    if concept is None:
        return _result("no_hits", detail="not a concept")
    hits: list[dict] = []
    seen: set[str] = set()
    try:
        for q in _queries(conn, concept["name"], concept_id):
            for h in scraper.search(q):
                ean = str(h.get("ean") or "").strip()
                if ean and ean not in seen and h.get("name"):
                    seen.add(ean)
                    hits.append({"name": str(h["name"]), "ean": ean,
                                 "description": h.get("description") or "",
                                 "image_url": h.get("image_url") or ""})
    except ScraperError as exc:
        return _result("error", detail=f"search: {exc}", stop=True)
    hits = hits[:MAX_HITS]
    if not hits:
        return _result("no_hits")

    try:
        answer = ai(_pick_prompt(conn, concept, hits), conn)
    except Exception as exc:  # noqa: BLE001 — an AI failure is an outage
        return _result("error", detail=f"ai: {exc}", stop=True)
    index = _int(answer.get("index")) if isinstance(answer, dict) else None
    confidence = str(answer.get("confidence", "")).lower() if isinstance(answer, dict) else ""
    if index is None or not 0 <= index < len(hits) or confidence != "high":
        return _result("no_match", detail=f"{len(hits)} hits, answer {answer!r}"[:300])
    hit = hits[index]

    existing = _by_barcode(conn, hit["ean"])
    if existing is not None:
        return _file_existing(conn, concept, existing)
    try:
        new_id = scraper.add_product(hit, parent_id=concept_id,
                                     product_group_id=concept["product_group_id"])
    except ScraperError as exc:
        return _result("error", detail=f"add: {exc}", stop=True)
    if new_id is None:  # the scraper skipped it: the barcode appeared meanwhile
        existing = _by_barcode(conn, hit["ean"])
        if existing is None:
            return _result("error", detail="add: nothing created")
        return _file_existing(conn, concept, existing)
    placed = conn.execute("SELECT parent_id FROM products WHERE id = ?", (new_id,)).fetchone()
    if placed is not None and placed["parent_id"] != concept_id:
        filed = _file_existing(conn, concept, new_id)  # an older scraper ignores parent_id
        if filed["status"] != "linked":
            return filed
    return _result("found", found=new_id, detail=hit["name"])


def record(conn: sqlite3.Connection, concept_id: int, result: dict) -> None:
    """Store the outcome of one search; attempts count every search."""
    conn.execute(
        "INSERT INTO sku_searches (product_id, status, attempts, last_attempt_at, "
        "                          found_product_id, detail) "
        "VALUES (?, ?, 1, datetime('now'), ?, ?) "
        "ON CONFLICT(product_id) DO UPDATE SET status = excluded.status, "
        "  attempts = sku_searches.attempts + 1, last_attempt_at = excluded.last_attempt_at, "
        "  found_product_id = excluded.found_product_id, detail = excluded.detail",
        (concept_id, result["status"], result.get("found_product_id"), result.get("detail") or ""),
    )
    conn.commit()


# ── scraper client ───────────────────────────────────────────────────────

# Storage's own nginx proxies /api/scraper/ to the auto-detected scraper add-on
# (a placeholder that refuses connections when there is none).
SCRAPER_BASE = "http://127.0.0.1:8099/api/scraper"
SEARCH_DEADLINE_S = 60
ADD_DEADLINE_S = 180


class ScraperClient:
    """The scraper's fire-and-poll API: a POST returns a task id, and
    GET /task/{id} answers until its status is no longer 'running'."""

    def __init__(self, base: str = SCRAPER_BASE, http: httpx.Client | None = None,
                 poll: float = 1.0):
        self.base, self.poll = base.rstrip("/"), poll
        self.http = http or httpx.Client(timeout=15)

    def _run(self, path: str, body: dict, deadline_s: float) -> dict:
        try:
            r = self.http.post(f"{self.base}/{path}", json=body)
        except httpx.HTTPError as exc:
            raise ScraperError(f"unreachable: {exc}") from exc
        if r.status_code == 409:
            raise ScraperError("busy")
        if r.status_code >= 400:
            raise ScraperError(f"HTTP {r.status_code}")
        try:
            task = (r.json() or {}).get("task_id")
        except ValueError as exc:
            raise ScraperError(f"bad answer: {exc}") from exc
        if not task:
            raise ScraperError("no task id")
        end = time.monotonic() + deadline_s
        while time.monotonic() < end:
            time.sleep(self.poll)
            try:
                answer = self.http.get(f"{self.base}/task/{task}")
                if answer.status_code != 200:  # e.g. 404: the scraper restarted
                    raise ScraperError(f"poll: HTTP {answer.status_code}")
                t = answer.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ScraperError(f"poll: {exc}") from exc
            if t.get("status") == "running":
                continue
            if t.get("status") == "error" or t.get("success") is False:
                raise ScraperError(str(t.get("error") or "; ".join(t.get("errors") or []) or "failed"))
            return t
        raise ScraperError("timed out")

    def search(self, query: str) -> list[dict]:
        return self._run("search", {"query": query, "max_products": MAX_HITS},
                         SEARCH_DEADLINE_S).get("products") or []

    def add_product(self, hit: dict, *, parent_id: int, product_group_id: int | None) -> int | None:
        item = {**{k: hit.get(k, "") for k in ("name", "ean", "description", "image_url")},
                "parent_id": parent_id}
        if product_group_id is not None:
            item["product_group_id"] = product_group_id
        ids = self._run("add_products", {"products": [item]}, ADD_DEADLINE_S).get("added_ids") or []
        return int(ids[0]) if ids else None


# ── background run ───────────────────────────────────────────────────────

_outage_until = 0.0  # time.monotonic() before which no run starts


def run_pending(conn: sqlite3.Connection, scraper: Any, ai: Callable[..., Any]) -> dict[str, int]:
    """Search for every pending concept once, including ones added meanwhile.
    An outage stops the run without a record and holds off new runs."""
    global _outage_until
    counts: dict[str, int] = {}
    done: set[int] = set()
    while todo := [c for c in pending(conn) if c not in done]:
        for cid in todo:
            done.add(cid)
            res = find_for(conn, cid, scraper, ai)
            counts[res["status"]] = counts.get(res["status"], 0) + 1
            log.info("Product search for concept %d: %s %s", cid, res["status"], res["detail"])
            if res["stop"]:
                _outage_until = time.monotonic() + OUTAGE_BACKOFF_S
                return counts
            record(conn, cid, res)
    return counts


_state: dict = {"running": False, "last_run": None}
_guard = threading.Lock()


def _finish(result: dict) -> None:
    with _guard:
        _state.update(running=False, last_run={
            **result, "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def _job() -> None:
    from ai_client import call_ai_json
    from database import get_db
    from main import DB_PATH

    result: dict = {}
    try:
        conn = get_db(DB_PATH)
        try:
            result = run_pending(conn, ScraperClient(), call_ai_json)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — a background run never raises
        log.warning("Product search run failed: %s", exc)
        result = {"failed": str(exc)}
    _finish(result)


def start() -> bool:
    """Run the search in the background unless it is running or no AI
    provider is configured. True when started."""
    from main import get_connection
    from routers.products import _ai_configured

    if time.monotonic() < _outage_until or not _ai_configured(get_connection()):
        return False
    with _guard:
        if _state["running"]:
            return False
        _state["running"] = True
    threading.Thread(target=_job, daemon=True, name="sku-finder").start()
    return True


def status(conn: sqlite3.Connection) -> dict:
    with _guard:
        state = dict(_state)
    left = _outage_until - time.monotonic()
    state["outage_until"] = (datetime.now(timezone.utc).timestamp() + left) if left > 0 else None
    records = conn.execute(
        "SELECT f.*, p.name FROM sku_searches f JOIN products p ON p.id = f.product_id "
        "ORDER BY f.last_attempt_at DESC, f.product_id DESC LIMIT 50"
    ).fetchall()
    return {**state, "records": records}
