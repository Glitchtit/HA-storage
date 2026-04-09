"""Product group endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from models import ProductGroup, ProductGroupCreate

router = APIRouter(tags=["product-groups"])


def _get_db():
    from main import get_connection
    return get_connection()


@router.get("/product-groups", response_model=list[ProductGroup])
def list_groups():
    return _get_db().execute("SELECT * FROM product_groups ORDER BY name").fetchall()


@router.post("/product-groups", response_model=ProductGroup, status_code=201)
def create_group(body: ProductGroupCreate):
    conn = _get_db()
    existing = conn.execute(
        "SELECT id FROM product_groups WHERE name = ?", (body.name,)
    ).fetchone()
    if existing:
        raise HTTPException(409, f"Product group '{body.name}' already exists")
    cur = conn.execute(
        "INSERT INTO product_groups (name, description) VALUES (?, ?)",
        (body.name, body.description),
    )
    conn.commit()
    return conn.execute("SELECT * FROM product_groups WHERE id = ?", (cur.lastrowid,)).fetchone()


@router.put("/product-groups/{group_id}", response_model=ProductGroup)
def update_group(group_id: int, body: ProductGroupCreate):
    conn = _get_db()
    if not conn.execute("SELECT id FROM product_groups WHERE id = ?", (group_id,)).fetchone():
        raise HTTPException(404, f"Product group {group_id} not found")
    conn.execute(
        "UPDATE product_groups SET name = ?, description = ? WHERE id = ?",
        (body.name, body.description, group_id),
    )
    conn.commit()
    return conn.execute("SELECT * FROM product_groups WHERE id = ?", (group_id,)).fetchone()


@router.delete("/product-groups/{group_id}", status_code=204)
def delete_group(group_id: int):
    conn = _get_db()
    if not conn.execute("SELECT id FROM product_groups WHERE id = ?", (group_id,)).fetchone():
        raise HTTPException(404, f"Product group {group_id} not found")
    conn.execute("DELETE FROM product_groups WHERE id = ?", (group_id,))
    conn.commit()
