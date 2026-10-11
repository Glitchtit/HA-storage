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

Given the lot the purchase created, every change is recorded in
`purchase_clearings`, so undoing a mis-scan (`restore_cleared`) can put the
rows back.
"""
from __future__ import annotations

import json
import sqlite3

from facts import Catalog
from quantity import convert
from recipe_rows import row_unit

EPS = 1e-9


def apply_purchase(
    conn: sqlite3.Connection, cat: Catalog, product_id: int, amount: float, unit_id: int | None,
    stock_id: int | None = None,
) -> list[dict]:
    """Apply `amount` of `unit_id` (None = the product's own unit) of a bought
    product to the list. Returns the touched rows as {id, product_id, removed,
    amount}, where amount is what the row still needs. With `stock_id` (the
    purchase's new lot) the changes are recorded for `restore_cleared`. Opens
    a write transaction when none is open and does not commit."""
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
        "SELECT * FROM shopping_list "
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
        if stock_id is not None:
            conn.execute(
                "INSERT INTO purchase_clearings (stock_id, purchase_amount, row_id, removed, taken, row_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (stock_id, float(amount), row["id"], int(remaining <= 0), need - max(remaining, 0.0),
                 json.dumps(dict(row), ensure_ascii=False)),
            )
    return touched


def _existing(conn: sqlite3.Connection, table: str, row_id: int | None) -> int | None:
    if row_id is None:
        return None
    return row_id if conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)).fetchone() else None


def restore_cleared(conn: sqlite3.Connection, stock_id: int, take: float) -> list[dict]:
    """Undo `take` of the purchase that created lot `stock_id`: the rows it
    took from get back the same share of what was taken. A row the purchase
    removed comes back with its id, note, recipe and set; a row removed by
    hand afterwards stays removed, and a ticked row or one whose unit was
    changed since (the ± stepper's packages, a swap) is left as it is.
    Returns the restored rows as {id, product_id, amount}. Does not commit."""
    restored: list[dict] = []
    for rec in conn.execute(
        "SELECT * FROM purchase_clearings WHERE stock_id = ? ORDER BY id", (stock_id,)
    ).fetchall():
        share = min(1.0, float(take) / rec["purchase_amount"]) if rec["purchase_amount"] > EPS else 1.0
        back = rec["taken"] * share
        left = rec["purchase_amount"] - float(take)
        if left <= EPS:
            conn.execute("DELETE FROM purchase_clearings WHERE id = ?", (rec["id"],))
        else:
            conn.execute("UPDATE purchase_clearings SET purchase_amount = ?, taken = ? WHERE id = ?",
                         (left, rec["taken"] - back, rec["id"]))
        if back <= EPS:
            continue
        snap = json.loads(rec["row_json"])
        row = conn.execute("SELECT * FROM shopping_list WHERE id = ?", (rec["row_id"],)).fetchone()
        if row is not None:
            if row["done"] or row["unit_id"] != snap.get("unit_id"):
                continue  # `back` is in the old unit; a changed row stays as set
            amount = round(float(row["amount"]) + back, 3)
            conn.execute("UPDATE shopping_list SET amount = ? WHERE id = ?", (amount, row["id"]))
            restored.append({"id": row["id"], "product_id": row["product_id"], "amount": amount})
            continue
        if not rec["removed"] or _existing(conn, "products", snap["product_id"]) is None:
            continue
        amount = round(back, 3)
        conn.execute(
            "INSERT INTO shopping_list (id, product_id, amount, unit_id, note, done, recipe_id, bundle_id, "
            "auto_added, ha_item_name, preferred_product_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?, 0, ?, ?, ?)",
            (snap["id"], snap["product_id"], amount, _existing(conn, "units", snap.get("unit_id")),
             snap.get("note") or "", _existing(conn, "recipes", snap.get("recipe_id")),
             _existing(conn, "bundles", snap.get("bundle_id")), snap.get("ha_item_name"),
             _existing(conn, "products", snap.get("preferred_product_id")), snap.get("created_at")),
        )
        restored.append({"id": snap["id"], "product_id": snap["product_id"], "amount": amount})
    return restored
