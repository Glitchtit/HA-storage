"""Each request gets its own database connection: concurrent requests must not
corrupt each other (HA-stock fires a burst of reads and shopping-list writes
when it opens), and a request that fails half-way must leave nothing behind."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "products"), exist_ok=True)
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "recipes"), exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException
from fastapi.testclient import TestClient
from main import app, get_connection

client = TestClient(app, raise_server_exceptions=False)


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def test_a_burst_of_reads_and_writes_never_fails():
    kpl = _u("kpl")
    pids = [client.post("/api/products", json={"name": f"Burst k1 {i}", "unit_id": kpl}).json()["id"]
            for i in range(12)]
    errors: list[str] = []

    def worker(n):
        for i in range(6):
            pid = pids[(n + i) % len(pids)]
            for method, path, body in (("get", "/api/products", None), ("get", "/api/stock", None),
                                       ("post", "/api/shopping-list", {"product_id": pid, "amount": 1}),
                                       ("get", "/api/shopping-list", None)):
                r = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
                if r.status_code >= 500:
                    errors.append(f"{method} {path}: {r.status_code} {r.text[:80]}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def _write_then_fail(key: str):
    get_connection().execute("INSERT INTO config (key, value) VALUES (?, 'half')", (key,))
    raise HTTPException(400, "failed after writing")


app.add_api_route("/api/test-only/write-then-fail/{key}", _write_then_fail, methods=["POST"])


def test_a_request_that_fails_after_writing_leaves_nothing_behind():
    assert client.post("/api/test-only/write-then-fail/half-k1").status_code == 400
    started = time.monotonic()
    r = client.post("/api/products", json={"name": "After half write k1", "unit_id": _u("kpl")})
    assert r.status_code == 201
    assert time.monotonic() - started < 2  # no wait on a lock the failed request kept
    assert get_connection().execute("SELECT 1 FROM config WHERE key = 'half-k1'").fetchone() is None
