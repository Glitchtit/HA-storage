"""AI optimize endpoints for the Storage backend.

POST /api/ai/optimize          — start background optimize job → {task_id}
GET  /api/ai/optimize/{task_id} — poll status and streaming logs
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter

router = APIRouter(tags=["ai"])

# ---------------------------------------------------------------------------
# In-memory task registry (fire-and-poll, same pattern as scraper ingress)
# ---------------------------------------------------------------------------

_tasks: dict[str, dict[str, Any]] = {}
_tasks_lock = threading.Lock()
_MAX_TASKS = 20


def _store_task(task_id: str, data: dict[str, Any]) -> None:
    with _tasks_lock:
        _tasks[task_id] = data
        # Evict oldest completed tasks when over limit
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


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def _run_optimize_task(task_id: str, product_ids: list[int] | None) -> None:
    def emit(msg: str) -> None:
        _append_log(task_id, msg)

    try:
        from main import get_connection
        import optimizer as _opt

        conn = get_connection()
        result = _opt.run_optimize(conn, product_ids=product_ids, emit=emit)

        with _tasks_lock:
            t = _tasks.get(task_id)
            if t:
                t["status"] = "done"
                t["updated"] = result["updated"]
                t["finished_at"] = time.time()

    except Exception as exc:
        emit(f"ERROR: {exc}")
        with _tasks_lock:
            t = _tasks.get(task_id)
            if t:
                t["status"] = "error"
                t["finished_at"] = time.time()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/ai/optimize")
def start_optimize(body: dict[str, Any] = None):  # type: ignore[assignment]
    """Start a background AI optimize job.

    Body (optional JSON):
        product_ids: list[int]  — if provided, only those products are optimized (incremental mode)

    Returns:
        task_id: str
    """
    if body is None:
        body = {}

    product_ids: list[int] | None = body.get("product_ids") or None

    task_id = str(uuid.uuid4())[:8]
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
        args=(task_id, product_ids),
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
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Task not found")
    return task
