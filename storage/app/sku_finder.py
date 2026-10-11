"""Find a retail product for a concept on the shopping list that has none
(spec 2026-10-11 §5).

pending   concepts on open shopping rows with no active SKU under them and
          no final search record (errors retry after 30 minutes, 5 times)
find_for  search K-Ruoka (through the scraper) by the concept's name and
          aliases, let the AI pick the hit that is the concept, then file
          it under the concept: move an existing unparented product there,
          or have the scraper create it
record    one row per concept in sku_searches

AI and scraper calls never hold CATALOG_LOCK; apply_link takes it for the
write. The scraper object is injected (ScraperClient in production).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Callable

import tree
from concepts import aliases_of, norm
from facts import Catalog
from locks import CATALOG_LOCK

log = logging.getLogger(__name__)

FINAL = frozenset({"found", "linked", "no_hits", "no_match", "exists_elsewhere"})
MAX_ATTEMPTS = 5
RETRY_AFTER_MINUTES = 30
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
        "   AND (f.product_id IS NULL OR (f.status = 'error' AND f.attempts < ? "
        "        AND f.last_attempt_at <= datetime('now', ?))) "
        " ORDER BY s.product_id",
        (MAX_ATTEMPTS, f"-{RETRY_AFTER_MINUTES} minutes"),
    ).fetchall()
    if not rows:
        return []
    cat = Catalog(conn)
    return [r["id"] for r in rows
            if not any(cat.is_sku(d) and (cat.product(d) or {}).get("active")
                       for d in cat.descendants(r["id"]))]


def _queries(conn: sqlite3.Connection, name: str, concept_id: int) -> list[str]:
    out: list[str] = []
    for text in [name] + [a["alias"] for a in aliases_of(conn, concept_id)]:
        q = norm(text)
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
    except Exception as exc:  # noqa: BLE001 — any AI failure is a retryable error
        return _result("error", detail=f"ai: {exc}")
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
