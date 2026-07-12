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

from ai_client import call_ai_json
from pack_size import ensure_pack_conversions, parse_pack_size
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
    """Set parent_id after cycle validation and record a 'link' history event."""
    tree.assert_valid_parent(conn, product_id, parent_id)
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


def _candidate_nodes(conn: sqlite3.Connection, exclude: set[int]) -> list[dict]:
    """Nodes a product may be placed under: anything with children, anything in
    the 'Group master' group, and unparented products without a size token in
    the name (i.e. category-shaped, not SKU-shaped)."""
    rows = conn.execute(
        """
        SELECT p.id, p.name, p.parent_id,
               EXISTS(SELECT 1 FROM products c WHERE c.parent_id = p.id) AS has_children,
               (SELECT 1 FROM product_groups g
                 WHERE g.id = p.product_group_id AND g.name = 'Group master') AS is_gm
        FROM products p
        """
    ).fetchall()
    nodes = []
    for r in rows:
        if r["id"] in exclude:
            continue
        sized = parse_pack_size(r["name"])
        looks_like_sku = sized["amount"] is not None or sized["count"] is not None
        if r["has_children"] or r["is_gm"] or (r["parent_id"] is None and not looks_like_sku):
            nodes.append({"id": r["id"], "name": r["name"], "parent_id": r["parent_id"]})
    return nodes


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
    nodes = _candidate_nodes(conn, set())
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
    """Idempotent sweep: backfill pack conversions everywhere, then try to link
    every unparented, childless, non-staple product that is not itself a
    category node."""
    conversions = 0
    all_ids = [r["id"] for r in conn.execute("SELECT id FROM products").fetchall()]
    for pid in all_ids:
        try:
            if ensure_pack_conversions(conn, pid):
                conversions += 1
        except Exception as exc:
            log.warning("Pack backfill for %d failed: %s", pid, exc)

    orphans = [
        r["id"]
        for r in conn.execute(
            """
            SELECT p.id FROM products p
            WHERE p.parent_id IS NULL
              AND COALESCE(p.staple, 0) = 0
              AND NOT EXISTS (SELECT 1 FROM products c WHERE c.parent_id = p.id)
              AND NOT EXISTS (SELECT 1 FROM product_groups g
                              WHERE g.id = p.product_group_id AND g.name = 'Group master')
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
        "conversions_backfilled": conversions,
    }


def link_async(product_id: int) -> None:
    """Background variant with its own connection (thread-safe)."""
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
