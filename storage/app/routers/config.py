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


@router.get("/config/ai")
def get_ai_config():
    """Return the full AI provider config (provider-agnostic, for sister apps)."""
    conn = _get_db()

    def _val(key: str, default: str = "") -> str:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row and row["value"] else default

    return {
        "provider": _val("ai_provider", "gemini"),
        "api_key": _val("gemini_api_key"),
        "model": _val("gemini_model", "gemini-2.0-flash"),
        "ollama_url": _val("ollama_url"),
        "ollama_model": _val("ollama_model", "llama3"),
        "claude_api_key": _val("claude_api_key"),
        "claude_model": _val("claude_model", "claude-3-5-haiku-20241022"),
    }


@router.get("/config/ai-key")
def get_ai_key():
    """Return the Gemini API key (for sister apps — legacy endpoint)."""
    conn = _get_db()

    def _val(key: str, default: str = "") -> str:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row and row["value"] else default

    provider = _val("ai_provider", "gemini")
    # For Ollama/Claude providers, return empty key without 404 so old clients degrade gracefully
    if provider == "ollama":
        return {"api_key": "", "model": _val("ollama_model", "llama3")}
    if provider == "claude":
        return {"api_key": _val("claude_api_key"), "model": _val("claude_model", "claude-3-5-haiku-20241022")}

    api_key = _val("gemini_api_key")
    if not api_key:
        raise HTTPException(404, "Gemini API key not configured")
    return {
        "api_key": api_key,
        "model": _val("gemini_model", "gemini-2.0-flash"),
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
