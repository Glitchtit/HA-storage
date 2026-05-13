"""Consumption velocity stats — drives the predictive shopping proposal.

For every active product with ``min_stock_amount > 0`` (i.e. the user opted into
keep-in-stock tracking), look at consume events in ``stock_history`` over the
last ``lookback_weeks`` and estimate a weekly consumption rate. If the current
total stock divided by that rate predicts depletion within ``horizon_days``,
the product is included in the proposal.

Consume events are always logged in the product's default unit (see
``routers/stock.py::consume_stock`` — no ``unit_id`` is passed to ``log_event``),
so summing ``amount`` across history rows is safe without unit conversion.
"""

from __future__ import annotations

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
        suggested = round(suggested + 0.05, 1)
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
