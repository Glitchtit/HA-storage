"""Store registry and per-store product availability endpoints.

Stores are keyed by K-group store ID (e.g. "N110"). Availability rows are
assortment-level ("this store carries the product"), written by HA-scraper.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from models import AvailabilityEntry, ProductStoreInfo, Store, StoreUpsert

router = APIRouter(tags=["stores"])
log = logging.getLogger(__name__)


def _get_db():
    from main import get_connection
    return get_connection()


@router.get("/stores", response_model=list[Store])
def list_stores():
    conn = _get_db()
    return conn.execute("SELECT * FROM stores ORDER BY id").fetchall()


@router.put("/stores/{store_id}", response_model=Store)
def upsert_store(store_id: str, body: StoreUpsert):
    conn = _get_db()
    conn.execute(
        """
        INSERT INTO stores (id, name) VALUES (?, ?)
        ON CONFLICT(id) DO UPDATE SET name = excluded.name,
                                      updated_at = datetime('now')
        """,
        (store_id, body.name),
    )
    conn.commit()
    return conn.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()


@router.put("/products/{product_id}/availability",
            response_model=list[ProductStoreInfo])
def set_product_availability(product_id: int, body: list[AvailabilityEntry]):
    """Upsert availability rows for *product_id*. Only the stores present in
    the body are written; existing rows for other stores stay untouched, so
    a scraper run that could not reach a store never erases its last-known
    state. ``checked_at`` is stamped server-side."""
    conn = _get_db()
    row = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Product {product_id} not found")

    for entry in body:
        # Auto-register unknown stores under their raw ID; the scraper's
        # name registration upgrades this to a friendly name later.
        conn.execute(
            "INSERT OR IGNORE INTO stores (id, name) VALUES (?, ?)",
            (entry.store_id, entry.store_id),
        )
        conn.execute(
            """
            INSERT INTO product_availability
                (product_id, store_id, available, price, price_currency, checked_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(product_id, store_id) DO UPDATE SET
                available      = excluded.available,
                price          = excluded.price,
                price_currency = excluded.price_currency,
                checked_at     = excluded.checked_at
            """,
            (product_id, entry.store_id, int(entry.available),
             entry.price, entry.price_currency),
        )
    conn.commit()
    return conn.execute(
        """
        SELECT pa.store_id, s.name, pa.available, pa.price,
               pa.price_currency, pa.checked_at
        FROM product_availability pa
        JOIN stores s ON s.id = pa.store_id
        WHERE pa.product_id = ?
        ORDER BY pa.store_id
        """,
        (product_id,),
    ).fetchall()
