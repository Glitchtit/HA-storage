"""Buy hints: which retail product to pick up for a shopping row, and how
many packages of it (spec §4.8).

A concept row ("3 dl kerma") says what is needed; the hint names the SKU the
household actually buys. That is the row's preferred product (set by a swap)
while it is still a SKU under the row's product, else the row's own product
when it is a SKU, else the active descendant SKU bought most often in the
last 180 days, then the one bought most recently; a concept none of whose
SKUs was ever bought takes any active SKU, one sold in a store first, then
one with a price, then the oldest. The count converts the
row's quantity into whole packages of that SKU; when the facts for that are
missing it is 1, marked approximate.
"""
from __future__ import annotations

import math
import sqlite3

from facts import Catalog
from quantity import COUNT, MASS, PACKAGE, VOLUME, convert
from recipe_rows import row_unit

LOOKBACK_DAYS = 180


def purchase_stats(conn: sqlite3.Connection) -> dict[int, tuple[int, str]]:
    """product id -> (purchases in the last 180 days, time of the last one).
    Counts purchase events, not amounts: a scan logs a pack size, a recount a
    difference, so summed amounts mix units."""
    rows = conn.execute(
        "SELECT product_id, SUM(created_at >= datetime('now', ?)) AS recent, "
        "       MAX(created_at) AS last "
        "  FROM stock_history WHERE event_type = 'purchase' GROUP BY product_id",
        (f"-{LOOKBACK_DAYS} days",),
    ).fetchall()
    return {r["product_id"]: (int(r["recent"] or 0), r["last"] or "") for r in rows}


def usual_sku(cat: Catalog, product_id: int, stats: dict[int, tuple[int, str]]) -> int | None:
    """The active descendant SKU bought most often lately, then most recently."""
    best, best_key = None, None
    for pid in cat.descendants(product_id):
        if pid not in stats or not cat.is_sku(pid) or not (cat.product(pid) or {}).get("active"):
            continue
        recent, last = stats[pid]
        key = (recent, last, -pid)
        if best_key is None or key > best_key:
            best, best_key = pid, key
    return best


def fallback_ranks(conn: sqlite3.Connection) -> dict[int, tuple[int, int]]:
    """SKU id -> (sold in at least one store, has a price): how to choose
    among variants nobody has bought yet."""
    rows = conn.execute(
        "SELECT p.id, "
        "       EXISTS (SELECT 1 FROM product_availability a "
        "                WHERE a.product_id = p.id AND a.available = 1) AS sold, "
        "       p.unit_price IS NOT NULL AS priced "
        "  FROM products p WHERE p.kind != 'concept'"
    ).fetchall()
    return {r["id"]: (int(r["sold"]), int(r["priced"])) for r in rows}


def any_sku(cat: Catalog, product_id: int, ranks: dict[int, tuple[int, int]]) -> int | None:
    """Any active descendant SKU: sold in a store first, then priced, then the oldest."""
    best, best_key = None, None
    for pid in cat.descendants(product_id):
        if not cat.is_sku(pid) or not (cat.product(pid) or {}).get("active"):
            continue
        key = (*ranks.get(pid, (0, 0)), -pid)
        if best_key is None or key > best_key:
            best, best_key = pid, key
    return best


def buy_product(cat: Catalog, row: dict, stats: dict[int, tuple[int, str]],
                ranks: dict[int, tuple[int, int]] | None = None) -> int | None:
    pid = row["product_id"]
    preferred = row.get("preferred_product_id")
    if preferred is not None and cat.is_sku(preferred) and preferred in cat.descendants(pid):
        return preferred
    if cat.is_sku(pid):
        return pid
    return usual_sku(cat, pid, stats) or any_sku(cat, pid, ranks or {})


def _sold_by_measure(facts) -> bool:
    """A package whose content is a weight or volume and whose piece weight is
    unknown: pieces cannot be counted into it."""
    cu = facts.content_unit
    return (bool(facts.content_amount) and cu is not None and cu.dimension in (MASS, VOLUME)
            and not facts.piece_weight_g)


def buy_count(cat: Catalog, row: dict, sku: int) -> tuple[int, bool]:
    """Whole packages of `sku` that cover the row, and whether that is an estimate."""
    amount = float(row["amount"])
    side = row_unit(cat, row["product_id"], row["unit_id"])
    if side is not None and side[1] == PACKAGE:
        return max(1, math.ceil(amount - 1e-9)), False
    kpl = cat.unit_by_abbr("kpl")
    facts = cat.facts(sku)
    conv = None
    if side is not None and kpl is not None:
        conv = convert(amount, side[0], kpl, facts, from_dim=side[1], to_dim=PACKAGE)
    if conv is not None and side[1] == COUNT and _sold_by_measure(facts):
        # Pieces into a bag or jar of known weight or volume: without a piece
        # weight, convert's one-piece-per-package fallback would turn "1,5
        # perunaa" into two 1 kg bags.
        conv = None
    if conv is None:
        return 1, True
    return max(1, math.ceil(conv.amount - 1e-9)), conv.approx


def buy_hint(cat: Catalog, row: dict, stats: dict[int, tuple[int, str]],
             ranks: dict[int, tuple[int, int]] | None = None) -> dict:
    """The buy-hint fields of a row's display block."""
    sku = buy_product(cat, row, stats, ranks)
    if sku is None:
        return {"buy_product_id": None, "buy_product_name": None, "buy_count": None, "approx": False}
    count, approx = buy_count(cat, row, sku)
    return {"buy_product_id": sku, "buy_product_name": cat.product(sku)["name"],
            "buy_count": count, "approx": approx}
