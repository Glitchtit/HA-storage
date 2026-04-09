"""Config endpoints — AI key sharing + general settings."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from models import ConfigEntry

router = APIRouter(tags=["config"])


def _get_db():
    from main import get_connection
    return get_connection()


@router.get("/config", response_model=list[ConfigEntry])
def get_config():
    """Get all non-sensitive config values."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM config WHERE key NOT LIKE '%api_key%' ORDER BY key"
    ).fetchall()
    return rows


@router.get("/config/ai-key")
def get_ai_key():
    """Return the Gemini API key (for sister apps)."""
    conn = _get_db()
    row = conn.execute("SELECT value FROM config WHERE key = 'gemini_api_key'").fetchone()
    if not row or not row["value"]:
        raise HTTPException(404, "Gemini API key not configured")
    model = conn.execute("SELECT value FROM config WHERE key = 'gemini_model'").fetchone()
    return {
        "api_key": row["value"],
        "model": model["value"] if model else "gemini-2.0-flash",
    }


@router.put("/config/{key}", response_model=ConfigEntry)
def set_config(key: str, body: ConfigEntry):
    conn = _get_db()
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
        (key, body.value),
    )
    conn.commit()
    return {"key": key, "value": body.value}
