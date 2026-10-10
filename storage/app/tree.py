"""Recursive product-tree helpers.

The products.parent_id chain is arbitrary depth (category -> variant -> SKU).
UNION (not UNION ALL) so a pre-existing cycle in data terminates instead of
looping forever.
"""
from __future__ import annotations

import sqlite3


def descendant_ids(conn: sqlite3.Connection, product_id: int) -> list[int]:
    """All descendants of product_id (children, grandchildren, ...), excluding self."""
    rows = conn.execute(
        """
        WITH RECURSIVE d(id) AS (
            SELECT id FROM products WHERE parent_id = ?
            UNION
            SELECT p.id FROM products p JOIN d ON p.parent_id = d.id
        )
        SELECT id FROM d
        """,
        (product_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def ancestor_ids(conn: sqlite3.Connection, product_id: int) -> list[int]:
    """All ancestors of product_id (parent, grandparent, ...), excluding self."""
    rows = conn.execute(
        """
        WITH RECURSIVE a(id) AS (
            SELECT parent_id FROM products WHERE id = ? AND parent_id IS NOT NULL
            UNION
            SELECT p.parent_id FROM products p JOIN a ON p.id = a.id
            WHERE p.parent_id IS NOT NULL
        )
        SELECT id FROM a
        """,
        (product_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def prepare_parent(
    conn: sqlite3.Connection, product_id: int | None, parent_id: int | None
) -> None:
    """Validate linking product_id under parent_id and enforce the role rule.

    Parents are concepts. A parent that is still a plain SKU — no stock lots
    and no barcodes — is promoted to kind='concept'; that keeps API clients
    that create a category product before its children working. A SKU that
    holds stock or barcodes is a real retail item and is rejected.

    Raises ValueError for self-parenting, a missing parent, a cycle, or a
    stocked/barcoded SKU parent. product_id may be None (product being
    created). Does not commit: the caller's write commits the promotion.
    """
    if parent_id is None:
        return
    if product_id is not None and int(parent_id) == int(product_id):
        raise ValueError("Product cannot be its own parent")
    parent = conn.execute(
        "SELECT id, name, kind FROM products WHERE id = ?", (parent_id,)
    ).fetchone()
    if not parent:
        raise ValueError(f"Parent product {parent_id} not found")
    if product_id is not None and int(product_id) in set(ancestor_ids(conn, int(parent_id))):
        raise ValueError("Parent chain would form a cycle")
    if parent["kind"] == "concept":
        return
    held = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM stock WHERE product_id = ? AND amount > 0) "
        "    OR EXISTS(SELECT 1 FROM barcodes WHERE product_id = ?) AS held",
        (parent_id, parent_id),
    ).fetchone()["held"]
    if held:
        raise ValueError(
            f"'{parent['name']}' is a stocked product and cannot have sub-products"
        )
    conn.execute("UPDATE products SET kind = 'concept' WHERE id = ?", (parent_id,))
