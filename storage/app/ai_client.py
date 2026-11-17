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


class AiTruncated(ValueError):
    """The answer was cut off at the output limit. The same request would be
    cut off again; the caller should ask for less (a smaller batch)."""


# ---------------------------------------------------------------------------
# Output schemas (JSON Schema, the subset every provider accepts)
# ---------------------------------------------------------------------------

INT = {"type": "integer"}
NUM = {"type": "number"}
STR = {"type": "string"}
CONFIDENCE = {"type": "string", "enum": ["high", "medium", "low"]}


def obj(**props: dict) -> dict:
    """A closed object with every property required."""
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}


def arr(item: dict) -> dict:
    return {"type": "array", "items": item}


def nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def enum(*values: str) -> dict:
    return {"type": "string", "enum": list(values)}


def capped(items: list, limit: int, what: str) -> list:
    """items[:limit], with a warning when something was left out: what is
    cut from a prompt can never be chosen by the model."""
    if len(items) > limit:
        logger.warning("%s: only %d of %d fit in the AI prompt; the rest cannot be matched",
                       what, limit, len(items))
    return items[:limit]


def _wrap(schema: dict | None) -> tuple[dict | None, bool]:
    """Providers want an object at the top; a caller may want a list back.
    Returns (schema to send, whether the answer must be unwrapped)."""
    if schema and schema.get("type") == "array":
        return obj(items=schema), True
    return schema, False


# (provider, model) pairs that answered HTTP 400 to a request carrying a
# schema: an older model or server. They get plain JSON mode from then on.
_schema_refused: set[tuple[str, str]] = set()


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

def _call_gemini(prompt: str, api_key: str, model: str, schema: dict | None = None) -> tuple[str, dict]:
    url = f"{_GEMINI_BASE_URL}{model}:generateContent"
    config: dict = {"responseMimeType": "application/json"}
    if schema is not None:
        config["responseJsonSchema"] = schema
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config}
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
    candidate = (data.get("candidates") or [{}])[0]
    if candidate.get("finishReason") == "MAX_TOKENS":
        raise AiTruncated("Gemini answer was cut off at the output limit")
    parts = (candidate.get("content") or {}).get("parts") or []
    if not parts:
        why = candidate.get("finishReason") or data.get("promptFeedback") or "no candidates"
        raise ValueError(f"Gemini returned no answer ({why})")
    return parts[0]["text"], stats


def _call_ollama(prompt: str, url: str, model: str, schema: dict | None = None) -> tuple[str, dict]:
    resp = requests.post(
        f"{url}/api/chat",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "format": schema if schema is not None else "json",
            "stream": False,
        },
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("done_reason") == "length":
        raise AiTruncated("Ollama answer was cut off at the output limit")
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
        raise AiTruncated("Claude answer was cut off at max_tokens")
    if response.stop_reason == "refusal":
        raise ValueError("Claude declined the request")
    return "".join(b.text for b in response.content if b.type == "text")


def _call_claude(prompt: str, api_key: str, model: str, schema: dict | None = None) -> tuple[str, dict]:
    import anthropic as _anthropic  # optional dependency

    client = _anthropic.Anthropic(api_key=api_key, max_retries=0)  # we retry ourselves
    extra = {}
    if schema is not None:
        extra["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
        **extra,
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
        except AiTruncated:
            raise
        except Exception as exc:
            if not _retryable(exc):
                raise AiConfigError(f"{label} refused the request ({_refusal(exc)})") from exc
            last_exc = exc
            logger.warning("%s attempt %d/%d failed: %s", label, attempt, _MAX_RETRIES, exc)
            if attempt < _MAX_RETRIES:
                time.sleep(_retry_delay(exc, attempt))
    raise ValueError(f"AI call failed after {_MAX_RETRIES} attempts: {last_exc}") from last_exc


def _call_provider(provider: str, prompt: str, cfg: dict[str, str],
                   schema: dict | None) -> tuple[str, dict]:
    if provider == "ollama":
        return _call_ollama(prompt, cfg["ollama_url"], cfg["ollama_model"], schema)
    if provider == "claude":
        return _call_claude(prompt, cfg["claude_api_key"], cfg["claude_model"], schema)
    return _call_gemini(prompt, cfg["gemini_api_key"], cfg["gemini_model"], schema)


def call_ai_json(
    prompt: str,
    conn: sqlite3.Connection,
    *,
    cfg: dict[str, str] | None = None,
    emit: Any = None,
    schema: dict | None = None,
) -> Any:
    """Call the configured AI provider and parse the response as JSON.

    *cfg* may be provided to avoid repeated DB reads inside loops; if omitted
    it is fetched from *conn* on every call.
    *emit* optional callable(str) — receives a formatted token/timing line on
    each successful call.
    *schema* JSON Schema of the value wanted back (build it with obj/arr/…).
    The provider then enforces the shape. A list schema is sent wrapped in an
    object and unwrapped again, so the caller gets its list. A provider or
    model that rejects the schema (HTTP 400) is asked again without it and
    not offered one again, so the prompt must still describe the shape.

    Raises AiConfigError when the provider is not configured or refuses the
    request (bad key, retired model): retrying cannot help. AiTruncated when
    the answer hit the output limit: ask for less. Transient errors and JSON
    parse failures are retried up to _MAX_RETRIES times with back-off, then
    raise ValueError.
    """
    if cfg is None:
        cfg = _get_ai_config(conn)

    provider = cfg.get("provider", "gemini")
    if not is_configured(cfg):
        raise AiConfigError(f"AI provider {provider!r} is not configured")
    model_name = cfg.get(f"{provider}_model") or cfg.get("gemini_model")
    wire_schema, wrapped = _wrap(schema)
    key = (provider, model_name)

    def attempt() -> Any:
        t0 = time.time()
        sent = wire_schema if key not in _schema_refused else None
        try:
            raw, stats = _call_provider(provider, prompt, cfg, sent)
        except Exception as exc:
            if sent is None or _status(exc) != 400:
                raise
            # Raises again when the schema was not what the provider minded
            # (Gemini answers a bad key with 400 too).
            raw, stats = _call_provider(provider, prompt, cfg, None)
            sent = None
            _schema_refused.add(key)
            logger.warning("%s (%s) rejected the output schema; continuing without it: %s",
                           provider.capitalize(), model_name, _refusal(exc))
        wall_ms = round((time.time() - t0) * 1000)

        text = _strip_control_chars(raw)
        # Without an enforced schema Claude and Ollama may wrap JSON in prose.
        if sent is None and provider in ("claude", "ollama"):
            text = _extract_json(text)
        result = json.loads(text)
        if wrapped and isinstance(result, dict) and isinstance(result.get("items"), list):
            result = result["items"]

        if emit:
            # Ollama provides native inference time; others use wall clock.
            elapsed_ms = stats.get("ms") or wall_ms
            emit(
                f"  ↳ {model_name}: {stats.get('in', 0):,} in / "
                f"{stats.get('out', 0):,} out tokens · {elapsed_ms:,}ms"
            )
        return result

    return _with_retries(provider.capitalize(), attempt)
