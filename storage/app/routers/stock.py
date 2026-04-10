"""Stock operation endpoints."""

from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, HTTPException

from models import StockAdd, StockConsume, StockEntry, StockOpen, StockSummary, StockTransfer

router = APIRouter(tags=["stock"])
log = logging.getLogger(__name__)


def _get_db():
    from main import get_connection
    return get_connection()


def _sync_shopping_list(conn: sqlite3.Connection, product_id: int) -> None:
    """Auto-manage shopping list based on stock vs min_stock_amount.

    - If stock < min_stock_amount: ensure an auto-added item exists and is on the HA list.
    - If stock >= min_stock_amount: remove ALL shopping list items for this product (auto and manual).
    """
    import ha_sync

    product = conn.execute(
        "SELECT name, min_stock_amount, unit_id FROM products WHERE id = ?", (product_id,)
    ).fetchone()
    if not product:
        return

    min_amount = product["min_stock_amount"] or 0
    if min_amount <= 0:
        return

    total = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) as t FROM stock WHERE product_id = ?", (product_id,)
    ).fetchone()["t"]

    existing = conn.execute(
        "SELECT id, ha_item_name FROM shopping_list WHERE product_id = ? AND done = 0",
        (product_id,),
    ).fetchall()

    if total < min_amount:
        if not existing:
            ha_sync.ha_ensure_entity(conn)
            item_name = product["name"]
            desc = f"Min: {min_amount}"
            conn.execute(
                "INSERT INTO shopping_list (product_id, amount, unit_id, note, auto_added, ha_item_name)"
                " VALUES (?, ?, ?, ?, 1, ?)",
                (product_id, min_amount, product["unit_id"], "", item_name),
            )
            conn.commit()
            ha_sync.ha_add_item(conn, item_name, desc)
            log.info("Auto-added '%s' to shopping list (stock %.2f < min %.2f).",
                     product["name"], total, min_amount)
    else:
        if existing:
            for item in existing:
                name = item["ha_item_name"] or product["name"]
                ha_sync.ha_remove_item(conn, name)
            conn.execute(
                "DELETE FROM shopping_list WHERE product_id = ? AND done = 0", (product_id,)
            )
            conn.commit()
            log.info("Removed %d shopping list item(s) for '%s' (stock %.2f >= min %.2f).",
                     len(existing), product["name"], total, min_amount)


@router.get("/stock", response_model=list[StockSummary])
def list_stock():
    """Aggregated stock per product."""
    conn = _get_db()
    rows = conn.execute("""
        SELECT p.*, COALESCE(SUM(s.amount), 0) as total_amount,
               COALESCE(SUM(s.amount_opened), 0) as total_opened
        FROM products p
        LEFT JOIN stock s ON s.product_id = p.id
        WHERE p.active = 1
        GROUP BY p.id
        HAVING total_amount > 0
        ORDER BY p.name
    """).fetchall()

    result = []
    for r in rows:
        product = {k: v for k, v in r.items() if k not in ("total_amount", "total_opened")}
        result.append(StockSummary(
            product_id=r["id"],
            product_name=r["name"],
            amount=r["total_amount"],
            amount_opened=r["total_opened"],
            min_stock_amount=r["min_stock_amount"],
            product=product,
        ))
    return result


@router.get("/stock/product/{product_id}", response_model=list[StockEntry])
def get_product_stock(product_id: int):
    conn = _get_db()
    if not conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone():
        raise HTTPException(404, f"Product {product_id} not found")
    return conn.execute(
        "SELECT * FROM stock WHERE product_id = ? ORDER BY best_before_date", (product_id,)
    ).fetchall()


@router.post("/stock/add", response_model=StockEntry, status_code=201)
def add_stock(body: StockAdd):
    conn = _get_db()
    product = conn.execute("SELECT * FROM products WHERE id = ?", (body.product_id,)).fetchone()
    if not product:
        raise HTTPException(404, f"Product {body.product_id} not found")

    unit_id = body.unit_id or product["unit_id"]
    location_id = body.location_id or product["location_id"]
    if not location_id:
        # Fall back to first location
        loc = conn.execute("SELECT id FROM locations LIMIT 1").fetchone()
        location_id = loc["id"] if loc else None
    if not location_id:
        raise HTTPException(400, "No location specified and no default location exists")

    # Calculate best_before_date if not provided
    best_before = body.best_before_date
    if not best_before and product["default_best_before_days"]:
        row = conn.execute(
            "SELECT date('now', '+' || ? || ' days') as d",
            (product["default_best_before_days"],),
        ).fetchone()
        best_before = row["d"]

    cur = conn.execute(
        """INSERT INTO stock (product_id, location_id, amount, unit_id, best_before_date)
           VALUES (?, ?, ?, ?, ?)""",
        (body.product_id, location_id, body.amount, unit_id, best_before),
    )
    conn.commit()
    log.info("Added %.1f to stock for product %d.", body.amount, body.product_id)
    entry = conn.execute("SELECT * FROM stock WHERE id = ?", (cur.lastrowid,)).fetchone()
    _sync_shopping_list(conn, body.product_id)
    return entry


@router.post("/stock/consume", status_code=200)
def consume_stock(body: StockConsume):
    """Consume from oldest stock entries (FIFO by best_before_date)."""
    conn = _get_db()
    entries = conn.execute(
        "SELECT * FROM stock WHERE product_id = ? AND amount > 0 ORDER BY best_before_date ASC",
        (body.product_id,),
    ).fetchall()

    remaining = body.amount
    consumed = 0.0
    for entry in entries:
        if remaining <= 0:
            break
        take = min(remaining, entry["amount"])
        new_amount = entry["amount"] - take
        if new_amount <= 0:
            conn.execute("DELETE FROM stock WHERE id = ?", (entry["id"],))
        else:
            conn.execute("UPDATE stock SET amount = ? WHERE id = ?", (new_amount, entry["id"]))
        remaining -= take
        consumed += take

    conn.commit()
    if consumed == 0:
        raise HTTPException(400, f"No stock available for product {body.product_id}")

    log.info("Consumed %.1f from product %d (%.1f remaining to consume).",
             consumed, body.product_id, remaining)
    _sync_shopping_list(conn, body.product_id)
    return {"consumed": consumed, "remaining_to_consume": remaining}


@router.post("/stock/open", status_code=200)
def open_stock(body: StockOpen):
    """Mark units as opened (FIFO)."""
    conn = _get_db()
    entries = conn.execute(
        "SELECT * FROM stock WHERE product_id = ? AND (amount - amount_opened) > 0 "
        "ORDER BY best_before_date ASC",
        (body.product_id,),
    ).fetchall()

    remaining = body.amount
    opened = 0.0
    for entry in entries:
        if remaining <= 0:
            break
        unopened = entry["amount"] - entry["amount_opened"]
        take = min(remaining, unopened)
        conn.execute(
            "UPDATE stock SET amount_opened = amount_opened + ? WHERE id = ?",
            (take, entry["id"]),
        )
        remaining -= take
        opened += take

    conn.commit()
    return {"opened": opened}


@router.post("/stock/transfer", status_code=200)
def transfer_stock(body: StockTransfer):
    """Move stock between locations."""
    conn = _get_db()
    entries = conn.execute(
        "SELECT * FROM stock WHERE product_id = ? AND location_id = ? AND amount > 0 "
        "ORDER BY best_before_date ASC",
        (body.product_id, body.from_location_id),
    ).fetchall()

    remaining = body.amount
    transferred = 0.0
    for entry in entries:
        if remaining <= 0:
            break
        take = min(remaining, entry["amount"])
        new_amount = entry["amount"] - take
        if new_amount <= 0:
            conn.execute("DELETE FROM stock WHERE id = ?", (entry["id"],))
        else:
            conn.execute("UPDATE stock SET amount = ? WHERE id = ?", (new_amount, entry["id"]))

        # Create new entry at destination
        conn.execute(
            """INSERT INTO stock (product_id, location_id, amount, amount_opened, unit_id,
               best_before_date, purchased_date)
               VALUES (?, ?, ?, 0, ?, ?, ?)""",
            (body.product_id, body.to_location_id, take, entry["unit_id"],
             entry["best_before_date"], entry["purchased_date"]),
        )
        remaining -= take
        transferred += take

    conn.commit()
    _sync_shopping_list(conn, body.product_id)
    return {"transferred": transferred}
@router.delete("/stock/{entry_id}", status_code=204)
def delete_stock_entry(entry_id: int):
    conn = _get_db()
    entry = conn.execute("SELECT product_id FROM stock WHERE id = ?", (entry_id,)).fetchone()
    if not entry:
        raise HTTPException(404, f"Stock entry {entry_id} not found")
    product_id = entry["product_id"]
    conn.execute("DELETE FROM stock WHERE id = ?", (entry_id,))
    conn.commit()
    _sync_shopping_list(conn, product_id)
