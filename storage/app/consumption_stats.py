"""Consumption velocity & predictive insights — drives both the predictive
shopping proposal and the ``/api/stats/runouts`` / ``/api/stats/digest``
endpoints used by the HA integration.

For every active product, look at consume events in ``stock_history`` over the
last ``lookback_weeks`` and estimate a weekly consumption rate. From that rate
we derive:

* :func:`compute_proposal` — shopping proposal (filters to products with
  ``min_stock_amount > 0``, sorted by urgency).
* :func:`predicted_runouts` — generic "what runs out in N days" list,
  surfaced in the Insights dashboard and the HA digest.
* :func:`expiring_within` — soonest-expiring lots, used by the HA digest.
* :func:`weekly_digest` — one-call snapshot bundling waste + expiring +
  predicted runouts for HA notifications.

Consume events are always logged in the product's default unit (see
``routers/stock.py::consume_stock`` — no ``unit_id`` is passed to ``log_event``),
so summing ``amount`` across history rows is safe without unit conversion.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any


def compute_proposal(
    conn,
    *,
    lookback_weeks: int = 8,
    horizon_days: int = 7,
) -> list[dict[str, Any]]:
    """Return products predicted to deplete within ``horizon_days``.

    Excludes products already on the shopping list (done = 0), and products with
    no consume history in the lookback window (we can't predict from nothing).
    """
    lookback_days = max(1, lookback_weeks) * 7
    rows = conn.execute(
        """
        SELECT
            p.id              AS product_id,
            p.name            AS product_name,
            p.unit_id         AS unit_id,
            p.min_stock_amount AS min_stock_amount,
            COALESCE((
                SELECT SUM(amount) FROM stock s WHERE s.product_id = p.id
            ), 0) AS current_qty,
            COALESCE((
                SELECT SUM(amount) FROM stock_history h
                WHERE h.product_id = p.id
                  AND h.event_type = 'consume'
                  AND h.created_at >= datetime('now', ?)
            ), 0) AS consumed_in_window,
            EXISTS(
                SELECT 1 FROM shopping_list sl
                WHERE sl.product_id = p.id AND sl.done = 0
            ) AS on_shopping_list
        FROM products p
        WHERE p.active = 1 AND p.min_stock_amount > 0
        """,
        (f"-{lookback_days} days",),
    ).fetchall()

    proposal: list[dict[str, Any]] = []
    for r in rows:
        if r["on_shopping_list"]:
            continue
        consumed = float(r["consumed_in_window"] or 0)
        if consumed <= 0:
            continue
        weekly_rate = consumed / max(1, lookback_weeks)
        if weekly_rate <= 0:
            continue
        current = float(r["current_qty"] or 0)
        days_to_zero = (current / weekly_rate) * 7 if weekly_rate > 0 else float("inf")
        if days_to_zero >= horizon_days:
            continue

        suggested = max(float(r["min_stock_amount"] or 0), weekly_rate * 2)
        # Shopping list carries whole units only — always round up so we never
        # propose a fraction (e.g. 2.3 → 3).
        suggested = float(math.ceil(suggested))
        proposal.append({
            "product_id": int(r["product_id"]),
            "product_name": r["product_name"],
            "unit_id": int(r["unit_id"]),
            "current_qty": round(current, 2),
            "weekly_rate": round(weekly_rate, 2),
            "days_to_zero": round(days_to_zero, 1),
            "suggested_amount": suggested,
            "reasoning": _reasoning_fi(weekly_rate, current),
        })

    proposal.sort(key=lambda x: x["days_to_zero"])
    return proposal


def _reasoning_fi(weekly_rate: float, current_qty: float) -> str:
    """Short Finnish reasoning string for the proposal UI."""
    return f"{round(weekly_rate, 1)}/vk, varastossa {round(current_qty, 1)}"


def compute_cadence_suggestions(
    conn,
    *,
    lookback_days: int = 180,
    window_days: int = 7,
    min_purchases: int = 3,
) -> list[dict[str, Any]]:
    """Suggest re-buys based on **purchase cadence** rather than consumption.

    Complements :func:`compute_proposal` (which is consumption-velocity based):
    here we learn the mean interval between ``purchase`` events in
    ``stock_history`` and surface a product when *today* falls within
    ``window_days`` of its expected next purchase (last purchase + mean
    interval).

    A product qualifies if it is "kept in stock" (``min_stock_amount > 0`` —
    always considered) **or** "frequently bought" (at least ``min_purchases``
    purchases in the lookback window). Either way it needs >= 2 purchases in
    the window to derive an interval.

    Excludes products already on the shopping list (done = 0) and products that
    are still well-stocked (current qty at/above the keep-in-stock threshold,
    or — for non-staples — at/above the typical purchase amount). Items overdue
    by more than ``window_days`` drop off; they resurface once the next
    purchase resets the anchor (so an already-rebought item never lingers).
    """
    lookback_days = max(1, lookback_days)
    rows = conn.execute(
        """
        SELECT p.id AS product_id, p.name AS product_name, p.unit_id AS unit_id,
               p.min_stock_amount AS min_stock_amount,
               COALESCE((SELECT SUM(amount) FROM stock s WHERE s.product_id = p.id), 0) AS current_qty,
               agg.cnt             AS cnt,
               agg.avg_amount      AS avg_amount,
               agg.span_days       AS span_days,
               agg.days_since_last AS days_since_last,
               EXISTS(
                   SELECT 1 FROM shopping_list sl
                   WHERE sl.product_id = p.id AND sl.done = 0
               ) AS on_list
        FROM products p
        JOIN (
            SELECT product_id,
                   COUNT(*) AS cnt,
                   AVG(amount) AS avg_amount,
                   julianday(MAX(created_at)) - julianday(MIN(created_at)) AS span_days,
                   julianday('now') - julianday(MAX(created_at)) AS days_since_last
            FROM stock_history
            WHERE event_type = 'purchase'
              AND created_at >= datetime('now', ?)
            GROUP BY product_id
        ) agg ON agg.product_id = p.id
        WHERE p.active = 1
        """,
        (f"-{lookback_days} days",),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        cnt = int(r["cnt"] or 0)
        if cnt < 2:
            continue  # need two purchases to derive an interval

        min_stock = float(r["min_stock_amount"] or 0)
        is_kept = min_stock > 0
        if not is_kept and cnt < min_purchases:
            continue  # not a staple and not frequent enough

        if r["on_list"]:
            continue

        span_days = float(r["span_days"] or 0)
        avg_interval = span_days / (cnt - 1)
        if avg_interval <= 0:
            continue  # all purchases logged at the same instant — no rhythm

        days_since_last = float(r["days_since_last"] or 0)
        days_until_expected = avg_interval - days_since_last
        if abs(days_until_expected) > window_days:
            continue  # not within +-1 week of expected restock

        current = float(r["current_qty"] or 0)
        avg_amount = float(r["avg_amount"] or 0)
        threshold = min_stock if is_kept else avg_amount
        if threshold > 0 and current >= threshold:
            continue  # still well-stocked

        # Whole units only on the shopping list — round the average purchase
        # amount up rather than carrying a fraction.
        suggested = float(math.ceil(avg_amount or min_stock or 1))
        out.append({
            "product_id": int(r["product_id"]),
            "product_name": r["product_name"],
            "unit_id": int(r["unit_id"]),
            "current_qty": round(current, 2),
            "purchase_count": cnt,
            "avg_interval_days": round(avg_interval, 1),
            "days_since_last": round(days_since_last, 1),
            "days_until_expected": round(days_until_expected, 1),
            "suggested_amount": suggested,
            "is_kept": is_kept,
            "reasoning": _cadence_reasoning_fi(avg_interval, days_since_last),
        })

    out.sort(key=lambda x: x["days_until_expected"])
    return out


def _cadence_reasoning_fi(avg_interval: float, days_since_last: float) -> str:
    """Short Finnish reasoning string for the cadence-suggestion UI."""
    base = f"~{round(avg_interval)} pv välein, ostettu {round(days_since_last)} pv sitten"
    return base + " (myöhässä)" if days_since_last > avg_interval else base


def predicted_runouts(
    conn,
    *,
    horizon_days: int = 14,
    lookback_weeks: int = 8,
) -> list[dict[str, Any]]:
    """Products predicted to deplete within ``horizon_days``.

    Unlike :func:`compute_proposal`, this is *not* filtered to opt-in products
    (no ``min_stock_amount`` requirement) and doesn't suppress items already on
    the shopping list — callers want to *see* upcoming runouts in the dashboard
    even if a row has been added to the cart already. Sorted ascending by days
    to runout. Products with no consumption in the window are excluded.
    """
    lookback_days = max(1, lookback_weeks) * 7
    rows = conn.execute(
        """
        SELECT
            p.id AS product_id,
            p.name AS product_name,
            p.unit_id AS unit_id,
            COALESCE((
                SELECT SUM(amount) FROM stock s WHERE s.product_id = p.id
            ), 0) AS current_qty,
            COALESCE((
                SELECT SUM(amount) FROM stock_history h
                WHERE h.product_id = p.id
                  AND h.event_type = 'consume'
                  AND h.created_at >= datetime('now', ?)
            ), 0) AS consumed_in_window
        FROM products p
        WHERE p.active = 1
        """,
        (f"-{lookback_days} days",),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        consumed = float(r["consumed_in_window"] or 0)
        if consumed <= 0:
            continue
        avg_daily = consumed / float(lookback_days)
        if avg_daily <= 0:
            continue
        current = float(r["current_qty"] or 0)
        days_to_zero = current / avg_daily if avg_daily > 0 else float("inf")
        if days_to_zero >= horizon_days:
            continue
        out.append({
            "product_id": int(r["product_id"]),
            "product_name": r["product_name"],
            "unit_id": int(r["unit_id"]),
            "current_qty": round(current, 2),
            "avg_daily": round(avg_daily, 3),
            "days_to_runout": round(days_to_zero, 1),
        })
    out.sort(key=lambda x: x["days_to_runout"])
    return out


def expiring_within(conn, *, days: int = 7) -> list[dict[str, Any]]:
    """Stock lots whose ``best_before_date`` falls within ``days`` days from
    today, including already-expired lots (treated as more urgent). Returned in
    canonical FIFO order. ``days_left`` is negative for past-due lots.
    """
    rows = conn.execute(
        """
        SELECT s.id AS lot_id, s.product_id, p.name AS product_name,
               s.amount, s.best_before_date,
               CAST(julianday(s.best_before_date) - julianday(date('now')) AS INTEGER) AS days_left
        FROM stock s
        JOIN products p ON p.id = s.product_id
        WHERE s.amount > 0
          AND s.best_before_date IS NOT NULL
          AND s.best_before_date <= date('now', '+' || ? || ' days')
        ORDER BY
          CASE WHEN s.best_before_date IS NULL THEN 1 ELSE 0 END,
          s.best_before_date ASC,
          s.purchased_date ASC,
          s.id ASC
        """,
        (days,),
    ).fetchall()
    return [
        {
            "lot_id": int(r["lot_id"]),
            "product_id": int(r["product_id"]),
            "product_name": r["product_name"],
            "amount": float(r["amount"]),
            "best_before_date": r["best_before_date"],
            "days_left": int(r["days_left"]) if r["days_left"] is not None else None,
        }
        for r in rows
    ]


def _waste_summary(conn, *, days: int = 30) -> dict[str, Any]:
    """Lightweight waste totals — value, amount, top spoilers. Used by the
    weekly digest. The full breakdown lives in ``routers/stats::stats_waste``.
    """
    rows = conn.execute(
        """
        SELECT h.product_id, h.amount,
               COALESCE(h.unit_price, p.unit_price) AS effective_price,
               p.name AS product_name,
               p.unit_price_currency AS currency
        FROM stock_history h
        JOIN products p ON p.id = h.product_id
        WHERE h.event_type = 'spoil'
          AND h.created_at >= datetime('now','-' || ? || ' days')
        """,
        (days,),
    ).fetchall()

    total_amount = 0.0
    total_value = 0.0
    currency = "EUR"
    by_product: dict[int, dict[str, Any]] = {}
    for r in rows:
        amt = float(r["amount"] or 0)
        price = r["effective_price"]
        val = amt * float(price) if price is not None else 0.0
        total_amount += amt
        total_value += val
        if r["currency"]:
            currency = r["currency"]
        pid = int(r["product_id"])
        bp = by_product.setdefault(pid, {
            "product_id": pid, "product_name": r["product_name"],
            "amount": 0.0, "value": 0.0,
        })
        bp["amount"] += amt
        bp["value"] += val

    top = sorted(by_product.values(), key=lambda x: x["value"], reverse=True)[:5]
    return {
        "total_amount": round(total_amount, 2),
        "total_value": round(total_value, 2),
        "currency": currency,
        "top": [
            {**v, "amount": round(v["amount"], 2), "value": round(v["value"], 2)} for v in top
        ],
    }


def weekly_digest(conn) -> dict[str, Any]:
    """One-call snapshot for the HA integration's weekly notification.

    Bundles monetary waste (last 30 days), expiring lots (next 7 days), and
    predicted runouts (next 14 days). The HA service ``ha_storage.get_weekly_digest``
    returns this verbatim so users can wire it to a notify automation.
    """
    waste = _waste_summary(conn, days=30)
    return {
        "days": 30,
        "currency": waste["currency"],
        "expiring_this_week": expiring_within(conn, days=7),
        "predicted_runouts_14d": predicted_runouts(conn, horizon_days=14),
        "waste_value_30d": waste["total_value"],
        "waste_amount_30d": waste["total_amount"],
        "top_spoilers_30d": waste["top"],
    }
