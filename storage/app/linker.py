# HA-storage/storage/app/linker.py
"""Product linker: place products into the category/variant tree.

Single brain for every entry path (create, purchase, receipt, scraper
discovery, recipe stubs) plus the idempotent reconcile sweep. Stages run
cheapest-first; AI failure degrades to "leave unlinked, sweep retries later".
Confidence policy: exact/normalized and high-confidence AI links auto-apply
(with a 'link' history event); medium/low queue in link_proposals for the
review UI; rejected pairs are never proposed again.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading

from ai_client import call_ai_json
from locks import CATALOG_LOCK
from pack_size import ensure_pack_facts
from routers.history import log_event
import tree

log = logging.getLogger(__name__)

_LINK_PROMPT = """You are organising a household grocery catalog into a tree.
Place each NEW PRODUCT under the most fitting existing CATEGORY/VARIANT node.

CONTEXT:
- Product names are Finnish; the household also uses Swedish and English.
- A product belongs under a node ONLY if it IS that thing (same product TYPE).
  A different brand or pack size of the same type IS a match:
  "Pirkka savulohifileepala 200g" belongs under "lohi".
- Genuinely different types must NOT be linked ("voi" is not "margariini",
  "maito" is not "kerma"). When unsure, use lower confidence or null.
- NEVER match on brand-name substrings. "Voi" (butter) is not "Voileipäkeksi".
- Prefer the DEEPEST fitting node: a variant like "cheddar" over its parent
  "Juusto".

Products to place (JSON):
{products_json}

Existing nodes (JSON; parent_id != null means the node is itself a variant
under that parent):
{nodes_json}

Return ONLY a JSON array:
[{{"product_id": <id>, "parent_id": <node id or null>, "confidence": "high"|"medium"|"low"}}]
"""


def _norm(name: str) -> str:
    s = (name or "").replace("\xa0", " ").replace("‑", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def apply_link(conn: sqlite3.Connection, product_id: int, parent_id: int, *, note: str = "") -> None:
    """Set parent_id after cycle validation and record a 'link' history event.
    Runs under the catalog lock."""
    with CATALOG_LOCK:
        tree.prepare_parent(conn, product_id, parent_id)
        conn.execute(
            "UPDATE products SET parent_id = ?, updated_at = datetime('now') WHERE id = ?",
            (parent_id, product_id),
        )
        parent = conn.execute("SELECT name FROM products WHERE id = ?", (parent_id,)).fetchone()
        log_event(
            conn,
            product_id=product_id,
            event_type="link",
            amount=1,
            note=note or f"linked under {parent['name'] if parent else parent_id}",
        )
        conn.commit()


def maybe_link_after_purchase(conn: sqlite3.Connection, product_id: int) -> None:
    """Fire a background link attempt for an unparented, childless product
    when autoplace is enabled. A purchase is a fresh signal the tree may want
    it categorised. Best-effort; never raises — every caller (stock add,
    receipt commit) must be able to trust this can't fail their write."""
    try:
        prod = conn.execute(
            "SELECT parent_id, kind FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if not prod or prod["parent_id"] is not None or prod["kind"] != "sku":
            return
        from routers.products import _autoplace_enabled  # lazy: avoid import cycle
        if _autoplace_enabled(conn):
            threading.Thread(
                target=link_async, args=(product_id,), daemon=True
            ).start()
    except Exception as exc:
        log.warning("maybe_link_after_purchase(%s) failed: %s", product_id, exc)


def _candidate_nodes(conn: sqlite3.Connection) -> list[dict]:
    """Nodes a product may be placed under: every concept."""
    return [
        {"id": r["id"], "name": r["name"], "parent_id": r["parent_id"]}
        for r in conn.execute(
            "SELECT id, name, parent_id FROM products WHERE kind = 'concept'"
        ).fetchall()
    ]


# Products with a link attempt running in some thread. Create-time autoplace
# and purchase-time linking used to race on the same product and link it two
# or three times in the same second.
_inflight: set[int] = set()
_inflight_lock = threading.Lock()


def claim(product_id: int) -> bool:
    """Reserve product_id for one link attempt; False if one is running."""
    with _inflight_lock:
        if product_id in _inflight:
            return False
        _inflight.add(product_id)
        return True


def release(product_id: int) -> None:
    with _inflight_lock:
        _inflight.discard(product_id)


def link_products(conn: sqlite3.Connection, product_ids: list[int], *, use_ai: bool = True) -> dict:
    linked: list[int] = []
    proposed: list[int] = []
    unmatched: list[int] = []

    todo: list[dict] = []
    for pid in product_ids:
        row = conn.execute(
            "SELECT id, name, parent_id FROM products WHERE id = ?", (pid,)
        ).fetchone()
        if row is None or row["parent_id"] is not None:
            continue
        todo.append({"id": row["id"], "name": row["name"]})
    if not todo:
        return {"linked": linked, "proposed": proposed, "unmatched": unmatched}

    # Nodes are computed for the WHOLE catalog, not just this batch: a
    # category and its own SKU are frequently orphaned together (e.g. a full
    # reconcile sweep), and the category must remain a valid target for its
    # sibling SKU. Self-linking is guarded at match time instead (below),
    # never by removing a product from the candidate pool.
    todo_ids = {t["id"] for t in todo}
    nodes = _candidate_nodes(conn)
    by_norm: dict[str, dict] = {}
    # Pre-existing (non-batch) nodes take priority for exact-name lookups so
    # that a same-batch peer can never shadow a real node — and, in
    # particular, so a product can never resolve its own name to itself.
    for n in nodes:
        if n["id"] not in todo_ids:
            by_norm.setdefault(_norm(n["name"]), n)
    for n in nodes:
        if n["id"] in todo_ids:
            by_norm.setdefault(_norm(n["name"]), n)

    rejected = {
        (r["product_id"], r["proposed_parent_id"])
        for r in conn.execute(
            "SELECT product_id, proposed_parent_id FROM link_proposals WHERE status = 'rejected'"
        ).fetchall()
    }

    # Stage 1: exact normalized name match (duplicate stub ↔ node).
    still: list[dict] = []
    for t in todo:
        hit = by_norm.get(_norm(t["name"]))
        if hit and hit["id"] != t["id"] and (t["id"], hit["id"]) not in rejected:
            try:
                apply_link(conn, t["id"], hit["id"], note="exact name match")
                linked.append(t["id"])
                continue
            except ValueError as exc:
                log.warning("Exact link %d→%d invalid: %s", t["id"], hit["id"], exc)
        still.append(t)

    # Stage 2: AI batch.
    if still and use_ai and nodes:
        prompt = _LINK_PROMPT.format(
            products_json=json.dumps(still, ensure_ascii=False),
            nodes_json=json.dumps(nodes[:500], ensure_ascii=False),
        )
        try:
            result = call_ai_json(prompt, conn)
        except Exception as exc:
            log.warning("Linker AI call failed: %s", exc)
            result = None
        matched_ids: set[int] = set()
        if isinstance(result, list):
            valid_products = {t["id"] for t in still}
            valid_nodes = {n["id"] for n in nodes}
            for m in result:
                if not isinstance(m, dict):
                    continue
                try:
                    pid = int(m["product_id"])
                    par = m.get("parent_id")
                except (KeyError, TypeError, ValueError):
                    continue
                if par is None or pid not in valid_products or int(par) not in valid_nodes:
                    continue
                par = int(par)
                if par == pid or (pid, par) in rejected:
                    continue
                conf = str(m.get("confidence", "low")).lower()
                if conf == "high":
                    try:
                        apply_link(conn, pid, par, note="AI link (high confidence)")
                        linked.append(pid)
                        matched_ids.add(pid)
                    except ValueError as exc:
                        log.warning("AI link %d→%d invalid: %s", pid, par, exc)
                elif conf in ("medium", "low"):
                    conn.execute(
                        "INSERT OR IGNORE INTO link_proposals "
                        "(product_id, proposed_parent_id, confidence, status) "
                        "VALUES (?, ?, ?, 'pending')",
                        (pid, par, conf),
                    )
                    conn.commit()
                    proposed.append(pid)
                    matched_ids.add(pid)
        unmatched.extend(t["id"] for t in still if t["id"] not in matched_ids)
    else:
        unmatched.extend(t["id"] for t in still)

    return {"linked": linked, "proposed": proposed, "unmatched": unmatched}


def run_reconcile(conn: sqlite3.Connection) -> dict:
    """Idempotent sweep: merge twin concepts, backfill pack facts on every
    SKU, then try to link every unparented, non-staple SKU."""
    from concepts import dedup_concepts

    merged = dedup_concepts(conn)
    backfilled = 0
    all_ids = [r["id"] for r in conn.execute("SELECT id FROM products").fetchall()]
    for pid in all_ids:
        try:
            if ensure_pack_facts(conn, pid):
                backfilled += 1
        except Exception as exc:
            log.warning("Pack backfill for %d failed: %s", pid, exc)

    orphans = [
        r["id"]
        for r in conn.execute(
            """
            SELECT p.id FROM products p
            WHERE p.parent_id IS NULL
              AND p.kind = 'sku'
              AND COALESCE(p.staple, 0) = 0
              AND NOT EXISTS (SELECT 1 FROM link_proposals lp
                              WHERE lp.product_id = p.id AND lp.status = 'pending')
            """
        ).fetchall()
    ]
    res = link_products(conn, orphans)
    return {
        "examined": len(orphans),
        "linked": len(res["linked"]),
        "proposed": len(res["proposed"]),
        "facts_backfilled": backfilled,
        "merged": len(merged),
    }


def link_async(product_id: int) -> None:
    """Background variant with its own connection (thread-safe). Skips the
    product when another link attempt for it is already running."""
    if not claim(product_id):
        return
    try:
        from main import DB_PATH
        from database import get_db

        conn = get_db(DB_PATH)
        try:
            link_products(conn, [product_id])
        finally:
            conn.close()
    except Exception as exc:
        log.warning("Async link for product %d failed: %s", product_id, exc)
    finally:
        release(product_id)
