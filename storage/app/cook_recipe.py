"""Cook a recipe — deduct each ingredient from the stock under its bound
product and queue any shortfall on the shopping list.

Deduction walks every lot in the bound product's subtree in canonical FIFO
order (best-before first), converting the need into each lot's unit through
that SKU's facts: 3.5 dl of flour takes 0.105 of a 2 kg bag. A partly used
retail pack is marked opened. One `consume` event is logged per SKU and unit.
Staples and to-taste ingredients are skipped. An ingredient whose stock
cannot be converted at all is reported as unmatched and nothing is deducted
for it. Shortfall goes through recipe_rows.add_recipe_need, the same writer
the to-shopping endpoint uses.
"""
from __future__ import annotations

import logging
from typing import Any

from availability import EPS
from facts import Catalog
from quantity import PACKAGE, VAGUE, convert
from recipe_rows import NOTE_PREFIX, add_recipe_need, recipe_already_listed
from routers.history import log_event

log = logging.getLogger(__name__)


def _deduct(conn, lot: dict, take: float, *, package: bool) -> None:
    """Remove `take` (in the lot's unit) from one lot and keep the cached lot
    dict in sync so later ingredients in the same cook see the new amount."""
    new_amount = float(lot["amount"]) - take
    if new_amount <= EPS:
        conn.execute("DELETE FROM stock WHERE id = ?", (lot["id"],))
        lot["amount"] = 0.0
        return
    opened = min(float(lot["amount_opened"] or 0), new_amount)
    if package and abs(new_amount - round(new_amount)) > 1e-6:
        opened = max(opened, min(1.0, new_amount))
    conn.execute(
        "UPDATE stock SET amount = ?, amount_opened = ? WHERE id = ?",
        (new_amount, opened, lot["id"]),
    )
    lot["amount"], lot["amount_opened"] = new_amount, opened


def cook_recipe(conn, recipe_id: int, *, servings: float | None = None) -> dict[str, Any]:
    """Deduct ingredients for one cook of ``recipe_id`` and queue shortfall.

    ``servings`` defaults to the recipe's stored servings count. Passing a
    different number scales every ingredient amount proportionally.
    """
    recipe = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not recipe:
        raise ValueError(f"Recipe {recipe_id} not found")
    base_servings = float(recipe["servings"] or 1)
    target_servings = float(servings) if servings else base_servings
    if target_servings <= 0:
        raise ValueError("servings must be > 0")
    ratio = target_servings / base_servings

    cat = Catalog(conn)
    ingredients = conn.execute(
        "SELECT ri.*, p.name AS product_name FROM recipe_ingredients ri "
        "JOIN products p ON p.id = ri.product_id WHERE ri.recipe_id = ? "
        "ORDER BY ri.sort_order, ri.id",
        (recipe_id,),
    ).fetchall()

    # Decided once, before anything is written: a recipe already on the list
    # (from "add to list") is not added again, while a product used twice in
    # this recipe still gets both shortfalls.
    listed = {
        pid for pid in {int(ing["product_id"]) for ing in ingredients}
        if recipe_already_listed(conn, pid, recipe_id, recipe["name"])
    }
    # Deductions and list rows commit together or not at all: the connection
    # is shared, so a half-done cook would leak into the next request's commit.
    try:
        deducted, shortfall_items, unmatched = _cook(conn, cat, recipe, ratio, ingredients, listed)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "recipe_id": recipe_id,
        "recipe_name": recipe["name"],
        "servings": target_servings,
        "deducted": deducted,
        "shortfall_added": shortfall_items,
        "unmatched": unmatched,
    }


def _cook(conn, cat: Catalog, recipe, ratio: float, ingredients, listed: set[int]):
    """Deduct and queue shortfall for every ingredient. Does not commit.
    Returns (deducted, shortfall_added, unmatched)."""
    taken: dict[tuple[int, int], float] = {}
    shortfall_items: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for ing in ingredients:
        pid = int(ing["product_id"])
        unit = cat.unit(ing["unit_id"])
        if (cat.product(pid) or {}).get("staple"):
            continue
        need = float(ing["amount"] or 0) * ratio
        if need <= EPS or unit is None or unit.dimension == VAGUE:
            continue

        remaining = need
        lots = cat.lots([pid, *cat.descendants(pid)])
        convertible = 0
        for lot in lots:
            if remaining <= EPS:
                break
            lot_unit = cat.unit(lot["unit_id"])
            if lot_unit is None:
                continue
            lot_dim = cat.stock_dim(lot["product_id"], lot_unit)
            conv = convert(lot["amount"], lot_unit, unit, cat.facts(lot["product_id"]), from_dim=lot_dim)
            if conv is None or conv.amount <= EPS:
                continue
            convertible += 1
            take_need = min(remaining, conv.amount)
            take_lot = float(lot["amount"]) * take_need / conv.amount
            _deduct(conn, lot, take_lot, package=lot_dim == PACKAGE)
            key = (int(lot["product_id"]), int(lot["unit_id"]))
            taken[key] = taken.get(key, 0.0) + take_lot
            remaining -= take_need

        if lots and not convertible:
            unmatched.append({
                "product_id": pid,
                "product_name": ing["product_name"],
                "amount": round(need, 3),
                "unit_id": int(ing["unit_id"]),
                "reason": "no_unit_conversion",
            })
            continue

        if remaining > 1e-6 and pid not in listed:
            add_recipe_need(
                conn, cat, product_id=pid, amount=remaining, unit_id=ing["unit_id"],
                recipe_id=recipe["id"], recipe_name=recipe["name"],
            )
            shortfall_items.append({
                "product_id": pid,
                "product_name": ing["product_name"],
                "amount": round(remaining, 3),
                "unit_id": int(ing["unit_id"]),
            })

    deducted: list[dict[str, Any]] = []
    for (pid, unit_id), amount in taken.items():
        log_event(
            conn, product_id=pid, event_type="consume", amount=amount,
            unit_id=unit_id, note=NOTE_PREFIX + recipe["name"],
        )
        deducted.append({
            "product_id": pid,
            "product_name": (cat.product(pid) or {}).get("name", ""),
            "amount": round(amount, 3),
            "unit_id": unit_id,
        })
    return deducted, shortfall_items, unmatched
