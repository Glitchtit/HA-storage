"""Full AI Optimize pipeline for the Storage app.

Two-phase approach:
  Phase 1 — establish categories (product_group) and parent products in
             batches of 100, with progressive context chaining.
  Phase 2 — assign locations, best-before days, and pack normalisation
             in batches of 100, using Phase 1 structure.

Reads and writes directly to SQLite — no HTTP round-trips.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any, Callable

from ai_client import call_ai_json, _get_ai_config, get_batch_size

logger = logging.getLogger(__name__)

_UNIT_ALIASES: dict[str, str] = {
    "g": "g", "gr": "g", "gram": "g", "gramma": "g", "grammaa": "g",
    "kg": "kg", "kilo": "kg", "kilogramma": "kg", "kilogrammaa": "kg",
    "ml": "ml", "millilitra": "ml", "millilitraa": "ml",
    "dl": "dl", "desilitra": "dl", "desilitraa": "dl",
    "l": "l", "litra": "l", "litraa": "l",
    "tl": "tl", "teelusikka": "tl", "teelusikkaa": "tl",
    "rkl": "rkl", "ruokalusikka": "rkl", "ruokalusikkaa": "rkl",
    "rs": "rs", "ripaus": "rs", "ripausta": "rs",
    "kpl": "kpl", "kappale": "kpl", "kappaletta": "kpl",
    "pcs": "kpl", "piece": "kpl", "pieces": "kpl", "st": "kpl",
    "stück": "kpl", "pack": "kpl",
}


def _canonical_unit(name: str) -> str | None:
    return _UNIT_ALIASES.get(name.lower().strip())


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_products(conn: sqlite3.Connection, product_ids: list[int] | None = None) -> list[dict]:
    if product_ids:
        placeholders = ",".join("?" * len(product_ids))
        rows = conn.execute(
            f"SELECT id, name, parent_id, location_id, product_group_id, unit_id, "
            f"default_best_before_days, min_stock_amount, active FROM products "
            f"WHERE id IN ({placeholders})",
            product_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, parent_id, location_id, product_group_id, unit_id, "
            "default_best_before_days, min_stock_amount, active FROM products"
        ).fetchall()
    return [dict(r) for r in rows]


def _load_locations(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT id, name FROM locations").fetchall()
    return [dict(r) for r in rows]


def _load_product_groups(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT id, name FROM product_groups").fetchall()
    return [dict(r) for r in rows]


def _load_units(conn: sqlite3.Connection) -> dict[str, int]:
    """Return {abbreviation: id} for all units."""
    unit_abbrev_to_id: dict[str, int] = {}
    rows = conn.execute("SELECT id, name, description FROM units").fetchall()
    for r in rows:
        desc = (r["description"] or "").lower().strip()
        if desc:
            unit_abbrev_to_id[desc] = r["id"]
        canonical = _canonical_unit((r["name"] or "").lower().strip())
        if canonical and canonical not in unit_abbrev_to_id:
            unit_abbrev_to_id[canonical] = r["id"]
    return unit_abbrev_to_id


def _ensure_product_group(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM product_groups WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO product_groups (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _ensure_parent_product(
    conn: sqlite3.Connection,
    name: str,
    name_to_product: dict[str, dict],
    unit_id: int,
    group_master_id: int | None,
) -> int:
    """Return the ID of a parent product with *name*, creating it if needed."""
    existing = name_to_product.get(name)
    if existing:
        pid = int(existing["id"])
        # Ensure it's inactive (parent placeholder) if it has no min_stock_amount
        if existing.get("active") and float(existing.get("min_stock_amount") or 0) == 0:
            conn.execute("UPDATE products SET active = 0 WHERE id = ?", (pid,))
            conn.commit()
        return pid

    # Create new parent placeholder
    now = int(time.time())
    pg_id = group_master_id
    cur = conn.execute(
        "INSERT INTO products (name, active, unit_id, product_group_id, "
        "default_best_before_days, min_stock_amount, created_at, updated_at) "
        "VALUES (?, 0, ?, ?, 60, 0, datetime('now'), datetime('now'))",
        (name, unit_id, pg_id),
    )
    conn.commit()
    new_id = cur.lastrowid
    new_product = {"id": new_id, "name": name, "active": False, "min_stock_amount": 0}
    name_to_product[name] = new_product
    return new_id  # type: ignore[return-value]


def _strip_parents(
    conn: sqlite3.Connection,
    products: list[dict],
    log: Callable[..., None],
    group_master_id: int | None = None,
) -> set[int]:
    """Remove all parent_id links, deactivate parent placeholders, and delete
    group-master products before the AI runs.

    Detection uses two criteria (neither requires active=False):
    1. Any product referenced as parent_id by another product (has children).
    2. Any product whose product_group_id equals group_master_id (optimizer-created).

    Group-master products are deleted UNLESS they are referenced by a recipe
    ingredient — those are kept (but deactivated) so recipe links are preserved.
    All identified parents are marked active=0 to exclude them from the AI feed.

    Returns the full set of identified parent IDs so the caller can filter them
    out of the working product list.
    """
    # 1. Products referenced as a parent by any other product
    has_children: set[int] = set()
    for p in products:
        if p.get("parent_id"):
            has_children.add(int(p["parent_id"]))

    # 2. Optimizer-created group-master products (safe to delete unless in a recipe)
    group_master_prods: set[int] = set()
    if group_master_id is not None:
        for p in products:
            if p.get("product_group_id") == group_master_id:
                group_master_prods.add(int(p["id"]))

    # Never delete group-master products that are referenced by a recipe ingredient
    recipe_linked: set[int] = set()
    if group_master_prods:
        placeholders_gm = ",".join("?" * len(group_master_prods))
        rows = conn.execute(
            f"SELECT DISTINCT product_id FROM recipe_ingredients WHERE product_id IN ({placeholders_gm})",
            list(group_master_prods),
        ).fetchall()
        recipe_linked = {int(r["product_id"]) for r in rows}
        if recipe_linked:
            log(
                "Keeping %d group-master product(s) that are referenced by recipes.",
                len(recipe_linked),
            )

    deletable_group_masters = group_master_prods - recipe_linked

    all_parent_ids = has_children | group_master_prods

    # Deactivate all identified parents (catches ones still marked active)
    if all_parent_ids:
        placeholders = ",".join("?" * len(all_parent_ids))
        conn.execute(
            f"UPDATE products SET active = 0 WHERE id IN ({placeholders})",
            list(all_parent_ids),
        )

    # Strip parent_id links from children before deleting parents
    stripped = 0
    for p in products:
        if p.get("parent_id"):
            conn.execute("UPDATE products SET parent_id = NULL WHERE id = ?", (p["id"],))
            p["parent_id"] = None
            stripped += 1

    # Delete group-master products that are not referenced by any recipe
    deleted = 0
    for pid in deletable_group_masters:
        conn.execute("DELETE FROM products WHERE id = ?", (pid,))
        deleted += 1

    conn.commit()

    log(
        "Clean-slate: stripped parents from %d product(s), "
        "deactivated %d parent placeholder(s), deleted %d group-master product(s) "
        "(%d preserved — referenced by recipes).",
        stripped, len(all_parent_ids), deleted, len(recipe_linked),
    )
    return all_parent_ids


# ---------------------------------------------------------------------------
# Phase 1: structure (categories + parent products)
# ---------------------------------------------------------------------------

def _phase1_structure(
    conn: sqlite3.Connection,
    products: list[dict],
    cfg: dict[str, str],
    log: Callable[..., None],
    *,
    product_ids: list[int] | None,
    name_to_product: dict[str, dict],
    group_master_id: int | None,
    initial_parent_names: list[str] | None = None,
    initial_category_names: list[str] | None = None,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Determine categories and parent products for all *products*.

    Returns:
        product_category:           product_id (int) -> category name
        product_group_name:         product_id (int) -> parent product name
        category_name_to_group_id:  category name -> product_group id
        parent_name_to_id:          parent name -> product id
    """
    product_category: dict[int, str] = {}
    product_group_name: dict[int, str] = {}
    category_name_to_group_id: dict[str, int] = {}
    parent_name_to_id: dict[str, int] = {}
    established_parent_names = list(initial_parent_names or [])
    established_category_names = list(initial_category_names or [])

    # Pre-populate category map from existing product groups
    for g in _load_product_groups(conn):
        if g["name"] and g["name"] != "Group master":
            category_name_to_group_id[g["name"]] = g["id"]

    # Default unit fallback (kpl)
    kpl_row = conn.execute("SELECT id FROM units WHERE name = 'kpl'").fetchone()
    default_unit_id = kpl_row["id"] if kpl_row else 1

    for batch_idx, i in enumerate(range(0, len(products), batch_size)):
        batch = products[i: i + batch_size]
        product_lines = "\n".join(f"  {p['id']}: {p.get('name', p['id'])}" for p in batch)

        parents_section = ""
        if established_parent_names:
            parents_str = ", ".join(f'"{n}"' for n in established_parent_names)
            parents_section = (
                "Established parent products (use these EXACT names when a "
                "product fits -- do NOT invent synonyms):\n"
                f"  {parents_str}\n\n"
            )

        cats_section = ""
        if established_category_names:
            cats_str = ", ".join(f'"{n}"' for n in established_category_names)
            cats_section = (
                "Established product categories (use these EXACT names when a "
                "product fits):\n"
                f"  {cats_str}\n\n"
            )

        prompt = (
            "You are a grocery database expert organising a Finnish home pantry.\n\n"
            f"{parents_section}"
            f"{cats_section}"
            "For each product below, return a JSON object mapping the product "
            "ID (as a string) to an object with:\n"
            '  "group_name": (string) a SPECIFIC Finnish parent product name '
            "that closely matches the product type. Be detailed: use separate "
            'parents for each distinct product (e.g. "Mustapippuri" for black '
            'pepper, "Maito" for milk, "Olut" for beer). If an established '
            "parent name fits, you MUST use that exact name. Null if the "
            "product is truly unique.\n"
            '  "category": (string) a product category in Finnish at a practical '
            "kitchen-shelf level. If an established category fits, you MUST use "
            "that exact name. Good examples: "
            "Mausteet for all spices, Maitotuotteet for dairy, Liha for meat, "
            "Juomat for drinks, Siivous for cleaning products, "
            "Hygienia for personal care/bathroom items. Null if truly "
            "one-of-a-kind.\n\n"
            "Return ONLY valid JSON, e.g.:\n"
            '{\"1\": {\"group_name\": \"Maito\", \"category\": \"Maitotuotteet\"}, '
            '  \"2\": {\"group_name\": null, \"category\": \"Siivous\"}}\n\n'
            "Products:\n"
            f"{product_lines}"
        )

        try:
            mapping = call_ai_json(prompt, conn, cfg=cfg, emit=log)
        except Exception as exc:
            log("AI structure batch %d failed: %s", batch_idx + 1, exc)
            continue

        if not isinstance(mapping, dict):
            log("AI structure batch %d: expected dict, got %s -- skipping.", batch_idx + 1, type(mapping).__name__)
            continue

        batch_parent_names: set[str] = set()
        batch_category_names: set[str] = set()

        for product in batch:
            pid = str(product["id"])
            info = mapping.get(pid)
            if not isinstance(info, dict):
                continue

            gn = info.get("group_name")
            if gn and isinstance(gn, str) and gn.strip():
                product_group_name[int(product["id"])] = gn.strip()
                batch_parent_names.add(gn.strip())

            cat = info.get("category")
            if cat and isinstance(cat, str) and cat.strip():
                product_category[int(product["id"])] = cat.strip()
                batch_category_names.add(cat.strip())

        # Resolve parent IDs + ensure product group rows exist
        for pname in batch_parent_names:
            if pname not in parent_name_to_id:
                pid_val = _ensure_parent_product(conn, pname, name_to_product, default_unit_id, group_master_id)
                parent_name_to_id[pname] = pid_val

        for cname in batch_category_names:
            if cname not in category_name_to_group_id:
                category_name_to_group_id[cname] = _ensure_product_group(conn, cname)

        new_parents = [n for n in batch_parent_names if n not in established_parent_names]
        new_cats = [n for n in batch_category_names if n not in established_category_names]
        established_parent_names.extend(new_parents)
        established_category_names.extend(new_cats)

        log(
            "Structure batch %d/%d: %d parent(s), %d categorie(s) assigned.",
            batch_idx + 1, -(-len(products) // batch_size),
            len(batch_parent_names), len(batch_category_names),
        )

    return {
        "product_category": product_category,
        "product_group_name": product_group_name,
        "category_name_to_group_id": category_name_to_group_id,
        "parent_name_to_id": parent_name_to_id,
    }


# ---------------------------------------------------------------------------
# Phase 2: details (location, best-before, pack)
# ---------------------------------------------------------------------------

def _phase2_details(
    conn: sqlite3.Connection,
    products: list[dict],
    p1: dict[str, Any],
    locations: list[dict],
    cfg: dict[str, str],
    log: Callable[..., None],
    *,
    name_to_product: dict[str, dict],
    batch_size: int = 100,
) -> int:
    product_category = p1["product_category"]
    product_group_name = p1["product_group_name"]
    category_name_to_group_id = p1["category_name_to_group_id"]
    parent_name_to_id = p1["parent_name_to_id"]

    location_names: dict[int, str] = {}
    location_lines = ""
    if locations:
        location_names = {int(loc["id"]): loc.get("name", str(loc["id"])) for loc in locations}
        location_lines = "\n".join(
            f"  {loc['id']}: {loc.get('name', loc['id'])}" for loc in locations
        )

    parent_context = ""
    if parent_name_to_id:
        parents_str = ", ".join(f'"{n}"' for n in sorted(parent_name_to_id))
        parent_context = (
            "Established parent products (for context only -- do not return "
            "these in your response):\n"
            f"  {parents_str}\n\n"
        )

    updated = 0

    for batch_idx, i in enumerate(range(0, len(products), batch_size)):
        batch = products[i: i + batch_size]

        def _product_line(p: dict) -> str:
            line = f"  {p['id']}: {p.get('name', p['id'])}"
            loc_id = p.get("location_id")
            if loc_id and location_names:
                loc_name = location_names.get(int(loc_id))
                if loc_name:
                    line += f" [current location: {loc_name}]"
            return line

        product_lines = "\n".join(_product_line(p) for p in batch)
        location_section = f"Available storage locations:\n{location_lines}\n\n" if location_lines else ""

        prompt = (
            "You are a grocery database expert organising a Finnish home pantry.\n\n"
            f"{location_section}"
            f"{parent_context}"
            "For each product below, return a JSON object mapping the product "
            "ID (as a string) to an object with:\n"
            '  "location_id": (integer) the most appropriate storage location ID, or null.\n'
            '  "best_before_days": (integer) estimated days until best-before for an unopened product.\n'
            '  "pack_size": (integer) ONLY when N identical, separately-sold consumer units are '
            "bundled under one barcode -- e.g. 6-pack of 0.33L soda cans, 4-pack of 200g yogurt "
            "cups, 12-pack of toilet rolls. Do NOT set when the number describes contents of one "
            "package (cotton swabs 200kpl, tea bags 100kpl, tablets 50kpl). Ask: would the store "
            "sell the individual item on its own? If no -> not a multi-pack. Return null when not "
            "a genuine multi-pack.\n"
            '  "pack_unit": (string) unit abbreviation for individual items in the pack (typically '
            '"kpl"), or null if not a pack.\n'
            '  "base_product_name": (string) Finnish name of the base single-unit product when '
            "pack_size > 1, else null.\n\n"
            "Guidelines:\n"
            "- Location: dairy/meat/fresh produce/drinks -> refrigerator; cleaning/laundry -> "
            "cleaning cabinet; hygiene/personal care/bathroom items (cotton swabs, toothbrush, "
            "toothpaste, shampoo, soap, deodorant, etc.) -> bathroom; dry goods/canned/packaged/"
            "eggs -> pantry/cupboard. If a product shows [current location], preserve it unless "
            "it is clearly incorrect.\n"
            "- Best-before: fresh milk ~7-14d; yogurt ~21d; butter ~90d; hard cheese ~180d; "
            "eggs ~28d; bread ~7d; canned ~730d; dry pasta/rice ~1095d; oil ~365d; frozen ~730d; "
            "cleaning/laundry ~1095d.\n"
            "- Pack: detect ONLY genuine multi-packs: 4-pack, 6x0.33l, monipakkaus, 6kpl Sprite. "
            "NOT when number describes package contents.\n\n"
            "Return ONLY valid JSON.\n\n"
            "Products:\n"
            f"{product_lines}"
        )

        try:
            mapping = call_ai_json(prompt, conn, cfg=cfg, emit=log)
        except Exception as exc:
            log("AI details batch %d failed: %s", batch_idx + 1, exc)
            continue

        if not isinstance(mapping, dict):
            log("AI details batch %d: expected dict, got %s -- skipping.", batch_idx + 1, type(mapping).__name__)
            continue

        for product in batch:
            pid = str(product["id"])
            product_id = int(product["id"])
            info = mapping.get(pid) or {}
            if not isinstance(info, dict):
                info = {}

            # --- Pack normalisation ---
            pack_size = info.get("pack_size")
            if pack_size:
                try:
                    pack_size = int(pack_size)
                except (TypeError, ValueError):
                    pack_size = None

            if pack_size and pack_size > 1:
                # Safety cap: pack_size > 24 almost certainly describes package contents
                # (e.g. "cotton swabs 200 kpl", "tea bags 100 kpl"), not a consumer
                # multi-pack. Cap at 24 to avoid inflating stock wildly if the AI errs.
                _MAX_PACK_MULTIPLIER = 24
                if pack_size > _MAX_PACK_MULTIPLIER:
                    log(
                        "  ~ pack_size=%d for '%s' exceeds cap (%d) — skipping stock multiply.",
                        pack_size, product.get("name"), _MAX_PACK_MULTIPLIER,
                    )
                    pack_size = 1  # disable multiplication for this product

                base_name = info.get("base_product_name")
                if base_name and isinstance(base_name, str):
                    base_name = base_name.strip()
                    base_product = name_to_product.get(base_name)
                    if base_product and int(base_product["id"]) != product_id:
                        base_pid = int(base_product["id"])
                        try:
                            # Move barcodes
                            bc_rows = conn.execute(
                                "SELECT id, barcode, pack_size FROM barcodes WHERE product_id = ?",
                                (product_id,),
                            ).fetchall()
                            for bc in bc_rows:
                                conn.execute(
                                    "UPDATE barcodes SET product_id = ?, pack_size = ? WHERE id = ?",
                                    (base_pid, pack_size, bc["id"]),
                                )
                                log("  -> Moved barcode '%s' from '%s' to '%s' (pack=%d).",
                                    bc["barcode"], product.get("name"), base_name, pack_size)
                            # Transfer stock to base product (amount × pack_size)
                            if pack_size > 1:
                                stock_rows = conn.execute(
                                    "SELECT * FROM stock WHERE product_id = ?", (product_id,)
                                ).fetchall()
                                for se in stock_rows:
                                    transferred = float(se["amount"]) * pack_size
                                    conn.execute(
                                        """INSERT INTO stock
                                           (product_id, location_id, amount, unit_id, best_before_date)
                                           VALUES (?, ?, ?, ?, ?)""",
                                        (
                                            base_pid,
                                            se["location_id"],
                                            transferred,
                                            se["unit_id"],
                                            se["best_before_date"],
                                        ),
                                    )
                                    log("  -> Transferred %.0f (%.0f×%d) stock to '%s'.",
                                        transferred, float(se["amount"]), pack_size, base_name)
                            # Delete multi-pack product (cascades stock deletion)
                            conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
                            conn.commit()
                            log("  -> Deleted multi-pack '%s' (merged into '%s').",
                                product.get("name"), base_name)
                            updated += 1
                            continue
                        except Exception as exc:
                            log("  ! Could not merge '%s' into '%s': %s",
                                product.get("name"), base_name, exc)
                            conn.rollback()
                    elif not base_product or int(base_product["id"]) == product_id:
                        # Rename in place and multiply stock × pack_size
                        if base_name and base_name != product.get("name"):
                            try:
                                conn.execute(
                                    "UPDATE products SET name = ? WHERE id = ?",
                                    (base_name, product_id),
                                )
                                # Multiply existing stock entries in-place
                                if pack_size > 1:
                                    stock_rows = conn.execute(
                                        "SELECT id, amount FROM stock WHERE product_id = ?",
                                        (product_id,),
                                    ).fetchall()
                                    for se in stock_rows:
                                        new_amount = float(se["amount"]) * pack_size
                                        conn.execute(
                                            "UPDATE stock SET amount = ? WHERE id = ?",
                                            (new_amount, se["id"]),
                                        )
                                        log("  -> Updated stock to %.0f (%.0f×%d) for '%s'.",
                                            new_amount, float(se["amount"]), pack_size, base_name)
                                conn.commit()
                                log("  -> Renamed '%s' -> '%s' (multi-pack normalised).",
                                    product.get("name"), base_name)
                                name_to_product[base_name] = {**product, "name": base_name}
                                name_to_product.pop(product.get("name", ""), None)
                                updated += 1
                            except Exception as exc:
                                log("  ! Could not rename '%s': %s", product.get("name"), exc)
                                conn.rollback()

            # --- Location ---
            loc_id = info.get("location_id")
            if loc_id is not None and locations:
                try:
                    conn.execute(
                        "UPDATE products SET location_id = ? WHERE id = ?",
                        (int(loc_id), product_id),
                    )
                    log("  -> Set location '%s' for '%s'.",
                        location_names.get(int(loc_id), loc_id), product.get("name"))
                    updated += 1
                except Exception as exc:
                    log("  ! Could not set location for '%s': %s", product.get("name"), exc)

            # --- Best-before ---
            days = info.get("best_before_days")
            if days is not None:
                try:
                    conn.execute(
                        "UPDATE products SET default_best_before_days = ? WHERE id = ?",
                        (int(days), product_id),
                    )
                    log("  -> Set %d best-before days for '%s'.", int(days), product.get("name"))
                    updated += 1
                except Exception as exc:
                    log("  ! Could not set best-before for '%s': %s", product.get("name"), exc)

            # --- Parent + category (from Phase 1) ---
            group_name = product_group_name.get(product_id)
            if not group_name:
                log("  ~ No group assigned for '%s' (AI returned null).", product.get("name"))
            else:
                parent_id = parent_name_to_id.get(group_name)
                if parent_id is None:
                    log("  ~ No parent product found for group '%s' (product: '%s').",
                        group_name, product.get("name"))
                elif parent_id == product_id:
                    log("  ~ Skipped self-parenting for '%s'.", product.get("name"))
            if group_name:
                parent_id = parent_name_to_id.get(group_name)
                if parent_id is not None and parent_id != product_id:
                    if float(product.get("min_stock_amount") or 0) > 0:
                        # Has tracked stock: assign group but skip parent
                        cat_name = product_category.get(product_id)
                        if cat_name:
                            cg_id = category_name_to_group_id.get(cat_name)
                            if cg_id is not None:
                                try:
                                    conn.execute(
                                        "UPDATE products SET product_group_id = ? WHERE id = ?",
                                        (cg_id, product_id),
                                    )
                                    updated += 1
                                except Exception:
                                    pass
                    else:
                        child_update_cols = ["parent_id = ?"]
                        child_update_vals: list[Any] = [parent_id]
                        cat_name = product_category.get(product_id)
                        if cat_name:
                            cg_id = category_name_to_group_id.get(cat_name)
                            if cg_id is not None:
                                child_update_cols.append("product_group_id = ?")
                                child_update_vals.append(cg_id)
                        child_update_vals.append(product_id)
                        try:
                            conn.execute(
                                f"UPDATE products SET {', '.join(child_update_cols)} WHERE id = ?",
                                child_update_vals,
                            )
                            log("  -> Grouped '%s' under '%s'.", product.get("name"), group_name)
                            updated += 1
                        except Exception as exc:
                            log("  ! Could not group '%s': %s", product.get("name"), exc)

        conn.commit()
        log(
            "Details batch %d/%d done.",
            batch_idx + 1, -(-len(products) // batch_size),
        )

    return updated


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_optimize(
    conn: sqlite3.Connection,
    *,
    product_ids: list[int] | None = None,
    emit: Callable[[str], None] | None = None,
    enforced_categories: list[str] | None = None,
) -> dict[str, int]:
    """Run the full AI optimize pipeline.

    *product_ids* = None  → full mode (all products, clean-slate parent strip)
    *product_ids* = [...]  → incremental mode (only those products)
    *emit*  → optional callable for streaming log messages to the frontend
    *enforced_categories* → category names the AI must prefer and that will
                            always be created as product_group rows

    Returns ``{"updated": int}``.
    """
    def log(msg: str, *args: Any) -> None:
        formatted = msg % args if args else msg
        logger.info(formatted)
        if emit:
            emit(formatted)

    cfg = _get_ai_config(conn)
    batch_size = get_batch_size(conn)
    log("Batch size: %d product(s) per AI call.", batch_size)

    # --- Load data ---
    all_products = _load_products(conn)
    locations = _load_locations(conn)
    name_to_product = {p.get("name", ""): p for p in all_products}

    # Ensure "Group master" product group exists
    group_master_id: int | None = None
    try:
        group_master_id = _ensure_product_group(conn, "Group master")
    except Exception as exc:
        log("Could not ensure 'Group master' group: %s", exc)

    # --- Determine working set ---
    old_parent_ids: set[int] = set()
    if product_ids is None:
        # Full mode: strip all parents, exclude old parent placeholders
        old_parent_ids = _strip_parents(conn, all_products, log, group_master_id)
        products = [p for p in all_products if int(p["id"]) not in old_parent_ids]
        # Purge deleted/deactivated parents from name lookup so _ensure_parent_product
        # creates fresh entries rather than referencing stale (possibly deleted) IDs.
        for p in all_products:
            if int(p["id"]) in old_parent_ids:
                name_to_product.pop(p.get("name", ""), None)
    else:
        allowed = set(product_ids)
        products = [p for p in all_products if int(p["id"]) in allowed]

    if not products:
        log("No products to optimize.")
        return {"updated": 0}

    log("Starting AI optimize for %d product(s)…", len(products))

    # --- Pre-load existing context (for incremental mode) ---
    initial_parent_names: list[str] | None = None
    initial_category_names: list[str] | None = None
    initial_parent_name_to_id: dict[str, int] | None = None
    initial_category_name_to_group_id: dict[str, int] | None = None

    if product_ids is not None:
        has_children: set[int] = set()
        for p in all_products:
            if p.get("parent_id"):
                has_children.add(int(p["parent_id"]))
        initial_parent_names = sorted({
            p.get("name", "") for p in all_products
            if int(p["id"]) in has_children and p.get("name")
        })
        initial_parent_name_to_id = {
            p.get("name", ""): int(p["id"])
            for p in all_products if int(p["id"]) in has_children
        }
        groups = _load_product_groups(conn)
        initial_category_names = sorted({
            g["name"] for g in groups
            if g.get("name") and g["name"] != "Group master"
        })
        initial_category_name_to_group_id = {
            g["name"]: g["id"] for g in groups if g.get("name")
        }
    else:
        # Full mode: seed Phase 1 with old parent names so the AI reuses them
        # (names collected from in-memory all_products AFTER strip deleted them from DB)
        initial_parent_names = sorted({
            p.get("name", "") for p in all_products
            if int(p["id"]) in old_parent_ids and p.get("name")
        })
        initial_parent_name_to_id = {}  # IDs are stale/deleted; new products created
        groups = _load_product_groups(conn)
        initial_category_names = sorted({
            g["name"] for g in groups
            if g.get("name") and g["name"] != "Group master"
        })
        initial_category_name_to_group_id = {
            g["name"]: g["id"] for g in groups if g.get("name")
        }
        if initial_parent_names:
            log("Seeding Phase 1 with %d existing parent name(s) for consistency.",
                len(initial_parent_names))

    # --- Phase 1: structure ---
    # Merge enforced_categories into initial list so AI strongly prefers them
    merged_category_names = list(initial_category_names or [])
    if enforced_categories:
        for cat in enforced_categories:
            if cat and cat not in merged_category_names:
                merged_category_names.append(cat)
        log("Enforcing %d user-defined category/categories.", len(enforced_categories))

    log("Phase 1: assigning categories and parent products…")
    p1 = _phase1_structure(
        conn, products, cfg, log,
        product_ids=product_ids,
        name_to_product=name_to_product,
        group_master_id=group_master_id,
        initial_parent_names=initial_parent_names,
        initial_category_names=merged_category_names or None,
        batch_size=batch_size,
    )

    if initial_parent_name_to_id:
        p1["parent_name_to_id"].update(initial_parent_name_to_id)
    if initial_category_name_to_group_id:
        for k, v in initial_category_name_to_group_id.items():
            p1["category_name_to_group_id"].setdefault(k, v)

    # Ensure every enforced category exists as a product_group row
    if enforced_categories:
        for cat in enforced_categories:
            if cat and cat not in p1["category_name_to_group_id"]:
                gid = _ensure_product_group(conn, cat)
                p1["category_name_to_group_id"][cat] = gid
                log("  Created enforced product group '%s'.", cat)
        conn.commit()

    # --- Phase 2: details ---
    log("Phase 2: assigning locations, best-before, and pack info…")
    updated = _phase2_details(
        conn, products, p1, locations, cfg, log,
        name_to_product=name_to_product,
        batch_size=batch_size,
    )

    log("Optimize complete — %d field(s) updated.", updated)
    return {"updated": updated}
