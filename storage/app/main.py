"""HA-Storage FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from database import get_db, init_db, sync_from_options
from models import HealthResponse

# ── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if os.getenv("DEBUG", "").lower() == "true" else logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
# Ensure app loggers are not silenced if uvicorn reconfigures the root level
for _mod in ("optimizer", "ai_client", "main"):
    logging.getLogger(_mod).setLevel(logging.INFO)
log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

def _read_version() -> str:
    """Read version from config.json next to this file, fallback to '0.0.0'."""
    try:
        import json as _json
        cfg = Path(__file__).parent.parent / "config.json"
        return _json.loads(cfg.read_text()).get("version", "0.0.0")
    except Exception:
        return "0.0.0"


VERSION = _read_version()
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "storage.db"

# ── Database lifecycle ─────────────────────────────────────────────────────

_init_lock = threading.Lock()
_initialized = False
_thread_local = threading.local()
# The open connection of the HTTP request being served (a one-item list),
# set by RequestConnectionMiddleware; None outside requests.
_request_conn: ContextVar[list | None] = ContextVar("_request_conn", default=None)


def _ensure_initialized() -> None:
    """Run the schema migrations and config seeding once per process."""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if not _initialized:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            conn = get_db(DB_PATH)
            try:
                init_db(conn)
                sync_from_options(conn)
                _seed_config(conn)
            finally:
                conn.close()
            _initialized = True


def get_connection():
    """Return the database connection for the current request.

    Every HTTP request gets its own connection, opened on first use and
    closed when the response is done, so uncommitted writes of a failed
    request are rolled back instead of being committed by (or blocking) the
    next one. One connection shared by FastAPI's worker threads raised
    "bad parameter or other API misuse" under concurrent requests. Outside a
    request (startup, the periodic sync, background threads, tests) each
    thread keeps one connection of its own.
    """
    _ensure_initialized()
    slot = _request_conn.get()
    if slot is None:
        conn = getattr(_thread_local, "conn", None)
        if conn is None:
            conn = _thread_local.conn = get_db(DB_PATH)
        return conn
    if not slot:
        slot.append(get_db(DB_PATH))
    return slot[0]


class RequestConnectionMiddleware:
    """Give each HTTP request its own database connection (see get_connection)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        slot: list = []
        token = _request_conn.set(slot)
        try:
            await self.app(scope, receive, send)
        finally:
            _request_conn.reset(token)
            for conn in slot:
                conn.close()  # discards anything the request did not commit


def _seed_config(conn):
    """Write addon config values to the config table.

    All values read from environment variables (set from HA Supervisor options)
    use INSERT OR REPLACE so that changes made via the HA interface always take
    effect on the next addon restart.  The UI Settings page saves to the same
    DB rows, which means the most-recent write wins.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    if api_key:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('gemini_api_key', ?)",
            (api_key,),
        )
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('gemini_model', ?)",
        (model,),
    )

    ai_provider = os.getenv("AI_PROVIDER", "")
    ollama_url = os.getenv("OLLAMA_URL", "")
    ollama_model = os.getenv("OLLAMA_MODEL", "")
    claude_api_key = os.getenv("CLAUDE_API_KEY", "")
    claude_model = os.getenv("CLAUDE_MODEL", "")
    if ai_provider:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('ai_provider', ?)",
            (ai_provider,),
        )
    if ollama_url:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('ollama_url', ?)",
            (ollama_url,),
        )
    if ollama_model:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('ollama_model', ?)",
            (ollama_model,),
        )
    if claude_api_key:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('claude_api_key', ?)",
            (claude_api_key,),
        )
    if claude_model:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('claude_model', ?)",
            (claude_model,),
        )

    # Seed ha_todo_entity default (INSERT OR IGNORE — user may override via UI)
    conn.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('ha_todo_entity', 'todo.smart_shopping_list')"
    )

    # Seed ha_stock_entity default (INSERT OR IGNORE — user may override via UI)
    conn.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('ha_stock_entity', 'todo.smart_stock_list')"
    )

    # Seed optimize_batch_size default (INSERT OR IGNORE — user may override via UI)
    conn.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('optimize_batch_size', '100')"
    )

    conn.commit()


# ── FastAPI app ────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    conn = get_connection()
    tables = conn.execute(
        "SELECT count(*) as cnt FROM sqlite_master WHERE type='table'"
    ).fetchone()
    log.info("Storage v%s ready — %d tables in DB.", VERSION, tables["cnt"])

    # Periodic shopping-list reconciliation. Runs every 5 minutes regardless
    # of whether the HA integration is installed, so auto-added rows are
    # cleared even if stock was changed via /stock/* hooks were missed (DB
    # tooling, restored backups, raised min_stock_amount, etc.).
    sync_task = asyncio.create_task(_periodic_shopping_sync())

    # After an upgrade, run the AI concept steps (placement, adoption, aisle
    # groups, facts) once in the background; retried on every start until an
    # AI provider has been available for one complete run.
    try:
        import concept_jobs
        concept_jobs.start_sweep_if_due()
    except Exception as exc:  # noqa: BLE001 — never block startup
        log.warning("Concept sweep did not start: %s", exc)

    yield

    # Shutdown
    sync_task.cancel()
    try:
        await sync_task
    except asyncio.CancelledError:
        pass


def periodic_sync_once() -> dict:
    """One run of the periodic shopping sync on this thread's connection.

    The loop reuses a worker thread, so the connection outlives the run. A run
    that fails is rolled back: Python's sqlite3 leaves the transaction open,
    and after a first write that transaction would keep the write lock and
    lock every other writer out.
    """
    from routers.shopping import sync_auto_shopping

    conn = get_connection()
    try:
        return sync_auto_shopping(conn)
    except BaseException:
        conn.rollback()
        raise


async def _periodic_shopping_sync() -> None:
    """Background loop that reconciles auto-added shopping rows every 5 min."""
    # First run shortly after startup so a stale list is cleaned up quickly.
    await asyncio.sleep(15)
    while True:
        try:
            await asyncio.to_thread(periodic_sync_once)
        except Exception as exc:  # noqa: BLE001 — never let the loop die
            log.warning("Periodic shopping sync failed: %s", exc)
        await asyncio.sleep(300)


app = FastAPI(
    title="HA-Storage",
    version=VERSION,
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestConnectionMiddleware)

# ── Ingress path rewrite middleware ────────────────────────────────────────

@app.middleware("http")
async def ingress_path_middleware(request: Request, call_next):
    """Strip HA ingress path prefix so routes match."""
    ingress_path = request.headers.get("X-Ingress-Path", "")
    if ingress_path and request.url.path.startswith(ingress_path):
        scope = request.scope
        scope["path"] = request.url.path[len(ingress_path):]
    return await call_next(request)


# ── Health endpoint ────────────────────────────────────────────────────────

@app.get("/api/health", response_model=HealthResponse)
def health():
    conn = get_connection()
    tables = conn.execute(
        "SELECT count(*) as cnt FROM sqlite_master WHERE type='table'"
    ).fetchone()
    return HealthResponse(status="ok", version=VERSION, db_tables=tables["cnt"])


# ── Include routers ───────────────────────────────────────────────────────

from routers import (
    products,
    stock,
    barcodes,
    units,
    locations,
    groups,
    recipes,
    shopping,
    bundles,
    files,
    config,
    migrate,
    ai,
    history,
    stats,
    receipts,
    stores,
    links,
    ingredients,
)

app.include_router(products.router, prefix="/api")
app.include_router(stock.router, prefix="/api")
app.include_router(barcodes.router, prefix="/api")
app.include_router(units.router, prefix="/api")
app.include_router(locations.router, prefix="/api")
app.include_router(groups.router, prefix="/api")
app.include_router(recipes.router, prefix="/api")
app.include_router(shopping.router, prefix="/api")
app.include_router(bundles.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(config.router, prefix="/api")
app.include_router(migrate.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(history.router, prefix="/api")
app.include_router(stats.router, prefix="/api")
app.include_router(receipts.router, prefix="/api")
app.include_router(stores.router, prefix="/api")
app.include_router(links.router, prefix="/api")
app.include_router(ingredients.router, prefix="/api")


# ── Error handler ──────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"error": str(exc)})


# ── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8100,
        log_level="debug" if os.getenv("DEBUG", "").lower() == "true" else "info",
    )
