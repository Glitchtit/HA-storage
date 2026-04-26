"""Shopping list endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from models import ShoppingItem, ShoppingItemCreate, ShoppingItemUpdate

router = APIRouter(tags=["shopping-list"])
log = logging.getLogger(__name__)


def _get_db():
    from main import get_connection
    return get_connection()


@router.get("/shopping-list", response_model=list[ShoppingItem])
def list_shopping():
    return _get_db().execute(
        "SELECT * FROM shopping_list ORDER BY done, created_at DESC"
    ).fetchall()


@router.delete("/shopping-list/done", status_code=204)
def clear_done():
    """Clear all completed items."""
    conn = _get_db()
    conn.execute("DELETE FROM shopping_list WHERE done = 1")
    conn.commit()


@router.post("/shopping-list", response_model=ShoppingItem, status_code=201)
def add_shopping_item(body: ShoppingItemCreate):
    conn = _get_db()
    product = conn.execute("SELECT id, name FROM products WHERE id = ?", (body.product_id,)).fetchone()
    if not product:
        raise HTTPException(400, f"Product {body.product_id} not found")
    cur = conn.execute(
        "INSERT INTO shopping_list (product_id, amount, unit_id, note, recipe_id, ha_item_name)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (body.product_id, body.amount, body.unit_id, body.note, body.recipe_id, product["name"]),
    )
    conn.commit()
    return conn.execute("SELECT * FROM shopping_list WHERE id = ?", (cur.lastrowid,)).fetchone()


@router.put("/shopping-list/{item_id}", response_model=ShoppingItem)
def update_shopping_item(item_id: int, body: ShoppingItemUpdate):
    conn = _get_db()
    existing = conn.execute("SELECT * FROM shopping_list WHERE id = ?", (item_id,)).fetchone()
    if not existing:
        raise HTTPException(404, f"Shopping list item {item_id} not found")

    updates = {}
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "done":
            value = int(value)
        updates[field] = value
    if not updates:
        return existing
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE shopping_list SET {set_clause} WHERE id = ?",
        list(updates.values()) + [item_id],
    )
    conn.commit()
    return conn.execute("SELECT * FROM shopping_list WHERE id = ?", (item_id,)).fetchone()


@router.delete("/shopping-list/{item_id}", status_code=204)
def delete_shopping_item(item_id: int):
    conn = _get_db()
    if not conn.execute("SELECT id FROM shopping_list WHERE id = ?", (item_id,)).fetchone():
        raise HTTPException(404, f"Shopping list item {item_id} not found")
    conn.execute("DELETE FROM shopping_list WHERE id = ?", (item_id,))
    conn.commit()
