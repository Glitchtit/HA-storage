"""Statistics endpoints derived from stock_history."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from consumption_stats import (
    expiring_within,
    predicted_runouts,
    weekly_digest,
)
from models import (
    PredictedRunout,
    StatsDigestResponse,
    StatsProductSummary,
    StatsPurchaseCostsProduct,
    StatsPurchaseCostsResponse,
    StatsPurchaseCostsSeriesPoint,
    StatsRunoutsResponse,
    StatsStockValueGroup,
    StatsStockValueResponse,
    StatsSummary,
    StatsTimelinePoint,
    StatsTopItem,
    StatsWasteBreakdown,
    StatsWasteResponse,
    StatsWasteSeriesPoint,
)

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


@router.get("/stats/waste", response_model=StatsWasteResponse)
def stats_waste(days: int = Query(30, ge=1, le=3650)):
    """Monetary spoilage breakdown over the last ``days`` days.

    Pulls every ``spoil`` event from ``stock_history``. Each row's valuation is
    ``amount * COALESCE(h.unit_price, p.unit_price)`` — the snapshot taken at
    spoil time is preferred so historic valuation doesn't drift when product
    defaults change; the current product default is the fallback for old rows.
    Rows with no valuation at all are counted toward ``total_amount`` but not
    ``total_value`` (the UI shows them as "price unknown").
    """
    conn = _get_db()
    rows = conn.execute(
        """
        SELECT h.product_id, h.amount,
               COALESCE(h.unit_price, p.unit_price) AS effective_price,
               h.location_id, h.created_at,
               p.name AS product_name,
               p.product_group_id AS category_id,
               p.unit_price_currency AS currency
        FROM stock_history h
        JOIN products p ON p.id = h.product_id
        WHERE h.event_type = 'spoil'
          AND h.created_at >= datetime('now','-' || ? || ' days')
        """,
        (days,),
    ).fetchall()

    locations = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM locations").fetchall()}
    groups = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM product_groups").fetchall()}

    by_product: dict[int, dict] = {}
    by_location: dict[int | None, dict] = {}
    by_category: dict[int | None, dict] = {}
    series: dict[str, dict] = {}
    total_amount = 0.0
    total_value = 0.0
    currency = "EUR"

    for r in rows:
        amt = float(r["amount"] or 0)
        price = r["effective_price"]
        val = float(amt) * float(price) if price is not None else 0.0
        total_amount += amt
        total_value += val
        if r["currency"]:
            currency = r["currency"]

        pid = r["product_id"]
        bp = by_product.setdefault(pid, {
            "product_id": pid, "product_name": r["product_name"],
            "amount": 0.0, "value": 0.0,
        })
        bp["amount"] += amt
        bp["value"] += val

        loc_id = r["location_id"]
        bl = by_location.setdefault(loc_id, {
            "location_id": loc_id,
            "location_name": locations.get(loc_id, "Unknown") if loc_id else "Unknown",
            "amount": 0.0, "value": 0.0,
        })
        bl["amount"] += amt
        bl["value"] += val

        cat_id = r["category_id"]
        bc = by_category.setdefault(cat_id, {
            "category_id": cat_id,
            "category_name": groups.get(cat_id, "Uncategorized") if cat_id else "Uncategorized",
            "amount": 0.0, "value": 0.0,
        })
        bc["amount"] += amt
        bc["value"] += val

        # Week bucket: Monday of the event's date (ISO week start).
        try:
            ts = datetime.fromisoformat(r["created_at"].replace("Z", ""))
        except ValueError:
            continue
        wk = (ts.date() - timedelta(days=ts.date().weekday())).isoformat()
        sp = series.setdefault(wk, {"week": wk, "amount": 0.0, "value": 0.0})
        sp["amount"] += amt
        sp["value"] += val

    return StatsWasteResponse(
        days=days,
        currency=currency,
        total_amount=round(total_amount, 2),
        total_value=round(total_value, 2),
        by_product=[
            StatsWasteBreakdown(**{**v, "value": round(v["value"], 2), "amount": round(v["amount"], 2)})
            for v in sorted(by_product.values(), key=lambda x: x["value"], reverse=True)
        ],
        by_location=[
            StatsWasteBreakdown(**{**v, "value": round(v["value"], 2), "amount": round(v["amount"], 2)})
            for v in sorted(by_location.values(), key=lambda x: x["value"], reverse=True)
        ],
        by_category=[
            StatsWasteBreakdown(**{**v, "value": round(v["value"], 2), "amount": round(v["amount"], 2)})
            for v in sorted(by_category.values(), key=lambda x: x["value"], reverse=True)
        ],
        series=[
            StatsWasteSeriesPoint(week=v["week"], amount=round(v["amount"], 2), value=round(v["value"], 2))
            for v in sorted(series.values(), key=lambda x: x["week"])
        ],
    )


@router.get("/stats/stock-value", response_model=StatsStockValueResponse)
def stats_stock_value():
    """Current monetary value of everything on hand.

    Each lot is valued at ``amount * COALESCE(stock.price_paid, products.unit_price)``
    — the price actually paid wins; the product's current default fills in for
    lots recorded before prices were tracked. Lots with neither price count
    toward ``unpriced_amount`` (units) and contribute nothing to ``total_value``.

    Only active products count: deactivated (retired) products can leave
    orphaned stock rows behind, and those are phantom inventory — the rest of
    the app hides inactive products entirely (same ``p.active = 1`` rule as
    the shopping proposal and runout queries).
    """
    conn = _get_db()
    rows = conn.execute(
        """
        SELECT s.amount,
               COALESCE(s.price_paid, p.unit_price) AS effective_price,
               p.product_group_id AS group_id,
               p.unit_price_currency AS currency
        FROM stock s
        JOIN products p ON p.id = s.product_id
        WHERE s.amount > 0
          AND p.active = 1
        """
    ).fetchall()
    groups = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM product_groups").fetchall()}

    total_value = 0.0
    priced_amount = 0.0
    unpriced_amount = 0.0
    currency = "EUR"
    by_group: dict[int | None, dict] = {}
    for r in rows:
        amt = float(r["amount"] or 0)
        price = r["effective_price"]
        if r["currency"]:
            currency = r["currency"]
        if price is None:
            unpriced_amount += amt
            continue
        val = amt * float(price)
        total_value += val
        priced_amount += amt
        gid = r["group_id"]
        bg = by_group.setdefault(gid, {
            "group_id": gid,
            "group_name": groups.get(gid, "Ungrouped") if gid else "Ungrouped",
            "value": 0.0,
        })
        bg["value"] += val

    return StatsStockValueResponse(
        total_value=round(total_value, 2),
        currency=currency,
        priced_amount=round(priced_amount, 2),
        unpriced_amount=round(unpriced_amount, 2),
        by_group=[
            StatsStockValueGroup(**{**g, "value": round(g["value"], 2)})
            for g in sorted(by_group.values(), key=lambda x: x["value"], reverse=True)
        ],
    )


@router.get("/stats/purchase-costs", response_model=StatsPurchaseCostsResponse)
def stats_purchase_costs(
    year: int | None = Query(None, ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
):
    """Purchase spend for one local calendar month, plus a trailing 12-month trend.

    Sums ``purchase`` history events valued at
    ``amount * COALESCE(h.unit_price, p.unit_price)`` — the snapshot taken at
    purchase time preferred, current product default as fallback for old rows.
    ``/stock/correct-purchase`` already reduces purchase events retroactively,
    so the sums are net of over-scan corrections. Timestamps are stored UTC;
    bucketing converts to localtime so months match the user's calendar.
    """
    now_local = datetime.now().astimezone()
    if year is None:
        year = now_local.year
    if month is None:
        month = now_local.month

    # Twelve YYYY-MM buckets ending at the selected month.
    months: list[str] = []
    y, m = year, month
    for _ in range(12):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    selected = months[-1]

    conn = _get_db()
    # 'localtime' resolves via the container's TZ (injected by Supervisor).
    # Outside Supervisor (bare docker/CI) months silently become UTC months.
    rows = conn.execute(
        """
        SELECT strftime('%Y-%m', h.created_at, 'localtime') AS ym,
               h.product_id, h.amount,
               COALESCE(h.unit_price, p.unit_price) AS effective_price,
               p.name AS product_name,
               p.unit_price_currency AS currency
        FROM stock_history h
        JOIN products p ON p.id = h.product_id
        WHERE h.event_type = 'purchase'
          AND strftime('%Y-%m', h.created_at, 'localtime') BETWEEN ? AND ?
        """,
        (months[0], selected),
    ).fetchall()

    series = {ym: 0.0 for ym in months}
    by_product: dict[int, dict] = {}
    total_value = 0.0
    event_count = 0
    currency = "EUR"
    for r in rows:
        amt = float(r["amount"] or 0)
        price = r["effective_price"]
        val = amt * float(price) if price is not None else 0.0
        if r["currency"]:
            currency = r["currency"]
        series[r["ym"]] += val
        if r["ym"] != selected:
            continue
        total_value += val
        event_count += 1
        pid = r["product_id"]
        bp = by_product.setdefault(pid, {
            "product_id": pid, "product_name": r["product_name"],
            "amount": 0.0, "value": 0.0,
        })
        bp["amount"] += amt
        bp["value"] += val

    top = sorted(by_product.values(), key=lambda x: x["value"], reverse=True)[:15]
    return StatsPurchaseCostsResponse(
        year=year,
        month=month,
        currency=currency,
        total_value=round(total_value, 2),
        event_count=event_count,
        by_product=[
            StatsPurchaseCostsProduct(
                **{**p, "amount": round(p["amount"], 2), "value": round(p["value"], 2)}
            )
            for p in top
        ],
        series=[
            StatsPurchaseCostsSeriesPoint(month=ym, value=round(series[ym], 2))
            for ym in months
        ],
    )


@router.get("/stats/runouts", response_model=StatsRunoutsResponse)
def stats_runouts(horizon: int = Query(14, ge=1, le=90)):
    """Products predicted to run out within ``horizon`` days, based on the
    same 8-week consumption velocity model that drives ``/shopping-list/proposal``.
    """
    return StatsRunoutsResponse(
        horizon=horizon,
        runouts=[PredictedRunout(**r) for r in predicted_runouts(_get_db(), horizon_days=horizon)],
    )


@router.get("/stats/digest", response_model=StatsDigestResponse)
def stats_digest():
    """One-call snapshot for the HA integration / weekly notification.

    Bundles the three signals that matter for "what's happening in my pantry":
    monetary waste (last 30d), expiring lots (next 7d), predicted runouts (next 14d).
    """
    conn = _get_db()
    digest = weekly_digest(conn)
    return StatsDigestResponse(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        days=digest["days"],
        currency=digest["currency"],
        expiring_this_week=digest["expiring_this_week"],
        predicted_runouts_14d=[PredictedRunout(**r) for r in digest["predicted_runouts_14d"]],
        waste_value_30d=digest["waste_value_30d"],
        waste_amount_30d=digest["waste_amount_30d"],
        top_spoilers_30d=digest["top_spoilers_30d"],
    )


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
