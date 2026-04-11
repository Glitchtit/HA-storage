"""Grocy migration endpoint — import barcodes and stock from Grocy REST API."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException

from models import GrocyMigrationRequest, MigrationResult

router = APIRouter(tags=["migration"])
log = logging.getLogger(__name__)

_RESET_TABLES = [
    "shopping_list",
    "recipe_ingredients",
    "recipes",
    "barcode_queue",
    "stock",
    "unit_conversions",
    "barcodes",
    "products",
    "product_groups",
    "locations",
    "units",
    "config",
    "_meta",
]


def _get_db():
    from main import get_connection
    return get_connection()


def _grocy_get(base_url: str, api_key: str, endpoint: str) -> list | dict:
    """Fetch from Grocy REST API."""
    headers = {"GROCY-API-KEY": api_key, "Accept": "application/json"}
    url = f"{base_url.rstrip('/')}/api/{endpoint}"
    resp = httpx.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


@router.post("/migrate/grocy", response_model=MigrationResult)
def migrate_from_grocy(body: GrocyMigrationRequest):
    """Import barcodes and stock amounts from a Grocy instance.

    Fetches product barcodes and current stock from Grocy, then queues
    each unique barcode in the barcode queue with its stock amount.
    The scraper's discover flow will create products and restore stock automatically.
    """
    conn = _get_db()
    result = MigrationResult()
    grocy_url = body.grocy_url.rstrip("/")
    api_key = body.api_key

    try:
        # Fetch barcodes: each entry has product_id and barcode
        log.info("Fetching barcodes from Grocy...")
        grocy_barcodes = _grocy_get(grocy_url, api_key, "objects/product_barcodes")

        # Fetch stock: each entry has product_id and amount
        log.info("Fetching stock from Grocy...")
        grocy_stock = _grocy_get(grocy_url, api_key, "stock")

        # Build product_id → stock amount map
        stock_map: dict[int, float] = {}
        for s in grocy_stock:
            pid = s.get("product_id")
            if pid is not None:
                stock_map[pid] = float(s.get("amount", 0))

        # Get already-queued barcodes to avoid duplicates
        existing = {
            r["barcode"]
            for r in conn.execute(
                "SELECT barcode FROM barcode_queue"
            ).fetchall()
        }
        # Also skip barcodes already registered as products
        known = {
            r["barcode"]
            for r in conn.execute("SELECT barcode FROM barcodes").fetchall()
        }
        skip_set = existing | known

        # Queue each unique barcode with its stock amount
        seen: set[str] = set()
        for bc_entry in grocy_barcodes:
            barcode = bc_entry.get("barcode", "").strip()
            if not barcode or barcode in seen:
                continue
            seen.add(barcode)

            if barcode in skip_set:
                result.barcodes_skipped += 1
                continue

            grocy_pid = bc_entry.get("product_id")
            stock_amount = stock_map.get(grocy_pid, 0) if grocy_pid else 0

            try:
                conn.execute(
                    "INSERT INTO barcode_queue (barcode, source, import_stock_amount) "
                    "VALUES (?, ?, ?)",
                    (barcode, "grocy-import", stock_amount if stock_amount > 0 else None),
                )
                result.barcodes_queued += 1
            except Exception as e:
                result.errors.append(f"Barcode '{barcode}': {e}")

        conn.commit()
        log.info(
            "Grocy import complete: %d barcodes queued, %d skipped.",
            result.barcodes_queued, result.barcodes_skipped,
        )

    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Grocy API error: {e.response.status_code} {e.response.text}")
    except httpx.ConnectError:
        raise HTTPException(502, f"Cannot connect to Grocy at {grocy_url}")
    except Exception as e:
        log.exception("Migration failed: %s", e)
        result.errors.append(str(e))

    return result


@router.post("/reset")
def factory_reset():
    """Wipe the entire database and uploaded images, then re-seed defaults.

    Deletes all user data (products, stock, barcodes, recipes, etc.),
    resets auto-increment IDs, and re-seeds standard units, conversions,
    and locations so the app is in a clean initial state.
    """
    from main import get_connection, DATA_DIR
    from database import init_db

    conn = get_connection()

    try:
        # foreign_keys pragma must be set outside a transaction
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("SAVEPOINT factory_reset")
        try:
            for table in _RESET_TABLES:
                conn.execute(f"DELETE FROM {table}")  # noqa: S608 (controlled list)
            conn.execute(
                "DELETE FROM sqlite_sequence WHERE name IN ({})".format(
                    ",".join("?" * len(_RESET_TABLES))
                ),
                _RESET_TABLES,
            )
            conn.execute("RELEASE factory_reset")
            conn.commit()
        except Exception:
            conn.execute("ROLLBACK TO factory_reset")
            conn.execute("RELEASE factory_reset")
            raise

        # Re-seed units, conversions, locations
        init_db(conn)

    except Exception as e:
        log.exception("Factory reset failed: %s", e)
        raise HTTPException(500, f"Reset failed: {e}")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

    # Delete all uploaded images
    for img_dir in ["images/products", "images/recipes"]:
        p = Path(DATA_DIR) / img_dir
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            p.mkdir(parents=True, exist_ok=True)

    log.info("Factory reset complete.")
    return {"status": "ok", "message": "Database reset complete"}

