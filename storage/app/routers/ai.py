"""AI optimize endpoints for the Storage backend.

POST /api/ai/optimize               — start background optimize job → {task_id}
GET  /api/ai/optimize/categories    — get enforced categories list
PUT  /api/ai/optimize/categories    — save enforced categories list
GET  /api/ai/optimize/{task_id}     — poll status and streaming logs
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter

router = APIRouter(tags=["ai"])

_CONFIG_KEY = "optimize_categories"

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
# Background worker
# ---------------------------------------------------------------------------

def _run_optimize_task(
    task_id: str,
    product_ids: list[int] | None,
    enforced_categories: list[str] | None,
) -> None:
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
        )

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
        from fastapi import HTTPException
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
    """Start a background AI optimize job.

    Body (optional JSON):
        product_ids: list[int]  — if provided, only those products are optimized (incremental mode)

    Returns:
        task_id: str
    """
    if body is None:
        body = {}

    product_ids: list[int] | None = body.get("product_ids") or None

    # Read enforced categories from DB
    from main import get_connection
    conn = get_connection()
    enforced_categories = _read_enforced_categories(conn)

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
        args=(task_id, product_ids, enforced_categories),
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
