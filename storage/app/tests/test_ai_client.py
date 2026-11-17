"""The Gemini key never lands in a URL, so request errors cannot write it
into the add-on log."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai_client


class _Resp:
    def __init__(self, status=200):
        self.status_code = status
        self.url = None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error: Not Found for url: {self.url}")

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}


def _capture(monkeypatch, status=200):
    seen = {}

    def post(url, **kw):
        seen.update(url=url, **kw)
        resp = _Resp(status)
        resp.url = requests.Request("POST", url, params=kw.get("params")).prepare().url
        return resp

    monkeypatch.setattr(ai_client.requests, "post", post)
    return seen


def test_the_key_goes_in_a_header(monkeypatch):
    seen = _capture(monkeypatch)
    ai_client._call_gemini("hi", "SECRET-KEY", "some-model")
    assert seen["headers"]["x-goog-api-key"] == "SECRET-KEY"
    assert "SECRET-KEY" not in seen["url"]
    assert not (seen.get("params") or {})


def test_a_failed_call_does_not_reveal_the_key(monkeypatch):
    _capture(monkeypatch, status=404)
    with pytest.raises(requests.HTTPError) as err:
        ai_client._call_gemini("hi", "SECRET-KEY", "retired-model")
    assert "SECRET-KEY" not in str(err.value)


# ── retry rule: only what can succeed is tried again ──────────────────────

import sqlite3


class _R:
    def __init__(self, status=200, text='{"ok": true}', headers=None):
        self.status_code, self.text = status, text
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": self.text}]}}]}


def _script(monkeypatch, *responses):
    """requests.post answers with each response in turn; sleeps are recorded."""
    calls, sleeps = [], []
    queue = iter(responses)

    def post(url, **kw):
        calls.append(url)
        answer = next(queue)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(ai_client.requests, "post", post)
    monkeypatch.setattr(ai_client.time, "sleep", sleeps.append)
    return calls, sleeps


CFG = {"provider": "gemini", "gemini_api_key": "SECRET-KEY", "gemini_model": "m"}


def test_a_refused_request_is_not_retried(monkeypatch):
    calls, sleeps = _script(
        monkeypatch, _R(404, text='{"error": {"message": "models/m is not found"}}'))
    with pytest.raises(ai_client.AiConfigError) as err:
        ai_client.call_ai_json("hi", None, cfg=CFG)
    assert len(calls) == 1 and sleeps == []
    assert "404" in str(err.value) and "not found" in str(err.value)
    assert "SECRET-KEY" not in str(err.value)


def test_rate_limit_waits_as_asked_then_succeeds(monkeypatch):
    calls, sleeps = _script(monkeypatch, _R(429, headers={"Retry-After": "7"}), _R())
    assert ai_client.call_ai_json("hi", None, cfg=CFG) == {"ok": True}
    assert len(calls) == 2 and sleeps == [7]


def test_server_errors_timeouts_and_bad_json_are_retried(monkeypatch):
    calls, _ = _script(
        monkeypatch, _R(503), requests.Timeout("slow"), _R(text="not json"), _R())
    assert ai_client.call_ai_json("hi", None, cfg=CFG) == {"ok": True}
    assert len(calls) == 4


def test_gives_up_after_four_attempts(monkeypatch):
    calls, sleeps = _script(monkeypatch, *[_R(500)] * 4)
    with pytest.raises(ValueError) as err:
        ai_client.call_ai_json("hi", None, cfg=CFG)
    assert not isinstance(err.value, ai_client.AiConfigError)
    assert len(calls) == 4 and len(sleeps) == 3


def test_a_missing_key_fails_without_a_call(monkeypatch):
    calls, _ = _script(monkeypatch)
    with pytest.raises(ai_client.AiConfigError):
        ai_client.call_ai_json("hi", None, cfg={**CFG, "gemini_api_key": ""})
    assert calls == []


def _config_db(**rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT)")
    conn.executemany("INSERT INTO config VALUES (?, ?)", rows.items())
    return conn


def test_default_models_are_current():
    cfg = ai_client._get_ai_config(_config_db())
    assert cfg["gemini_model"] == "gemini-3.6-flash"
    assert cfg["claude_model"] == "claude-haiku-5-5"
    assert cfg["ollama_model"] == "llama3"


@pytest.mark.parametrize("stored, expected", [("1", 10), ("5000", 200), ("50", 50), ("x", 100), (None, 100)])
def test_batch_size_is_clamped(stored, expected):
    conn = _config_db(**({"optimize_batch_size": stored} if stored is not None else {}))
    assert ai_client.get_batch_size(conn) == expected


# ── output schema: the provider enforces the shape, with a way back ───────

import sys
import types

ROWS = ai_client.arr(ai_client.obj(id=ai_client.INT))
WRAPPED = {"type": "object", "properties": {"items": ROWS},
           "required": ["items"], "additionalProperties": False}


@pytest.fixture(autouse=True)
def _forget_refused_schemas():
    ai_client._schema_refused.clear()


def _gemini_script(monkeypatch, *responses):
    """Like _script, but keeps each request body."""
    bodies = []
    queue = iter(responses)

    def post(url, **kw):
        bodies.append(kw["json"])
        answer = next(queue)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(ai_client.requests, "post", post)
    monkeypatch.setattr(ai_client.time, "sleep", lambda s: None)
    return bodies


def test_gemini_gets_the_schema_and_the_caller_gets_its_list(monkeypatch):
    bodies = _gemini_script(monkeypatch, _R(text='{"items": [{"id": 1}]}'))
    assert ai_client.call_ai_json("hi", None, cfg=CFG, schema=ROWS) == [{"id": 1}]
    assert bodies[0]["generationConfig"]["responseJsonSchema"] == WRAPPED


def test_an_object_schema_is_sent_as_is(monkeypatch):
    schema = ai_client.obj(index=ai_client.nullable(ai_client.INT))
    bodies = _gemini_script(monkeypatch, _R(text='{"index": null}'))
    assert ai_client.call_ai_json("hi", None, cfg=CFG, schema=schema) == {"index": None}
    assert bodies[0]["generationConfig"]["responseJsonSchema"] == schema


def test_a_rejected_schema_is_dropped_and_remembered(monkeypatch):
    bodies = _gemini_script(monkeypatch, _R(400, text="unknown field"),
                            _R(text='[{"id": 1}]'), _R(text='[{"id": 2}]'))
    assert ai_client.call_ai_json("hi", None, cfg=CFG, schema=ROWS) == [{"id": 1}]
    assert ai_client.call_ai_json("hi", None, cfg=CFG, schema=ROWS) == [{"id": 2}]
    assert ["responseJsonSchema" in b["generationConfig"] for b in bodies] == [True, False, False]


def test_a_400_that_is_not_about_the_schema_is_a_config_error(monkeypatch):
    bodies = _gemini_script(monkeypatch, _R(400, text="API key not valid"),
                            _R(400, text="API key not valid"))
    with pytest.raises(ai_client.AiConfigError, match="API key not valid"):
        ai_client.call_ai_json("hi", None, cfg=CFG, schema=ROWS)
    assert len(bodies) == 2 and not ai_client._schema_refused


def test_a_400_without_a_schema_is_still_a_config_error(monkeypatch):
    bodies = _gemini_script(monkeypatch, _R(400, text="bad request"))
    with pytest.raises(ai_client.AiConfigError):
        ai_client.call_ai_json("hi", None, cfg=CFG)
    assert len(bodies) == 1


class _Cut(_R):
    def json(self):
        return {"candidates": [{"finishReason": "MAX_TOKENS",
                                "content": {"parts": [{"text": '{"items": [{"id"'}]}}]}


def test_a_cut_off_answer_is_not_asked_for_again(monkeypatch):
    bodies = _gemini_script(monkeypatch, _Cut())
    with pytest.raises(ai_client.AiTruncated):
        ai_client.call_ai_json("hi", None, cfg=CFG, schema=ROWS)
    assert len(bodies) == 1


class _Empty(_R):
    def json(self):
        return {"promptFeedback": {"blockReason": "SAFETY"}}


def test_an_empty_answer_says_why(monkeypatch):
    _gemini_script(monkeypatch, *[_Empty()] * 4)
    with pytest.raises(ValueError, match="SAFETY"):
        ai_client.call_ai_json("hi", None, cfg=CFG, schema=ROWS)


def _fake_anthropic(monkeypatch, *answers):
    """A stand-in anthropic module: messages.create records its kwargs and
    returns (or raises) each answer in turn."""
    seen = []
    queue = iter(answers)

    class _Messages:
        def create(self, **kw):
            seen.append(kw)
            answer = next(queue)
            if isinstance(answer, Exception):
                raise answer
            return answer

    class Anthropic:
        def __init__(self, **kw):
            self.messages = _Messages()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Anthropic))
    monkeypatch.setattr(ai_client.time, "sleep", lambda s: None)
    return seen


def _claude_answer(text, stop_reason="end_turn"):
    return types.SimpleNamespace(
        stop_reason=stop_reason,
        content=[types.SimpleNamespace(type="thinking", thinking=""),
                 types.SimpleNamespace(type="text", text=text)],
        usage=types.SimpleNamespace(input_tokens=3, output_tokens=4))


CLAUDE = {"provider": "claude", "claude_api_key": "SECRET-KEY", "claude_model": "claude-x"}


class _Refused(Exception):
    status_code = 400
    response = types.SimpleNamespace(status_code=400, text="output_config: unknown", headers={})


def test_claude_gets_the_schema(monkeypatch):
    seen = _fake_anthropic(monkeypatch, _claude_answer('{"items": [{"id": 7}]}'))
    assert ai_client.call_ai_json("hi", None, cfg=CLAUDE, schema=ROWS) == [{"id": 7}]
    assert seen[0]["output_config"] == {"format": {"type": "json_schema", "schema": WRAPPED}}
    assert seen[0]["model"] == "claude-x"


def test_claude_without_schema_support_falls_back_to_prose_parsing(monkeypatch):
    seen = _fake_anthropic(monkeypatch, _Refused(),
                           _claude_answer('Here you go:\n```json\n[{"id": 7}]\n```'))
    assert ai_client.call_ai_json("hi", None, cfg=CLAUDE, schema=ROWS) == [{"id": 7}]
    assert "output_config" in seen[0] and "output_config" not in seen[1]


def test_claude_cut_off_at_max_tokens_is_truncation(monkeypatch):
    seen = _fake_anthropic(monkeypatch, _claude_answer('{"items": [', stop_reason="max_tokens"))
    with pytest.raises(ai_client.AiTruncated):
        ai_client.call_ai_json("hi", None, cfg=CLAUDE, schema=ROWS)
    assert len(seen) == 1


def test_ollama_gets_the_schema_as_format(monkeypatch):
    bodies = []

    class _O(_R):
        def json(self):
            return {"message": {"content": '{"items": []}'}, "done_reason": "stop"}

    def post(url, **kw):
        bodies.append(kw["json"])
        return _O()

    monkeypatch.setattr(ai_client.requests, "post", post)
    cfg = {"provider": "ollama", "ollama_url": "http://o", "ollama_model": "llama3"}
    assert ai_client.call_ai_json("hi", None, cfg=cfg, schema=ROWS) == []
    assert bodies[0]["format"] == WRAPPED
    assert ai_client.call_ai_json("hi", None, cfg=cfg) == {"items": []}
    assert bodies[1]["format"] == "json"


def test_capping_a_prompt_list_is_logged(caplog):
    with caplog.at_level("WARNING"):
        assert ai_client.capped([1, 2, 3], 2, "Things") == [1, 2]
        assert ai_client.capped([1, 2], 2, "Things") == [1, 2]
    assert [r.message for r in caplog.records] == [
        "Things: only 2 of 3 fit in the AI prompt; the rest cannot be matched"]
