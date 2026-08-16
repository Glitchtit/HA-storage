"""Store registry and per-store product availability endpoints.

Stores are keyed by K-group store ID (e.g. "N110"). Availability rows are
assortment-level ("this store carries the product"), written by HA-scraper.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from fastapi import APIRouter, HTTPException, Response

from models import (
    AvailabilityEntry,
    ManualAvailabilityCreate,
    ProductStoreInfo,
    Store,
    StoreUpsert,
)

router = APIRouter(tags=["stores"])
log = logging.getLogger(__name__)


def _get_db():
    from main import get_connection
    return get_connection()


def _slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def _product_stores(conn, product_id: int):
    return conn.execute(
        """
        SELECT pa.store_id, s.name, pa.available, pa.price,
               pa.price_currency, pa.checked_at, pa.source
        FROM product_availability pa
        JOIN stores s ON s.id = pa.store_id
        WHERE pa.product_id = ?
        ORDER BY pa.store_id
        """,
        (product_id,),
    ).fetchall()


def _require_product(conn, product_id: int) -> None:
    if not conn.execute("SELECT id FROM products WHERE id = ?",
                        (product_id,)).fetchone():
        raise HTTPException(404, f"Product {product_id} not found")


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
    _require_product(conn, product_id)

    for entry in body:
        # Auto-register unknown stores under their raw ID; the scraper's
        # name registration upgrades this to a friendly name later.
        conn.execute(
            "INSERT OR IGNORE INTO stores (id, name) VALUES (?, ?)",
            (entry.store_id, entry.store_id),
        )
        # source flips back to 'scraper': a checked store beats a manual claim.
        conn.execute(
            """
            INSERT INTO product_availability
                (product_id, store_id, available, price, price_currency,
                 checked_at, source)
            VALUES (?, ?, ?, ?, ?, datetime('now'), 'scraper')
            ON CONFLICT(product_id, store_id) DO UPDATE SET
                available      = excluded.available,
                price          = excluded.price,
                price_currency = excluded.price_currency,
                checked_at     = excluded.checked_at,
                source         = excluded.source
            """,
            (product_id, entry.store_id, int(entry.available),
             entry.price, entry.price_currency),
        )
    conn.commit()
    return _product_stores(conn, product_id)


@router.post("/products/{product_id}/stores",
             response_model=list[ProductStoreInfo])
def add_manual_store(product_id: int, body: ManualAvailabilityCreate):
    """Manually mark a store as carrying *product_id*. Pass store_id for a
    registry store, or name to register a free-text 'manual-<slug>' store."""
    conn = _get_db()
    _require_product(conn, product_id)

    if bool(body.store_id) == bool(body.name and body.name.strip()):
        raise HTTPException(400, "Provide exactly one of store_id or name")

    if body.store_id:
        store_id = body.store_id
        if not conn.execute("SELECT id FROM stores WHERE id = ?",
                            (store_id,)).fetchone():
            raise HTTPException(404, f"Store {store_id} not found")
    else:
        name = body.name.strip()
        slug = _slugify(name)
        if not slug:
            raise HTTPException(400, "Store name has no usable characters")
        store_id = f"manual-{slug}"
        conn.execute("INSERT OR IGNORE INTO stores (id, name) VALUES (?, ?)",
                     (store_id, name))

    conn.execute(
        """
        INSERT INTO product_availability
            (product_id, store_id, available, checked_at, source)
        VALUES (?, ?, 1, datetime('now'), 'manual')
        ON CONFLICT(product_id, store_id) DO UPDATE SET
            available  = 1,
            checked_at = excluded.checked_at,
            source     = 'manual'
        """,
        (product_id, store_id),
    )
    conn.commit()
    return _product_stores(conn, product_id)


@router.delete("/products/{product_id}/stores/{store_id}", status_code=204)
def remove_product_store(product_id: int, store_id: str):
    conn = _get_db()
    cur = conn.execute(
        "DELETE FROM product_availability WHERE product_id = ? AND store_id = ?",
        (product_id, store_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, f"No availability row for product {product_id} "
                                 f"at store {store_id}")
    return Response(status_code=204)
