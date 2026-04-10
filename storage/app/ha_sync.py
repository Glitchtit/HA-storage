"""Home Assistant To-do list sync for the Smart Shopping List."""

from __future__ import annotations

import logging
import os
import sqlite3

import httpx

log = logging.getLogger(__name__)

_HA_BASE = "http://supervisor/core/api"
_DEFAULT_ENTITY = "todo.smart_shopping_list"
_DEFAULT_LIST_NAME = "Smart shopping list"


def _token() -> str | None:
    return os.environ.get("SUPERVISOR_TOKEN")


def _headers() -> dict[str, str] | None:
    tok = _token()
    if not tok:
        return None
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _entity(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM config WHERE key = 'ha_todo_entity'"
    ).fetchone()
    return (row["value"].strip() if row and row["value"] else "") or _DEFAULT_ENTITY


def ha_ensure_entity(conn: sqlite3.Connection) -> bool:
    """Auto-create the HA to-do list if it doesn't exist. Returns True if entity exists after call."""
    hdrs = _headers()
    if not hdrs:
        log.debug("SUPERVISOR_TOKEN not set — skipping HA todo sync.")
        return False

    entity_id = _entity(conn)

    try:
        resp = httpx.get(f"{_HA_BASE}/states/{entity_id}", headers=hdrs, timeout=5)
        if resp.status_code == 200:
            return True
        if resp.status_code != 404:
            log.warning("HA entity check returned %d: %s", resp.status_code, resp.text[:300])
    except Exception as exc:
        log.warning("HA entity check failed: %s", exc)
        return False

    # Create via config flow — field name is "todo_list_name" in local_todo integration
    try:
        r1 = httpx.post(
            f"{_HA_BASE}/config/config_entries/flow",
            headers=hdrs,
            json={"handler": "local_todo"},
            timeout=10,
        )
        if not r1.is_success:
            log.warning("HA local_todo flow init failed: %d %s", r1.status_code, r1.text[:300])
            return False
        flow = r1.json()
        flow_id = flow.get("flow_id")
        if not flow_id:
            log.warning("HA flow response missing flow_id: %s", flow)
            return False

        r2 = httpx.post(
            f"{_HA_BASE}/config/config_entries/flow/{flow_id}",
            headers=hdrs,
            json={"todo_list_name": _DEFAULT_LIST_NAME},
            timeout=10,
        )
        if r2.is_success:
            result = r2.json()
            log.info(
                "Created HA to-do list '%s' (entity: %s) — flow result: %s",
                _DEFAULT_LIST_NAME, entity_id, result.get("type", "?"),
            )
            return True
        log.warning("HA flow submit failed: %d %s", r2.status_code, r2.text[:300])
    except Exception as exc:
        log.warning("HA entity creation failed: %s", exc)
    return False


def ha_add_item(conn: sqlite3.Connection, item_name: str, description: str = "") -> None:
    hdrs = _headers()
    if not hdrs:
        return
    entity_id = _entity(conn)
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
            log.warning("HA add_item failed for '%s': %d %s", item_name, resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("HA add_item error: %s", exc)


def ha_remove_item(conn: sqlite3.Connection, item_name: str) -> None:
    hdrs = _headers()
    if not hdrs:
        return
    entity_id = _entity(conn)
    try:
        resp = httpx.post(
            f"{_HA_BASE}/services/todo/remove_item",
            headers=hdrs,
            json={"entity_id": entity_id, "item": item_name},
            timeout=5,
        )
        if not resp.is_success and resp.status_code != 404:
            log.warning("HA remove_item failed for '%s': %d %s", item_name, resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("HA remove_item error: %s", exc)


def ha_complete_item(conn: sqlite3.Connection, item_name: str) -> None:
    _ha_update_status(conn, item_name, "completed")


def ha_uncomplete_item(conn: sqlite3.Connection, item_name: str) -> None:
    _ha_update_status(conn, item_name, "needs_action")


def _ha_update_status(conn: sqlite3.Connection, item_name: str, status: str) -> None:
    hdrs = _headers()
    if not hdrs:
        return
    entity_id = _entity(conn)
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


def sync_product_shopping_list(conn: sqlite3.Connection, product_id: int) -> None:
    """Auto-manage shopping list entry for one product based on stock vs min_stock_amount.

    - stock < min  → ensure an auto-added item exists + add to HA
    - stock >= min → remove ALL pending items (auto and manual) + remove from HA
    """
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
        # Ensure HA entity exists if there's anything on the shopping list
        if _token():
            ha_ensure_entity(conn)
    except Exception as exc:
        log.warning("Startup shopping list sync failed: %s", exc)


def ha_full_sync(conn: sqlite3.Connection) -> dict:
    """Ensure entity exists, then sync all pending shopping list items to HA."""
    if not _token():
        return {"skipped": True, "reason": "SUPERVISOR_TOKEN not available"}

    created = ha_ensure_entity(conn)

    items = conn.execute("""
        SELECT sl.id, sl.done, sl.ha_item_name,
               p.name as product_name, sl.note
        FROM shopping_list sl
        JOIN products p ON p.id = sl.product_id
    """).fetchall()

    added = completed = 0
    for item in items:
        name = item["ha_item_name"] or item["product_name"]
        if not item["ha_item_name"]:
            conn.execute("UPDATE shopping_list SET ha_item_name = ? WHERE id = ?",
                         (item["product_name"], item["id"]))
        if item["done"]:
            ha_complete_item(conn, name)
            completed += 1
        else:
            ha_add_item(conn, name, item["note"] or "")
            added += 1
    conn.commit()

    return {"entity_created": created, "added": added, "completed": completed}



def _token() -> str | None:
    return os.environ.get("SUPERVISOR_TOKEN")


def _headers() -> dict[str, str] | None:
    tok = _token()
    if not tok:
        return None
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _entity(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM config WHERE key = 'ha_todo_entity'"
    ).fetchone()
    return (row["value"].strip() if row and row["value"] else "") or _DEFAULT_ENTITY


def ha_ensure_entity(conn: sqlite3.Connection) -> bool:
    """Auto-create the HA to-do list if it doesn't exist. Returns True on success."""
    hdrs = _headers()
    if not hdrs:
        log.debug("SUPERVISOR_TOKEN not set — skipping HA todo sync.")
        return False

    entity_id = _entity(conn)

    # Check if entity already exists
    try:
        resp = httpx.get(f"{_HA_BASE}/states/{entity_id}", headers=hdrs, timeout=5)
        if resp.status_code == 200:
            return True
        if resp.status_code != 404:
            log.warning("HA entity check returned %d", resp.status_code)
    except Exception as exc:
        log.warning("HA entity check failed: %s", exc)
        return False

    # Create via config flow
    list_name = _DEFAULT_LIST_NAME
    try:
        # Determine list name from entity_id (reverse: todo.smart_shopping_list → Smart shopping list)
        # Use stored list name or derive it
        r1 = httpx.post(
            f"{_HA_BASE}/config/config_entries/flow",
            headers=hdrs,
            json={"handler": "local_todo"},
            timeout=10,
        )
        if not r1.is_success:
            log.warning("HA local_todo flow init failed: %d %s", r1.status_code, r1.text[:200])
            return False
        flow = r1.json()
        flow_id = flow.get("flow_id")
        if not flow_id:
            log.warning("HA flow response missing flow_id: %s", flow)
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


def ha_add_item(conn: sqlite3.Connection, item_name: str, description: str = "") -> None:
    """Add an item to the HA to-do list."""
    hdrs = _headers()
    if not hdrs:
        return
    entity_id = _entity(conn)
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
            log.warning("HA add_item failed for '%s': %d", item_name, resp.status_code)
    except Exception as exc:
        log.warning("HA add_item error: %s", exc)


def ha_remove_item(conn: sqlite3.Connection, item_name: str) -> None:
    """Remove an item from the HA to-do list (silent if not found)."""
    hdrs = _headers()
    if not hdrs:
        return
    entity_id = _entity(conn)
    try:
        resp = httpx.post(
            f"{_HA_BASE}/services/todo/remove_item",
            headers=hdrs,
            json={"entity_id": entity_id, "item": item_name},
            timeout=5,
        )
        if not resp.is_success and resp.status_code != 404:
            log.warning("HA remove_item failed for '%s': %d", item_name, resp.status_code)
    except Exception as exc:
        log.warning("HA remove_item error: %s", exc)


def ha_complete_item(conn: sqlite3.Connection, item_name: str) -> None:
    """Mark an item completed in the HA to-do list."""
    _ha_update_status(conn, item_name, "completed")


def ha_uncomplete_item(conn: sqlite3.Connection, item_name: str) -> None:
    """Mark an item needs_action in the HA to-do list."""
    _ha_update_status(conn, item_name, "needs_action")


def _ha_update_status(conn: sqlite3.Connection, item_name: str, status: str) -> None:
    hdrs = _headers()
    if not hdrs:
        return
    entity_id = _entity(conn)
    try:
        resp = httpx.post(
            f"{_HA_BASE}/services/todo/update_item",
            headers=hdrs,
            json={"entity_id": entity_id, "item": item_name, "status": status},
            timeout=5,
        )
        if not resp.is_success:
            log.warning("HA update_item(%s) failed for '%s': %d", status, item_name, resp.status_code)
    except Exception as exc:
        log.warning("HA update_item error: %s", exc)


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
            # Store ha_item_name if not already set
            if not item["ha_item_name"]:
                conn.execute(
                    "UPDATE shopping_list SET ha_item_name = ? WHERE id = ?",
                    (item["product_name"], item["id"]),
                )
    conn.commit()

    return {"entity_created": created, "added": added, "completed": completed, "removed": removed}
