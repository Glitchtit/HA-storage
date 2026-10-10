"""Merge one product into another without losing any reference.

Used by POST /products/{id}/merge, the optimizer's multi-pack normalisation
and the concept dedup sweep. Everything that points at the source — recipe
ingredients, shopping rows, children, lots, barcodes, history, store
availability, bundle items, legacy conversion rows, aliases — is re-pointed at
the target, and a concept target learns the source's name as an alias. Recipe
ingredients are never de-duplicated: the same product twice in one recipe
(butter for the dough and for frying) is legitimate. Facts and flags the
target lacks are copied over (package facts only onto a SKU), then the source
row is deleted. Runs under the catalog lock.
"""
from __future__ import annotations

import json
import sqlite3

import tree
from concepts import add_alias
from locks import CATALOG_LOCK
from routers.history import log_event

_FILL_COLUMNS = (
    "pack_count", "density_g_ml", "piece_weight_g", "picture_filename",
    "unit_price", "location_id", "product_group_id",
)
# Facts that describe one retail package: never copied onto a concept.
_PACKAGE_COLUMNS = frozenset({"pack_count", "unit_price"})
_FACT_KEYS = {"pack_count": "pack_count", "density_g_ml": "density_g_ml", "piece_weight_g": "piece_weight_g"}


def _meta(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def merge_products(conn: sqlite3.Connection, source_id: int, target_id: int) -> dict:
    """Merge source into target. Raises LookupError (missing product) or
    ValueError (merge would break the tree or the concept/SKU roles).
    Commits on success, rolls back on failure."""
    with CATALOG_LOCK:
        return _merge(conn, source_id, target_id)


def _merge(conn: sqlite3.Connection, source_id: int, target_id: int) -> dict:
    if source_id == target_id:
        raise ValueError("Cannot merge a product into itself")
    src = conn.execute("SELECT * FROM products WHERE id = ?", (source_id,)).fetchone()
    tgt = conn.execute("SELECT * FROM products WHERE id = ?", (target_id,)).fetchone()
    if not src or not tgt:
        raise LookupError("Product not found")
    if target_id in tree.descendant_ids(conn, source_id):
        raise ValueError("Cannot merge a product into one of its own sub-products")
    has_children = conn.execute(
        "SELECT 1 FROM products WHERE parent_id = ? LIMIT 1", (source_id,)
    ).fetchone()
    if has_children and tgt["kind"] != "concept":
        raise ValueError(f"'{tgt['name']}' is a stocked product and cannot take over sub-products")
    holds = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM stock WHERE product_id = ?) "
        "    OR EXISTS(SELECT 1 FROM barcodes WHERE product_id = ?) AS held",
        (source_id, source_id),
    ).fetchone()["held"]
    if holds and tgt["kind"] == "concept":
        raise ValueError(
            f"'{src['name']}' holds stock or barcodes; place it under '{tgt['name']}' instead of merging"
        )

    moved: dict[str, int] = {}
    try:
        def repoint(table: str, column: str = "product_id") -> None:
            cur = conn.execute(
                f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (target_id, source_id)
            )
            moved[table] = cur.rowcount

        def repoint_unique(table: str) -> None:
            cur = conn.execute(
                f"UPDATE OR IGNORE {table} SET product_id = ? WHERE product_id = ?",
                (target_id, source_id),
            )
            moved[table] = cur.rowcount
            conn.execute(f"DELETE FROM {table} WHERE product_id = ?", (source_id,))

        repoint("recipe_ingredients")
        cur = conn.execute(
            "UPDATE shopping_list SET product_id = ?, ha_item_name = ? WHERE product_id = ?",
            (target_id, tgt["name"], source_id),
        )
        moved["shopping_list"] = cur.rowcount
        repoint("shopping_list", "preferred_product_id")
        repoint("stock")
        repoint("barcodes")
        repoint("stock_history")
        repoint("barcode_queue", "result_product_id")
        cur = conn.execute(
            "UPDATE products SET parent_id = ? WHERE parent_id = ?", (target_id, source_id)
        )
        moved["children"] = cur.rowcount
        repoint_unique("product_availability")
        repoint_unique("bundle_items")
        repoint_unique("unit_conversions")
        conn.execute(
            "DELETE FROM link_proposals WHERE product_id = ? OR proposed_parent_id = ?",
            (source_id, source_id),
        )
        repoint("product_aliases")
        if tgt["kind"] == "concept":
            add_alias(conn, src["name"], target_id, "merge")

        fill: dict = {}
        meta = _meta(tgt["facts_meta"])
        src_meta = _meta(src["facts_meta"])
        is_sku = tgt["kind"] == "sku"
        for col in _FILL_COLUMNS:
            if col in _PACKAGE_COLUMNS and not is_sku:
                continue
            if tgt[col] is None and src[col] is not None:
                fill[col] = src[col]
                if col in _FACT_KEYS and col in src_meta:
                    meta[_FACT_KEYS[col]] = src_meta[col]
        if is_sku and tgt["content_amount"] is None and src["content_amount"] is not None:
            fill["content_amount"] = src["content_amount"]
            fill["content_unit_id"] = src["content_unit_id"]
            if "content" in src_meta:
                meta["content"] = src_meta["content"]
        fill["min_stock_amount"] = max(tgt["min_stock_amount"] or 0, src["min_stock_amount"] or 0)
        fill["pin_brand"] = max(tgt["pin_brand"] or 0, src["pin_brand"] or 0)
        fill["staple"] = max(tgt["staple"] or 0, src["staple"] or 0)
        fill["facts_meta"] = json.dumps(meta, sort_keys=True) if meta else None
        conn.execute(
            f"UPDATE products SET {', '.join(f'{k} = ?' for k in fill)}, "
            f"updated_at = datetime('now') WHERE id = ?",
            [*fill.values(), target_id],
        )
        log_event(conn, product_id=target_id, event_type="link", amount=1,
                  note=f"merged from {src['name']}")
        conn.execute("DELETE FROM products WHERE id = ?", (source_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"target_id": target_id, "moved": moved}
