"""Stock history endpoints — append-only audit log for stock movements."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from models import StockHistoryEntry, StockHistoryEntryWithProduct

router = APIRouter(tags=["history"])
log = logging.getLogger(__name__)

VALID_EVENTS = {"purchase", "consume", "open", "transfer", "spoil"}


def _get_db():
    from main import get_connection
    return get_connection()


def log_event(
    conn,
    *,
    product_id: int,
    event_type: str,
    amount: float,
    unit_id: int | None = None,
    location_id: int | None = None,
    from_location_id: int | None = None,
    stock_id: int | None = None,
    note: str = "",
) -> None:
    """Insert a stock_history row. Caller is responsible for commit()."""
    if event_type not in VALID_EVENTS:
        log.warning("Ignoring invalid event_type=%s", event_type)
        return
    if amount <= 0:
        return
    conn.execute(
        "INSERT INTO stock_history "
        "(product_id, event_type, amount, unit_id, location_id, from_location_id, stock_id, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (product_id, event_type, amount, unit_id, location_id, from_location_id, stock_id, note or ""),
    )


@router.get("/history", response_model=list[StockHistoryEntryWithProduct])
def list_history(
    product_id: int | None = None,
    event_type: str | None = None,
    since: str | None = Query(None, description="ISO date or datetime, inclusive"),
    until: str | None = Query(None, description="ISO date or datetime, exclusive"),
    limit: int = Query(200, ge=1, le=1000),
):
    """List stock history events (newest first) with optional filters."""
    conn = _get_db()
    where: list[str] = []
    params: list = []
    if product_id is not None:
        where.append("h.product_id = ?")
        params.append(product_id)
    if event_type:
        if event_type not in VALID_EVENTS:
            raise HTTPException(400, f"Invalid event_type: {event_type}")
        where.append("h.event_type = ?")
        params.append(event_type)
    if since:
        where.append("h.created_at >= ?")
        params.append(since)
    if until:
        where.append("h.created_at < ?")
        params.append(until)

    sql = (
        "SELECT h.*, p.name AS product_name FROM stock_history h "
        "JOIN products p ON p.id = h.product_id"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY h.created_at DESC, h.id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


@router.get("/history/product/{product_id}", response_model=list[StockHistoryEntry])
def get_product_history(product_id: int, limit: int = Query(200, ge=1, le=1000)):
    """Per-product event log, newest first."""
    conn = _get_db()
    if not conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone():
        raise HTTPException(404, f"Product {product_id} not found")
    return conn.execute(
        "SELECT * FROM stock_history WHERE product_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (product_id, limit),
    ).fetchall()


@router.delete("/history/{entry_id}", status_code=204)
def delete_history_entry(entry_id: int):
    """Remove a single history entry (manual cleanup)."""
    conn = _get_db()
    if not conn.execute("SELECT id FROM stock_history WHERE id = ?", (entry_id,)).fetchone():
        raise HTTPException(404, f"History entry {entry_id} not found")
    conn.execute("DELETE FROM stock_history WHERE id = ?", (entry_id,))
    conn.commit()
