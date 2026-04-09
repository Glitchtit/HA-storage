"""Location endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from models import Location, LocationCreate

router = APIRouter(tags=["locations"])


def _get_db():
    from main import get_connection
    return get_connection()


@router.get("/locations", response_model=list[Location])
def list_locations():
    return _get_db().execute("SELECT * FROM locations ORDER BY name").fetchall()


@router.post("/locations", response_model=Location, status_code=201)
def create_location(body: LocationCreate):
    conn = _get_db()
    existing = conn.execute("SELECT id FROM locations WHERE name = ?", (body.name,)).fetchone()
    if existing:
        raise HTTPException(409, f"Location '{body.name}' already exists")
    cur = conn.execute(
        "INSERT INTO locations (name, description) VALUES (?, ?)",
        (body.name, body.description),
    )
    conn.commit()
    return conn.execute("SELECT * FROM locations WHERE id = ?", (cur.lastrowid,)).fetchone()


@router.put("/locations/{location_id}", response_model=Location)
def update_location(location_id: int, body: LocationCreate):
    conn = _get_db()
    if not conn.execute("SELECT id FROM locations WHERE id = ?", (location_id,)).fetchone():
        raise HTTPException(404, f"Location {location_id} not found")
    conn.execute(
        "UPDATE locations SET name = ?, description = ? WHERE id = ?",
        (body.name, body.description, location_id),
    )
    conn.commit()
    return conn.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()


@router.delete("/locations/{location_id}", status_code=204)
def delete_location(location_id: int):
    conn = _get_db()
    if not conn.execute("SELECT id FROM locations WHERE id = ?", (location_id,)).fetchone():
        raise HTTPException(404, f"Location {location_id} not found")
    conn.execute("DELETE FROM locations WHERE id = ?", (location_id,))
    conn.commit()
