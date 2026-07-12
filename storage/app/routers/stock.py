"""Stock operation endpoints."""

from __future__ import annotations

import logging
import sqlite3
import threading

from fastapi import APIRouter, HTTPException

from models import (
    StockAdd,
    StockConsume,
    StockCorrectPurchase,
    StockEntry,
    StockEntryWithProduct,
    StockOpen,
    StockSpoilLot,
    StockSummary,
    StockTransfer,
)
from routers.history import log_event
from routers.shopping import sync_auto_shopping, consume_shopping_for_purchase

router = APIRouter(tags=["stock"])
log = logging.getLogger(__name__)

# Canonical FIFO order: dated lots before undated, then oldest expiry first,
# then oldest purchase date, then lowest id as final tiebreaker.
_FIFO_ORDER_SQL = (
    " ORDER BY "
    " CASE WHEN best_before_date IS NULL THEN 1 ELSE 0 END, "
    " best_before_date ASC, "
    " purchased_date ASC, "
    " id ASC"
)


def _get_db():
    from main import get_connection
    return get_connection()


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


@router.get("/stock/entries", response_model=list[StockEntryWithProduct])
def list_stock_entries(expiring_within_days: int | None = None, expired: bool | None = None):
    """All stock entries joined with product name. Supports expiry filters.

    - expiring_within_days=N → entries with best_before_date on or before today+N.
      Includes already-expired entries (no lower bound) since they are more urgent
      than soon-to-expire ones.
    - expired=true → entries whose best_before_date is strictly before today.
    """
    conn = _get_db()
    where = ["s.amount > 0"]
    params: list = []
    if expired:
        where.append("s.best_before_date IS NOT NULL AND s.best_before_date < date('now')")
    elif expiring_within_days is not None:
        where.append(
            "s.best_before_date IS NOT NULL "
            "AND s.best_before_date <= date('now', '+' || ? || ' days')"
        )
        params.append(expiring_within_days)
    sql = (
        "SELECT s.*, p.name AS product_name FROM stock s "
        "JOIN products p ON p.id = s.product_id "
        "WHERE " + " AND ".join(where)
        # Mirror of _FIFO_ORDER_SQL with s. prefix for the join.
        + " ORDER BY "
        + " CASE WHEN s.best_before_date IS NULL THEN 1 ELSE 0 END, "
        + " s.best_before_date ASC, "
        + " s.purchased_date ASC, "
        + " s.id ASC"
    )
    return conn.execute(sql, params).fetchall()


@router.get("/stock/product/{product_id}", response_model=list[StockEntry])
def get_product_stock(product_id: int):
    conn = _get_db()
    if not conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone():
        raise HTTPException(404, f"Product {product_id} not found")
    return conn.execute(
        "SELECT * FROM stock WHERE product_id = ?" + _FIFO_ORDER_SQL,
        (product_id,),
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
        loc = conn.execute("SELECT id FROM locations LIMIT 1").fetchone()
        location_id = loc["id"] if loc else None
    if not location_id:
        raise HTTPException(400, "No location specified and no default location exists")

    # Anchor date: explicit override, else today.
    purchased_date = body.purchased_date
    if not purchased_date:
        row = conn.execute("SELECT date('now') as d").fetchone()
        purchased_date = row["d"]

    # best_before_days is authoritative. If the user supplied a best_before_date,
    # convert it into a per-lot bb_days value (which may be 0 or negative — those
    # are still valid: "expires today" or "already expired on import") and store
    # the override date as-is. Otherwise snapshot the product's default and derive
    # the date from (purchased_date, bb_days). The displayed expiry always equals
    # purchased_date + best_before_days.
    if body.best_before_date:
        diff = conn.execute(
            "SELECT CAST(julianday(?) - julianday(?) AS INTEGER) AS d",
            (body.best_before_date, purchased_date),
        ).fetchone()
        bb_days = int(diff["d"])
        best_before = body.best_before_date
    else:
        bb_days = int(product["default_best_before_days"] or 0)
        if bb_days > 0:
            row = conn.execute(
                "SELECT date(?, '+' || ? || ' days') as d",
                (purchased_date, bb_days),
            ).fetchone()
            best_before = row["d"]
        else:
            best_before = None

    # Snapshot the paid price onto the lot. Explicit override wins; otherwise
    # fall back to the product's current default. NULL means "price unknown".
    price_paid = body.price_paid
    if price_paid is None:
        price_paid = product.get("unit_price")

    cur = conn.execute(
        """INSERT INTO stock
              (product_id, location_id, amount, unit_id,
               best_before_date, best_before_days, purchased_date, price_paid)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (body.product_id, location_id, body.amount, unit_id,
         best_before, bb_days, purchased_date, price_paid),
    )
    log_event(
        conn,
        product_id=body.product_id,
        event_type="purchase",
        amount=body.amount,
        unit_id=unit_id,
        location_id=location_id,
        stock_id=cur.lastrowid,
        note=body.note,
        unit_price=price_paid,
    )
    conn.commit()
    log.info("Added %.1f to stock for product %d (purchased=%s, bb_days=%d).",
             body.amount, body.product_id, purchased_date, bb_days)
    # Best-effort background linking: a purchase of a still-unparented, still-
    # childless product is a fresh signal the tree may want it categorised.
    prod = conn.execute(
        """
        SELECT p.parent_id,
               EXISTS(SELECT 1 FROM products c WHERE c.parent_id = p.id) AS has_children
        FROM products p WHERE p.id = ?
        """,
        (body.product_id,),
    ).fetchone()
    if prod and prod["parent_id"] is None and not prod["has_children"]:
        from routers.products import _autoplace_enabled
        if _autoplace_enabled(conn):
            import linker
            threading.Thread(
                target=linker.link_async, args=(body.product_id,), daemon=True
            ).start()
    entry = conn.execute("SELECT * FROM stock WHERE id = ?", (cur.lastrowid,)).fetchone()
    consume_shopping_for_purchase(conn, body.product_id, body.amount, unit_id)
    sync_auto_shopping(conn)
    return entry


@router.post("/stock/consume", status_code=200)
def consume_stock(body: StockConsume):
    """Consume from oldest stock entries (FIFO by best_before_date)."""
    conn = _get_db()
    entries = conn.execute(
        "SELECT * FROM stock WHERE product_id = ? AND amount > 0" + _FIFO_ORDER_SQL,
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

    # Aggregate consume/spoil event (single row per request, matching request intent).
    # For spoil events without a specific lot context, snapshot the product's current
    # unit_price so monetary waste tracking has a value to multiply against.
    event_price = None
    if body.spoiled:
        prod_row = conn.execute(
            "SELECT unit_price FROM products WHERE id = ?", (body.product_id,)
        ).fetchone()
        event_price = prod_row.get("unit_price") if prod_row else None
    log_event(
        conn,
        product_id=body.product_id,
        event_type="spoil" if body.spoiled else "consume",
        amount=consumed,
        note=body.note,
        unit_price=event_price,
    )
    conn.commit()

    log.info("Consumed %.1f from product %d (%.1f remaining to consume).",
             consumed, body.product_id, remaining)
    sync_auto_shopping(conn)
    return {"consumed": consumed, "remaining_to_consume": remaining}


@router.post("/stock/correct-purchase", status_code=200)
def correct_purchase(body: StockCorrectPurchase):
    """Undo an over-scan from the current shopping session.

    Unlike /stock/consume (FIFO + logs a 'consume' event), a correction targets
    what was *just added*: it removes stock LIFO (newest lots first) and reduces
    the matching recent 'purchase' history events instead of recording a
    consumption. The net result reads as a clean purchase with no phantom
    consume row. Used by HA-stock's shopping-mode swipe-down on recents.
    """
    conn = _get_db()

    # 1. Reverse stock, newest lots first — the inverse of the scan's add.
    entries = conn.execute(
        "SELECT * FROM stock WHERE product_id = ? AND amount > 0 ORDER BY id DESC",
        (body.product_id,),
    ).fetchall()
    remaining = body.amount
    corrected = 0.0
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
        corrected += take

    if corrected == 0:
        conn.commit()
        raise HTTPException(400, f"No stock available for product {body.product_id}")

    # 2. Reduce recent 'purchase' history events by the corrected amount, newest
    #    first; delete any event that reaches zero. No consume event is written.
    to_reverse = corrected
    purchases = conn.execute(
        "SELECT id, amount FROM stock_history "
        "WHERE product_id = ? AND event_type = 'purchase' "
        "ORDER BY created_at DESC, id DESC",
        (body.product_id,),
    ).fetchall()
    for p in purchases:
        if to_reverse <= 0:
            break
        take = min(to_reverse, p["amount"])
        new_amount = p["amount"] - take
        if new_amount <= 0:
            conn.execute("DELETE FROM stock_history WHERE id = ?", (p["id"],))
        else:
            conn.execute("UPDATE stock_history SET amount = ? WHERE id = ?", (new_amount, p["id"]))
        to_reverse -= take

    # 3. Defensive: if purchase events ran out (should not happen mid-session),
    #    log the unreversed remainder as a normal consume so the books balance.
    if to_reverse > 0:
        log_event(
            conn,
            product_id=body.product_id,
            event_type="consume",
            amount=to_reverse,
            note=body.note or "correction (no matching purchase)",
        )

    conn.commit()
    log.info("Corrected %.1f purchase units for product %d.", corrected, body.product_id)
    sync_auto_shopping(conn)
    return {"corrected": corrected, "remaining": remaining}


@router.post("/stock/open", status_code=200)
def open_stock(body: StockOpen):
    """Mark units as opened (FIFO)."""
    conn = _get_db()
    entries = conn.execute(
        "SELECT * FROM stock WHERE product_id = ? AND (amount - amount_opened) > 0"
        + _FIFO_ORDER_SQL,
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

    if opened > 0:
        log_event(
            conn,
            product_id=body.product_id,
            event_type="open",
            amount=opened,
            note=body.note,
        )
    conn.commit()
    return {"opened": opened}


@router.post("/stock/transfer", status_code=200)
def transfer_stock(body: StockTransfer):
    """Move stock between locations."""
    conn = _get_db()
    entries = conn.execute(
        "SELECT * FROM stock WHERE product_id = ? AND location_id = ? AND amount > 0"
        + _FIFO_ORDER_SQL,
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

        # Create new entry at destination — carry the audit snapshot along.
        conn.execute(
            """INSERT INTO stock (product_id, location_id, amount, amount_opened, unit_id,
               best_before_date, best_before_days, purchased_date, price_paid)
               VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?)""",
            (body.product_id, body.to_location_id, take, entry["unit_id"],
             entry["best_before_date"], entry["best_before_days"], entry["purchased_date"],
             entry.get("price_paid")),
        )
        remaining -= take
        transferred += take

    if transferred > 0:
        log_event(
            conn,
            product_id=body.product_id,
            event_type="transfer",
            amount=transferred,
            location_id=body.to_location_id,
            from_location_id=body.from_location_id,
            note=body.note,
        )
    conn.commit()
    return {"transferred": transferred}


@router.delete("/stock/{entry_id}", status_code=204)
def delete_stock_entry(entry_id: int, reason: str | None = None):
    """Delete a stock entry. If reason is supplied (e.g., 'spoiled'), log a
    spoil history event with the deleted amount."""
    conn = _get_db()
    entry = conn.execute("SELECT * FROM stock WHERE id = ?", (entry_id,)).fetchone()
    if not entry:
        raise HTTPException(404, f"Stock entry {entry_id} not found")
    conn.execute("DELETE FROM stock WHERE id = ?", (entry_id,))
    if reason:
        prod_row = conn.execute(
            "SELECT unit_price FROM products WHERE id = ?", (entry["product_id"],)
        ).fetchone()
        snap_price = entry.get("price_paid") or (prod_row.get("unit_price") if prod_row else None)
        log_event(
            conn,
            product_id=entry["product_id"],
            event_type="spoil",
            amount=entry["amount"],
            unit_id=entry["unit_id"],
            location_id=entry["location_id"],
            stock_id=entry_id,
            note=reason,
            unit_price=snap_price,
        )
    conn.commit()
    sync_auto_shopping(conn)


@router.post("/stock/spoil/{lot_id}", status_code=200)
def spoil_lot(lot_id: int, body: StockSpoilLot):
    """Spoil a specific stock lot (not FIFO). If amount is null, spoil the whole lot.
    Larger-than-lot amounts are clamped to the lot's remaining amount."""
    conn = _get_db()
    entry = conn.execute("SELECT * FROM stock WHERE id = ?", (lot_id,)).fetchone()
    if not entry:
        raise HTTPException(404, f"Stock entry {lot_id} not found")

    requested = entry["amount"] if body.amount is None else float(body.amount)
    spoiled = min(requested, entry["amount"])
    if spoiled <= 0:
        return {"spoiled": 0}

    new_amount = entry["amount"] - spoiled
    if new_amount <= 0:
        conn.execute("DELETE FROM stock WHERE id = ?", (lot_id,))
    else:
        conn.execute("UPDATE stock SET amount = ? WHERE id = ?", (new_amount, lot_id))

    note = body.note
    if entry["best_before_date"]:
        suffix = f"lot bb={entry['best_before_date']}"
        note = f"{note} ({suffix})" if note else suffix

    prod_row = conn.execute(
        "SELECT unit_price FROM products WHERE id = ?", (entry["product_id"],)
    ).fetchone()
    snap_price = entry.get("price_paid") or (prod_row.get("unit_price") if prod_row else None)
    log_event(
        conn,
        product_id=entry["product_id"],
        event_type="spoil",
        amount=spoiled,
        unit_id=entry["unit_id"],
        location_id=entry["location_id"],
        stock_id=lot_id,
        note=note,
        unit_price=snap_price,
    )
    conn.commit()
    sync_auto_shopping(conn)
    log.info("Spoiled %.1f from lot %d (product %d).", spoiled, lot_id, entry["product_id"])
    return {"spoiled": spoiled}
