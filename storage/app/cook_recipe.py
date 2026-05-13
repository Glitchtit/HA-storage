"""Cook a recipe — deduct required ingredients from stock and queue the
shortfall on the shopping list.

The deduction uses the same FIFO-by-best-before-date order that the regular
``/stock/consume`` endpoint uses, and logs a ``consume`` event per ingredient
in ``stock_history`` so the predictive shopping proposal stays accurate.

Unit conversion goes through the same BFS used by
``routers.units.resolve_conversion`` — duplicated here as a non-raising helper
so we can compute per-ingredient factors in one pass without HTTPExceptions.
If no path exists we treat the ingredient as unmatched (better to flag than
to silently consume the wrong amount).
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

from routers.history import log_event

log = logging.getLogger(__name__)


def _resolve_factor(conn, from_unit_id: int, to_unit_id: int, product_id: int | None) -> float | None:
    """BFS the unit-conversion graph. Returns None if no path exists."""
    if from_unit_id == to_unit_id:
        return 1.0
    if product_id is not None:
        rows = conn.execute(
            "SELECT * FROM unit_conversions WHERE product_id = ? OR product_id IS NULL",
            (product_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM unit_conversions WHERE product_id IS NULL"
        ).fetchall()
    graph: dict[int, list[tuple[int, float]]] = {}
    for r in rows:
        graph.setdefault(r["from_unit_id"], []).append((r["to_unit_id"], r["factor"]))
        if r["factor"]:
            graph.setdefault(r["to_unit_id"], []).append((r["from_unit_id"], 1.0 / r["factor"]))

    queue: deque[tuple[int, float]] = deque([(from_unit_id, 1.0)])
    visited = {from_unit_id}
    while queue:
        current, factor = queue.popleft()
        for neighbor, edge in graph.get(current, []):
            if neighbor == to_unit_id:
                return factor * edge
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, factor * edge))
    return None


def _consume_fifo(conn, product_id: int, target_amount: float) -> float:
    """Consume up to ``target_amount`` from oldest stock entries.

    Returns the actual amount consumed (may be less than target if stock runs
    out). Mirrors the loop in ``routers/stock.py::consume_stock`` so a single
    log_event() upstream covers everything we removed.
    """
    entries = conn.execute(
        "SELECT * FROM stock WHERE product_id = ? AND amount > 0 ORDER BY best_before_date ASC",
        (product_id,),
    ).fetchall()
    remaining = target_amount
    consumed = 0.0
    for entry in entries:
        if remaining <= 0:
            break
        take = min(remaining, float(entry["amount"]))
        new_amount = float(entry["amount"]) - take
        if new_amount <= 0:
            conn.execute("DELETE FROM stock WHERE id = ?", (entry["id"],))
        else:
            conn.execute("UPDATE stock SET amount = ? WHERE id = ?", (new_amount, entry["id"]))
        remaining -= take
        consumed += take
    return consumed


def cook_recipe(
    conn,
    recipe_id: int,
    *,
    servings: float | None = None,
) -> dict[str, Any]:
    """Deduct ingredients for one cook of ``recipe_id`` and queue shortfall.

    ``servings`` defaults to the recipe's stored servings count. Passing a
    different number scales every ingredient amount proportionally.
    """
    recipe = conn.execute(
        "SELECT * FROM recipes WHERE id = ?", (recipe_id,)
    ).fetchone()
    if not recipe:
        raise ValueError(f"Recipe {recipe_id} not found")

    base_servings = float(recipe["servings"] or 1)
    target_servings = float(servings) if servings else base_servings
    if target_servings <= 0:
        raise ValueError("servings must be > 0")
    ratio = target_servings / base_servings

    ingredients = conn.execute(
        """
        SELECT ri.*, p.unit_id AS product_unit_id, p.name AS product_name
        FROM recipe_ingredients ri
        JOIN products p ON p.id = ri.product_id
        WHERE ri.recipe_id = ?
        """,
        (recipe_id,),
    ).fetchall()

    deducted: list[dict[str, Any]] = []
    shortfall_items: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    recipe_note = f"Reseptistä: {recipe['name']}"

    for ing in ingredients:
        needed_in_recipe_unit = float(ing["amount"]) * ratio
        factor = _resolve_factor(
            conn,
            from_unit_id=int(ing["unit_id"]),
            to_unit_id=int(ing["product_unit_id"]),
            product_id=int(ing["product_id"]),
        )
        if factor is None:
            unmatched.append({
                "product_id": int(ing["product_id"]),
                "product_name": ing["product_name"],
                "amount": needed_in_recipe_unit,
                "unit_id": int(ing["unit_id"]),
                "reason": "no_unit_conversion",
            })
            continue

        needed_in_stock_unit = needed_in_recipe_unit * factor
        if needed_in_stock_unit <= 0:
            continue

        taken = _consume_fifo(conn, int(ing["product_id"]), needed_in_stock_unit)
        if taken > 0:
            log_event(
                conn,
                product_id=int(ing["product_id"]),
                event_type="consume",
                amount=taken,
                unit_id=int(ing["product_unit_id"]),
                note=recipe_note,
            )
            deducted.append({
                "product_id": int(ing["product_id"]),
                "product_name": ing["product_name"],
                "amount": round(taken, 3),
                "unit_id": int(ing["product_unit_id"]),
            })

        shortfall = needed_in_stock_unit - taken
        if shortfall > 1e-6:
            # Queue the missing amount on the shopping list. We use the
            # product's stock-unit_id so the shopping-list row matches stock
            # conventions; the user can edit before checkout if needed.
            conn.execute(
                """
                INSERT INTO shopping_list (product_id, amount, unit_id, note, recipe_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(ing["product_id"]),
                    round(shortfall, 3),
                    int(ing["product_unit_id"]),
                    recipe_note,
                    recipe_id,
                ),
            )
            shortfall_items.append({
                "product_id": int(ing["product_id"]),
                "product_name": ing["product_name"],
                "amount": round(shortfall, 3),
                "unit_id": int(ing["product_unit_id"]),
            })

    conn.commit()
    return {
        "recipe_id": recipe_id,
        "recipe_name": recipe["name"],
        "servings": target_servings,
        "deducted": deducted,
        "shortfall_added": shortfall_items,
        "unmatched": unmatched,
    }
