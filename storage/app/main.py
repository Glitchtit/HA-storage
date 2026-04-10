"""HA-Storage FastAPI application entry point."""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
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

_db = None


def get_connection():
    """Return the shared database connection."""
    global _db
    if _db is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _db = get_db(DB_PATH)
        init_db(_db)
        sync_from_options(_db)
        _seed_config(_db)
    return _db


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
    # Sync shopping list for all products with a minimum stock level
    import ha_sync as _ha
    _ha.startup_sync(conn)
    yield
    # Shutdown
    global _db
    if _db:
        _db.close()
        _db = None


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
    files,
    config,
    migrate,
    ai,
)

app.include_router(products.router, prefix="/api")
app.include_router(stock.router, prefix="/api")
app.include_router(barcodes.router, prefix="/api")
app.include_router(units.router, prefix="/api")
app.include_router(locations.router, prefix="/api")
app.include_router(groups.router, prefix="/api")
app.include_router(recipes.router, prefix="/api")
app.include_router(shopping.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(config.router, prefix="/api")
app.include_router(migrate.router, prefix="/api")
app.include_router(ai.router, prefix="/api")


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
