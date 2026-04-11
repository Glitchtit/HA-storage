"""AI optimize endpoints for the Storage backend.

POST /api/ai/optimize               — start background optimize job → {task_id}
GET  /api/ai/optimize/categories    — get enforced categories list
PUT  /api/ai/optimize/categories    — save enforced categories list
GET  /api/ai/optimize/{task_id}     — poll status and streaming logs
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["ai"])
logger = logging.getLogger(__name__)

_CONFIG_KEY = "optimize_categories"

# ---------------------------------------------------------------------------
# In-memory task registry — single-flight optimized
# ---------------------------------------------------------------------------

_tasks: dict[str, dict[str, Any]] = {}
_tasks_lock = threading.Lock()
_MAX_TASKS = 20
# Tracks the currently running optimize job; None when idle
_running_task_id: str | None = None


def _store_task(task_id: str, data: dict[str, Any]) -> None:
    with _tasks_lock:
        _tasks[task_id] = data
        if len(_tasks) > _MAX_TASKS:
            done_ids = [
                k for k, v in _tasks.items()
                if v.get("status") in ("done", "error") and k != task_id
            ]
            for k in done_ids[: len(_tasks) - _MAX_TASKS]:
                del _tasks[k]


def _get_task(task_id: str) -> dict[str, Any] | None:
    with _tasks_lock:
        return _tasks.get(task_id)


def _append_log(task_id: str, msg: str) -> None:
    with _tasks_lock:
        t = _tasks.get(task_id)
        if t is not None:
            t["logs"].append(msg)


def _read_enforced_categories(conn) -> list[str]:
    row = conn.execute(
        "SELECT value FROM config WHERE key = ?", (_CONFIG_KEY,)
    ).fetchone()
    if not row or not row["value"]:
        return []
    try:
        cats = json.loads(row["value"])
        return [c for c in cats if isinstance(c, str) and c.strip()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Background worker (single-flight: releases _running_task_id on exit)
# ---------------------------------------------------------------------------

def _run_optimize_task(
    task_id: str,
    product_ids: list[int] | None,
    enforced_categories: list[str] | None,
    fresh_seed: bool = False,
) -> None:
    global _running_task_id

    def emit(msg: str) -> None:
        _append_log(task_id, msg)

    try:
        from main import get_connection
        import optimizer as _opt

        conn = get_connection()
        result = _opt.run_optimize(
            conn,
            product_ids=product_ids,
            emit=emit,
            enforced_categories=enforced_categories or None,
            fresh_seed=fresh_seed,
        )

        with _tasks_lock:
            t = _tasks.get(task_id)
            if t:
                t["status"] = "done"
                t["updated"] = result["updated"]
                t["finished_at"] = time.time()

    except Exception as exc:
        logger.exception("Optimize task %s failed", task_id)
        emit(f"ERROR: {exc}")
        with _tasks_lock:
            t = _tasks.get(task_id)
            if t:
                t["status"] = "error"
                t["finished_at"] = time.time()
    finally:
        with _tasks_lock:
            if _running_task_id == task_id:
                _running_task_id = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/ai/optimize/categories")
def get_optimize_categories():
    """Return the current list of user-enforced product categories."""
    from main import get_connection
    conn = get_connection()
    return {"categories": _read_enforced_categories(conn)}


@router.put("/ai/optimize/categories")
def set_optimize_categories(body: dict[str, Any]):
    """Save the list of user-enforced product categories.

    Body JSON:
        categories: list[str]
    """
    from main import get_connection
    cats = body.get("categories", [])
    if not isinstance(cats, list):
        raise HTTPException(status_code=400, detail="'categories' must be a list")
    cats = [str(c).strip() for c in cats if str(c).strip()]
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
        (_CONFIG_KEY, json.dumps(cats, ensure_ascii=False)),
    )
    conn.commit()
    return {"categories": cats}


@router.post("/ai/optimize")
def start_optimize(body: dict[str, Any] = None):  # type: ignore[assignment]
    """Start a background AI optimize job (single-flight).

    Returns 409 if an optimize job is already running.

    Body (optional JSON):
        product_ids: list[int]  — if provided, only those products are optimized (incremental mode)

    Returns:
        task_id: str
    """
    global _running_task_id

    if body is None:
        body = {}

    product_ids: list[int] | None = body.get("product_ids") or None
    fresh_seed: bool = bool(body.get("fresh_seed", False))

    with _tasks_lock:
        # Reject if another optimize is already running
        if _running_task_id is not None:
            existing = _tasks.get(_running_task_id)
            if existing and existing.get("status") == "running":
                raise HTTPException(
                    status_code=409,
                    detail=f"Optimize already running (task {_running_task_id})",
                )
            # Stale running state (crash/restart) — clear it
            _running_task_id = None

        task_id = str(uuid.uuid4())[:8]
        _running_task_id = task_id

    # Read enforced categories from DB
    from main import get_connection
    conn = get_connection()
    enforced_categories = _read_enforced_categories(conn)

    _store_task(task_id, {
        "task_id": task_id,
        "status": "running",
        "logs": [],
        "updated": 0,
        "started_at": time.time(),
        "finished_at": None,
        "mode": "incremental" if product_ids else "full",
    })

    t = threading.Thread(
        target=_run_optimize_task,
        args=(task_id, product_ids, enforced_categories, fresh_seed),
        daemon=True,
        name=f"optimizer-{task_id}",
    )
    t.start()

    return {"task_id": task_id, "status": "running"}


@router.get("/ai/optimize/{task_id}")
def get_optimize_status(task_id: str):
    """Poll the status of a running or completed optimize job."""
    task = _get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task
