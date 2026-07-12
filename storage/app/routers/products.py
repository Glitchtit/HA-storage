"""Product CRUD endpoints."""

from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, HTTPException, Query

from models import Product, ProductCreate, ProductDetail, ProductUpdate
import tree

router = APIRouter(tags=["products"])
log = logging.getLogger(__name__)


def _get_db():
    from main import get_connection
    return get_connection()


def _stores_by_product(conn, product_ids: list[int] | None = None) -> dict[int, list[dict]]:
    """Map product_id → per-store availability dicts (joined with store names).
    With product_ids=None the whole table is fetched (bulk list endpoint)."""
    sql = """
        SELECT pa.product_id, pa.store_id, s.name, pa.available,
               pa.price, pa.price_currency, pa.checked_at
        FROM product_availability pa
        JOIN stores s ON s.id = pa.store_id
    """
    params: list = []
    if product_ids is not None:
        sql += " WHERE pa.product_id IN (%s)" % ",".join("?" for _ in product_ids)
        params = list(product_ids)
    out: dict[int, list[dict]] = {}
    for r in conn.execute(sql + " ORDER BY pa.store_id", params).fetchall():
        out.setdefault(r["product_id"], []).append({
            "store_id": r["store_id"],
            "name": r["name"],
            "available": bool(r["available"]),
            "price": r["price"],
            "price_currency": r["price_currency"],
            "checked_at": r["checked_at"],
        })
    return out


def _ai_configured(conn) -> bool:
    """True only when the active AI provider has the credential it needs — no
    point spawning an auto-placement thread that will just fail (and it keeps
    the test suite, which has no keys, from making live calls)."""
    from ai_client import _get_ai_config
    cfg = _get_ai_config(conn)
    provider = cfg.get("provider", "gemini")
    needed = {"gemini": "gemini_api_key", "claude": "claude_api_key",
              "ollama": "ollama_url"}.get(provider, "gemini_api_key")
    return bool(cfg.get(needed))


def _autoplace_enabled(conn) -> bool:
    """Whether freshly created, ungrouped products should be AI-categorised at
    create time. Requires the `autoplace_on_create` config key to be on (default
    on) AND a usable AI provider configured."""
    row = conn.execute(
        "SELECT value FROM config WHERE key = 'autoplace_on_create'"
    ).fetchone()
    toggle_on = row is None or str(row["value"]).strip().lower() not in (
        "0", "false", "no", "off")
    return toggle_on and _ai_configured(conn)


def _autoplace_product(product_id: int) -> None:
    """Background, best-effort: assign a type-parent + category to a just-created
    product via the AI optimizer. Opens its OWN connection — never shares the
    request connection across threads. Any failure (AI offline, etc.) is a
    no-op; product creation already succeeded."""
    try:
        from main import DB_PATH
        from database import get_db
        from optimizer import run_optimize
        conn = get_db(DB_PATH)
        try:
            run_optimize(conn, product_ids=[product_id])
        finally:
            conn.close()
    except Exception as exc:
        log.warning("Auto-placement for product %d failed: %s", product_id, exc)


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
        clauses.append("p.active = 1")
    if parent_id is not None:
        clauses.append("p.parent_id = ?")
        params.append(parent_id)
    if group_id is not None:
        clauses.append("p.product_group_id = ?")
        params.append(group_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"""
        WITH child_totals AS (
            SELECT cp.parent_id AS pid,
                   COALESCE(SUM(cs.amount), 0) AS amount,
                   COALESCE(SUM(cs.amount_opened), 0) AS opened
            FROM products cp
            JOIN stock cs ON cs.product_id = cp.id
            WHERE cp.parent_id IS NOT NULL
            GROUP BY cp.parent_id
        )
        SELECT p.*,
               COALESCE(SUM(s.amount), 0) AS stock_amount,
               COALESCE(SUM(s.amount_opened), 0) AS stock_opened,
               COALESCE(ct.amount, 0) AS children_stock_amount,
               COALESCE(ct.opened, 0) AS children_stock_opened
        FROM products p
        LEFT JOIN stock s ON s.product_id = p.id
        LEFT JOIN child_totals ct ON ct.pid = p.id
        {where}
        GROUP BY p.id
        ORDER BY p.name
        """,
        params,
    ).fetchall()
    stores_map = _stores_by_product(conn)
    return [{**row, "stores": stores_map.get(row["id"], [])} for row in rows]


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
    child_stock_row = conn.execute(
        "SELECT COALESCE(SUM(s.amount), 0) as total, COALESCE(SUM(s.amount_opened), 0) as opened "
        "FROM stock s JOIN products p ON p.id = s.product_id WHERE p.parent_id = ?",
        (product_id,),
    ).fetchone()

    return {
        **row,
        "children": children,
        "barcodes": barcodes,
        "stock_amount": stock_row["total"],
        "stock_opened": stock_row["opened"],
        "children_stock_amount": child_stock_row["total"],
        "children_stock_opened": child_stock_row["opened"],
        "stores": _stores_by_product(conn, [product_id]).get(product_id, []),
    }


@router.get("/products/by-barcode/{barcode}", response_model=ProductDetail)
def get_product_by_barcode(barcode: str):
    conn = _get_db()
    bc = conn.execute("SELECT * FROM barcodes WHERE barcode = ?", (barcode,)).fetchone()
    if not bc:
        raise HTTPException(404, f"Barcode '{barcode}' not found")
    detail = get_product(bc["product_id"])
    return {**detail, "matched_pack_size": float(bc["pack_size"] or 1)}


# ── Create ─────────────────────────────────────────────────────────────────

@router.post("/products", response_model=Product, status_code=201)
def create_product(body: ProductCreate):
    conn = _get_db()
    # Validate unit exists
    if not conn.execute("SELECT id FROM units WHERE id = ?", (body.unit_id,)).fetchone():
        raise HTTPException(400, f"Unit {body.unit_id} not found")
    try:
        tree.assert_valid_parent(conn, None, body.parent_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    cur = conn.execute(
        """INSERT INTO products (name, description, parent_id, location_id,
           product_group_id, unit_id, default_best_before_days, min_stock_amount,
           picture_filename, active, unit_price, unit_price_currency, pack_count, staple)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            body.name, body.description, body.parent_id, body.location_id,
            body.product_group_id, body.unit_id, body.default_best_before_days,
            body.min_stock_amount, body.picture_filename, int(body.active),
            body.unit_price, body.unit_price_currency or "EUR", body.pack_count, int(body.staple),
        ),
    )
    conn.commit()
    new = conn.execute("SELECT * FROM products WHERE id = ?", (cur.lastrowid,)).fetchone()
    # New brands scanned during shopping arrive ungrouped; place them under the
    # right type-parent so the catalog (and future exact matching) stays tidy.
    # Best-effort, off the request path — never blocks or fails the create.
    if new["parent_id"] is None and new["product_group_id"] is None and _autoplace_enabled(conn):
        threading.Thread(
            target=_autoplace_product, args=(new["id"],), daemon=True
        ).start()
    return new


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
        if field == "staple":
            value = int(value)
        updates[field] = value

    if not updates:
        return existing

    if "unit_id" in updates:
        if not conn.execute("SELECT id FROM units WHERE id = ?", (updates["unit_id"],)).fetchone():
            raise HTTPException(400, f"Unit {updates['unit_id']} not found")

    if "parent_id" in updates:
        try:
            tree.assert_valid_parent(conn, product_id, updates["parent_id"])
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [product_id]
    conn.execute(
        f"UPDATE products SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
        params,
    )
    conn.commit()
    if "min_stock_amount" in updates or "active" in updates:
        from routers.shopping import sync_auto_shopping
        sync_auto_shopping(conn)
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
