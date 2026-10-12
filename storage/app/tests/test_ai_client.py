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
