"""Shopping list endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

import ha_sync
from models import ShoppingItem, ShoppingItemCreate, ShoppingItemUpdate

router = APIRouter(tags=["shopping-list"])
log = logging.getLogger(__name__)


def _get_db():
    from main import get_connection
    return get_connection()


def _item_ha_name(conn, item_id: int) -> str | None:
    row = conn.execute(
        "SELECT sl.ha_item_name, p.name FROM shopping_list sl"
        " JOIN products p ON p.id = sl.product_id WHERE sl.id = ?",
        (item_id,),
    ).fetchone()
    if not row:
        return None
    return row["ha_item_name"] or row["name"]


@router.get("/shopping-list", response_model=list[ShoppingItem])
def list_shopping():
    return _get_db().execute(
        "SELECT * FROM shopping_list ORDER BY done, created_at DESC"
    ).fetchall()


@router.delete("/shopping-list/done", status_code=204)
def clear_done():
    """Clear all completed items."""
    conn = _get_db()
    done_items = conn.execute(
        "SELECT sl.id, sl.ha_item_name, p.name FROM shopping_list sl"
        " JOIN products p ON p.id = sl.product_id WHERE sl.done = 1"
    ).fetchall()
    for item in done_items:
        name = item["ha_item_name"] or item["name"]
        ha_sync.ha_remove_item(conn, name)
    conn.execute("DELETE FROM shopping_list WHERE done = 1")
    conn.commit()


@router.post("/shopping-list/ha-sync", status_code=200)
def trigger_ha_sync():
    """Manually trigger a full sync to the HA to-do list."""
    conn = _get_db()
    result = ha_sync.ha_full_sync(conn)
    return result


@router.get("/shopping-list/ha-status", status_code=200)
def ha_status():
    """Return HA To-do integration status (token availability + entity existence)."""
    conn = _get_db()
    return ha_sync.ha_check_status(conn)


@router.post("/shopping-list", response_model=ShoppingItem, status_code=201)
def add_shopping_item(body: ShoppingItemCreate):
    conn = _get_db()
    product = conn.execute("SELECT id, name FROM products WHERE id = ?", (body.product_id,)).fetchone()
    if not product:
        raise HTTPException(400, f"Product {body.product_id} not found")
    item_name = product["name"]
    ha_sync.ha_ensure_entity(conn)
    cur = conn.execute(
        "INSERT INTO shopping_list (product_id, amount, unit_id, note, recipe_id, ha_item_name)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (body.product_id, body.amount, body.unit_id, body.note, body.recipe_id, item_name),
    )
    conn.commit()
    ha_sync.ha_add_item(conn, item_name, body.note or "")
    return conn.execute("SELECT * FROM shopping_list WHERE id = ?", (cur.lastrowid,)).fetchone()


@router.put("/shopping-list/{item_id}", response_model=ShoppingItem)
def update_shopping_item(item_id: int, body: ShoppingItemUpdate):
    conn = _get_db()
    existing = conn.execute("SELECT * FROM shopping_list WHERE id = ?", (item_id,)).fetchone()
    if not existing:
        raise HTTPException(404, f"Shopping list item {item_id} not found")

    was_done = bool(existing["done"])
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

    if "done" in updates:
        ha_name = existing["ha_item_name"] or _item_ha_name(conn, item_id)
        if ha_name:
            now_done = bool(updates["done"])
            if now_done and not was_done:
                ha_sync.ha_complete_item(conn, ha_name)
            elif not now_done and was_done:
                ha_sync.ha_uncomplete_item(conn, ha_name)

    return conn.execute("SELECT * FROM shopping_list WHERE id = ?", (item_id,)).fetchone()


@router.delete("/shopping-list/{item_id}", status_code=204)
def delete_shopping_item(item_id: int):
    conn = _get_db()
    item = conn.execute(
        "SELECT sl.ha_item_name, p.name FROM shopping_list sl"
        " JOIN products p ON p.id = sl.product_id WHERE sl.id = ?",
        (item_id,),
    ).fetchone()
    if not item:
        raise HTTPException(404, f"Shopping list item {item_id} not found")
    ha_name = item["ha_item_name"] or item["name"]
    conn.execute("DELETE FROM shopping_list WHERE id = ?", (item_id,))
    conn.commit()
    ha_sync.ha_remove_item(conn, ha_name)
