"""Shopping list endpoints."""

from __future__ import annotations

import json
import logging
import math

from fastapi import APIRouter, HTTPException, Query, Response

from ai_client import call_ai_json
from consumption_stats import compute_cadence_suggestions, compute_proposal
from facts import Catalog
from list_clearing import apply_purchase
from buy_hints import buy_hint, fallback_ranks, purchase_stats
from quantity import COUNT, PACKAGE, VAGUE, convert, format_qty
from recipe_rows import row_unit
from models import (
    ApplyBasketRequest,
    ApplyBasketResponse,
    CadenceSuggestionResponse,
    ReconcileApplyRequest,
    ReconcileApplyResponse,
    ReconcileMatch,
    ReconcileRequest,
    ReconcileResponse,
    ShoppingItem,
    ShoppingItemCreate,
    ShoppingItemUpdate,
    ShoppingProposalResponse,
    ShoppingSwap,
)

router = APIRouter(tags=["shopping-list"])
log = logging.getLogger(__name__)

# Treat row amounts within this tolerance of zero as fully consumed.
# Subtracting fractional amounts across rows accumulates IEEE-754
# residues that can leave near-zero positives; this epsilon snaps them
# to "done".
_AMOUNT_EPSILON = 1e-9

# A shopping row's `pinned` is derived from the product, never the stored
# shopping_list.pinned column — products.pin_brand is the single source of
# truth (see the pin_brand migration). Every read of a ShoppingItem goes
# through this so all add paths inherit a product's persistent pin for free.
_SHOPPING_SELECT = """
    SELECT s.id, s.product_id, s.amount, s.unit_id, s.note, s.done,
           s.recipe_id, s.bundle_id, s.auto_added, s.ha_item_name, s.created_at,
           s.preferred_product_id, COALESCE(p.pin_brand, 0) AS pinned
      FROM shopping_list s
      LEFT JOIN products p ON p.id = s.product_id
"""


def _get_db():
    from main import get_connection
    return get_connection()


def _with_display(conn, rows: list[dict]) -> list[dict]:
    """Attach the display block to shopping rows: qty_text / unit_dimension
    and the buy hint (see buy_hints.py).

    NULL unit = retail items. A count unit on a SKU row also means retail
    items (the SKU's own kpl); on a concept row it means natural pieces.
    A unit id that no longer exists renders as a bare number, dimension vague.
    """
    if not rows:
        return rows
    cat = Catalog(conn)
    concepts = any(not cat.is_sku(r["product_id"]) for r in rows)
    stats = purchase_stats(conn) if concepts else {}
    ranks = fallback_ranks(conn) if concepts else {}
    out = []
    for row in rows:
        unit = cat.unit(row["unit_id"])
        if row["unit_id"] is None:
            abbr, dim = None, PACKAGE
        elif unit is None:
            abbr, dim = None, VAGUE
        else:
            abbr, dim = unit.abbreviation, cat.stock_dim(row["product_id"], unit)
        out.append({**row, "display": {
            "qty_text": format_qty(float(row["amount"]), abbr),
            "unit_abbr": abbr,
            "unit_dimension": dim,
            **buy_hint(cat, row, stats, ranks),
        }})
    return out


def _one(conn, row_id: int) -> dict:
    return _with_display(conn, [conn.execute(_SHOPPING_SELECT + "WHERE s.id = ?", (row_id,)).fetchone()])[0]


def sync_auto_shopping(conn) -> dict:
    """Reconcile auto-added shopping list rows against current stock levels.

    For every active product with `min_stock_amount > 0`:

    * If current total stock is **below** the threshold and the product has no
      shopping_list row at all, insert one with ``auto_added = 1``.
    * If current total stock is **below** the threshold and an ``auto_added = 1``
      row already exists, update its amount to the **current** deficit. An
      auto-added row represents "how much you still need", so a partial restock
      since it was created must shrink it (buy 2 of a 3-deficit item → the row
      now reads 1), not leave it frozen at the original deficit.
    * If current total stock is **at or above** the threshold, delete any
      existing ``auto_added = 1`` row that is **not yet done** — the user has
      already restocked, so the auto-added entry is stale.

    Rows the user added manually (``auto_added = 0``) are never touched here
    (their amount is decremented on purchase by ``consume_shopping_for_purchase``
    instead), and rows that are already ``done = 1`` are preserved so we don't
    yank items out from under an active shopping trip. A manual or done row also
    suppresses auto-add so we never create a duplicate.

    Returns a dict with ``added``, ``removed`` and ``updated`` counts for
    callers/logging.
    """
    from availability import own_unit_have

    rows = conn.execute(
        "SELECT id, name, unit_id, min_stock_amount FROM products "
        "WHERE active = 1 AND min_stock_amount > 0"
    ).fetchall()
    cat = Catalog(conn) if rows else None

    added = 0
    removed = 0
    updated = 0
    for r in rows:
        pid = r["id"]
        min_amt = float(r["min_stock_amount"] or 0)
        h = own_unit_have(cat, pid)
        # Stock we cannot measure still counts as "have": never nag about it.
        have = min_amt if (h.unknown_lots and h.amount < min_amt) else h.amount
        if have < min_amt:
            need = max(1.0, min_amt - have)
            auto_row = conn.execute(
                "SELECT id, amount FROM shopping_list"
                " WHERE product_id = ? AND auto_added = 1 AND done = 0 LIMIT 1",
                (pid,),
            ).fetchone()
            if auto_row:
                # Keep the auto-added amount equal to the live deficit.
                if abs(float(auto_row["amount"]) - need) > _AMOUNT_EPSILON:
                    conn.execute(
                        "UPDATE shopping_list SET amount = ? WHERE id = ?",
                        (need, auto_row["id"]),
                    )
                    updated += 1
                # Two clients adding the same row at once leave a duplicate.
                cur = conn.execute(
                    "DELETE FROM shopping_list WHERE product_id = ? AND auto_added = 1"
                    " AND done = 0 AND id != ?",
                    (pid, auto_row["id"]),
                )
                removed += cur.rowcount
                continue
            existing = conn.execute(
                "SELECT 1 FROM shopping_list WHERE product_id = ? LIMIT 1",
                (pid,),
            ).fetchone()
            if existing:
                continue  # manual or done row present — don't duplicate
            conn.execute(
                "INSERT INTO shopping_list (product_id, amount, unit_id, ha_item_name, auto_added)"
                " VALUES (?, ?, ?, ?, 1)",
                (pid, need, r["unit_id"], r["name"]),
            )
            added += 1
        else:
            cur = conn.execute(
                "DELETE FROM shopping_list WHERE product_id = ? AND auto_added = 1 AND done = 0",
                (pid,),
            )
            if cur.rowcount:
                removed += cur.rowcount

    # Always end the transaction: even a DELETE that removed nothing opened a
    # write transaction, and leaving it open on the shared connection blocks
    # every background job that writes through its own connection.
    conn.commit()
    if added or removed or updated:
        log.info("Shopping auto-sync: added=%d, removed=%d, updated=%d",
                 added, removed, updated)
    return {"added": added, "removed": removed, "updated": updated}


def _decrement_rows(conn, rows, amount: float) -> list[int]:
    """Subtract `amount` across `rows` oldest-first, spilling leftover into the
    next row. A row whose new amount is `<= _AMOUNT_EPSILON` is hard-deleted;
    others are updated in place. `rows` are sqlite Rows with `id` and `amount`.

    Does NOT commit — the caller owns the transaction (lets the reconcile-apply
    path batch many decrements into one commit). Returns affected row ids.
    """
    remaining = float(amount)
    affected: list[int] = []
    for row in rows:
        if remaining <= 0:
            break
        new_amount = float(row["amount"]) - remaining
        if new_amount <= _AMOUNT_EPSILON:
            conn.execute("DELETE FROM shopping_list WHERE id = ?", (row["id"],))
            remaining = -new_amount  # spill leftover into next row
        else:
            conn.execute(
                "UPDATE shopping_list SET amount = ? WHERE id = ?",
                (new_amount, row["id"]),
            )
            remaining = 0
        affected.append(row["id"])
    return affected


def _decrement_one_row(conn, row_id: int, amount: float) -> int | None:
    """Decrement a single non-done shopping row by `amount`. Returns the row id
    if it was touched, else None (row gone / already done). Does NOT commit."""
    row = conn.execute(
        "SELECT id, amount FROM shopping_list WHERE id = ? AND done = 0", (row_id,)
    ).fetchone()
    if not row:
        return None
    affected = _decrement_rows(conn, [row], amount)
    return affected[0] if affected else None


def consume_shopping_for_purchase(
    conn,
    product_id: int,
    amount: float,
    unit_id: int | None,
    stock_id: int | None = None,
) -> list[dict]:
    """Apply a purchase of `product_id` to the shopping list: rows of the
    product and of the concepts above it, converted into each row's unit
    (list_clearing.apply_purchase). Auto-added rows are sync_auto_shopping's.

    Returns the touched rows ({id, product_id, removed, amount}) and always
    ends the transaction apply_purchase opened.
    """
    touched = apply_purchase(conn, Catalog(conn), product_id, amount, unit_id, stock_id)
    conn.commit()
    if touched:
        log.info(
            "Shopping clear-on-purchase: product=%d, amount=%.3f, affected=%d",
            product_id, amount, len(touched),
        )
    return touched


@router.get("/shopping-list", response_model=list[ShoppingItem])
def list_shopping():
    conn = _get_db()
    return _with_display(conn, conn.execute(
        _SHOPPING_SELECT + "ORDER BY s.done, s.created_at DESC"
    ).fetchall())


@router.get("/shopping-list/proposal", response_model=ShoppingProposalResponse)
def shopping_proposal(
    lookback_weeks: int = Query(8, ge=1, le=52),
    horizon_days: int = Query(7, ge=1, le=30),
):
    """Predictive shopping proposal based on consumption velocity.

    For every active product with ``min_stock_amount > 0``, compute the mean
    weekly consume rate over the last ``lookback_weeks``. If the product is
    predicted to deplete within ``horizon_days`` and is not already on the
    shopping list, include it in the proposal sorted by urgency.
    """
    items = compute_proposal(
        _get_db(),
        lookback_weeks=lookback_weeks,
        horizon_days=horizon_days,
    )
    return {
        "lookback_weeks": lookback_weeks,
        "horizon_days": horizon_days,
        "proposal": items,
    }


@router.get("/shopping-list/cadence-suggestions", response_model=CadenceSuggestionResponse)
def cadence_suggestions(
    lookback_days: int = Query(180, ge=14, le=730),
    window_days: int = Query(7, ge=1, le=30),
    min_purchases: int = Query(3, ge=2, le=20),
):
    """Purchase-cadence shopping suggestions.

    For products that are kept in stock (``min_stock_amount > 0``) or bought at
    least ``min_purchases`` times in the last ``lookback_days``, learn the mean
    interval between purchases and suggest a re-buy when today is within
    ``window_days`` of the expected next purchase. Excludes items already on the
    list or still well-stocked. Distinct from ``/shopping-list/proposal``, which
    is consumption-velocity based.
    """
    items = compute_cadence_suggestions(
        _get_db(),
        lookback_days=lookback_days,
        window_days=window_days,
        min_purchases=min_purchases,
    )
    return {
        "lookback_days": lookback_days,
        "window_days": window_days,
        "min_purchases": min_purchases,
        "suggestions": items,
    }


@router.post("/shopping-list/sync", status_code=200)
def sync_shopping():
    """Reconcile auto-added rows with current stock — adds rows for products
    that fell below their `min_stock_amount` and removes auto-added rows for
    products that are now back at or above the threshold."""
    return sync_auto_shopping(_get_db())


@router.post("/shopping-list/apply-basket", response_model=ApplyBasketResponse)
def apply_basket(body: ApplyBasketRequest):
    """Finishing a trip, step one (spec §4.8): apply the basket items whose
    scan cleared nothing once more, since the linker may have placed a new
    product under a concept in the meantime. Send only those items: this
    adds no stock, and an item applied twice clears its rows twice. Items
    that still clear nothing come back as `unmatched`, for the AI reconcile."""
    conn = _get_db()
    cat = Catalog(conn)
    cleared: list[dict] = []
    unmatched = []
    for item in body.basket:
        rows = apply_purchase(conn, cat, item.product_id, item.amount, item.unit_id)
        if rows:
            cleared.extend(rows)
        else:
            unmatched.append(item)
    conn.commit()
    return {"cleared_rows": cleared, "unmatched": unmatched}


# ── Cross-brand reconcile ────────────────────────────────────────────────────

_RECONCILE_PROMPT = """You are matching grocery items a household just BOUGHT \
against items still on their shopping list, to decide which bought item fulfils \
which list item.

CONTEXT:
- Product names are Finnish; the household also uses Swedish and English.
- A bought item fulfils a list item ONLY if it is the SAME PRODUCT TYPE. A \
different brand, pack size, or variant of the same thing IS a valid match \
(list "Maito" <- bought "Arla Kevytmaito 1L"; list "Bearnaisekastike" <- bought \
any brand of bearnaise sauce).
- Genuinely DIFFERENT types must NOT match ("Maito" must not match "Piima" or \
"Kerma"; "Voi" must not match "Margariini"). When unsure, DO NOT match.
- Match by TYPE and MEANING, never by brand name or substring overlap.

Shopping list items still needed (JSON):
{shopping_json}

Items bought this session (JSON):
{basket_json}

Return ONLY a JSON array. Each element matches one bought item to one list item:
[{{"shopping_row_id": <int>, "bought_product_id": <int>, "confidence": "high"|"medium"|"low"}}]
Rules:
- Each shopping_row_id appears AT MOST ONCE. Each bought_product_id appears AT MOST ONCE.
- Omit any list item that has no same-type bought item. Use "low" for anything uncertain.
"""


@router.post("/shopping-list/reconcile", response_model=ReconcileResponse)
def reconcile_shopping(body: ReconcileRequest):
    """Propose cross-brand fulfillments for a finished shopping session.

    Pure read — never mutates the DB. Given the basket of items bought this
    session, ask the AI which of them are a variation (different brand/pack,
    same type) of any remaining non-pinned manual list row, and return the
    proposed matches for the user to confirm. Items consumed by the real-time
    exact path (`consume_shopping_for_purchase`) are excluded so the AI pass
    can never double-count. Returns empty (and skips the AI call) when there
    are no leftovers; returns ``ai_available: false`` if the AI is unreachable
    so the caller's "finish" flow never fails."""
    conn = _get_db()

    rows = conn.execute(
        """
        SELECT s.id, s.product_id, s.amount,
               COALESCE(s.ha_item_name, p.name) AS name
        FROM shopping_list s
        LEFT JOIN products p ON p.id = s.product_id
        WHERE s.done = 0 AND COALESCE(p.pin_brand, 0) = 0 AND s.auto_added = 0
        ORDER BY s.created_at ASC, s.id ASC
        """
    ).fetchall()
    if not rows:
        return {"proposals": [], "ai_available": True}

    # Exclude basket items the exact path already owns: anything whose product
    # equals a remaining row's product, or explicitly flagged by the caller.
    exclude = set(body.exclude_product_ids) | {r["product_id"] for r in rows}
    basket_amounts: dict[int, float] = {}
    for it in body.basket:
        if it.product_id in exclude:
            continue
        basket_amounts[it.product_id] = basket_amounts.get(it.product_id, 0.0) + float(it.amount)

    # Resolve names; drop products we can't name (nothing to match on).
    bought_names: dict[int, str] = {}
    for pid in list(basket_amounts):
        prow = conn.execute("SELECT name FROM products WHERE id = ?", (pid,)).fetchone()
        if prow and prow["name"]:
            bought_names[pid] = prow["name"]
        else:
            del basket_amounts[pid]
    if not basket_amounts:
        return {"proposals": [], "ai_available": True}

    shopping_json = json.dumps(
        [{"shopping_row_id": r["id"], "name": r["name"]} for r in rows],
        ensure_ascii=False,
    )
    basket_json = json.dumps(
        [{"bought_product_id": p, "name": bought_names[p]} for p in basket_amounts],
        ensure_ascii=False,
    )
    prompt = _RECONCILE_PROMPT.format(shopping_json=shopping_json, basket_json=basket_json)

    try:
        result = call_ai_json(prompt, conn)
    except Exception as exc:  # AI offline / unconfigured — finish must not fail.
        log.warning("Reconcile AI call failed: %s", exc)
        return {"proposals": [], "ai_available": False}

    if not isinstance(result, list):
        log.warning("Reconcile AI returned %s, expected list.", type(result).__name__)
        return {"proposals": [], "ai_available": True}

    row_by_id = {r["id"]: r for r in rows}
    row_need = {r["id"]: float(r["amount"]) for r in rows}
    used_rows: set[int] = set()
    used_bought: set[int] = set()
    proposals: list[ReconcileMatch] = []

    for m in result:
        if not isinstance(m, dict):
            continue
        try:
            srid = int(m["shopping_row_id"])
            bpid = int(m["bought_product_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if str(m.get("confidence", "")).lower() not in ("high", "medium"):
            continue
        # Hallucination + at-most-once guards.
        if srid not in row_by_id or bpid not in basket_amounts:
            continue
        if srid in used_rows or bpid in used_bought:
            continue
        take = min(row_need.get(srid, 0.0), basket_amounts.get(bpid, 0.0))
        if take <= 0:
            continue
        used_rows.add(srid)
        used_bought.add(bpid)
        proposals.append(ReconcileMatch(
            shopping_row_id=srid,
            bought_product_id=bpid,
            amount=take,
            confidence=str(m["confidence"]).lower(),
            shopping_name=row_by_id[srid]["name"],
            bought_name=bought_names[bpid],
        ))

    return {"proposals": proposals, "ai_available": True}


def _reconcile_link_target(conn, row_product_id: int) -> int | None:
    """The concept a bought SKU should be linked under, given the shopping
    row's product: the row's product itself when it is a concept, else that
    SKU's own parent when the parent is a concept, else nothing (a SKU is
    never a parent)."""
    row_prod = conn.execute(
        "SELECT p.kind, p.parent_id, pp.kind AS parent_kind "
        "FROM products p LEFT JOIN products pp ON pp.id = p.parent_id WHERE p.id = ?",
        (row_product_id,),
    ).fetchone()
    if row_prod is None:
        return None
    if row_prod["kind"] == "concept":
        return row_product_id
    if row_prod["parent_id"] is not None and row_prod["parent_kind"] == "concept":
        return row_prod["parent_id"]
    return None


@router.post("/shopping-list/reconcile/apply", response_model=ReconcileApplyResponse)
def reconcile_apply(body: ReconcileApplyRequest):
    """Apply user-confirmed cross-brand fulfillments. Decrements each matched
    row by its amount via the shared helper; rows already gone/done land in
    ``skipped`` (idempotent). Runs one ``sync_auto_shopping`` afterward, matching
    the ``/stock/add`` ordering. As a side effect, each confirmed match whose
    bought product differs from the row's product and is still unparented gets
    persisted as a tree link — under the row's node if it is node-shaped, or
    under the row product's own parent if the row product is itself a SKU (never
    directly under another SKU); link failures are logged and never fail the
    apply."""
    from linker import apply_link

    conn = _get_db()
    applied: list[int] = []
    skipped: list[int] = []
    links_to_apply: list[tuple[int, int]] = []
    for m in body.matches:
        row = conn.execute(
            "SELECT product_id FROM shopping_list WHERE id = ?", (m.shopping_row_id,)
        ).fetchone()
        touched = _decrement_one_row(conn, m.shopping_row_id, m.amount)
        if touched is None:
            skipped.append(m.shopping_row_id)
            continue
        applied.append(touched)
        # A confirmed cross-brand match is ground truth: persist it as a tree
        # link so recipe availability sees this SKU under the generic node.
        # Only collect here — apply_link commits internally, and committing
        # mid-loop would flush partial decrements before the batch commit.
        if row and row["product_id"] and m.bought_product_id != row["product_id"]:
            bought = conn.execute(
                "SELECT parent_id FROM products WHERE id = ?", (m.bought_product_id,)
            ).fetchone()
            if bought and bought["parent_id"] is None:
                target = _reconcile_link_target(conn, row["product_id"])
                if target is not None:
                    links_to_apply.append((m.bought_product_id, target))
    if applied:
        conn.commit()
        log.info("Reconcile apply: applied=%d, skipped=%d", len(applied), len(skipped))
        sync_auto_shopping(conn)
    for bought_id, parent_id in links_to_apply:
        try:
            apply_link(conn, bought_id, parent_id,
                       note="confirmed via shopping reconcile")
        except Exception as exc:  # Link is best-effort; apply must never fail.
            log.warning("Reconcile link %d->%d skipped: %s",
                        bought_id, parent_id, exc)
    return {"applied": applied, "skipped": skipped}


@router.delete("/shopping-list/done", status_code=204)
def clear_done():
    """Clear all completed items."""
    conn = _get_db()
    conn.execute("DELETE FROM shopping_list WHERE done = 1")
    conn.commit()


@router.post("/shopping-list", response_model=ShoppingItem, status_code=201)
def add_shopping_item(body: ShoppingItemCreate, response: Response):
    conn = _get_db()
    product = conn.execute("SELECT id, name, kind FROM products WHERE id = ?", (body.product_id,)).fetchone()
    if not product:
        raise HTTPException(400, f"Product {body.product_id} not found")
    if body.auto_added and product["kind"] == "concept":
        # A concept's minimum stock counts the stock of every product under it,
        # which only sync_auto_shopping judges. An HA-stock older than 2.12.1
        # would add these rows from the concept's own, empty lots.
        raise HTTPException(409, "Storage keeps minimum-stock rows for concepts itself")
    # An explicit pinned=True on add is a persistent product preference, not a
    # one-off: promote it to the product so every future row inherits it.
    if body.pinned:
        conn.execute("UPDATE products SET pin_brand = 1 WHERE id = ?", (body.product_id,))
    # A plain manual add (no note, no recipe attribution) merges into an
    # existing plain manual row for the same product+unit instead of creating
    # a duplicate — re-scanning or re-picking a product means "+N", not a new
    # row. Excluded on purpose: noted rows (free-text Muistilappu entries share
    # one sentinel product and differ only by note), recipe-attributed rows,
    # bundle-attributed rows, done rows (active shopping-trip history), and
    # auto_added rows (their amount is owned by sync_auto_shopping's
    # live-deficit reconciliation). Bundle rows are excluded so a plain re-add
    # never double-counts or un-badges them. Returns 200 on merge vs 201 on create.
    if not body.auto_added and not body.note and body.recipe_id is None:
        existing = conn.execute(
            """
            SELECT id FROM shopping_list
            WHERE product_id = ? AND done = 0 AND auto_added = 0
              AND recipe_id IS NULL AND bundle_id IS NULL
              AND (note IS NULL OR note = '')
              AND unit_id IS ?
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """,
            (body.product_id, body.unit_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE shopping_list SET amount = amount + ? WHERE id = ?",
                (body.amount, existing["id"]),
            )
            conn.commit()
            response.status_code = 200
            return _one(conn, existing["id"])
    cur = conn.execute(
        "INSERT INTO shopping_list (product_id, amount, unit_id, note, recipe_id, ha_item_name, auto_added, pinned)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (body.product_id, body.amount, body.unit_id, body.note, body.recipe_id, product["name"],
         int(body.auto_added), int(body.pinned)),
    )
    conn.commit()
    return _one(conn, cur.lastrowid)


@router.put("/shopping-list/{item_id}", response_model=ShoppingItem)
def update_shopping_item(item_id: int, body: ShoppingItemUpdate):
    conn = _get_db()
    existing = conn.execute("SELECT * FROM shopping_list WHERE id = ?", (item_id,)).fetchone()
    if not existing:
        raise HTTPException(404, f"Shopping list item {item_id} not found")

    updates = {}
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is None and field not in ("unit_id", "note"):
            continue  # only a unit or a note can be cleared
        if field in ("done", "pinned"):
            value = int(value)
        if field == "note" and value is None:
            value = ""
        updates[field] = value
    if updates.get("unit_id") is not None and not conn.execute(
        "SELECT 1 FROM units WHERE id = ?", (updates["unit_id"],)
    ).fetchone():
        raise HTTPException(400, f"Unit {updates['unit_id']} not found")
    # Pinning is a product-level preference, not a row attribute: route it to
    # products.pin_brand so it persists across re-adds and applies to every
    # row of the product. The stored shopping_list.pinned is left untouched.
    pin = updates.pop("pinned", None)
    if pin is not None:
        conn.execute("UPDATE products SET pin_brand = ? WHERE id = ?",
                     (pin, existing["product_id"]))
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE shopping_list SET {set_clause} WHERE id = ?",
            list(updates.values()) + [item_id],
        )
    conn.commit()
    return _one(conn, item_id)


def _swapped_quantity(cat: Catalog, row: dict, target_id: int) -> tuple[float, int | None]:
    """The row's quantity carried over to another product: retail packages
    stay packages, natural pieces become whole packages of a SKU, and mass
    or volume stays as it is."""
    amount = float(row["amount"])
    side = row_unit(cat, row["product_id"], row["unit_id"])
    if side is None:
        return amount, row["unit_id"]
    unit, dim = side
    if dim == PACKAGE:
        return amount, None
    kpl = cat.unit_by_abbr("kpl")
    if dim == COUNT and cat.is_sku(target_id) and kpl is not None:
        conv = convert(amount, unit, kpl, cat.facts(target_id), from_dim=COUNT, to_dim=PACKAGE)
        return float(max(1, math.ceil((conv.amount if conv else amount) - 1e-9))), None
    return amount, row["unit_id"]


@router.post("/shopping-list/{item_id}/swap", response_model=ShoppingItem)
def swap_shopping_item(item_id: int, body: ShoppingSwap):
    """Buy something else for a row (spec §4.8); amount, unit, recipe, bundle
    and note stay. A SKU under the row's product becomes the row's preferred
    product, so the requirement stays and the buy hint follows it; the row's
    own product clears that preference. Any other product takes the row over
    (see _swapped_quantity). A minimum-stock row belongs to its product, so it
    can only take a preferred product (409 otherwise)."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM shopping_list WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Shopping list item {item_id} not found")
    cat = Catalog(conn)
    target = cat.product(body.product_id)
    if target is None:
        raise HTTPException(400, f"Product {body.product_id} not found")
    if body.product_id == row["product_id"]:
        conn.execute("UPDATE shopping_list SET preferred_product_id = NULL WHERE id = ?", (item_id,))
    elif cat.is_sku(body.product_id) and body.product_id in cat.descendants(row["product_id"]):
        conn.execute(
            "UPDATE shopping_list SET preferred_product_id = ? WHERE id = ?", (body.product_id, item_id)
        )
    else:
        if row["auto_added"]:
            raise HTTPException(
                409, "A minimum-stock row belongs to its product; pick a product under it instead"
            )
        amount, unit_id = _swapped_quantity(cat, row, body.product_id)
        conn.execute(
            "UPDATE shopping_list SET product_id = ?, preferred_product_id = NULL, amount = ?, "
            "unit_id = ?, ha_item_name = ? WHERE id = ?",
            (body.product_id, amount, unit_id, target["name"], item_id),
        )
    conn.commit()
    return _one(conn, item_id)


@router.delete("/shopping-list/{item_id}", status_code=204)
def delete_shopping_item(item_id: int):
    conn = _get_db()
    if not conn.execute("SELECT id FROM shopping_list WHERE id = ?", (item_id,)).fetchone():
        raise HTTPException(404, f"Shopping list item {item_id} not found")
    conn.execute("DELETE FROM shopping_list WHERE id = ?", (item_id,))
    conn.commit()
