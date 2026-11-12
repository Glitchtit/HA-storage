"""Using or moving stock by hand keeps each lot's opened share and leaves no
float dust behind (WRE-69)."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "products"), exist_ok=True)
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "recipes"), exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def _product(name):
    kpl = next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == "kpl")
    r = client.post("/api/products", json={"name": name, "unit_id": kpl})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _lots(pid, location_id=None):
    lots = client.get(f"/api/stock/product/{pid}").json()
    return sorted((s["amount"], s["amount_opened"]) for s in lots
                  if location_id is None or s["location_id"] == location_id)


def _post(path, **body):
    r = client.post(path, json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_consume_after_opening_all_keeps_opened_within_amount():
    pid = _product("lot-integrity open both")
    _post("/api/stock/add", product_id=pid, amount=2)
    _post("/api/stock/open", product_id=pid, amount=2)
    _post("/api/stock/consume", product_id=pid, amount=1)
    assert _lots(pid) == [(1.0, 1.0)]
    summary = next(s for s in client.get("/api/stock").json() if s["product_id"] == pid)
    assert summary["amount"] == 1.0
    assert summary["amount_opened"] == 1.0


def test_consume_takes_the_opened_item_first():
    pid = _product("lot-integrity opened first")
    _post("/api/stock/add", product_id=pid, amount=2)
    _post("/api/stock/open", product_id=pid, amount=1)
    _post("/api/stock/consume", product_id=pid, amount=1)
    assert _lots(pid) == [(1.0, 0.0)]


def test_consume_in_tenths_leaves_no_dust():
    pid = _product("lot-integrity tenths")
    _post("/api/stock/add", product_id=pid, amount=1)
    for _ in range(10):
        _post("/api/stock/consume", product_id=pid, amount=0.1)
    assert _lots(pid) == []
    assert pid not in [s["product_id"] for s in client.get("/api/stock").json()]


def test_transfer_moves_the_opened_share_along():
    pid = _product("lot-integrity transfer")
    src, dst = (loc["id"] for loc in client.get("/api/locations").json()[:2])
    _post("/api/stock/add", product_id=pid, amount=2, location_id=src)
    _post("/api/stock/open", product_id=pid, amount=2)
    _post("/api/stock/transfer", product_id=pid, amount=1,
          from_location_id=src, to_location_id=dst)
    assert _lots(pid, src) == [(1.0, 1.0)]
    assert _lots(pid, dst) == [(1.0, 1.0)]
