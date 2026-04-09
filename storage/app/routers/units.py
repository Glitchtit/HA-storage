"""Unit and unit conversion endpoints."""

from __future__ import annotations

from collections import deque

from fastapi import APIRouter, HTTPException, Query

from models import Conversion, ConversionCreate, ConversionResult, Unit, UnitCreate

router = APIRouter(tags=["units"])


def _get_db():
    from main import get_connection
    return get_connection()


# ── Units ──────────────────────────────────────────────────────────────────

@router.get("/units", response_model=list[Unit])
def list_units():
    return _get_db().execute("SELECT * FROM units ORDER BY abbreviation").fetchall()


@router.post("/units", response_model=Unit, status_code=201)
def create_unit(body: UnitCreate):
    conn = _get_db()
    existing = conn.execute(
        "SELECT id FROM units WHERE abbreviation = ?", (body.abbreviation,)
    ).fetchone()
    if existing:
        raise HTTPException(409, f"Unit '{body.abbreviation}' already exists")
    cur = conn.execute(
        "INSERT INTO units (name, abbreviation, name_plural) VALUES (?, ?, ?)",
        (body.name, body.abbreviation, body.name_plural),
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
