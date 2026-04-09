"""Grocy migration endpoint — one-time import from Grocy REST API."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException

from models import GrocyMigrationRequest, MigrationResult

router = APIRouter(tags=["migration"])
log = logging.getLogger(__name__)


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


def _grocy_get_file(base_url: str, api_key: str, group: str, filename: str) -> bytes | None:
    """Download a file from Grocy."""
    import base64
    headers = {"GROCY-API-KEY": api_key}
    encoded = base64.b64encode(filename.encode()).decode()
    url = f"{base_url.rstrip('/')}/api/files/{group}/{encoded}"
    try:
        resp = httpx.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.content
    except Exception:
        pass
    return None


@router.post("/migrate/grocy", response_model=MigrationResult)
def migrate_from_grocy(body: GrocyMigrationRequest):
    """Import all data from a Grocy instance."""
    conn = _get_db()
    result = MigrationResult()
    grocy_url = body.grocy_url.rstrip("/")
    api_key = body.api_key

    # ID maps: grocy_id → storage_id
    unit_map: dict[int, int] = {}
    location_map: dict[int, int] = {}
    group_map: dict[int, int] = {}
    product_map: dict[int, int] = {}
    recipe_map: dict[int, int] = {}

    try:
        # ── 1. Units ──────────────────────────────────────────────────
        log.info("Migrating units...")
        grocy_units = _grocy_get(grocy_url, api_key, "objects/quantity_units")
        existing_units = {
            r["abbreviation"]: r["id"]
            for r in conn.execute("SELECT id, abbreviation FROM units").fetchall()
        }

        for u in grocy_units:
            name = u.get("name", "")
            # Use name as abbreviation if it looks like one (short), else use first few chars
            abbrev = name.lower().strip()
            if len(abbrev) > 5:
                abbrev = abbrev[:3]

            if abbrev in existing_units:
                unit_map[u["id"]] = existing_units[abbrev]
            else:
                cur = conn.execute(
                    "INSERT INTO units (name, abbreviation, name_plural) VALUES (?, ?, ?)",
                    (name, abbrev, u.get("name_plural", "")),
                )
                unit_map[u["id"]] = cur.lastrowid
                existing_units[abbrev] = cur.lastrowid
                result.units += 1

        # ── 2. Locations ──────────────────────────────────────────────
        log.info("Migrating locations...")
        grocy_locations = _grocy_get(grocy_url, api_key, "objects/locations")
        existing_locations = {
            r["name"]: r["id"]
            for r in conn.execute("SELECT id, name FROM locations").fetchall()
        }

        for loc in grocy_locations:
            name = loc.get("name", "")
            if name in existing_locations:
                location_map[loc["id"]] = existing_locations[name]
            else:
                cur = conn.execute(
                    "INSERT INTO locations (name, description) VALUES (?, ?)",
                    (name, loc.get("description", "")),
                )
                location_map[loc["id"]] = cur.lastrowid
                existing_locations[name] = cur.lastrowid
                result.locations += 1

        # ── 3. Product groups ─────────────────────────────────────────
        log.info("Migrating product groups...")
        grocy_groups = _grocy_get(grocy_url, api_key, "objects/product_groups")
        existing_groups = {
            r["name"]: r["id"]
            for r in conn.execute("SELECT id, name FROM product_groups").fetchall()
        }

        for g in grocy_groups:
            name = g.get("name", "")
            if name in existing_groups:
                group_map[g["id"]] = existing_groups[name]
            else:
                cur = conn.execute(
                    "INSERT INTO product_groups (name, description) VALUES (?, ?)",
                    (name, g.get("description", "")),
                )
                group_map[g["id"]] = cur.lastrowid
                existing_groups[name] = cur.lastrowid
                result.product_groups += 1

        # ── 4. Products ───────────────────────────────────────────────
        log.info("Migrating products...")
        grocy_products = _grocy_get(grocy_url, api_key, "objects/products")

        # Default unit fallback: kpl
        kpl_unit = conn.execute(
            "SELECT id FROM units WHERE abbreviation = 'kpl'"
        ).fetchone()
        default_unit_id = kpl_unit["id"] if kpl_unit else 1

        # First pass: create all products without parent_id
        for p in grocy_products:
            unit_id = unit_map.get(p.get("qu_id_stock"), default_unit_id)
            location_id = location_map.get(p.get("location_id"))
            group_id = group_map.get(p.get("product_group_id"))

            cur = conn.execute(
                """INSERT INTO products (name, description, location_id, product_group_id,
                   unit_id, default_best_before_days, min_stock_amount, picture_filename, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    p.get("name", ""),
                    p.get("description", ""),
                    location_id,
                    group_id,
                    unit_id,
                    p.get("default_best_before_days", 60),
                    p.get("min_stock_amount", 0),
                    p.get("picture_file_name"),
                    1 if p.get("active", True) else 0,
                ),
            )
            product_map[p["id"]] = cur.lastrowid
            result.products += 1

        # Second pass: set parent_id
        for p in grocy_products:
            parent_grocy_id = p.get("parent_product_id")
            if parent_grocy_id and parent_grocy_id in product_map:
                storage_id = product_map[p["id"]]
                parent_storage_id = product_map[parent_grocy_id]
                conn.execute(
                    "UPDATE products SET parent_id = ? WHERE id = ?",
                    (parent_storage_id, storage_id),
                )

        # ── 5. Barcodes ───────────────────────────────────────────────
        log.info("Migrating barcodes...")
        grocy_barcodes = _grocy_get(grocy_url, api_key, "objects/product_barcodes")
        for bc in grocy_barcodes:
            prod_id = product_map.get(bc.get("product_id"))
            if not prod_id:
                continue
            barcode = bc.get("barcode", "")
            if not barcode:
                continue
            try:
                conn.execute(
                    "INSERT INTO barcodes (product_id, barcode, pack_size, pack_unit_id) VALUES (?, ?, ?, ?)",
                    (
                        prod_id,
                        barcode,
                        bc.get("amount", 1) or 1,
                        unit_map.get(bc.get("qu_id")) if bc.get("qu_id") else None,
                    ),
                )
                result.barcodes += 1
            except Exception as e:
                result.errors.append(f"Barcode '{barcode}': {e}")

        # ── 6. Unit conversions ───────────────────────────────────────
        log.info("Migrating conversions...")
        grocy_convs = _grocy_get(grocy_url, api_key, "objects/quantity_unit_conversions")
        for c in grocy_convs:
            from_id = unit_map.get(c.get("from_qu_id"))
            to_id = unit_map.get(c.get("to_qu_id"))
            if not from_id or not to_id:
                continue
            prod_id = product_map.get(c.get("product_id")) if c.get("product_id") else None
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO unit_conversions (from_unit_id, to_unit_id, factor, product_id) "
                    "VALUES (?, ?, ?, ?)",
                    (from_id, to_id, c.get("factor", 1), prod_id),
                )
                result.conversions += 1
            except Exception as e:
                result.errors.append(f"Conversion: {e}")

        # ── 7. Stock ──────────────────────────────────────────────────
        log.info("Migrating stock...")
        grocy_stock = _grocy_get(grocy_url, api_key, "stock")
        for s in grocy_stock:
            prod_id = product_map.get(s.get("product_id"))
            if not prod_id:
                continue
            product = conn.execute("SELECT * FROM products WHERE id = ?", (prod_id,)).fetchone()
            location_id = product["location_id"] if product else None
            if not location_id:
                loc = conn.execute("SELECT id FROM locations LIMIT 1").fetchone()
                location_id = loc["id"] if loc else None
            if not location_id:
                continue

            conn.execute(
                """INSERT INTO stock (product_id, location_id, amount, amount_opened, unit_id, best_before_date)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    prod_id,
                    location_id,
                    s.get("amount", 0),
                    s.get("amount_opened", 0),
                    product["unit_id"],
                    s.get("best_before_date"),
                ),
            )
            result.stock_entries += 1

        # ── 8. Recipes ────────────────────────────────────────────────
        log.info("Migrating recipes...")
        grocy_recipes = _grocy_get(grocy_url, api_key, "objects/recipes")
        for r in grocy_recipes:
            cur = conn.execute(
                """INSERT INTO recipes (name, description, servings, picture_filename)
                   VALUES (?, ?, ?, ?)""",
                (
                    r.get("name", ""),
                    r.get("description", ""),
                    r.get("base_servings", 4),
                    r.get("picture_file_name"),
                ),
            )
            recipe_map[r["id"]] = cur.lastrowid
            result.recipes += 1

        # ── 9. Recipe ingredients ─────────────────────────────────────
        log.info("Migrating recipe ingredients...")
        grocy_positions = _grocy_get(grocy_url, api_key, "objects/recipes_pos")
        for pos in grocy_positions:
            rec_id = recipe_map.get(pos.get("recipe_id"))
            prod_id = product_map.get(pos.get("product_id"))
            if not rec_id or not prod_id:
                continue
            unit_id = unit_map.get(pos.get("qu_id"), default_unit_id)
            conn.execute(
                """INSERT INTO recipe_ingredients (recipe_id, product_id, amount, unit_id, note)
                   VALUES (?, ?, ?, ?, ?)""",
                (rec_id, prod_id, pos.get("amount", 1), unit_id, pos.get("note", "")),
            )
            result.recipe_ingredients += 1

        # ── 10. Product images ────────────────────────────────────────
        log.info("Migrating product images...")
        from routers.files import PRODUCT_IMG_DIR, RECIPE_IMG_DIR
        PRODUCT_IMG_DIR.mkdir(parents=True, exist_ok=True)
        RECIPE_IMG_DIR.mkdir(parents=True, exist_ok=True)

        for p in grocy_products:
            pic = p.get("picture_file_name")
            if not pic:
                continue
            data = _grocy_get_file(grocy_url, api_key, "productpictures", pic)
            if data:
                (PRODUCT_IMG_DIR / pic).write_bytes(data)

        for r in grocy_recipes:
            pic = r.get("picture_file_name")
            if not pic:
                continue
            data = _grocy_get_file(grocy_url, api_key, "recipepictures", pic)
            if data:
                (RECIPE_IMG_DIR / pic).write_bytes(data)

        conn.commit()
        log.info(
            "Migration complete: %d products, %d stock, %d barcodes, %d recipes.",
            result.products, result.stock_entries, result.barcodes, result.recipes,
        )

    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Grocy API error: {e.response.status_code} {e.response.text}")
    except httpx.ConnectError:
        raise HTTPException(502, f"Cannot connect to Grocy at {grocy_url}")
    except Exception as e:
        log.exception("Migration failed: %s", e)
        result.errors.append(str(e))

    return result
