"""Product CRUD endpoints."""

from __future__ import annotations

import json
import logging
import threading

from fastapi import APIRouter, HTTPException, Query

from availability import own_unit_have
from facts import Catalog
from locks import CATALOG_LOCK
from models import (
    AliasCreate, Product, ProductCreate, ProductDetail, ProductLookup, ProductMerge, ProductUpdate,
)
import tree
import pack_size

router = APIRouter(tags=["products"])
log = logging.getLogger(__name__)


def _get_db():
    from main import get_connection
    return get_connection()


KINDS = ("concept", "sku")
# Fact columns whose provenance is tracked in products.facts_meta. The two
# content columns share one key because they are one fact ("2 kg").
_FACT_FIELDS = ("content_amount", "content_unit_id", "pack_count", "density_g_ml", "piece_weight_g")


def _fact_key(field: str) -> str:
    return "content" if field.startswith("content_") else field


def _infer_kind(kind: str | None) -> str:
    """An explicit kind wins; anything created without one is a SKU (concepts
    come from the ingredient resolver, the admin UI or a parent promotion)."""
    if kind is not None and kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {KINDS}")
    return kind or "sku"


def _manual_facts_meta(existing_json: str | None, changed: dict) -> str | None:
    """Mark each changed fact as 'manual'; a fact cleared to NULL loses its
    provenance entry. Returns the JSON to store (None when empty)."""
    try:
        meta = json.loads(existing_json or "{}")
        if not isinstance(meta, dict):
            meta = {}
    except ValueError:
        meta = {}
    for field, value in changed.items():
        key = _fact_key(field)
        if value is None:
            meta.pop(key, None)
        else:
            meta[key] = "manual"
    return json.dumps(meta, sort_keys=True) if meta else None


def _validate_unit(conn, unit_id: int | None, label: str) -> None:
    if unit_id is not None and not conn.execute(
        "SELECT id FROM units WHERE id = ?", (unit_id,)
    ).fetchone():
        raise HTTPException(400, f"{label} {unit_id} not found")


def _stores_by_product(conn, product_ids: list[int] | None = None) -> dict[int, list[dict]]:
    """Map product_id → per-store availability dicts (joined with store names).
    With product_ids=None the whole table is fetched (bulk list endpoint)."""
    sql = """
        SELECT pa.product_id, pa.store_id, s.name, pa.available,
               pa.price, pa.price_currency, pa.checked_at, pa.source
        FROM product_availability pa
        JOIN stores s ON s.id = pa.store_id
    """
    params: list = []
    if product_ids is not None:
        sql += " WHERE pa.product_id IN (%s)" % ",".join("?" for _ in product_ids)
        params = list(product_ids)
    out: dict[int, list[dict]] = {}
    order = " ORDER BY pa.available DESC, s.name COLLATE NOCASE, pa.store_id"
    for r in conn.execute(sql + order, params).fetchall():
        out.setdefault(r["product_id"], []).append({
            "store_id": r["store_id"],
            "name": r["name"],
            "available": bool(r["available"]),
            "price": r["price"],
            "price_currency": r["price_currency"],
            "checked_at": r["checked_at"],
            "source": r["source"],
        })
    return out


def _ai_configured(conn) -> bool:
    """True only when the active AI provider has the credential it needs — no
    point spawning an auto-placement thread that will just fail (and it keeps
    the test suite, which has no keys, from making live calls)."""
    from ai_client import _get_ai_config
    cfg = _get_ai_config(conn)
    provider = cfg.get("provider", "gemini")
    needed = {"gemini": "gemini_api_key", "claude": "claude_api_key",
              "ollama": "ollama_url"}.get(provider, "gemini_api_key")
    return bool(cfg.get(needed))


def _autoplace_enabled(conn) -> bool:
    """Whether freshly created, ungrouped products should be AI-categorised at
    create time. Requires the `autoplace_on_create` config key to be on (default
    on) AND a usable AI provider configured."""
    row = conn.execute(
        "SELECT value FROM config WHERE key = 'autoplace_on_create'"
    ).fetchone()
    toggle_on = row is None or str(row["value"]).strip().lower() not in (
        "0", "false", "no", "off")
    return toggle_on and _ai_configured(conn)


def _autoplace_product(product_id: int) -> None:
    """Background, best-effort: place a just-created product in the tree via
    the linker; if the linker finds nothing, fall back to the AI optimizer
    (which also assigns a product group). Opens its OWN connection."""
    import linker

    if not linker.claim(product_id):
        return
    try:
        from main import DB_PATH
        from database import get_db
        from optimizer import run_optimize
        conn = get_db(DB_PATH)
        try:
            res = linker.link_products(conn, [product_id])
            if not res["linked"] and not res["proposed"]:
                run_optimize(conn, product_ids=[product_id], link=False)
        finally:
            conn.close()
    except Exception as exc:
        log.warning("Auto-placement for product %d failed: %s", product_id, exc)
    finally:
        linker.release(product_id)


# ── List / Get ─────────────────────────────────────────────────────────────

@router.get("/products", response_model=list[Product])
def list_products(
    parent_id: int | None = Query(None),
    group_id: int | None = Query(None),
    active_only: bool = Query(True),
):
    conn = _get_db()
    clauses = []
    params: list = []
    if active_only:
        clauses.append("p.active = 1")
    if parent_id is not None:
        clauses.append("p.parent_id = ?")
        params.append(parent_id)
    if group_id is not None:
        clauses.append("p.product_group_id = ?")
        params.append(group_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"""
        WITH RECURSIVE closure(ancestor, descendant) AS (
            SELECT parent_id, id FROM products WHERE parent_id IS NOT NULL
            UNION
            SELECT c.ancestor, p.id
            FROM products p JOIN closure c ON p.parent_id = c.descendant
        ),
        child_totals AS (
            SELECT c.ancestor AS pid,
                   COALESCE(SUM(cs.amount), 0) AS amount,
                   COALESCE(SUM(cs.amount_opened), 0) AS opened
            FROM closure c
            JOIN stock cs ON cs.product_id = c.descendant
            GROUP BY c.ancestor
        )
        SELECT p.*,
               COALESCE(SUM(s.amount), 0) AS stock_amount,
               COALESCE(SUM(s.amount_opened), 0) AS stock_opened,
               COALESCE(ct.amount, 0) AS children_stock_amount,
               COALESCE(ct.opened, 0) AS children_stock_opened
        FROM products p
        LEFT JOIN stock s ON s.product_id = p.id
        LEFT JOIN child_totals ct ON ct.pid = p.id
        {where}
        GROUP BY p.id
        ORDER BY p.name
        """,
        params,
    ).fetchall()
    stores_map = _stores_by_product(conn)
    cat = Catalog(conn)
    return [
        {**row, **_subtree_fields(cat, row["id"]), "stores": stores_map.get(row["id"], [])}
        for row in rows
    ]


def _subtree_fields(cat: Catalog, product_id: int) -> dict:
    """Converted whole-subtree stock for products that have descendants."""
    if not cat.children(product_id):
        return {}
    h = own_unit_have(cat, product_id)
    return {
        "subtree_amount": round(h.amount, 3),
        "subtree_unit_id": (cat.product(product_id) or {}).get("unit_id"),
        "subtree_approx": h.approx or h.unknown_lots > 0,
    }


@router.get("/products/lookup", response_model=ProductLookup)
def lookup_product(name: str = Query(..., min_length=1)):
    """The product a spoken or typed name means (voice adds, spec §5.1): a
    concept by alias or exact name first, so "maito" is the concept and the
    shopping list shows the usual SKU as its buy hint; else the one active
    product with exactly this name. Names are compared normalized (case,
    accents, punctuation). No match: product is null."""
    from concepts import find_concept, norm

    conn = _get_db()
    pid = find_concept(conn, name)
    if pid is None:
        key = norm(name)
        same = [r["id"] for r in conn.execute("SELECT id, name FROM products WHERE active = 1").fetchall()
                if norm(r["name"]) == key]
        pid = same[0] if len(same) == 1 else None
    product = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone() if pid else None
    return {"product": product}


@router.get("/products/{product_id}", response_model=ProductDetail)
def get_product(product_id: int):
    conn = _get_db()
    row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Product {product_id} not found")

    children = conn.execute(
        "SELECT * FROM products WHERE parent_id = ?", (product_id,)
    ).fetchall()
    barcodes = conn.execute(
        "SELECT * FROM barcodes WHERE product_id = ?", (product_id,)
    ).fetchall()
    stock_row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) as total, COALESCE(SUM(amount_opened), 0) as opened "
        "FROM stock WHERE product_id = ?",
        (product_id,),
    ).fetchone()
    desc = tree.descendant_ids(conn, product_id)
    if desc:
        qmarks = ",".join("?" * len(desc))
        child_stock_row = conn.execute(
            f"SELECT COALESCE(SUM(amount), 0) AS total, COALESCE(SUM(amount_opened), 0) AS opened "
            f"FROM stock WHERE product_id IN ({qmarks})",
            desc,
        ).fetchone()
    else:
        child_stock_row = {"total": 0, "opened": 0}

    return {
        **row,
        "children": children,
        "barcodes": barcodes,
        "stock_amount": stock_row["total"],
        "stock_opened": stock_row["opened"],
        "children_stock_amount": child_stock_row["total"],
        "children_stock_opened": child_stock_row["opened"],
        **_subtree_fields(Catalog(conn), product_id),
        "stores": _stores_by_product(conn, [product_id]).get(product_id, []),
    }


@router.get("/products/by-barcode/{barcode}", response_model=ProductDetail)
def get_product_by_barcode(barcode: str):
    conn = _get_db()
    bc = conn.execute("SELECT * FROM barcodes WHERE barcode = ?", (barcode,)).fetchone()
    if not bc:
        raise HTTPException(404, f"Barcode '{barcode}' not found")
    detail = get_product(bc["product_id"])
    return {**detail, "matched_pack_size": float(bc["pack_size"] or 1)}


# ── Create ─────────────────────────────────────────────────────────────────

@router.post("/products", response_model=Product, status_code=201)
def create_product(body: ProductCreate):
    conn = _get_db()
    _validate_unit(conn, body.unit_id, "Unit")
    _validate_unit(conn, body.content_unit_id, "Content unit")
    kind = _infer_kind(body.kind)
    # The parent promotion and the INSERT commit together or not at all: the
    # connection is shared, so a half-done write would leak into the next
    # request's commit. The catalog lock keeps it from interleaving with an
    # ingredient resolve, a link or a merge.
    CATALOG_LOCK.acquire()
    try:
        tree.prepare_parent(conn, None, body.parent_id)
        facts = {f: getattr(body, f) for f in _FACT_FIELDS if getattr(body, f) is not None}
        cur = conn.execute(
            """INSERT INTO products (name, description, parent_id, location_id,
               product_group_id, unit_id, default_best_before_days, min_stock_amount,
               picture_filename, active, unit_price, unit_price_currency, pack_count, staple,
               kind, content_amount, content_unit_id, density_g_ml, piece_weight_g, facts_meta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                body.name, body.description, body.parent_id, body.location_id,
                body.product_group_id, body.unit_id, body.default_best_before_days,
                body.min_stock_amount, body.picture_filename, int(body.active),
                body.unit_price, body.unit_price_currency or "EUR", body.pack_count, int(body.staple),
                kind, body.content_amount, body.content_unit_id, body.density_g_ml,
                body.piece_weight_g, _manual_facts_meta(None, facts),
            ),
        )
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(400, str(exc))
    except Exception:
        conn.rollback()
        raise
    finally:
        CATALOG_LOCK.release()
    new = conn.execute("SELECT * FROM products WHERE id = ?", (cur.lastrowid,)).fetchone()
    try:
        pack_size.ensure_pack_facts(conn, new["id"])
        new = conn.execute("SELECT * FROM products WHERE id = ?", (new["id"],)).fetchone()
    except Exception as exc:
        log.warning("Pack-size parse for product %d failed: %s", new["id"], exc)
    # New brands scanned during shopping arrive ungrouped; place them under the
    # right type-parent so the catalog (and future exact matching) stays tidy.
    # Best-effort, off the request path — never blocks or fails the create.
    if new["parent_id"] is None and new["product_group_id"] is None and _autoplace_enabled(conn):
        threading.Thread(
            target=_autoplace_product, args=(new["id"],), daemon=True
        ).start()
    return new


# ── Update ─────────────────────────────────────────────────────────────────

@router.put("/products/{product_id}", response_model=Product)
def update_product(product_id: int, body: ProductUpdate):
    conn = _get_db()
    existing = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not existing:
        raise HTTPException(404, f"Product {product_id} not found")

    updates = {}
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "active":
            value = int(value)
        if field == "staple":
            value = int(value)
        updates[field] = value

    if not updates:
        return existing

    if "unit_id" in updates:
        _validate_unit(conn, updates["unit_id"], "Unit")
    if "content_unit_id" in updates:
        _validate_unit(conn, updates["content_unit_id"], "Content unit")

    if "kind" in updates:
        if updates["kind"] not in KINDS:
            raise HTTPException(400, f"kind must be one of {KINDS}")
        has_children = conn.execute(
            "SELECT 1 FROM products WHERE parent_id = ? LIMIT 1", (product_id,)
        ).fetchone()
        if updates["kind"] == "sku" and has_children:
            raise HTTPException(400, "A product with sub-products must stay a concept")

    # The parent promotion and the UPDATE commit together or not at all (the
    # connection is shared between requests), under the catalog lock.
    CATALOG_LOCK.acquire()
    try:
        if "parent_id" in updates:
            tree.prepare_parent(conn, product_id, updates["parent_id"])

        changed_facts = {
            f: updates[f] for f in _FACT_FIELDS
            if f in updates and updates[f] != existing[f]
        }
        if changed_facts:
            updates["facts_meta"] = _manual_facts_meta(existing["facts_meta"], changed_facts)

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [product_id]
        conn.execute(
            f"UPDATE products SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            params,
        )
        if "name" in updates and updates["name"] != existing["name"]:
            # Shopping rows keep the name they were added under (HA todo, print).
            conn.execute("UPDATE shopping_list SET ha_item_name = ? WHERE product_id = ?",
                         (updates["name"], product_id))
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(400, str(exc))
    except Exception:
        conn.rollback()
        raise
    finally:
        CATALOG_LOCK.release()
    if "min_stock_amount" in updates or "active" in updates:
        from routers.shopping import sync_auto_shopping
        sync_auto_shopping(conn)
    return conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()


# ── Delete ─────────────────────────────────────────────────────────────────

@router.delete("/products/{product_id}", status_code=204)
def delete_product(product_id: int, force: bool = Query(False)):
    """Delete a product. Refused (409) while recipe ingredients or bundles use
    it — merge it into another product instead. force=true deletes anyway and
    takes the referencing recipe ingredients and shopping rows with it."""
    conn = _get_db()
    existing = conn.execute("SELECT id, name FROM products WHERE id = ?", (product_id,)).fetchone()
    if not existing:
        raise HTTPException(404, f"Product {product_id} not found")
    if not force:
        refs = conn.execute(
            "SELECT (SELECT COUNT(*) FROM recipe_ingredients WHERE product_id = ?) AS ingredients, "
            "       (SELECT COUNT(*) FROM bundle_items WHERE product_id = ?) AS bundle_items",
            (product_id, product_id),
        ).fetchone()
        if refs["ingredients"] or refs["bundle_items"]:
            raise HTTPException(
                409,
                f"'{existing['name']}' is used by {refs['ingredients']} recipe ingredient(s) "
                f"and {refs['bundle_items']} bundle item(s). Merge it into another product "
                "instead, or delete with force=true.",
            )
    # Clean up references that lack ON DELETE CASCADE
    conn.execute("DELETE FROM recipe_ingredients WHERE product_id = ?", (product_id,))
    conn.execute("DELETE FROM shopping_list WHERE product_id = ?", (product_id,))
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()


@router.post("/products/{product_id}/merge", response_model=Product)
def merge_product(product_id: int, body: ProductMerge):
    """Merge this product into body.target_id: every recipe ingredient,
    shopping row, sub-product, lot, barcode and history row moves over, then
    this product is deleted."""
    from merge import merge_products

    conn = _get_db()
    try:
        merge_products(conn, product_id, body.target_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return conn.execute("SELECT * FROM products WHERE id = ?", (body.target_id,)).fetchone()


# ── Aliases (spelling -> concept) ─────────────────────────────────────────

@router.get("/products/{product_id}/aliases")
def list_aliases(product_id: int):
    from concepts import aliases_of

    conn = _get_db()
    if not conn.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone():
        raise HTTPException(404, f"Product {product_id} not found")
    return aliases_of(conn, product_id)


@router.post("/products/{product_id}/aliases", status_code=201)
def add_alias_route(product_id: int, body: AliasCreate):
    """Bind a spelling to this concept. A manual alias takes the spelling over
    from whichever concept had it, so a wrong automatic binding can be fixed."""
    from concepts import norm
    from locks import CATALOG_LOCK

    conn = _get_db()
    prod = conn.execute("SELECT kind FROM products WHERE id = ?", (product_id,)).fetchone()
    if not prod:
        raise HTTPException(404, f"Product {product_id} not found")
    if prod["kind"] != "concept":
        raise HTTPException(400, "Aliases belong to concepts")
    key = norm(body.alias)
    if not key:
        raise HTTPException(400, "Alias is empty")
    other = next((r for r in conn.execute(
        "SELECT id, name FROM products WHERE kind = 'concept' AND id != ?", (product_id,)
    ).fetchall() if norm(r["name"]) == key), None)
    if other is not None:
        # Such an alias would make the next reconcile merge the two concepts.
        raise HTTPException(
            409, f"'{body.alias.strip()}' is the name of the concept '{other['name']}'. "
                 "Merge the two concepts instead.")
    with CATALOG_LOCK:
        conn.execute(
            "INSERT OR REPLACE INTO product_aliases (alias_norm, product_id, source) "
            "VALUES (?, ?, 'manual')",
            (key, product_id),
        )
        conn.commit()
    return {"alias": key, "source": "manual"}


@router.delete("/products/{product_id}/aliases/{alias}", status_code=204)
def delete_alias(product_id: int, alias: str):
    from concepts import norm

    conn = _get_db()
    cur = conn.execute(
        "DELETE FROM product_aliases WHERE alias_norm = ? AND product_id = ?",
        (norm(alias), product_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(404, "Alias not found")
    conn.commit()
