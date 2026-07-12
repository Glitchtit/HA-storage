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


def assert_valid_parent(
    conn: sqlite3.Connection, product_id: int | None, parent_id: int | None
) -> None:
    """Raise ValueError if linking product_id under parent_id is invalid.

    product_id may be None (a product being created — it cannot be anyone's
    ancestor yet, so only existence is checked).
    """
    if parent_id is None:
        return
    if product_id is not None and int(parent_id) == int(product_id):
        raise ValueError("Product cannot be its own parent")
    if not conn.execute("SELECT id FROM products WHERE id = ?", (parent_id,)).fetchone():
        raise ValueError(f"Parent product {parent_id} not found")
    if product_id is not None and int(product_id) in set(ancestor_ids(conn, int(parent_id))):
        raise ValueError("Parent chain would form a cycle")
