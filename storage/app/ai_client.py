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
# The one place the default model ids live (config.json repeats them for the
# add-on options form).
DEFAULT_MODELS = {
    "gemini": "gemini-3.6-flash",
    "claude": "claude-haiku-5-5",
    "ollama": "llama3",
}
# What each provider cannot work without.
_REQUIRED = {"gemini": "gemini_api_key", "claude": "claude_api_key", "ollama": "ollama_url"}
_DEFAULT_BATCH_SIZE = 100
_BATCH_SIZE_MIN = 10
_BATCH_SIZE_MAX = 200  # more products per call than this truncates the answer
_MAX_RETRIES = 4


class AiConfigError(ValueError):
    """The provider refused the request itself: no or wrong key, an unknown
    or retired model, a malformed request. Trying again cannot help."""


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
        "gemini_model": _val("gemini_model", DEFAULT_MODELS["gemini"]),
        "ollama_url": _val("ollama_url"),
        "ollama_model": _val("ollama_model", DEFAULT_MODELS["ollama"]),
        "claude_api_key": _val("claude_api_key"),
        "claude_model": _val("claude_model", DEFAULT_MODELS["claude"]),
    }


def is_configured(cfg: dict[str, str]) -> bool:
    """True when the active provider has the key or URL it needs."""
    return bool(cfg.get(_REQUIRED.get(cfg.get("provider", "gemini"), "gemini_api_key")))


def get_batch_size(conn: sqlite3.Connection) -> int:
    """Return the configured optimize batch size (clamped to 10–200, default 100)."""
    row = conn.execute("SELECT value FROM config WHERE key = 'optimize_batch_size'").fetchone()
    try:
        val = int(row["value"]) if row and row["value"] else _DEFAULT_BATCH_SIZE
    except (ValueError, TypeError):
        val = _DEFAULT_BATCH_SIZE
    return max(_BATCH_SIZE_MIN, min(_BATCH_SIZE_MAX, val))


# ---------------------------------------------------------------------------
# Raw provider calls
# ---------------------------------------------------------------------------

def _call_gemini(prompt: str, api_key: str, model: str) -> tuple[str, dict]:
    url = f"{_GEMINI_BASE_URL}{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    # The key goes in a header: in the URL, any request error would print it
    # into the add-on log.
    resp = requests.post(url, json=payload, headers={"x-goog-api-key": api_key}, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usageMetadata", {})
    stats: dict = {}
    if usage:
        stats = {
            "in": usage.get("promptTokenCount") or 0,
            "out": usage.get("candidatesTokenCount") or 0,
        }
        logger.info(
            "Gemini usage — prompt tokens: %s, output tokens: %s, total: %s",
            stats["in"], stats["out"],
            usage.get("totalTokenCount", "?"),
        )
    return data["candidates"][0]["content"]["parts"][0]["text"], stats


def _call_ollama(prompt: str, url: str, model: str) -> tuple[str, dict]:
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
    prompt_tokens = data.get("prompt_eval_count") or 0
    output_tokens = data.get("eval_count") or 0
    total_ns = data.get("total_duration")
    total_ms = round(total_ns / 1_000_000) if total_ns else 0
    logger.info(
        "Ollama usage — prompt tokens: %s, output tokens: %s, duration: %sms",
        prompt_tokens, output_tokens, total_ms,
    )
    return data["message"]["content"], {"in": prompt_tokens, "out": output_tokens, "ms": total_ms}


def _claude_response_text(response) -> str:
    """Join the text blocks of a Claude response.

    Newer models think by default, so content[0] may be a thinking block.
    """
    if response.stop_reason == "max_tokens":
        logger.warning("Claude response hit max_tokens; output may be truncated")
    return "".join(b.text for b in response.content if b.type == "text")


def _call_claude(prompt: str, api_key: str, model: str) -> tuple[str, dict]:
    import anthropic as _anthropic  # optional dependency

    client = _anthropic.Anthropic(api_key=api_key, max_retries=0)  # we retry ourselves
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
    )
    usage = response.usage
    stats = {"in": usage.input_tokens, "out": usage.output_tokens}
    logger.info(
        "Claude usage — input tokens: %s, output tokens: %s",
        stats["in"], stats["out"],
    )
    return _claude_response_text(response), stats


def _call_claude_vision(
    prompt: str,
    image_b64: str,
    mime_type: str,
    api_key: str,
    model: str,
) -> tuple[str, dict]:
    """Claude vision call — sends a base64-encoded image + a text prompt and
    returns the model's text response.

    Used for receipt OCR. Other providers do not currently route through here;
    callers must check provider == 'claude' before invoking.
    """
    import anthropic as _anthropic

    client = _anthropic.Anthropic(api_key=api_key, max_retries=0)  # we retry ourselves
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    usage = response.usage
    stats = {"in": usage.input_tokens, "out": usage.output_tokens}
    logger.info(
        "Claude vision usage — input tokens: %s, output tokens: %s",
        stats["in"], stats["out"],
    )
    return _claude_response_text(response), stats


def call_ai_vision_json(
    prompt: str,
    image_b64: str,
    mime_type: str,
    conn: sqlite3.Connection,
    *,
    cfg: dict[str, str] | None = None,
) -> Any:
    """Call the AI provider with an image + prompt; parse the response as JSON.

    Claude-only for now (Gemini supports vision but isn't wired here). Raises
    ValueError when provider != claude or claude_api_key is missing.
    """
    if cfg is None:
        cfg = _get_ai_config(conn)
    provider = cfg.get("provider", "gemini")
    if provider != "claude":
        raise ValueError(
            f"call_ai_vision_json requires ai_provider=claude (got {provider!r})"
        )
    if not cfg.get("claude_api_key"):
        raise AiConfigError("claude_api_key is not configured")

    def attempt() -> Any:
        raw, _stats = _call_claude_vision(
            prompt, image_b64, mime_type, cfg["claude_api_key"], cfg["claude_model"],
        )
        return json.loads(_extract_json(_strip_control_chars(raw)))

    return _with_retries("Claude vision", attempt)


# ---------------------------------------------------------------------------
# JSON extraction + retry wrapper
# ---------------------------------------------------------------------------

def _strip_control_chars(text: str) -> str:
    """Drop the control characters AI models occasionally embed."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def _extract_json(text: str) -> str:
    """Extract JSON from a response that may include prose or markdown fences."""
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence:
        return fence.group(1)
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if match:
        return match.group(1)
    return text.strip()


def _status(exc: Exception) -> int | None:
    """HTTP status of a provider error: requests carries it on .response,
    the anthropic SDK on .status_code. None for network errors and bad JSON."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def _retryable(exc: Exception) -> bool:
    """Timeouts, connection errors, unparseable answers, rate limits and
    server errors may pass on a second try. Any other 4xx will not."""
    status = _status(exc)
    return status is None or status in (408, 409, 429) or status >= 500


def _retry_delay(exc: Exception, attempt: int) -> float:
    """Exponential back-off, or the provider's Retry-After when that is longer."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        asked = float(headers.get("retry-after") or 0)
    except (TypeError, ValueError):
        asked = 0.0
    return min(60.0, max(2 ** attempt, asked))


def _refusal(exc: Exception) -> str:
    """The provider's own words for a refused request. The key travels in a
    header, so neither the URL nor the body can contain it."""
    body = (getattr(getattr(exc, "response", None), "text", "") or "").strip()
    return f"HTTP {_status(exc)}: {body[:300] or exc}"


def _with_retries(label: str, attempt_fn: Any) -> Any:
    """Run attempt_fn up to _MAX_RETRIES times. A refused request raises
    AiConfigError at once; anything else is retried with back-off."""
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return attempt_fn()
        except Exception as exc:
            if not _retryable(exc):
                raise AiConfigError(f"{label} refused the request ({_refusal(exc)})") from exc
            last_exc = exc
            logger.warning("%s attempt %d/%d failed: %s", label, attempt, _MAX_RETRIES, exc)
            if attempt < _MAX_RETRIES:
                time.sleep(_retry_delay(exc, attempt))
    raise ValueError(f"AI call failed after {_MAX_RETRIES} attempts: {last_exc}") from last_exc


def call_ai_json(
    prompt: str,
    conn: sqlite3.Connection,
    *,
    cfg: dict[str, str] | None = None,
    emit: Any = None,
) -> Any:
    """Call the configured AI provider and parse the response as JSON.

    *cfg* may be provided to avoid repeated DB reads inside loops; if omitted
    it is fetched from *conn* on every call.
    *emit* optional callable(str) — receives a formatted token/timing line on
    each successful call.

    Raises AiConfigError when the provider is not configured or refuses the
    request (bad key, retired model): retrying cannot help. Transient errors
    and JSON parse failures are retried up to _MAX_RETRIES times with back-off,
    then raise ValueError.
    """
    if cfg is None:
        cfg = _get_ai_config(conn)

    provider = cfg.get("provider", "gemini")
    if not is_configured(cfg):
        raise AiConfigError(f"AI provider {provider!r} is not configured")
    model_name = cfg.get(f"{provider}_model") or cfg.get("gemini_model")

    def attempt() -> Any:
        t0 = time.time()
        if provider == "ollama":
            raw, stats = _call_ollama(prompt, cfg["ollama_url"], cfg["ollama_model"])
        elif provider == "claude":
            raw, stats = _call_claude(prompt, cfg["claude_api_key"], cfg["claude_model"])
        else:
            raw, stats = _call_gemini(prompt, cfg["gemini_api_key"], cfg["gemini_model"])
        wall_ms = round((time.time() - t0) * 1000)

        sanitized = _strip_control_chars(raw)
        # Extract JSON for providers that may wrap in prose/fences.
        if provider in ("claude", "ollama"):
            sanitized = _extract_json(sanitized)
        result = json.loads(sanitized)

        if emit:
            # Ollama provides native inference time; others use wall clock.
            elapsed_ms = stats.get("ms") or wall_ms
            emit(
                f"  ↳ {model_name}: {stats.get('in', 0):,} in / "
                f"{stats.get('out', 0):,} out tokens · {elapsed_ms:,}ms"
            )
        return result

    return _with_retries(provider.capitalize(), attempt)
