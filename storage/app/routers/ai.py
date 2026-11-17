"""AI optimize endpoints for the Storage backend.

POST /api/ai/optimize               — start a background optimize job → {task_id}
POST /api/ai/fix-expiry             — same, best-before days only
GET  /api/ai/optimize/categories    — the user's own list of product groups
PUT  /api/ai/optimize/categories    — save that list (creates the groups)
GET  /api/ai/optimize/{task_id}     — poll status and log
GET  /api/ai/optimize               — the running or most recent job

The work itself is enrich.run; this module is the single-flight job
registry around it. New products are enriched by enrich.start() on their own
and need no call here.
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
# In-memory task registry — single-flight
# ---------------------------------------------------------------------------

_tasks: dict[str, dict[str, Any]] = {}
_tasks_lock = threading.Lock()
_MAX_TASKS = 20
_MAX_LOG_LINES = 1000
# The currently running job; None when idle.
_running_task_id: str | None = None


def _append_log(task_id: str, msg: str) -> None:
    with _tasks_lock:
        t = _tasks.get(task_id)
        if t is not None:
            t["logs"].append(msg)
            del t["logs"][:-_MAX_LOG_LINES]  # the page shows the tail


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


def _run_task(task_id: str, kwargs: dict[str, Any]) -> None:
    """Background worker: run enrich.run and record how it ended."""
    global _running_task_id

    def emit(msg: str) -> None:
        _append_log(task_id, msg)

    try:
        from ai_client import call_ai_json
        from main import get_connection
        import enrich

        result = enrich.run(get_connection(), call_ai_json, emit=emit, **kwargs)
        outcome = {"status": "done", "updated": result["updated"]}
        if kwargs.get("dry_run"):
            outcome["proposals"] = result["proposals"]
    except Exception as exc:
        logger.exception("Optimize task %s failed", task_id)
        emit(f"ERROR: {exc}")
        outcome = {"status": "error"}
    with _tasks_lock:
        t = _tasks.get(task_id)
        if t:
            t.update(outcome, finished_at=time.time())
        if _running_task_id == task_id:
            _running_task_id = None


def _start_task(mode: str, kwargs: dict[str, Any]) -> dict[str, str]:
    """Register and start a job; 409 while another one runs."""
    global _running_task_id

    with _tasks_lock:
        running = _tasks.get(_running_task_id) if _running_task_id else None
        if running and running.get("status") == "running":
            raise HTTPException(
                status_code=409,
                detail=f"Optimize already running (task {_running_task_id})",
            )
        task_id = str(uuid.uuid4())[:8]
        _running_task_id = task_id
        _tasks[task_id] = {
            "task_id": task_id,
            "status": "running",
            "logs": [],
            "updated": 0,
            "started_at": time.time(),
            "finished_at": None,
            "mode": mode,
        }
        finished = [k for k, v in _tasks.items() if v["status"] != "running"]
        for k in finished[: max(0, len(_tasks) - _MAX_TASKS)]:
            del _tasks[k]

    threading.Thread(
        target=_run_task, args=(task_id, kwargs), daemon=True, name=f"optimize-{task_id}"
    ).start()
    return {"task_id": task_id, "status": "running"}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/ai/optimize/categories")
def get_optimize_categories():
    """Return the user's own list of product groups."""
    from main import get_connection
    return {"categories": _read_enforced_categories(get_connection())}


@router.put("/ai/optimize/categories")
def set_optimize_categories(body: dict[str, Any]):
    """Save the user's list of product groups and create the missing ones.
    The AI only ever picks from the groups that exist.

    Body JSON:
        categories: list[str]
    """
    from main import get_connection
    import enrich

    cats = body.get("categories", [])
    if not isinstance(cats, list):
        raise HTTPException(status_code=400, detail="'categories' must be a list")
    cats = [str(c).strip() for c in cats if str(c).strip()]
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
        (_CONFIG_KEY, json.dumps(cats, ensure_ascii=False)),
    )
    enrich.ensure_groups(conn, cats)  # commits
    return {"categories": cats}


@router.post("/ai/optimize")
def start_optimize(body: dict[str, Any] = None):  # type: ignore[assignment]
    """Start a background AI optimize job (single-flight; 409 while one runs).

    Body (optional JSON):
        product_ids: list[int]  — re-check exactly these products
        ungrouped_only: bool    — only products that lack a group, a location
                                  or best-before days; fills what is missing
        dry_run: bool           — write nothing; the finished task lists the
                                  changes it would make under "proposals"
    Without a body every active product is re-checked. Values set by hand are
    never overwritten.
    """
    from main import get_connection
    import enrich

    body = body or {}
    product_ids: list[int] | None = body.get("product_ids") or None
    missing_only = bool(body.get("ungrouped_only", False)) and not product_ids
    if missing_only and not enrich.missing(get_connection()):
        raise HTTPException(
            status_code=400,
            detail="Nothing is missing: every product has a group, a location and best-before days.",
        )
    return _start_task(
        "incremental" if product_ids or missing_only else "full",
        {
            "product_ids": product_ids,
            "missing_only": missing_only,
            "dry_run": bool(body.get("dry_run", False)),
            "groups": _read_enforced_categories(get_connection()),
        },
    )


@router.post("/ai/fix-expiry")
def start_expiry_fix(body: dict[str, Any] = None):  # type: ignore[assignment]
    """Start a background job that re-checks best-before days only and
    rebases stock lots that inherited the old default. Shares the optimize
    single-flight lock; poll status via GET /api/ai/optimize/{task_id}.

    Body (optional JSON):
        product_ids: list[int]
    """
    body = body or {}
    return _start_task(
        "expiry-fix",
        {"product_ids": body.get("product_ids") or None, "fields": ("shelf_life",)},
    )


@router.get("/ai/optimize/{task_id}")
def get_optimize_status(task_id: str):
    """Poll the status of a running or completed optimize job."""
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return {**task, "logs": list(task["logs"])}


@router.get("/ai/optimize")
def get_current_optimize_status():
    """Return the running or most-recent optimize task — `idle` if none."""
    with _tasks_lock:
        task = _tasks.get(_running_task_id) if _running_task_id else None
        if task is None and _tasks:
            task = max(_tasks.values(), key=lambda t: t.get("started_at") or 0)
        if task is None:
            return {
                "status": "idle",
                "task_id": None,
                "started_at": None,
                "finished_at": None,
                "logs": [],
                "updated": 0,
            }
        return {**task, "logs": list(task["logs"])}
