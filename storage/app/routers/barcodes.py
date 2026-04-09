"""Barcode and barcode queue endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from models import (
    Barcode,
    BarcodeCreate,
    BarcodeQueueCreate,
    BarcodeQueueEntry,
    BarcodeQueueUpdate,
    BarcodeUpdate,
)

router = APIRouter(tags=["barcodes"])


def _get_db():
    from main import get_connection
    return get_connection()


# ── Barcodes ───────────────────────────────────────────────────────────────

@router.get("/barcodes", response_model=list[Barcode])
def list_barcodes():
    return _get_db().execute("SELECT * FROM barcodes ORDER BY barcode").fetchall()


@router.post("/barcodes", response_model=Barcode, status_code=201)
def create_barcode(body: BarcodeCreate):
    conn = _get_db()
    if not conn.execute("SELECT id FROM products WHERE id = ?", (body.product_id,)).fetchone():
        raise HTTPException(400, f"Product {body.product_id} not found")
    existing = conn.execute("SELECT id FROM barcodes WHERE barcode = ?", (body.barcode,)).fetchone()
    if existing:
        raise HTTPException(409, f"Barcode '{body.barcode}' already exists")
    cur = conn.execute(
        "INSERT INTO barcodes (product_id, barcode, pack_size, pack_unit_id) VALUES (?, ?, ?, ?)",
        (body.product_id, body.barcode, body.pack_size, body.pack_unit_id),
    )
    conn.commit()
    return conn.execute("SELECT * FROM barcodes WHERE id = ?", (cur.lastrowid,)).fetchone()


@router.put("/barcodes/{barcode_id}", response_model=Barcode)
def update_barcode(barcode_id: int, body: BarcodeUpdate):
    conn = _get_db()
    existing = conn.execute("SELECT * FROM barcodes WHERE id = ?", (barcode_id,)).fetchone()
    if not existing:
        raise HTTPException(404, f"Barcode {barcode_id} not found")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return existing
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE barcodes SET {set_clause} WHERE id = ?",
        list(updates.values()) + [barcode_id],
    )
    conn.commit()
    return conn.execute("SELECT * FROM barcodes WHERE id = ?", (barcode_id,)).fetchone()


@router.delete("/barcodes/{barcode_id}", status_code=204)
def delete_barcode(barcode_id: int):
    conn = _get_db()
    if not conn.execute("SELECT id FROM barcodes WHERE id = ?", (barcode_id,)).fetchone():
        raise HTTPException(404, f"Barcode {barcode_id} not found")
    conn.execute("DELETE FROM barcodes WHERE id = ?", (barcode_id,))
    conn.commit()


# ── Barcode Queue ──────────────────────────────────────────────────────────

@router.get("/barcode-queue", response_model=list[BarcodeQueueEntry])
def list_barcode_queue(status: str | None = None):
    conn = _get_db()
    if status:
        return conn.execute(
            "SELECT * FROM barcode_queue WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    return conn.execute("SELECT * FROM barcode_queue ORDER BY created_at DESC").fetchall()


@router.post("/barcode-queue", response_model=BarcodeQueueEntry, status_code=201)
def enqueue_barcode(body: BarcodeQueueCreate):
    conn = _get_db()
    cur = conn.execute(
        "INSERT INTO barcode_queue (barcode, source) VALUES (?, ?)",
        (body.barcode, body.source),
    )
    conn.commit()
    return conn.execute("SELECT * FROM barcode_queue WHERE id = ?", (cur.lastrowid,)).fetchone()


@router.put("/barcode-queue/{entry_id}", response_model=BarcodeQueueEntry)
def update_queue_entry(entry_id: int, body: BarcodeQueueUpdate):
    conn = _get_db()
    existing = conn.execute("SELECT * FROM barcode_queue WHERE id = ?", (entry_id,)).fetchone()
    if not existing:
        raise HTTPException(404, f"Queue entry {entry_id} not found")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return existing
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE barcode_queue SET {set_clause} WHERE id = ?",
        list(updates.values()) + [entry_id],
    )
    conn.commit()
    return conn.execute("SELECT * FROM barcode_queue WHERE id = ?", (entry_id,)).fetchone()


@router.delete("/barcode-queue/{entry_id}", status_code=204)
def delete_queue_entry(entry_id: int):
    conn = _get_db()
    if not conn.execute("SELECT id FROM barcode_queue WHERE id = ?", (entry_id,)).fetchone():
        raise HTTPException(404, f"Queue entry {entry_id} not found")
    conn.execute("DELETE FROM barcode_queue WHERE id = ?", (entry_id,))
    conn.commit()
