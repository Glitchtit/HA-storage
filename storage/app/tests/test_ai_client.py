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
