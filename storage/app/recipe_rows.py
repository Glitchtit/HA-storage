"""Write recipe needs onto the shopping list (shared by to-shopping and cook).

A need lands on the ingredient's own bound product — the variant for strict
ingredients, the concept itself for loose ones, never its parent — in the
ingredient's unit. Two exceptions keep rows buyable:
- a count need bound directly to a SKU ("1 kpl" of a 1 kg potato bag) is
  converted to whole retail packages (unit NULL);
- a need is merged into an existing open recipe row of the same product when
  the units convert, and the row's note lists every recipe ("Reseptistä: A, B").
Manual rows are never touched.
"""
from __future__ import annotations

import math
import sqlite3

from facts import Catalog
from quantity import COUNT, PACKAGE, Unit, convert

NOTE_PREFIX = "Reseptistä: "


def _row_unit(cat: Catalog, product_id: int, unit_id: int | None) -> tuple[Unit, str] | None:
    """(unit, dimension) of a shopping quantity; NULL means retail packages."""
    if unit_id is None:
        kpl = cat.unit_by_abbr("kpl")
        return (kpl, PACKAGE) if kpl else None
    unit = cat.unit(unit_id)
    if unit is None:
        return None
    return unit, cat.stock_dim(product_id, unit)


def _merged_note(note: str | None, recipe_name: str) -> str:
    note = note or ""
    if not note.startswith(NOTE_PREFIX):
        return note
    names = [n.strip() for n in note[len(NOTE_PREFIX):].split(",") if n.strip()]
    if recipe_name not in names:
        names.append(recipe_name)
    return NOTE_PREFIX + ", ".join(names)


def recipe_already_listed(conn: sqlite3.Connection, product_id: int, recipe_id: int, recipe_name: str) -> bool:
    """True when an open recipe row for this product already carries this
    recipe — pressing "add to list" twice must not double the amounts."""
    for row in conn.execute(
        "SELECT recipe_id, note FROM shopping_list "
        "WHERE product_id = ? AND done = 0 AND recipe_id IS NOT NULL",
        (product_id,),
    ).fetchall():
        if row["recipe_id"] == recipe_id:
            return True
        note = row["note"] or ""
        if note.startswith(NOTE_PREFIX) and recipe_name in [
            n.strip() for n in note[len(NOTE_PREFIX):].split(",")
        ]:
            return True
    return False


def needs_in_unit(cat: Catalog, product_id: int, unit: Unit, needs: list[tuple[float, Unit]]) -> float:
    """Sum earlier recipe needs of the same product, converted into `unit`.
    Needs that cannot be converted are left out."""
    total = 0.0
    for amount, need_unit in needs:
        conv = convert(amount, need_unit, unit, cat.facts(product_id))
        if conv is not None:
            total += conv.amount
    return total


def add_recipe_need(
    conn: sqlite3.Connection,
    cat: Catalog,
    *,
    product_id: int,
    amount: float,
    unit_id: int | None,
    recipe_id: int,
    recipe_name: str,
) -> tuple[int, bool]:
    """Put `amount` of `unit_id` for `product_id` on the list. Returns
    (row_id, merged). Does not commit."""
    unit = cat.unit(unit_id)
    if unit is not None and unit.dimension == COUNT and cat.is_sku(product_id):
        kpl = cat.unit_by_abbr("kpl")
        conv = convert(amount, unit, kpl, cat.facts(product_id), from_dim=COUNT, to_dim=PACKAGE)
        amount = float(math.ceil((conv.amount if conv else amount) - 1e-9))
        unit_id = None

    new_side = _row_unit(cat, product_id, unit_id)
    for row in conn.execute(
        "SELECT id, amount, unit_id, note FROM shopping_list "
        "WHERE product_id = ? AND done = 0 AND recipe_id IS NOT NULL AND auto_added = 0 "
        "ORDER BY created_at, id",
        (product_id,),
    ).fetchall():
        row_side = _row_unit(cat, product_id, row["unit_id"])
        if new_side is None or row_side is None:
            continue
        conv = convert(
            amount, new_side[0], row_side[0], cat.facts(product_id),
            from_dim=new_side[1], to_dim=row_side[1],
        )
        if conv is None:
            continue
        conn.execute(
            "UPDATE shopping_list SET amount = ?, note = ? WHERE id = ?",
            (round(float(row["amount"]) + conv.amount, 3), _merged_note(row["note"], recipe_name), row["id"]),
        )
        return row["id"], True

    product = cat.product(product_id) or {}
    cur = conn.execute(
        "INSERT INTO shopping_list (product_id, amount, unit_id, note, recipe_id, ha_item_name) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (product_id, round(amount, 3), unit_id, NOTE_PREFIX + recipe_name, recipe_id,
         product.get("name")),
    )
    return cur.lastrowid, False
