"""Statistics endpoints derived from stock_history."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from models import StatsProductSummary, StatsSummary, StatsTimelinePoint, StatsTopItem

router = APIRouter(tags=["stats"])
log = logging.getLogger(__name__)


def _get_db():
    from main import get_connection
    return get_connection()


@router.get("/stats/summary", response_model=StatsSummary)
def stats_summary():
    conn = _get_db()
    total = conn.execute("SELECT COUNT(*) AS c FROM stock_history").fetchone()["c"]
    last7 = conn.execute(
        "SELECT COUNT(*) AS c FROM stock_history WHERE created_at >= datetime('now','-7 days')"
    ).fetchone()["c"]
    last30 = conn.execute(
        "SELECT COUNT(*) AS c FROM stock_history WHERE created_at >= datetime('now','-30 days')"
    ).fetchone()["c"]
    purchased = conn.execute(
        "SELECT COUNT(DISTINCT product_id) AS c FROM stock_history "
        "WHERE event_type='purchase' AND created_at >= datetime('now','-30 days')"
    ).fetchone()["c"]
    consumed = conn.execute(
        "SELECT COUNT(DISTINCT product_id) AS c FROM stock_history "
        "WHERE event_type='consume' AND created_at >= datetime('now','-30 days')"
    ).fetchone()["c"]
    spoiled = conn.execute(
        "SELECT COUNT(*) AS c FROM stock_history "
        "WHERE event_type='spoil' AND created_at >= datetime('now','-30 days')"
    ).fetchone()["c"]
    return StatsSummary(
        events_total=total,
        events_7d=last7,
        events_30d=last30,
        products_purchased_30d=purchased,
        products_consumed_30d=consumed,
        spoiled_30d=spoiled,
    )


def _top(conn, event_type: str, days: int, limit: int) -> list[dict]:
    return conn.execute(
        "SELECT h.product_id, p.name AS product_name, "
        "       SUM(h.amount) AS total_amount, COUNT(*) AS event_count "
        "FROM stock_history h JOIN products p ON p.id = h.product_id "
        "WHERE h.event_type = ? AND h.created_at >= datetime('now','-' || ? || ' days') "
        "GROUP BY h.product_id ORDER BY total_amount DESC LIMIT ?",
        (event_type, days, limit),
    ).fetchall()


@router.get("/stats/top-consumed", response_model=list[StatsTopItem])
def top_consumed(days: int = Query(30, ge=1, le=3650), limit: int = Query(10, ge=1, le=100)):
    return _top(_get_db(), "consume", days, limit)


@router.get("/stats/top-purchased", response_model=list[StatsTopItem])
def top_purchased(days: int = Query(30, ge=1, le=3650), limit: int = Query(10, ge=1, le=100)):
    return _top(_get_db(), "purchase", days, limit)


@router.get("/stats/spoilage", response_model=list[StatsTopItem])
def spoilage(days: int = Query(30, ge=1, le=3650), limit: int = Query(20, ge=1, le=100)):
    return _top(_get_db(), "spoil", days, limit)


@router.get("/stats/timeline", response_model=list[StatsTimelinePoint])
def timeline(
    days: int = Query(30, ge=1, le=365),
    event_type: str | None = None,
    product_id: int | None = None,
):
    conn = _get_db()
    where = ["h.created_at >= datetime('now','-' || ? || ' days')"]
    params: list = [days]
    if event_type:
        where.append("h.event_type = ?")
        params.append(event_type)
    if product_id is not None:
        where.append("h.product_id = ?")
        params.append(product_id)

    sql = (
        "SELECT date(h.created_at) AS day, "
        "       SUM(h.amount) AS amount, COUNT(*) AS event_count "
        "FROM stock_history h WHERE " + " AND ".join(where) +
        " GROUP BY day ORDER BY day"
    )
    return conn.execute(sql, params).fetchall()


@router.get("/stats/product/{product_id}", response_model=StatsProductSummary)
def product_stats(product_id: int):
    conn = _get_db()
    if not conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone():
        raise HTTPException(404, f"Product {product_id} not found")

    rows = conn.execute(
        "SELECT event_type, SUM(amount) AS total, COUNT(*) AS cnt, "
        "       MAX(created_at) AS last_at "
        "FROM stock_history WHERE product_id = ? GROUP BY event_type",
        (product_id,),
    ).fetchall()
    by_type = {r["event_type"]: r for r in rows}

    purchased_total = (by_type.get("purchase") or {}).get("total") or 0
    consumed_total = (by_type.get("consume") or {}).get("total") or 0
    spoiled_total = (by_type.get("spoil") or {}).get("total") or 0
    purchase_count = (by_type.get("purchase") or {}).get("cnt") or 0
    consume_count = (by_type.get("consume") or {}).get("cnt") or 0
    last_purchase = (by_type.get("purchase") or {}).get("last_at")
    last_consume = (by_type.get("consume") or {}).get("last_at")

    avg_gap: float | None = None
    if consume_count >= 2:
        # Average days between consume events: (last - first) / (count - 1)
        span = conn.execute(
            "SELECT (julianday(MAX(created_at)) - julianday(MIN(created_at))) AS span "
            "FROM stock_history WHERE product_id = ? AND event_type = 'consume'",
            (product_id,),
        ).fetchone()
        if span and span["span"] is not None:
            avg_gap = float(span["span"]) / max(consume_count - 1, 1)

    return StatsProductSummary(
        product_id=product_id,
        purchased_total=float(purchased_total),
        consumed_total=float(consumed_total),
        spoiled_total=float(spoiled_total),
        purchase_count=int(purchase_count),
        consume_count=int(consume_count),
        avg_days_between_consumes=avg_gap,
        last_purchase=last_purchase,
        last_consume=last_consume,
    )
