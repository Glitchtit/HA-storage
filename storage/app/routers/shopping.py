"""Shopping list endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from consumption_stats import compute_proposal
from models import (
    ShoppingItem,
    ShoppingItemCreate,
    ShoppingItemUpdate,
    ShoppingProposalResponse,
)

router = APIRouter(tags=["shopping-list"])
log = logging.getLogger(__name__)

# Treat row amounts within this tolerance of zero as fully consumed.
# Subtracting fractional amounts across rows accumulates IEEE-754
# residues that can leave near-zero positives; this epsilon snaps them
# to "done".
_AMOUNT_EPSILON = 1e-9


def _get_db():
    from main import get_connection
    return get_connection()


def sync_auto_shopping(conn) -> dict:
    """Reconcile auto-added shopping list rows against current stock levels.

    For every active product with `min_stock_amount > 0`:

    * If current total stock is **below** the threshold and the product has no
      shopping_list row at all, insert one with ``auto_added = 1``.
    * If current total stock is **at or above** the threshold, delete any
      existing ``auto_added = 1`` row that is **not yet done** — the user has
      already restocked, so the auto-added entry is stale.

    Rows the user added manually (``auto_added = 0``) are never removed here,
    and rows that are already ``done = 1`` are preserved so we don't yank
    items out from under an active shopping trip.

    Returns a dict with ``added`` and ``removed`` counts for callers/logging.
    """
    rows = conn.execute(
        """
        SELECT p.id, p.name, p.unit_id, p.min_stock_amount,
               COALESCE(SUM(s.amount), 0) AS total_amount
        FROM products p
        LEFT JOIN stock s ON s.product_id = p.id
        WHERE p.active = 1 AND p.min_stock_amount > 0
        GROUP BY p.id
        """
    ).fetchall()

    added = 0
    removed = 0
    for r in rows:
        pid = r["id"]
        min_amt = float(r["min_stock_amount"] or 0)
        have = float(r["total_amount"] or 0)
        if have < min_amt:
            existing = conn.execute(
                "SELECT 1 FROM shopping_list WHERE product_id = ? LIMIT 1",
                (pid,),
            ).fetchone()
            if existing:
                continue
            need = max(1.0, min_amt - have)
            conn.execute(
                "INSERT INTO shopping_list (product_id, amount, unit_id, ha_item_name, auto_added)"
                " VALUES (?, ?, ?, ?, 1)",
                (pid, need, r["unit_id"], r["name"]),
            )
            added += 1
        else:
            cur = conn.execute(
                "DELETE FROM shopping_list WHERE product_id = ? AND auto_added = 1 AND done = 0",
                (pid,),
            )
            if cur.rowcount:
                removed += cur.rowcount

    if added or removed:
        conn.commit()
        log.info("Shopping auto-sync: added=%d, removed=%d", added, removed)
    return {"added": added, "removed": removed}


def consume_shopping_for_purchase(
    conn,
    product_id: int,
    amount: float,
    unit_id: int | None,
) -> list[int]:
    """Decrement manual shopping rows when stock is purchased for `product_id`.

    Targets the disjoint complement of `sync_auto_shopping`: matches only
    non-done **manual** rows (`auto_added = 0`, `done = 0`) for the given
    product. A row's `unit_id` matches when it is NULL ("no preference" —
    the common case, since the HA-stock frontend never sends a unit_id on
    manual add) or equals the purchase's `unit_id`. A row that does specify
    a unit is only consumed by a purchase in that same unit.

    Iterates oldest-first (`created_at ASC`, with `id ASC` as a tiebreaker
    because `created_at` is a TEXT column with one-second resolution and two
    rows inserted in the same second would otherwise have unspecified order),
    subtracting `amount` from each
    matching row. A row whose new amount is `<= 0` is hard-deleted and the
    leftover (the negation of `new_amount`) spills into the next row. Rows
    whose new amount stays `> 0` are updated in place.

    Returns the list of affected row ids (deleted or updated). The helper
    commits only if at least one row changed, matching `sync_auto_shopping`.
    """
    rows = conn.execute(
        """
        SELECT id, amount FROM shopping_list
        WHERE product_id = ?
          AND auto_added = 0
          AND done = 0
          AND (unit_id IS NULL OR unit_id = ?)
        ORDER BY created_at ASC, id ASC
        """,
        (product_id, unit_id),
    ).fetchall()

    remaining = float(amount)
    affected: list[int] = []
    for row in rows:
        if remaining <= 0:
            break
        new_amount = float(row["amount"]) - remaining
        if new_amount <= _AMOUNT_EPSILON:
            conn.execute("DELETE FROM shopping_list WHERE id = ?", (row["id"],))
            remaining = -new_amount  # spill leftover into next row
        else:
            conn.execute(
                "UPDATE shopping_list SET amount = ? WHERE id = ?",
                (new_amount, row["id"]),
            )
            remaining = 0
        affected.append(row["id"])

    if affected:
        conn.commit()
        log.info(
            "Shopping clear-on-purchase: product=%d, amount=%.3f, affected=%d",
            product_id, amount, len(affected),
        )
    return affected


@router.get("/shopping-list", response_model=list[ShoppingItem])
def list_shopping():
    return _get_db().execute(
        "SELECT * FROM shopping_list ORDER BY done, created_at DESC"
    ).fetchall()


@router.get("/shopping-list/proposal", response_model=ShoppingProposalResponse)
def shopping_proposal(
    lookback_weeks: int = Query(8, ge=1, le=52),
    horizon_days: int = Query(7, ge=1, le=30),
):
    """Predictive shopping proposal based on consumption velocity.

    For every active product with ``min_stock_amount > 0``, compute the mean
    weekly consume rate over the last ``lookback_weeks``. If the product is
    predicted to deplete within ``horizon_days`` and is not already on the
    shopping list, include it in the proposal sorted by urgency.
    """
    items = compute_proposal(
        _get_db(),
        lookback_weeks=lookback_weeks,
        horizon_days=horizon_days,
    )
    return {
        "lookback_weeks": lookback_weeks,
        "horizon_days": horizon_days,
        "proposal": items,
    }


@router.post("/shopping-list/sync", status_code=200)
def sync_shopping():
    """Reconcile auto-added rows with current stock — adds rows for products
    that fell below their `min_stock_amount` and removes auto-added rows for
    products that are now back at or above the threshold."""
    return sync_auto_shopping(_get_db())


@router.delete("/shopping-list/done", status_code=204)
def clear_done():
    """Clear all completed items."""
    conn = _get_db()
    conn.execute("DELETE FROM shopping_list WHERE done = 1")
    conn.commit()


@router.post("/shopping-list", response_model=ShoppingItem, status_code=201)
def add_shopping_item(body: ShoppingItemCreate):
    conn = _get_db()
    product = conn.execute("SELECT id, name FROM products WHERE id = ?", (body.product_id,)).fetchone()
    if not product:
        raise HTTPException(400, f"Product {body.product_id} not found")
    cur = conn.execute(
        "INSERT INTO shopping_list (product_id, amount, unit_id, note, recipe_id, ha_item_name, auto_added)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (body.product_id, body.amount, body.unit_id, body.note, body.recipe_id, product["name"], int(body.auto_added)),
    )
    conn.commit()
    return conn.execute("SELECT * FROM shopping_list WHERE id = ?", (cur.lastrowid,)).fetchone()


@router.put("/shopping-list/{item_id}", response_model=ShoppingItem)
def update_shopping_item(item_id: int, body: ShoppingItemUpdate):
    conn = _get_db()
    existing = conn.execute("SELECT * FROM shopping_list WHERE id = ?", (item_id,)).fetchone()
    if not existing:
        raise HTTPException(404, f"Shopping list item {item_id} not found")

    updates = {}
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "done":
            value = int(value)
        updates[field] = value
    if not updates:
        return existing
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE shopping_list SET {set_clause} WHERE id = ?",
        list(updates.values()) + [item_id],
    )
    conn.commit()
    return conn.execute("SELECT * FROM shopping_list WHERE id = ?", (item_id,)).fetchone()


@router.delete("/shopping-list/{item_id}", status_code=204)
def delete_shopping_item(item_id: int):
    conn = _get_db()
    if not conn.execute("SELECT id FROM shopping_list WHERE id = ?", (item_id,)).fetchone():
        raise HTTPException(404, f"Shopping list item {item_id} not found")
    conn.execute("DELETE FROM shopping_list WHERE id = ?", (item_id,))
    conn.commit()
