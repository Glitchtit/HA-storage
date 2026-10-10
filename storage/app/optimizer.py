"""AI Optimize: non-destructive enrichment for the Storage catalog.

  Phase 1 — assign aisle categories (product_group) in batches, with
            progressive context chaining so names stay consistent.
  Phase 2 — assign locations and best-before days, and normalise genuine
            multi-packs (merged into their base product via merge.py).

Optimize never creates or strips parent products and never deletes recipe
ingredients: the product tree belongs to the linker (linker.py), which runs
as the last step. Reads and writes SQLite directly — no HTTP round-trips.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any, Callable

from ai_client import call_ai_json, _get_ai_config, get_batch_size
from merge import merge_products

logger = logging.getLogger(__name__)

# Shared best-before guidance for every AI pass that estimates shelf life.
# 0 is a first-class value meaning "does not meaningfully expire" — the stock
# flow stores NULL best_before_date for such products, so they never appear in
# the expiring/expired panel.
_SHELF_LIFE_GUIDE = (
    "Best-before rules (days, for an UNOPENED package):\n"
    "- Return 0 for products with NO practical expiry: paper goods (toilet "
    "paper, paper towels, baking paper, foil, freezer/waste bags), cleaning & "
    "laundry products (detergent, stain remover, dishwasher tablets, "
    "all-purpose cleaners, air fresheners), hygiene consumables (tampons, "
    "pads, cotton swabs, razor blades), candles, batteries, matches and "
    "similar non-food household goods. These must NEVER get a food-like "
    "expiry estimate.\n"
    "- Food estimates: fresh milk 10; yogurt/fresh dairy 21; fresh meat/fish "
    "4; bread 7; eggs 28; butter 90; hard cheese 180; fresh produce 10; "
    "cereal/muesli/crackers 365; chocolate/candy 365; soft drinks/juice 270; "
    "beer/cider 270; wine/spirits 1825; coffee/tea 540; condiments & sauces "
    "(ketchup, mustard, mayonnaise, soy sauce, vinegar, syrup, jam, honey) "
    "540; oil 540; spices & dried herbs 1095; dry pasta/rice/flour/sugar "
    "1095; canned goods 1095; frozen 365; supplements/vitamins 730; "
    "cosmetics/toothpaste/shampoo 1095.\n"
    "- Shelf-stable products must never get a short (<180 day) estimate; when "
    "unsure between two ranges, choose the LONGER one.\n"
)

# Anything above 10 years is an AI hallucination; treat 0..3650 as sane.
_MAX_BB_DAYS = 3650

# The hardcoded default every product was created with before the AI ever
# saw it (database.py column default, scraper StorageClient). Lots carrying
# this exact snapshot are treated as inherited, never as manual overrides.
_LEGACY_DEFAULT_BB_DAYS = 60


def _sanitize_bb_days(value: Any) -> int | None:
    """Normalise an AI best_before_days value; None when unusable."""
    try:
        days = int(value)
    except (TypeError, ValueError):
        return None
    if days < 0:
        return None
    return min(days, _MAX_BB_DAYS)


def _rebase_inherited_stock_dates(
    conn: sqlite3.Connection,
    product_id: int,
    old_days: int,
    new_days: int,
) -> int:
    """Recompute lot best-before dates that inherited a product default.

    A lot is considered inherited (not a manual override) when its
    best_before_days snapshot equals *old_days* — the product default at the
    time of this pass — or the historic hardcoded creation default of 60 that
    every scanned product started with (earlier optimize runs changed product
    defaults without rebasing lots, so stale 60-day snapshots survive under
    products whose default has long since moved on). Manual overrides carry
    their own arbitrary diff and stay untouched. new_days == 0 clears the
    date entirely. Returns the number of rebased lots.
    """
    inherited = {old_days, _LEGACY_DEFAULT_BB_DAYS} - {new_days}
    if not inherited:
        return 0
    placeholders = ",".join("?" * len(inherited))
    if new_days > 0:
        cur = conn.execute(
            f"""UPDATE stock SET
                   best_before_days = ?,
                   best_before_date = date(
                       COALESCE(purchased_date, date(created_at), date('now')),
                       '+' || ? || ' days')
               WHERE product_id = ? AND best_before_days IN ({placeholders})""",
            (new_days, new_days, product_id, *inherited),
        )
    else:
        cur = conn.execute(
            f"UPDATE stock SET best_before_days = 0, best_before_date = NULL "
            f"WHERE product_id = ? AND best_before_days IN ({placeholders})",
            (product_id, *inherited),
        )
    return cur.rowcount


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_products(conn: sqlite3.Connection, product_ids: list[int] | None = None) -> list[dict]:
    if product_ids:
        placeholders = ",".join("?" * len(product_ids))
        rows = conn.execute(
            f"SELECT id, name, description, parent_id, location_id, product_group_id, unit_id, "
            f"default_best_before_days, min_stock_amount, active, kind FROM products "
            f"WHERE id IN ({placeholders})",
            product_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, description, parent_id, location_id, product_group_id, unit_id, "
            "default_best_before_days, min_stock_amount, active, kind FROM products"
        ).fetchall()
    return [dict(r) for r in rows]


def _load_locations(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT id, name FROM locations").fetchall()
    return [dict(r) for r in rows]


def _load_product_groups(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT id, name FROM product_groups").fetchall()
    return [dict(r) for r in rows]


def _ensure_product_group(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM product_groups WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO product_groups (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Phase 1: categories (aisle product groups)
# ---------------------------------------------------------------------------

def _phase1_categories(
    conn: sqlite3.Connection,
    products: list[dict],
    cfg: dict[str, str],
    log: Callable[..., None],
    *,
    initial_category_names: list[str] | None = None,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Assign an aisle category to every product in *products*.

    Returns:
        product_category:           product_id (int) -> category name
        category_name_to_group_id:  category name -> product_group id
    """
    product_category: dict[int, str] = {}
    category_name_to_group_id: dict[str, int] = {}
    established_category_names = list(initial_category_names or [])

    for g in _load_product_groups(conn):
        if g["name"] and g["name"] != "Group master":
            category_name_to_group_id[g["name"]] = g["id"]

    for batch_idx, i in enumerate(range(0, len(products), batch_size)):
        batch = products[i: i + batch_size]
        product_lines = "\n".join(f"  {p['id']}: {p.get('name', p['id'])}" for p in batch)
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
            f"{cats_section}"
            "For each product below, return a JSON object mapping the product "
            "ID (as a string) to an object with:\n"
            '  "category": (string) a product category in Finnish at a practical '
            "kitchen-shelf level. If an established category fits, you MUST use "
            "that exact name. Good examples: "
            "Mausteet for all spices, Maitotuotteet for dairy, Liha for meat, "
            "Juomat for drinks, Siivous for cleaning products, "
            "Hygienia for personal care/bathroom items. Null if truly "
            "one-of-a-kind.\n\n"
            "Return ONLY valid JSON, e.g.:\n"
            '{"1": {"category": "Maitotuotteet"}, "2": {"category": "Siivous"}}\n\n'
            "Products:\n"
            f"{product_lines}"
        )
        try:
            mapping = call_ai_json(prompt, conn, cfg=cfg, emit=log)
        except Exception as exc:
            log("AI category batch %d failed: %s", batch_idx + 1, exc)
            continue
        if isinstance(mapping, list):
            mapping = {
                str(item.pop("id")): item
                for item in mapping if isinstance(item, dict) and "id" in item
            }
        if not isinstance(mapping, dict):
            log("AI category batch %d: expected dict, got %s -- skipping.",
                batch_idx + 1, type(mapping).__name__)
            continue

        batch_category_names: set[str] = set()
        for product in batch:
            info = mapping.get(str(product["id"]))
            if not isinstance(info, dict):
                continue
            cat = info.get("category")
            if cat and isinstance(cat, str) and cat.strip():
                product_category[int(product["id"])] = cat.strip()
                batch_category_names.add(cat.strip())

        for cname in batch_category_names:
            if cname not in category_name_to_group_id:
                category_name_to_group_id[cname] = _ensure_product_group(conn, cname)
        established_category_names.extend(
            n for n in batch_category_names if n not in established_category_names
        )
        log("Category batch %d/%d: %d categorie(s) assigned.",
            batch_idx + 1, -(-len(products) // batch_size), len(batch_category_names))

    return {
        "product_category": product_category,
        "category_name_to_group_id": category_name_to_group_id,
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
    category_name_to_group_id = p1["category_name_to_group_id"]

    location_names: dict[int, str] = {}
    location_lines = ""
    if locations:
        location_names = {int(loc["id"]): loc.get("name", str(loc["id"])) for loc in locations}
        location_lines = "\n".join(
            f"  {loc['id']}: {loc.get('name', loc['id'])}" for loc in locations
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
            "For each product below, return a JSON object mapping the product "
            "ID (as a string) to an object with:\n"
            '  "location_id": (integer) the most appropriate storage location ID, or null.\n'
            '  "best_before_days": (integer) estimated days until best-before for an unopened '
            "product, or 0 when the product does not meaningfully expire (see rules below).\n"
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
            "- Location: dairy/meat/fresh produce -> refrigerator; "
            "ALL drinks of ANY kind (juice, soda, energy drink, sports drink, water, beer, wine, "
            "cider, alcopop, coffee drink, milk drink — ALWAYS refrigerator, no exceptions); "
            "cleaning/laundry -> "
            "cleaning cabinet; hygiene/personal care/bathroom items (cotton swabs, toothbrush, "
            "toothpaste, shampoo, soap, deodorant, etc.) -> bathroom; dry goods/canned/packaged/"
            "eggs -> pantry/cupboard. If a product shows [current location], preserve it unless "
            "it is clearly incorrect.\n"
            f"- {_SHELF_LIFE_GUIDE}"
            "- Pack: detect ONLY genuine multi-packs: 4-pack, 6x0.33l, monipakkaus, 6kpl Sprite. "
            "NOT when number describes package contents.\n\n"
            "Return ONLY a valid JSON object (not a list). Example:\n"
            '{\"1\": {\"location_id\": 2, \"best_before_days\": 7, '
            '\"pack_size\": null, \"pack_unit\": null, \"base_product_name\": null}}\n\n'
            "Products:\n"
            f"{product_lines}"
        )

        try:
            mapping = call_ai_json(prompt, conn, cfg=cfg, emit=log)
        except Exception as exc:
            log("AI details batch %d failed: %s", batch_idx + 1, exc)
            continue

        if isinstance(mapping, list):
            recovered = {}
            for item in mapping:
                if isinstance(item, dict) and "id" in item:
                    item_id = str(item.pop("id"))
                    recovered[item_id] = item
            if recovered:
                log("AI details batch %d: recovered list→dict (%d entries).", batch_idx + 1, len(recovered))
                mapping = recovered
            else:
                log("AI details batch %d: expected dict, got list — cannot recover, skipping.", batch_idx + 1)
                continue
        elif not isinstance(mapping, dict):
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
                            # A scan of this barcode now adds pack_size base
                            # units, and a held multi-pack becomes pack_size
                            # base units; merge.py then moves everything else
                            # (ingredients, rows, history) without losing it.
                            conn.execute(
                                "UPDATE barcodes SET pack_size = ? WHERE product_id = ?",
                                (pack_size, product_id),
                            )
                            if pack_size > 1:
                                conn.execute(
                                    "UPDATE stock SET amount = amount * ?, "
                                    "amount_opened = COALESCE(amount_opened, 0) * ? "
                                    "WHERE product_id = ?",
                                    (pack_size, pack_size, product_id),
                                )
                            merge_products(conn, product_id, base_pid)
                            log("  -> Merged multi-pack '%s' into '%s' (pack=%d).",
                                product.get("name"), base_name, pack_size)
                            updated += 1
                            continue
                        except Exception as exc:
                            conn.rollback()
                            log("  ! Could not merge '%s' into '%s': %s",
                                product.get("name"), base_name, exc)
                    elif not base_product or int(base_product["id"]) == product_id:
                        # Rename in place and multiply stock × pack_size
                        if base_name and base_name != product.get("name"):
                            try:
                                conn.execute("SAVEPOINT pack_rename")
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
                                conn.execute("RELEASE pack_rename")
                                conn.commit()
                                log("  -> Renamed '%s' -> '%s' (multi-pack normalised).",
                                    product.get("name"), base_name)
                                name_to_product[base_name] = {**product, "name": base_name}
                                name_to_product.pop(product.get("name", ""), None)
                                updated += 1
                            except Exception as exc:
                                log("  ! Could not rename '%s': %s", product.get("name"), exc)
                                try:
                                    conn.execute("ROLLBACK TO pack_rename")
                                    conn.execute("RELEASE pack_rename")
                                except Exception:
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
            days = _sanitize_bb_days(info.get("best_before_days"))
            if days is not None:
                try:
                    old_days = int(product.get("default_best_before_days") or 0)
                    conn.execute(
                        "UPDATE products SET default_best_before_days = ? WHERE id = ?",
                        (days, product_id),
                    )
                    log("  -> Set %d best-before days for '%s'.", days, product.get("name"))
                    rebased = _rebase_inherited_stock_dates(conn, product_id, old_days, days)
                    if rebased:
                        log("  -> Rebased %d stock lot(s) from %dd default to %dd.",
                            rebased, old_days, days)
                    updated += 1
                except Exception as exc:
                    log("  ! Could not set best-before for '%s': %s", product.get("name"), exc)

            # --- Category (from Phase 1) ---
            cat_name = product_category.get(product_id)
            cg_id = category_name_to_group_id.get(cat_name) if cat_name else None
            if cg_id is not None:
                try:
                    conn.execute(
                        "UPDATE products SET product_group_id = ? WHERE id = ?",
                        (cg_id, product_id),
                    )
                    updated += 1
                except Exception as exc:
                    log("  ! Could not set product_group for '%s': %s", product.get("name"), exc)

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
    fresh_seed: bool = False,
    link: bool = True,
) -> dict[str, int]:
    """Run the AI enrichment pipeline.

    *product_ids* = None  → every SKU in the catalog
    *product_ids* = [...] → only those products
    *emit*                → optional callable streaming log lines to the UI
    *enforced_categories* → category names the AI must prefer; always created
    *fresh_seed*          → accepted for API compatibility, ignored (there is
                            no parent naming to seed any more)
    *link*                → finish with the linker: the reconcile sweep for a
                            full run, link_products for an explicit list

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
    if fresh_seed:
        log("Fresh naming no longer applies: optimize does not create parent products.")

    all_products = _load_products(conn)
    locations = _load_locations(conn)
    name_to_product = {p.get("name", ""): p for p in all_products}
    if product_ids is None:
        products = [p for p in all_products if p.get("kind", "sku") == "sku"]
    else:
        allowed = set(product_ids)
        products = [p for p in all_products if int(p["id"]) in allowed]

    if not products:
        log("No products to optimize.")
        return {"updated": 0}

    log("Starting AI optimize for %d product(s)…", len(products))
    groups = _load_product_groups(conn)
    category_names = sorted({
        g["name"] for g in groups if g.get("name") and g["name"] != "Group master"
    })
    for cat in enforced_categories or []:
        if cat and cat not in category_names:
            category_names.append(cat)
    if enforced_categories:
        log("Enforcing %d user-defined category/categories.", len(enforced_categories))

    log("Phase 1: assigning categories…")
    p1 = _phase1_categories(
        conn, products, cfg, log,
        initial_category_names=category_names or None,
        batch_size=batch_size,
    )
    for cat in enforced_categories or []:
        if cat and cat not in p1["category_name_to_group_id"]:
            p1["category_name_to_group_id"][cat] = _ensure_product_group(conn, cat)
            log("  Created enforced product group '%s'.", cat)
    conn.commit()

    log("Phase 2: assigning locations, best-before, and pack info…")
    updated = _phase2_details(
        conn, products, p1, locations, cfg, log,
        name_to_product=name_to_product,
        batch_size=batch_size,
    )

    if link:
        import linker
        if product_ids is None:
            res = linker.run_reconcile(conn)
            log("Linker sweep: %d linked, %d proposed.", res["linked"], res["proposed"])
        else:
            existing = [
                pid for pid in product_ids
                if conn.execute("SELECT 1 FROM products WHERE id = ?", (pid,)).fetchone()
            ]
            res = linker.link_products(conn, existing)
            log("Linker: %d linked, %d proposed.", len(res["linked"]), len(res["proposed"]))

    log("Optimize complete — %d field(s) updated.", updated)
    return {"updated": updated}


# ---------------------------------------------------------------------------
# Focused expiry fix — no structural changes, safe to run any time
# ---------------------------------------------------------------------------

def run_expiry_fix(
    conn: sqlite3.Connection,
    *,
    product_ids: list[int] | None = None,
    emit: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """AI pass that ONLY fixes default_best_before_days on products and
    rebases stock lots that inherited the old default.

    Unlike run_optimize, this never touches categories, parents, locations,
    or pack structure — it is safe to run over the whole database to repair
    bad expiry defaults (e.g. the hardcoded 60-day default on scanned
    products).
    """
    def log(msg: str, *args: Any) -> None:
        formatted = msg % args if args else msg
        logger.info(formatted)
        if emit:
            emit(formatted)

    cfg = _get_ai_config(conn)
    batch_size = get_batch_size(conn)

    if product_ids:
        placeholders = ",".join("?" * len(product_ids))
        rows = conn.execute(
            f"SELECT id, name, default_best_before_days FROM products "
            f"WHERE id IN ({placeholders})",
            product_ids,
        ).fetchall()
    else:
        # Active products plus anything that still holds stock.
        rows = conn.execute(
            "SELECT id, name, default_best_before_days FROM products p "
            "WHERE p.active = 1 "
            "   OR EXISTS (SELECT 1 FROM stock s WHERE s.product_id = p.id)"
        ).fetchall()
    products = [dict(r) for r in rows]

    if not products:
        log("No products for expiry fix.")
        return {"updated": 0}

    log("Starting expiry fix for %d product(s)…", len(products))
    updated = 0

    for batch_idx, i in enumerate(range(0, len(products), batch_size)):
        batch = products[i: i + batch_size]
        product_lines = "\n".join(
            f"  {p['id']}: {p.get('name', p['id'])}" for p in batch
        )
        prompt = (
            "You are a grocery database expert maintaining a Finnish home "
            "pantry. For each product below, estimate the best-before shelf "
            "life in days.\n\n"
            f"{_SHELF_LIFE_GUIDE}\n"
            "Return ONLY a valid JSON object mapping product ID (as a string) "
            'to an integer number of days. Example: {"1": 540, "2": 0}\n\n'
            "Products:\n"
            f"{product_lines}"
        )

        try:
            mapping = call_ai_json(prompt, conn, cfg=cfg, emit=log)
        except Exception as exc:
            log("Expiry batch %d failed: %s", batch_idx + 1, exc)
            continue
        if not isinstance(mapping, dict):
            log("Expiry batch %d: expected dict, got %s — skipping.",
                batch_idx + 1, type(mapping).__name__)
            continue

        for product in batch:
            product_id = int(product["id"])
            days = _sanitize_bb_days(mapping.get(str(product_id)))
            if days is None:
                continue
            old_days = int(product.get("default_best_before_days") or 0)
            try:
                if days != old_days:
                    conn.execute(
                        "UPDATE products SET default_best_before_days = ? WHERE id = ?",
                        (days, product_id),
                    )
                    log("  -> '%s': %dd -> %dd.", product.get("name"), old_days, days)
                    updated += 1
                rebased = _rebase_inherited_stock_dates(conn, product_id, old_days, days)
                if rebased:
                    log("  -> Rebased %d stock lot(s) for '%s'.",
                        rebased, product.get("name"))
            except Exception as exc:
                log("  ! Could not fix expiry for '%s': %s", product.get("name"), exc)

        conn.commit()
        log("Expiry batch %d/%d done.",
            batch_idx + 1, -(-len(products) // batch_size))

    log("Expiry fix complete — %d product(s) updated.", updated)
    return {"updated": updated}
