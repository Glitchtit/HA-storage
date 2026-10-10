"""Unit and unit conversion endpoints."""

from __future__ import annotations

from collections import deque

from fastapi import APIRouter, HTTPException, Query

from models import (
    Conversion,
    ConversionCreate,
    ConversionResult,
    Unit,
    UnitCreate,
    UnitResolveResult,
)
from quantity import DIMENSIONS, normalize_unit_text

router = APIRouter(tags=["units"])


def _get_db():
    from main import get_connection
    return get_connection()


# ── Units ──────────────────────────────────────────────────────────────────

@router.get("/units", response_model=list[Unit])
def list_units():
    return _get_db().execute("SELECT * FROM units ORDER BY abbreviation").fetchall()


@router.get("/units/resolve", response_model=UnitResolveResult)
def resolve_unit_text(text: str = Query(..., min_length=1)):
    """Map free unit text ("msk", "burk", "cups") to a Storage unit.

    The multiplier converts an amount written in `text` into the returned
    unit (1 cup = 2.4 dl). 404 when the text is not a known unit word —
    callers must surface that instead of guessing a unit.
    """
    hit = normalize_unit_text(text)
    if hit is None:
        raise HTTPException(404, f"Unknown unit '{text}'")
    abbr, multiplier = hit
    row = _get_db().execute(
        "SELECT id FROM units WHERE abbreviation = ?", (abbr,)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Unit '{abbr}' is not installed")
    return UnitResolveResult(unit_id=row["id"], abbreviation=abbr, multiplier=multiplier)


@router.post("/units", response_model=Unit, status_code=201)
def create_unit(body: UnitCreate):
    conn = _get_db()
    if body.dimension is not None and body.dimension not in DIMENSIONS:
        raise HTTPException(400, f"dimension must be one of {DIMENSIONS}")
    existing = conn.execute(
        "SELECT id FROM units WHERE abbreviation = ?", (body.abbreviation,)
    ).fetchone()
    if existing:
        raise HTTPException(409, f"Unit '{body.abbreviation}' already exists")
    cur = conn.execute(
        "INSERT INTO units (name, abbreviation, name_plural, dimension, base_factor) "
        "VALUES (?, ?, ?, ?, ?)",
        (body.name, body.abbreviation, body.name_plural, body.dimension, body.base_factor),
    )
    conn.commit()
    return conn.execute("SELECT * FROM units WHERE id = ?", (cur.lastrowid,)).fetchone()


@router.delete("/units/{unit_id}", status_code=204)
def delete_unit(unit_id: int):
    conn = _get_db()
    if not conn.execute("SELECT id FROM units WHERE id = ?", (unit_id,)).fetchone():
        raise HTTPException(404, f"Unit {unit_id} not found")
    # Check if any products use this unit
    products_using = conn.execute(
        "SELECT count(*) as cnt FROM products WHERE unit_id = ?", (unit_id,)
    ).fetchone()
    if products_using["cnt"] > 0:
        raise HTTPException(
            409,
            f"Cannot delete unit {unit_id}: {products_using['cnt']} product(s) use it",
        )
    conn.execute("DELETE FROM units WHERE id = ?", (unit_id,))
    conn.commit()


# ── Conversions ────────────────────────────────────────────────────────────

@router.get("/conversions", response_model=list[Conversion])
def list_conversions(product_id: int | None = Query(None)):
    conn = _get_db()
    if product_id is not None:
        return conn.execute(
            "SELECT * FROM unit_conversions WHERE product_id = ? OR product_id IS NULL",
            (product_id,),
        ).fetchall()
    return conn.execute("SELECT * FROM unit_conversions ORDER BY id").fetchall()


@router.post("/conversions", response_model=Conversion, status_code=201)
def create_conversion(body: ConversionCreate):
    conn = _get_db()
    for uid in (body.from_unit_id, body.to_unit_id):
        if not conn.execute("SELECT id FROM units WHERE id = ?", (uid,)).fetchone():
            raise HTTPException(400, f"Unit {uid} not found")
    try:
        cur = conn.execute(
            """INSERT INTO unit_conversions (from_unit_id, to_unit_id, factor, product_id)
               VALUES (?, ?, ?, ?)""",
            (body.from_unit_id, body.to_unit_id, body.factor, body.product_id),
        )
        conn.commit()
    except Exception:
        raise HTTPException(409, "Conversion already exists")
    return conn.execute("SELECT * FROM unit_conversions WHERE id = ?", (cur.lastrowid,)).fetchone()


@router.delete("/conversions/{conversion_id}", status_code=204)
def delete_conversion(conversion_id: int):
    conn = _get_db()
    if not conn.execute("SELECT id FROM unit_conversions WHERE id = ?", (conversion_id,)).fetchone():
        raise HTTPException(404, f"Conversion {conversion_id} not found")
    conn.execute("DELETE FROM unit_conversions WHERE id = ?", (conversion_id,))
    conn.commit()


@router.get("/conversions/resolve", response_model=ConversionResult)
def resolve_conversion(
    from_unit_id: int = Query(...),
    to_unit_id: int = Query(...),
    product_id: int | None = Query(None),
):
    """BFS through conversion graph to find a path from one unit to another."""
    if from_unit_id == to_unit_id:
        return ConversionResult(factor=1.0, path=[from_unit_id])

    conn = _get_db()

    # Load all relevant conversions (product-specific + global)
    if product_id is not None:
        rows = conn.execute(
            "SELECT * FROM unit_conversions WHERE product_id = ? OR product_id IS NULL",
            (product_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM unit_conversions WHERE product_id IS NULL"
        ).fetchall()

    # Build adjacency list: unit_id → [(neighbor_id, factor)]
    graph: dict[int, list[tuple[int, float]]] = {}
    for r in rows:
        graph.setdefault(r["from_unit_id"], []).append((r["to_unit_id"], r["factor"]))
        # Add reverse edge
        if r["factor"] != 0:
            graph.setdefault(r["to_unit_id"], []).append((r["from_unit_id"], 1.0 / r["factor"]))

    # BFS
    queue: deque[tuple[int, float, list[int]]] = deque([(from_unit_id, 1.0, [from_unit_id])])
    visited = {from_unit_id}

    while queue:
        current, cumulative_factor, path = queue.popleft()
        for neighbor, factor in graph.get(current, []):
            if neighbor == to_unit_id:
                return ConversionResult(
                    factor=cumulative_factor * factor,
                    path=path + [neighbor],
                )
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, cumulative_factor * factor, path + [neighbor]))

    raise HTTPException(
        404,
        f"No conversion path from unit {from_unit_id} to {to_unit_id}"
        + (f" for product {product_id}" if product_id else ""),
    )
