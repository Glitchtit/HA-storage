"""Clear shopping rows when something is bought (spec §4.8).

A purchase of a SKU applies to the open, manual rows of that SKU and of every
concept above it: its own rows first, then the nearest concept's, oldest
first within each. The purchase is converted into each row's unit with the
bought SKU's facts, so a 2 kg flour bag covers "5 dl vehnäjauho" and a
10-egg carton covers "6 kpl kananmuna"; without a pack size one package
counts as one piece. A row the purchase cannot be converted into is
satisfied whole and uses the purchase up. Rows of a pinned product
(pin_brand) above the bought SKU wait for an exact purchase. Minimum-stock
rows follow stock levels instead (sync_auto_shopping).
"""
from __future__ import annotations

import sqlite3

from facts import Catalog
from quantity import convert
from recipe_rows import row_unit

EPS = 1e-9


def apply_purchase(
    conn: sqlite3.Connection, cat: Catalog, product_id: int, amount: float, unit_id: int | None,
) -> list[dict]:
    """Apply `amount` of `unit_id` (None = the product's own unit) of a bought
    product to the list. Returns the touched rows as {id, product_id, removed,
    amount}, where amount is what the row still needs. Opens a write
    transaction when none is open and does not commit."""
    product = cat.product(product_id)
    unit = cat.unit(unit_id) if unit_id is not None else None
    if unit is None and product is not None:
        unit = cat.unit(product["unit_id"])
    if product is None or unit is None or float(amount) <= EPS:
        return []
    if not conn.in_transaction:
        # Hold the write lock while reading the rows, so purchases cleared at
        # the same moment never start from the same amounts. The caller ends
        # the transaction.
        conn.execute("BEGIN IMMEDIATE")

    ancestors = cat.ancestors(product_id)
    pinned: set[int] = set()
    if ancestors:
        marks = ",".join("?" * len(ancestors))
        pinned = {r["id"] for r in conn.execute(
            f"SELECT id FROM products WHERE pin_brand = 1 AND id IN ({marks})", ancestors,
        ).fetchall()}
    chain = [product_id] + [a for a in ancestors if a not in pinned]
    distance = {pid: i for i, pid in enumerate(chain)}
    marks = ",".join("?" * len(chain))
    rows = conn.execute(
        "SELECT id, product_id, amount, unit_id, created_at FROM shopping_list "
        f"WHERE done = 0 AND auto_added = 0 AND product_id IN ({marks})", chain,
    ).fetchall()
    rows.sort(key=lambda r: (distance[r["product_id"]], r["created_at"] or "", r["id"]))

    from_dim = cat.stock_dim(product_id, unit)
    facts = cat.facts(product_id)
    left = float(amount)
    touched: list[dict] = []
    for row in rows:
        if left <= EPS:
            break
        need = float(row["amount"])
        side = row_unit(cat, row["product_id"], row["unit_id"])
        conv = convert(left, unit, side[0], facts, from_dim=from_dim, to_dim=side[1]) if side else None
        if conv is None or conv.amount <= EPS:
            taken, left = need, 0.0
        else:
            taken = min(need, conv.amount)
            left *= 1 - taken / conv.amount
        remaining = round(need - taken, 3)
        if remaining <= 0:
            conn.execute("DELETE FROM shopping_list WHERE id = ?", (row["id"],))
            touched.append({"id": row["id"], "product_id": row["product_id"], "removed": True, "amount": 0})
        else:
            conn.execute("UPDATE shopping_list SET amount = ? WHERE id = ?", (remaining, row["id"]))
            touched.append({"id": row["id"], "product_id": row["product_id"], "removed": False,
                            "amount": remaining})
    return touched
