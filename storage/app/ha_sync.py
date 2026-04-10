"""Home Assistant To-do list sync — Smart Shopping List and Smart Stock List."""

from __future__ import annotations

import logging
import os
import sqlite3

import httpx

log = logging.getLogger(__name__)

_HA_BASE = "http://supervisor/core/api"

# Shopping list
_DEFAULT_SHOPPING_ENTITY = "todo.smart_shopping_list"
_DEFAULT_SHOPPING_LIST_NAME = "Smart shopping list"

# Stock list
_DEFAULT_STOCK_ENTITY = "todo.smart_stock_list"
_DEFAULT_STOCK_LIST_NAME = "Smart stock list"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _token() -> str | None:
    return os.environ.get("SUPERVISOR_TOKEN")


def _headers() -> dict[str, str] | None:
    tok = _token()
    if not tok:
        return None
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _entity(conn: sqlite3.Connection) -> str:
    """Return the shopping-list HA entity ID from config."""
    row = conn.execute(
        "SELECT value FROM config WHERE key = 'ha_todo_entity'"
    ).fetchone()
    return (row["value"].strip() if row and row["value"] else "") or _DEFAULT_SHOPPING_ENTITY


def _stock_entity(conn: sqlite3.Connection) -> str:
    """Return the stock-list HA entity ID from config."""
    row = conn.execute(
        "SELECT value FROM config WHERE key = 'ha_stock_entity'"
    ).fetchone()
    return (row["value"].strip() if row and row["value"] else "") or _DEFAULT_STOCK_ENTITY


def _entity_exists(entity_id: str) -> bool:
    hdrs = _headers()
    if not hdrs:
        return False
    try:
        resp = httpx.get(f"{_HA_BASE}/states/{entity_id}", headers=hdrs, timeout=5)
        return resp.status_code == 200
    except Exception as exc:
        log.warning("HA entity check failed: %s", exc)
        return False


def _add_item(entity_id: str, item_name: str, description: str = "") -> None:
    hdrs = _headers()
    if not hdrs:
        return
    try:
        payload: dict = {"entity_id": entity_id, "item": item_name}
        if description:
            payload["description"] = description
        resp = httpx.post(
            f"{_HA_BASE}/services/todo/add_item",
            headers=hdrs,
            json=payload,
            timeout=5,
        )
        if not resp.is_success:
            log.warning("HA add_item failed for '%s' on '%s': %d %s",
                        item_name, entity_id, resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("HA add_item error: %s", exc)


def _remove_item(entity_id: str, item_name: str) -> None:
    hdrs = _headers()
    if not hdrs:
        return
    try:
        resp = httpx.post(
            f"{_HA_BASE}/services/todo/remove_item",
            headers=hdrs,
            json={"entity_id": entity_id, "item": item_name},
            timeout=5,
        )
        if not resp.is_success and resp.status_code != 404:
            log.warning("HA remove_item failed for '%s' on '%s': %d %s",
                        item_name, entity_id, resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("HA remove_item error: %s", exc)


def _update_status(entity_id: str, item_name: str, status: str) -> None:
    hdrs = _headers()
    if not hdrs:
        return
    try:
        resp = httpx.post(
            f"{_HA_BASE}/services/todo/update_item",
            headers=hdrs,
            json={"entity_id": entity_id, "item": item_name, "status": status},
            timeout=5,
        )
        if not resp.is_success:
            log.warning("HA update_item(%s) failed for '%s': %d %s",
                        status, item_name, resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("HA update_item error: %s", exc)


def _ensure_entity(entity_id: str, list_name: str) -> bool:
    """Ensure a HA Local To-do entity exists, creating it via config flow if needed."""
    hdrs = _headers()
    if not hdrs:
        log.debug("SUPERVISOR_TOKEN not set — skipping HA todo sync.")
        return False

    try:
        resp = httpx.get(f"{_HA_BASE}/states/{entity_id}", headers=hdrs, timeout=5)
        if resp.status_code == 200:
            return True
        if resp.status_code != 404:
            log.warning("HA entity check returned %d", resp.status_code)
    except Exception as exc:
        log.warning("HA entity check failed: %s", exc)
        return False

    try:
        r1 = httpx.post(
            f"{_HA_BASE}/config/config_entries/flow",
            headers=hdrs,
            json={"handler": "local_todo"},
            timeout=10,
        )
        if not r1.is_success:
            log.warning("HA local_todo flow init failed: %d %s", r1.status_code, r1.text[:200])
            return False
        flow_id = r1.json().get("flow_id")
        if not flow_id:
            return False

        r2 = httpx.post(
            f"{_HA_BASE}/config/config_entries/flow/{flow_id}",
            headers=hdrs,
            json={"name": list_name},
            timeout=10,
        )
        if r2.is_success:
            log.info("Created HA to-do list '%s' (entity: %s)", list_name, entity_id)
            return True
        log.warning("HA flow submit failed: %d %s", r2.status_code, r2.text[:200])
    except Exception as exc:
        log.warning("HA entity creation failed: %s", exc)
    return False


# ---------------------------------------------------------------------------
# Shopping List — public API (entity = ha_todo_entity config key)
# ---------------------------------------------------------------------------

def ha_ensure_entity(conn: sqlite3.Connection) -> bool:
    return _ensure_entity(_entity(conn), _DEFAULT_SHOPPING_LIST_NAME)


def ha_check_status(conn: sqlite3.Connection) -> dict:
    token_available = bool(_token())
    entity_id = _entity(conn)
    entity_exists = _entity_exists(entity_id) if token_available else False
    return {"token_available": token_available, "entity_id": entity_id, "entity_exists": entity_exists}


def ha_add_item(conn: sqlite3.Connection, item_name: str, description: str = "") -> None:
    _add_item(_entity(conn), item_name, description)


def ha_remove_item(conn: sqlite3.Connection, item_name: str) -> None:
    _remove_item(_entity(conn), item_name)


def ha_complete_item(conn: sqlite3.Connection, item_name: str) -> None:
    _update_status(_entity(conn), item_name, "completed")


def ha_uncomplete_item(conn: sqlite3.Connection, item_name: str) -> None:
    _update_status(_entity(conn), item_name, "needs_action")


def sync_product_shopping_list(conn: sqlite3.Connection, product_id: int) -> None:
    """Auto-manage shopping list entry for one product based on stock vs min_stock_amount."""
    product = conn.execute(
        "SELECT name, min_stock_amount, unit_id FROM products WHERE id = ?", (product_id,)
    ).fetchone()
    if not product:
        return

    min_amount = product["min_stock_amount"] or 0
    if min_amount <= 0:
        return

    total = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) as t FROM stock WHERE product_id = ?", (product_id,)
    ).fetchone()["t"]

    existing = conn.execute(
        "SELECT id, ha_item_name FROM shopping_list WHERE product_id = ? AND done = 0",
        (product_id,),
    ).fetchall()

    if total < min_amount:
        if not existing:
            ha_ensure_entity(conn)
            item_name = product["name"]
            conn.execute(
                "INSERT INTO shopping_list (product_id, amount, unit_id, note, auto_added, ha_item_name)"
                " VALUES (?, ?, ?, ?, 1, ?)",
                (product_id, min_amount, product["unit_id"], "", item_name),
            )
            conn.commit()
            ha_add_item(conn, item_name, f"Min: {min_amount}")
            log.info("Auto-added '%s' to shopping list (stock %.2f < min %.2f).",
                     product["name"], total, min_amount)
    else:
        if existing:
            for item in existing:
                name = item["ha_item_name"] or product["name"]
                ha_remove_item(conn, name)
            conn.execute(
                "DELETE FROM shopping_list WHERE product_id = ? AND done = 0", (product_id,)
            )
            conn.commit()
            log.info("Removed %d shopping list item(s) for '%s' (stock %.2f >= min %.2f).",
                     len(existing), product["name"], total, min_amount)


def startup_sync(conn: sqlite3.Connection) -> None:
    """On startup: scan all products with min_stock_amount > 0 and sync shopping list + HA."""
    try:
        products = conn.execute(
            "SELECT id FROM products WHERE min_stock_amount > 0 AND active = 1"
        ).fetchall()
        if not products:
            return
        log.info("Startup shopping list sync: checking %d product(s) with min stock set.", len(products))
        for row in products:
            sync_product_shopping_list(conn, row["id"])
        if _token():
            ha_ensure_entity(conn)
    except Exception as exc:
        log.warning("Startup shopping list sync failed: %s", exc)


def ha_full_sync(conn: sqlite3.Connection) -> dict:
    """Ensure entity exists, then sync all shopping list items to HA."""
    if not _token():
        return {"skipped": True, "reason": "SUPERVISOR_TOKEN not available"}

    created = ha_ensure_entity(conn)

    items = conn.execute("""
        SELECT sl.id, sl.done, sl.ha_item_name,
               p.name as product_name, sl.note, sl.amount
        FROM shopping_list sl
        JOIN products p ON p.id = sl.product_id
    """).fetchall()

    added = removed = completed = 0
    for item in items:
        name = item["ha_item_name"] or item["product_name"]
        desc = item["note"] or ""
        if item["done"]:
            ha_complete_item(conn, name)
            completed += 1
        else:
            ha_add_item(conn, name, desc)
            added += 1
            if not item["ha_item_name"]:
                conn.execute(
                    "UPDATE shopping_list SET ha_item_name = ? WHERE id = ?",
                    (item["product_name"], item["id"]),
                )
    conn.commit()

    return {"entity_created": created, "added": added, "completed": completed, "removed": removed}


# ---------------------------------------------------------------------------
# Stock List — public API (entity = ha_stock_entity config key)
# ---------------------------------------------------------------------------

def ha_check_stock_status(conn: sqlite3.Connection) -> dict:
    token_available = bool(_token())
    entity_id = _stock_entity(conn)
    entity_exists = _entity_exists(entity_id) if token_available else False
    return {"token_available": token_available, "entity_id": entity_id, "entity_exists": entity_exists}


def _stock_description(amount: float, unit_abbrev: str | None) -> str:
    if amount == int(amount):
        amt_str = str(int(amount))
    else:
        amt_str = f"{amount:.2f}".rstrip("0").rstrip(".")
    return f"{amt_str} {unit_abbrev}" if unit_abbrev else amt_str


def sync_product_stock_list(conn: sqlite3.Connection, product_id: int) -> None:
    """Push one product's stock state to the Smart Stock HA to-do list.

    - stock > 0  → remove (if present) then re-add with current amount as description
    - stock == 0 → remove
    Always remove-before-add to keep the description (amount) current.
    """
    if not _token():
        return

    product = conn.execute(
        """SELECT p.name, u.abbreviation
           FROM products p
           LEFT JOIN units u ON u.id = p.unit_id
           WHERE p.id = ?""",
        (product_id,),
    ).fetchone()
    if not product:
        return

    total_row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) as t FROM stock WHERE product_id = ?",
        (product_id,),
    ).fetchone()
    total = total_row["t"] if total_row else 0

    entity_id = _stock_entity(conn)
    item_name = product["name"]

    # Always remove first (idempotent — silent if not present)
    _remove_item(entity_id, item_name)

    if total > 0:
        _ensure_entity(entity_id, _DEFAULT_STOCK_LIST_NAME)
        desc = _stock_description(total, product["abbreviation"])
        _add_item(entity_id, item_name, desc)
        log.debug("Stock list: updated '%s' → %s", item_name, desc)
    else:
        log.debug("Stock list: removed '%s' (stock 0)", item_name)


def startup_sync_stock(conn: sqlite3.Connection) -> None:
    """On startup: rebuild the stock list from scratch (all products with stock > 0)."""
    try:
        if not _token():
            return
        rows = conn.execute("""
            SELECT DISTINCT s.product_id
            FROM stock s
            WHERE s.amount > 0
        """).fetchall()
        if not rows:
            return
        log.info("Startup stock list sync: updating %d product(s) with stock.", len(rows))
        for row in rows:
            sync_product_stock_list(conn, row["product_id"])
    except Exception as exc:
        log.warning("Startup stock list sync failed: %s", exc)


def ha_full_stock_sync(conn: sqlite3.Connection) -> dict:
    """Rebuild the entire Smart Stock list from current stock data."""
    if not _token():
        return {"skipped": True, "reason": "SUPERVISOR_TOKEN not available"}

    rows = conn.execute("""
        SELECT s.product_id, COALESCE(SUM(s.amount), 0) as total,
               p.name, u.abbreviation
        FROM stock s
        JOIN products p ON p.id = s.product_id
        LEFT JOIN units u ON u.id = p.unit_id
        GROUP BY s.product_id
    """).fetchall()

    entity_id = _stock_entity(conn)
    added = removed = 0
    for row in rows:
        item_name = row["name"]
        _remove_item(entity_id, item_name)
        if row["total"] > 0:
            _ensure_entity(entity_id, _DEFAULT_STOCK_LIST_NAME)
            desc = _stock_description(row["total"], row["abbreviation"])
            _add_item(entity_id, item_name, desc)
            added += 1
        else:
            removed += 1

    return {"added": added, "removed": removed}
