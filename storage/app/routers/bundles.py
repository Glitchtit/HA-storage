"""Quick-add shopping bundle endpoints.

A bundle is a named set of products ("Taco night") the user pushes onto the
shopping list in one tap. No amounts, no units — every push adds amount 1 of
each checked product. Distinct from recipes on purpose: no ingredients table,
no availability math, no AI.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from models import (
    Bundle,
    BundleCreate,
    BundleDetail,
    BundleToShoppingRequest,
    BundleToShoppingResponse,
    BundleUpdate,
)

router = APIRouter(tags=["bundles"])
log = logging.getLogger(__name__)


def _get_db():
    from main import get_connection
    return get_connection()


def _bundle_row(conn, bundle_id: int) -> dict:
    row = conn.execute(
        """
        SELECT b.id, b.name, b.emoji, b.sort_order, b.created_at,
               (SELECT COUNT(*) FROM bundle_items bi WHERE bi.bundle_id = b.id) AS item_count
          FROM bundles b
         WHERE b.id = ?
        """,
        (bundle_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Bundle {bundle_id} not found")
    return row


def _bundle_detail(conn, bundle_id: int) -> dict:
    bundle = dict(_bundle_row(conn, bundle_id))
    bundle["items"] = conn.execute(
        """
        SELECT bi.id, bi.product_id, bi.sort_order,
               COALESCE(p.name, '') AS product_name,
               COALESCE((SELECT SUM(s.amount) FROM stock s
                          WHERE s.product_id = bi.product_id), 0) AS stock_amount,
               EXISTS(SELECT 1 FROM shopping_list sl
                       WHERE sl.product_id = bi.product_id AND sl.done = 0) AS on_list
          FROM bundle_items bi
          LEFT JOIN products p ON p.id = bi.product_id
         WHERE bi.bundle_id = ?
         ORDER BY bi.sort_order, bi.id
        """,
        (bundle_id,),
    ).fetchall()
    for item in bundle["items"]:
        item["on_list"] = bool(item["on_list"])
    return bundle


def _validate_products(conn, product_ids: list[int]) -> None:
    for pid in product_ids:
        if not conn.execute("SELECT id FROM products WHERE id = ?", (pid,)).fetchone():
            raise HTTPException(400, f"Product {pid} not found")


def _replace_items(conn, bundle_id: int, items) -> None:
    conn.execute("DELETE FROM bundle_items WHERE bundle_id = ?", (bundle_id,))
    for idx, item in enumerate(items):
        conn.execute(
            "INSERT OR IGNORE INTO bundle_items (bundle_id, product_id, sort_order) VALUES (?, ?, ?)",
            (bundle_id, item.product_id, idx),
        )


@router.get("/bundles", response_model=list[Bundle])
def list_bundles():
    return _get_db().execute(
        """
        SELECT b.id, b.name, b.emoji, b.sort_order, b.created_at,
               (SELECT COUNT(*) FROM bundle_items bi WHERE bi.bundle_id = b.id) AS item_count
          FROM bundles b
         ORDER BY b.sort_order, b.name
        """
    ).fetchall()


@router.get("/bundles/{bundle_id}", response_model=BundleDetail)
def get_bundle(bundle_id: int):
    return _bundle_detail(_get_db(), bundle_id)


@router.post("/bundles", response_model=BundleDetail, status_code=201)
def create_bundle(body: BundleCreate):
    conn = _get_db()
    _validate_products(conn, [i.product_id for i in body.items])
    cur = conn.execute(
        "INSERT INTO bundles (name, emoji, sort_order) VALUES (?, ?, ?)",
        (body.name, body.emoji, body.sort_order),
    )
    _replace_items(conn, cur.lastrowid, body.items)
    conn.commit()
    return _bundle_detail(conn, cur.lastrowid)


@router.put("/bundles/{bundle_id}", response_model=BundleDetail)
def update_bundle(bundle_id: int, body: BundleUpdate):
    conn = _get_db()
    _bundle_row(conn, bundle_id)  # 404 guard
    # Validate products BEFORE any mutations
    if body.items is not None:
        _validate_products(conn, [i.product_id for i in body.items])
    updates = {}
    for field in ("name", "emoji", "sort_order"):
        value = getattr(body, field)
        if value is not None:
            updates[field] = value
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE bundles SET {set_clause} WHERE id = ?",
            list(updates.values()) + [bundle_id],
        )
    if body.items is not None:
        _replace_items(conn, bundle_id, body.items)
    conn.commit()
    return _bundle_detail(conn, bundle_id)


@router.delete("/bundles/{bundle_id}", status_code=204)
def delete_bundle(bundle_id: int):
    conn = _get_db()
    _bundle_row(conn, bundle_id)  # 404 guard
    conn.execute("DELETE FROM bundles WHERE id = ?", (bundle_id,))
    conn.commit()
