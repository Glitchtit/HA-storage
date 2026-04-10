"""AI provider abstraction for the Storage backend.

Supports Gemini, Claude (Anthropic), and Ollama.
Configuration is read from the Storage config database at call time.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/"
_GEMINI_DEFAULT_MODEL = "gemini-2.0-flash"
_BATCH_SIZE = 100
_MAX_RETRIES = 4


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _get_ai_config(conn: sqlite3.Connection) -> dict[str, str]:
    """Read AI provider config from the Storage config table."""
    def _val(key: str, default: str = "") -> str:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row and row["value"] else default

    return {
        "provider": _val("ai_provider", "gemini"),
        "gemini_api_key": _val("gemini_api_key"),
        "gemini_model": _val("gemini_model", _GEMINI_DEFAULT_MODEL),
        "ollama_url": _val("ollama_url"),
        "ollama_model": _val("ollama_model", "llama3"),
        "claude_api_key": _val("claude_api_key"),
        "claude_model": _val("claude_model", "claude-3-5-haiku-20241022"),
    }


# ---------------------------------------------------------------------------
# Raw provider calls
# ---------------------------------------------------------------------------

def _call_gemini(prompt: str, api_key: str, model: str) -> str:
    url = f"{_GEMINI_BASE_URL}{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    resp = requests.post(url, json=payload, params={"key": api_key}, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usageMetadata", {})
    if usage:
        logger.info(
            "Gemini usage — prompt tokens: %s, output tokens: %s, total: %s",
            usage.get("promptTokenCount", "?"),
            usage.get("candidatesTokenCount", "?"),
            usage.get("totalTokenCount", "?"),
        )
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_ollama(prompt: str, url: str, model: str) -> str:
    resp = requests.post(
        f"{url}/api/chat",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "format": "json",
            "stream": False,
        },
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json()
    prompt_tokens = data.get("prompt_eval_count", "?")
    output_tokens = data.get("eval_count", "?")
    total_ns = data.get("total_duration")
    total_ms = round(total_ns / 1_000_000) if total_ns else "?"
    logger.info(
        "Ollama usage — prompt tokens: %s, output tokens: %s, duration: %sms",
        prompt_tokens, output_tokens, total_ms,
    )
    return data["message"]["content"]


def _call_claude(prompt: str, api_key: str, model: str) -> str:
    import anthropic as _anthropic  # optional dependency

    client = _anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model or "claude-3-5-haiku-20241022",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    usage = response.usage
    logger.info(
        "Claude usage — input tokens: %s, output tokens: %s",
        usage.input_tokens, usage.output_tokens,
    )
    return response.content[0].text


# ---------------------------------------------------------------------------
# JSON extraction + retry wrapper
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> str:
    """Extract JSON from a response that may include prose or markdown fences."""
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence:
        return fence.group(1)
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if match:
        return match.group(1)
    return text.strip()


def call_ai_json(
    prompt: str,
    conn: sqlite3.Connection,
    *,
    cfg: dict[str, str] | None = None,
) -> Any:
    """Call the configured AI provider and parse the response as JSON.

    *cfg* may be provided to avoid repeated DB reads inside loops; if omitted
    it is fetched from *conn* on every call.

    Retries up to _MAX_RETRIES times with exponential back-off on transient
    errors or JSON parse failures.
    """
    if cfg is None:
        cfg = _get_ai_config(conn)

    provider = cfg.get("provider", "gemini")
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            if provider == "ollama":
                raw = _call_ollama(prompt, cfg["ollama_url"], cfg["ollama_model"])
            elif provider == "claude":
                raw = _call_claude(prompt, cfg["claude_api_key"], cfg["claude_model"])
            else:
                raw = _call_gemini(prompt, cfg["gemini_api_key"], cfg["gemini_model"])

            # Strip control characters that AI models occasionally embed.
            sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)

            # Extract JSON for providers that may wrap in prose/fences.
            if provider in ("claude", "ollama"):
                sanitized = _extract_json(sanitized)

            return json.loads(sanitized)

        except Exception as exc:
            last_exc = exc
            logger.warning(
                "%s attempt %d/%d failed: %s",
                provider.capitalize(), attempt, _MAX_RETRIES, exc,
            )
            if attempt < _MAX_RETRIES:
                time.sleep(2 ** attempt)

    raise ValueError(f"AI call failed after {_MAX_RETRIES} attempts: {last_exc}") from last_exc
