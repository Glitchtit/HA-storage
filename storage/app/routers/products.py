"""Product CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from models import Product, ProductCreate, ProductDetail, ProductUpdate

router = APIRouter(tags=["products"])


def _get_db():
    from main import get_connection
    return get_connection()


# ── List / Get ─────────────────────────────────────────────────────────────

@router.get("/products", response_model=list[Product])
def list_products(
    parent_id: int | None = Query(None),
    group_id: int | None = Query(None),
    active_only: bool = Query(True),
):
    conn = _get_db()
    clauses = []
    params: list = []
    if active_only:
        clauses.append("active = 1")
    if parent_id is not None:
        clauses.append("parent_id = ?")
        params.append(parent_id)
    if group_id is not None:
        clauses.append("product_group_id = ?")
        params.append(group_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return conn.execute(f"SELECT * FROM products{where} ORDER BY name", params).fetchall()


@router.get("/products/{product_id}", response_model=ProductDetail)
def get_product(product_id: int):
    conn = _get_db()
    row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Product {product_id} not found")

    children = conn.execute(
        "SELECT * FROM products WHERE parent_id = ?", (product_id,)
    ).fetchall()
    barcodes = conn.execute(
        "SELECT * FROM barcodes WHERE product_id = ?", (product_id,)
    ).fetchall()
    stock_row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) as total, COALESCE(SUM(amount_opened), 0) as opened "
        "FROM stock WHERE product_id = ?",
        (product_id,),
    ).fetchone()

    return {
        **row,
        "children": children,
        "barcodes": barcodes,
        "stock_amount": stock_row["total"],
        "stock_opened": stock_row["opened"],
    }


@router.get("/products/by-barcode/{barcode}", response_model=ProductDetail)
def get_product_by_barcode(barcode: str):
    conn = _get_db()
    bc = conn.execute("SELECT * FROM barcodes WHERE barcode = ?", (barcode,)).fetchone()
    if not bc:
        raise HTTPException(404, f"Barcode '{barcode}' not found")
    return get_product(bc["product_id"])


# ── Create ─────────────────────────────────────────────────────────────────

@router.post("/products", response_model=Product, status_code=201)
def create_product(body: ProductCreate):
    conn = _get_db()
    # Validate unit exists
    if not conn.execute("SELECT id FROM units WHERE id = ?", (body.unit_id,)).fetchone():
        raise HTTPException(400, f"Unit {body.unit_id} not found")
    cur = conn.execute(
        """INSERT INTO products (name, description, parent_id, location_id,
           product_group_id, unit_id, default_best_before_days, min_stock_amount,
           picture_filename, active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            body.name, body.description, body.parent_id, body.location_id,
            body.product_group_id, body.unit_id, body.default_best_before_days,
            body.min_stock_amount, body.picture_filename, int(body.active),
        ),
    )
    conn.commit()
    return conn.execute("SELECT * FROM products WHERE id = ?", (cur.lastrowid,)).fetchone()


# ── Update ─────────────────────────────────────────────────────────────────

@router.put("/products/{product_id}", response_model=Product)
def update_product(product_id: int, body: ProductUpdate):
    conn = _get_db()
    existing = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not existing:
        raise HTTPException(404, f"Product {product_id} not found")

    updates = {}
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "active":
            value = int(value)
        updates[field] = value

    if not updates:
        return existing

    if "unit_id" in updates:
        if not conn.execute("SELECT id FROM units WHERE id = ?", (updates["unit_id"],)).fetchone():
            raise HTTPException(400, f"Unit {updates['unit_id']} not found")

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [product_id]
    conn.execute(
        f"UPDATE products SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
        params,
    )
    conn.commit()
    return conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()


# ── Delete ─────────────────────────────────────────────────────────────────

@router.delete("/products/{product_id}", status_code=204)
def delete_product(product_id: int):
    conn = _get_db()
    existing = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
    if not existing:
        raise HTTPException(404, f"Product {product_id} not found")
    # Clean up references that lack ON DELETE CASCADE
    conn.execute("DELETE FROM recipe_ingredients WHERE product_id = ?", (product_id,))
    conn.execute("DELETE FROM shopping_list WHERE product_id = ?", (product_id,))
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
