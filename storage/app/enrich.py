"""Enrichment: give a retail product (SKU) its place and its attributes.

enrich    for a list of products: the linker places them in the tree (one AI
          call), a multi-pack merges into its single-unit SKU when that
          exists (no AI), and one AI call per batch proposes aisle group,
          storage location and best-before days. The AI picks only from
          the groups and locations that exist; it never creates one.
apply     fills empty fields. With overwrite it also replaces values the AI
          set or nobody in particular did, never ones a person set by hand
          (facts_meta keys "group", "location", "shelf_life" = "manual").
pending   active SKUs the AI has never looked at (ai_enriched_at IS NULL)
start     drains `pending` on a daemon thread, single-flight. Product
          creation, purchases and the periodic sync call it, so a burst of
          scans is handled a batch at a time and an outage is retried. A
          purchase of a product still loose in the tree queues it again.
run       the manual job behind /api/ai/optimize and /api/ai/fix-expiry

An AI outage records nothing against a product: the run stops and no queue
run starts for OUTAGE_BACKOFF_S. The AI callable is injected (call_ai_json
in production).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from ai_client import INT, AiConfigError, AiTruncated, arr, get_batch_size, nullable, obj
from locks import CATALOG_LOCK
from merge import merge_products
from pack_size import single_unit_name

log = logging.getLogger(__name__)

AiFn = Callable[..., Any]
Emit = Callable[[str], None] | None

FIELDS = ("group", "location", "shelf_life")
OUTAGE_BACKOFF_S = 15 * 60
# A new product's creator still adds its barcode and picture right after the
# create; waiting lets that finish and lets a burst of scans share a batch.
# shortcut: a fixed delay, not a signal from the creator; replace it if a
# creator ever takes longer than this between create and barcode.
DEBOUNCE_S = 2.0

# Anything above 10 years is an AI hallucination; treat 0..3650 as sane.
_MAX_BB_DAYS = 3650
# The hardcoded default every product was created with before the AI ever
# saw it (database.py column default, scraper StorageClient). It counts as
# "not set" until something says otherwise, and lots carrying this exact
# snapshot are treated as inherited, never as manual overrides.
_LEGACY_DEFAULT_BB_DAYS = 60
# A "pack" of more than this is package contents, not a consumer multi-pack.
_MAX_PACK = 24

# Aisle groups for a catalog that has none yet (the aisles HA-stock sorts by).
_SEED_GROUPS = (
    "Hedelmät & vihannekset", "Leipä & leivonnaiset", "Maitotuotteet", "Liha & kala",
    "Eineet", "Pakaste", "Kuivamuonat", "Makeiset & naposteltavat", "Juomat",
    "Alkoholi", "Pesuaineet & kodinhoito", "Hygienia & kosmetiikka", "Vauva & lemmikki",
)

DEFAULT_HOUSE_RULES = (
    "Drinks of every kind go in the refrigerator. Dairy, meat, fish and fresh "
    "produce go in the refrigerator. Eggs, dry goods, canned and packaged food "
    "go in the pantry. Cleaning and laundry products go in the cleaning "
    "cabinet. Hygiene and personal-care items go in the bathroom."
)

_PROMPT = """You keep a Finnish household's grocery catalog. For each product
below, choose its aisle group, where it is stored at home, and how many days
an unopened package keeps.

Aisle groups (JSON). Use only these ids; null when none fits:
{groups_json}

Storage locations (JSON). Use only these ids; null when you cannot tell:
{locations_json}

How this household stores things:
{house_rules}

Best-before days, for an unopened package:
- 0 means it does not expire in practice: paper goods, cleaning and laundry
  products, hygiene consumables, candles, batteries and other non-food
  household goods.
- Typical food values: fresh milk 10, yogurt and fresh dairy 21, fresh meat
  and fish 4, bread 7, eggs 28, butter 90, hard cheese 180, fresh produce 10,
  cereal and crackers 365, sweets 365, soft drinks and juice 270, beer and
  cider 270, wine and spirits 1825, coffee and tea 540, condiments and sauces
  540, oil 540, spices 1095, dry pasta, rice, flour and sugar 1095, canned
  goods 1095, frozen food 365, supplements 730, cosmetics 1095.
- A shelf-stable product keeps at least 180 days. Between two ranges, take
  the longer one.

Products (JSON). "group", "location" and "days" show what is set today; keep
them unless they are clearly wrong:
{products_json}

Return a JSON object {{"items": [...]}} with one entry per product:
{{"id": <product id>, "group_id": <group id or null>, "location_id": <location id or null>, "best_before_days": <integer>}}
"""

_SCHEMA = arr(obj(id=INT, group_id=nullable(INT), location_id=nullable(INT),
                  best_before_days=nullable(INT)))


# ── small helpers ────────────────────────────────────────────────────────

def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sanitize_bb_days(value: Any) -> int | None:
    """Normalise an AI best_before_days value; None when unusable."""
    days = _int(value)
    if days is None or days < 0:
        return None
    return min(days, _MAX_BB_DAYS)


def _meta(raw: str | None) -> dict:
    try:
        meta = json.loads(raw or "{}")
    except ValueError:
        return {}
    return meta if isinstance(meta, dict) else {}


def _shelf_life_unset(product: dict) -> bool:
    """No best-before days yet, or only the placeholder every product is
    created with."""
    days = product["default_best_before_days"]
    return days is None or (
        days == _LEGACY_DEFAULT_BB_DAYS and "shelf_life" not in _meta(product["facts_meta"]))


def _emitter(emit: Emit) -> Callable[..., None]:
    def say(msg: str, *args: Any) -> None:
        text = msg % args if args else msg
        log.info(text)
        if emit:
            emit(text)
    return say


def _groups(conn: sqlite3.Connection) -> dict[int, str]:
    return {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM product_groups WHERE name != 'Group master'").fetchall()}


def ensure_groups(conn: sqlite3.Connection, names: list[str] | tuple[str, ...]) -> None:
    """Create the named product groups that do not exist yet."""
    for name in names:
        name = (name or "").strip()
        if name:
            conn.execute(
                "INSERT INTO product_groups (name) SELECT ? WHERE NOT EXISTS "
                "(SELECT 1 FROM product_groups WHERE name = ?)", (name, name))
    conn.commit()


def house_rules(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM config WHERE key = 'ai_house_rules'").fetchone()
    return (row["value"] if row and row["value"] else "").strip() or DEFAULT_HOUSE_RULES


def _load(conn: sqlite3.Connection, ids: list[int]) -> list[dict]:
    """The SKUs among `ids`, in id order."""
    out: list[dict] = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        out += [dict(r) for r in conn.execute(
            "SELECT id, name, parent_id, location_id, product_group_id, "
            "default_best_before_days, facts_meta FROM products "
            f"WHERE kind = 'sku' AND id IN ({','.join('?' * len(chunk))})", chunk).fetchall()]
    return sorted(out, key=lambda p: p["id"])


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


# ── multi-packs (no AI) ──────────────────────────────────────────────────

def _merge_multipack(conn: sqlite3.Connection, product: dict,
                     sku_by_name: dict[str, int], say: Callable[..., None]) -> bool:
    """Merge "X 6x0,33l" into the existing SKU "X 0,33l": its barcode then
    adds six units per scan and held packs become units. With no such SKU the
    pack stays its own product: pack_size.py already records how many pieces
    a package holds, so nothing is renamed or multiplied."""
    from linker import _norm

    single = single_unit_name(product["name"])
    if single is None or not 1 < single[1] <= _MAX_PACK:
        return False
    base_name, count = single
    base_id = sku_by_name.get(_norm(base_name))
    pid = product["id"]
    if base_id is None or base_id == pid:
        return False
    codes = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(COALESCE(pack_size, 1) != 1), 0) AS sized "
        "FROM barcodes WHERE product_id = ?", (pid,)).fetchone()
    # No barcode yet: nothing to gain. A barcode that already carries a pack
    # size means the stock is counted in units already.
    if not codes["n"] or codes["sized"]:
        return False
    try:
        conn.execute("UPDATE barcodes SET pack_size = ? WHERE product_id = ?", (count, pid))
        conn.execute(
            "UPDATE stock SET amount = amount * ?, "
            "amount_opened = COALESCE(amount_opened, 0) * ?, "
            "price_paid = price_paid / ? WHERE product_id = ?",  # per unit now
            (count, count, count, pid))
        merge_products(conn, pid, base_id)  # moves everything else, commits
    except Exception as exc:
        conn.rollback()
        say("  ! Could not merge '%s' into '%s': %s", product["name"], base_name, exc)
        return False
    say("  -> Merged multi-pack '%s' into '%s' (pack of %d).", product["name"], base_name, count)
    return True


# ── propose and apply ────────────────────────────────────────────────────

def _propose(conn: sqlite3.Connection, batch: list[dict], groups: dict[int, str],
             locations: dict[int, str], rules: str, ai: AiFn, emit: Emit) -> dict[int, dict]:
    """Ask the AI about one batch: product id -> its answer row. A cut-off
    answer is asked for again in two halves."""
    products = [
        {"id": p["id"], "name": p["name"],
         **({"group": groups[p["product_group_id"]]} if p["product_group_id"] in groups else {}),
         **({"location": locations[p["location_id"]]} if p["location_id"] in locations else {}),
         **({} if _shelf_life_unset(p) else {"days": p["default_best_before_days"]})}
        for p in batch
    ]
    prompt = _PROMPT.format(
        groups_json=json.dumps([{"id": i, "name": n} for i, n in sorted(groups.items())],
                               ensure_ascii=False),
        locations_json=json.dumps([{"id": i, "name": n} for i, n in sorted(locations.items())],
                                  ensure_ascii=False),
        house_rules=rules,
        products_json=json.dumps(products, ensure_ascii=False),
    )
    try:
        rows = ai(prompt, conn, schema=_SCHEMA, emit=emit)
    except AiTruncated:
        if len(batch) == 1:
            raise
        mid = len(batch) // 2
        return {**_propose(conn, batch[:mid], groups, locations, rules, ai, emit),
                **_propose(conn, batch[mid:], groups, locations, rules, ai, emit)}
    wanted = {p["id"] for p in batch}
    out: dict[int, dict] = {}
    for row in rows if isinstance(rows, list) else []:
        pid = _int(row.get("id")) if isinstance(row, dict) else None
        if pid in wanted:
            out[pid] = row
    return out


def _needs(product: dict, fields: tuple[str, ...]) -> bool:
    """Whether a fill (no overwrite) has anything to ask about this product."""
    meta = _meta(product["facts_meta"])
    return any(key in fields and meta.get(key) != "manual" and empty for key, empty in (
        ("group", product["product_group_id"] is None),
        ("location", product["location_id"] is None),
        ("shelf_life", _shelf_life_unset(product)),
    ))


def _decide(product: dict, row: dict, groups: dict[int, str], locations: dict[int, str],
            *, overwrite: bool, fields: tuple[str, ...]) -> dict:
    """What to write for one product: {"sets": {column: value}, "meta": {...},
    "days": evaluated shelf life or None}. A field is written when it is empty,
    or when overwrite is on and no person set it."""
    meta = _meta(product["facts_meta"])

    def may(key: str, empty: bool) -> bool:
        return key in fields and meta.get(key) != "manual" and (empty or overwrite)

    sets: dict = {}
    gid = _int(row.get("group_id"))
    if gid in groups and may("group", product["product_group_id"] is None):
        meta["group"] = "ai"
        if gid != product["product_group_id"]:
            sets["product_group_id"] = gid

    loc = _int(row.get("location_id"))
    if loc in locations and may("location", product["location_id"] is None):
        meta["location"] = "ai"
        if loc != product["location_id"]:
            sets["location_id"] = loc

    days = _sanitize_bb_days(row.get("best_before_days"))
    current = product["default_best_before_days"]
    if days is None or not may("shelf_life", _shelf_life_unset(product)):
        days = None
    else:
        meta["shelf_life"] = "ai"  # looked at, also when 60 turns out right
        if days != current:
            sets["default_best_before_days"] = days
    return {"sets": sets, "meta": meta, "days": days}


def _apply(conn: sqlite3.Connection, product: dict, decision: dict,
           groups: dict[int, str], locations: dict[int, str], say: Callable[..., None]) -> int:
    """Write one product's decision. Returns the number of changed fields."""
    sets, pid = decision["sets"], product["id"]
    meta_json = json.dumps(decision["meta"], sort_keys=True) if decision["meta"] else None
    if not sets and meta_json == product["facts_meta"] and decision["days"] is None:
        return 0
    try:
        columns = {**sets, "facts_meta": meta_json}
        conn.execute(
            f"UPDATE products SET {', '.join(f'{c} = ?' for c in columns)} WHERE id = ?",
            [*columns.values(), pid])
        if "location_id" in sets and not product["location_id"]:
            # Stock added before the product had a location sits in the
            # arbitrary fallback location; move it to the real one.
            conn.execute("UPDATE stock SET location_id = ? WHERE product_id = ?",
                         (sets["location_id"], pid))
        if decision["days"] is not None:
            rebased = _rebase_inherited_stock_dates(
                conn, pid, int(product["default_best_before_days"] or 0), decision["days"])
            if rebased:
                say("  -> Rebased %d stock lot(s) of '%s' to %dd.",
                    rebased, product["name"], decision["days"])
        conn.commit()  # per product: a failure further on undoes only itself
    except Exception as exc:
        conn.rollback()
        say("  ! Could not update '%s': %s", product["name"], exc)
        return 0
    if sets:
        parts = []
        if "product_group_id" in sets:
            parts.append(f"group {groups[sets['product_group_id']]}")
        if "location_id" in sets:
            parts.append(f"location {locations[sets['location_id']]}")
        if "default_best_before_days" in sets:
            parts.append(f"{sets['default_best_before_days']} best-before days")
        say("  -> '%s': %s.", product["name"], ", ".join(parts))
    return len(sets)


def enrich(
    conn: sqlite3.Connection,
    ids: list[int],
    ai: AiFn,
    *,
    overwrite: bool = False,
    link: bool = True,
    fields: tuple[str, ...] = FIELDS,
    dry_run: bool = False,
    emit: Emit = None,
) -> dict:
    """Place and fill in the SKUs among `ids`. Returns {"updated": changed
    fields, "merged": multi-packs merged, "proposals": [...]}; a dry run writes
    nothing and lists what it would change. Raises when the AI call for the
    attributes fails; nothing is stamped for that batch."""
    import linker

    say = _emitter(emit)
    result: dict = {"updated": 0, "merged": 0, "proposals": []}
    products = _load(conn, ids)
    if not products:
        return result

    if not dry_run:
        if link:
            orphans = [p["id"] for p in products if p["parent_id"] is None]
            if orphans:
                res = linker.link_products(conn, orphans)
                if res.get("ai_error"):
                    # Nothing is stamped, so these products are tried again.
                    raise ValueError(f"linking failed: {res['ai_error']}")
                say("Linker: %d linked, %d proposed.", len(res["linked"]), len(res["proposed"]))
        if set(fields) == set(FIELDS):
            sku_by_name = {linker._norm(r["name"]): r["id"] for r in conn.execute(
                "SELECT id, name FROM products WHERE kind = 'sku' AND active = 1").fetchall()}
            for p in products:
                if _merge_multipack(conn, p, sku_by_name, say):
                    result["merged"] += 1
        if not _groups(conn):
            ensure_groups(conn, _SEED_GROUPS)
        products = _load(conn, [p["id"] for p in products])  # parents, merges

    groups = _groups(conn)
    locations = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM locations").fetchall()}
    rules = house_rules(conn)
    size = get_batch_size(conn)

    for start in range(0, len(products), size):
        batch = products[start:start + size]
        ask = batch if overwrite else [p for p in batch if _needs(p, fields)]
        rows = _propose(conn, ask, groups, locations, rules, ai, emit) if ask else {}
        # Decide on the rows as they are now, under the lock a PUT takes: a
        # change a person made while the AI was answering wins.
        with CATALOG_LOCK:
            for p in _load(conn, [p["id"] for p in ask]):
                decision = _decide(p, rows.get(p["id"], {}), groups, locations,
                                   overwrite=overwrite, fields=fields)
                if dry_run:
                    if decision["sets"]:
                        result["proposals"].append({"id": p["id"], "name": p["name"], **decision["sets"]})
                    continue
                result["updated"] += _apply(conn, p, decision, groups, locations, say)
            if not dry_run:
                conn.execute(
                    "UPDATE products SET ai_enriched_at = datetime('now') WHERE id IN "
                    f"({','.join('?' * len(batch))})", [p["id"] for p in batch])
                conn.commit()
        say("Batch %d/%d done.", start // size + 1, -(-len(products) // size))
    return result


# ── the manual job ───────────────────────────────────────────────────────

# Held while any enrichment runs, so the queue and a manual job never work
# on the same products at once.
BUSY = threading.Lock()


def missing(conn: sqlite3.Connection) -> list[int]:
    """Active SKUs that lack a group, a location or a real shelf life."""
    return [r["id"] for r in conn.execute(
        "SELECT id FROM products WHERE kind = 'sku' AND active = 1 AND ("
        " product_group_id IS NULL OR location_id IS NULL"
        " OR default_best_before_days IS NULL"
        " OR (default_best_before_days = ? AND COALESCE(facts_meta, '') NOT LIKE '%shelf_life%'))"
        " ORDER BY id", (_LEGACY_DEFAULT_BB_DAYS,)).fetchall()]


def run(
    conn: sqlite3.Connection,
    ai: AiFn,
    *,
    product_ids: list[int] | None = None,
    missing_only: bool = False,
    fields: tuple[str, ...] = FIELDS,
    dry_run: bool = False,
    groups: list[str] | None = None,
    emit: Emit = None,
) -> dict:
    """The job behind the Optimize page.

    product_ids   re-check exactly these (concepts among them get their group
                  from concept_jobs)
    missing_only  every active SKU that lacks a group, a location or a real
                  shelf life; only the empty fields are filled
    neither       re-check every active SKU, then run the linker sweep
    fields        which attributes may be written (fix-expiry: shelf life only)
    groups        product groups to make sure exist first (the user's list)
    """
    say = _emitter(emit)
    with BUSY:
        if groups and not dry_run:
            ensure_groups(conn, groups)
        if product_ids is not None:
            ids = [int(i) for i in product_ids]
        elif missing_only:
            ids = missing(conn)
        else:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM products WHERE kind = 'sku' AND active = 1 ORDER BY id").fetchall()]
        if not ids:
            say("No products to optimize.")
            return {"updated": 0, "merged": 0, "proposals": []}

        say("Starting AI optimize for %d product(s)…", len(ids))
        # A full run leaves linking to the sweep below.
        result = enrich(conn, ids, ai, overwrite=not missing_only,
                        link=(product_ids is not None or missing_only) and set(fields) == set(FIELDS),
                        fields=fields, dry_run=dry_run, emit=emit)
        if not dry_run and set(fields) == set(FIELDS):
            if product_ids is not None:
                concepts = [r["id"] for r in conn.execute(
                    "SELECT id FROM products WHERE kind = 'concept' AND id IN "
                    f"({','.join('?' * len(ids))})", ids).fetchall()]
                if concepts:
                    import concept_jobs
                    result["updated"] += concept_jobs.assign_groups(conn, concepts, ai)
            elif not missing_only:
                import linker
                res = linker.run_reconcile(conn)
                say("Linker sweep: %d linked, %d proposed.", res["linked"], res["proposed"])
        say("Optimize complete — %d field(s) updated.", result["updated"])
        return result


# ── the queue ────────────────────────────────────────────────────────────

_outage_until = 0.0  # time.monotonic() before which no queue run starts
_state: dict = {"running": False, "last_run": None}
_guard = threading.Lock()


_LONE_FAILURES_MAX = 3
_lone_failures: dict[int, int] = {}  # product id -> failed runs as the only one


def pending(conn: sqlite3.Connection, *, settled: bool = False) -> list[int]:
    """Active SKUs the AI has never looked at. settled: only those created
    DEBOUNCE_S ago or earlier, so their creator has added the barcode."""
    age = f" AND (created_at IS NULL OR created_at <= datetime('now', '-{int(DEBOUNCE_S)} seconds'))"
    return [r["id"] for r in conn.execute(
        "SELECT id FROM products WHERE kind = 'sku' AND active = 1 "
        f"AND ai_enriched_at IS NULL{age if settled else ''} ORDER BY id").fetchall()]


def requeue_loose(conn: sqlite3.Connection, product_ids: list[int]) -> None:
    """A purchased product that is still loose in the tree is queued again
    for the linker, then the queue is started. Never raises."""
    try:
        ids = [int(i) for i in product_ids]
        if ids:
            conn.execute(
                "UPDATE products SET ai_enriched_at = NULL WHERE kind = 'sku' AND parent_id IS NULL "
                f"AND id IN ({','.join('?' * len(ids))})", ids)
            conn.commit()
    except Exception as exc:  # noqa: BLE001 — must not fail the purchase
        conn.rollback()
        log.warning("Could not queue purchased products again: %s", exc)
    start()


def _skip(conn: sqlite3.Connection, pid: int, why: str) -> None:
    """Stop asking about one product the AI cannot answer for."""
    conn.execute("UPDATE products SET ai_enriched_at = datetime('now') WHERE id = ?", (pid,))
    conn.commit()
    _lone_failures.pop(pid, None)
    log.warning("Enrichment skipped product %d after repeated failures: %s", pid, why)


def run_pending(conn: sqlite3.Connection, ai: AiFn) -> dict[str, int]:
    """Enrich every settled pending product, a batch at a time, including
    ones that settle meanwhile.

    A failed batch is narrowed down: its first product is asked alone, then
    its last. When the last answers, the AI is up and the first product is
    the problem: it is skipped and the run goes on. When neither answers it
    is an outage: the run stops and no run starts for OUTAGE_BACKOFF_S. A
    product that fails alone in _LONE_FAILURES_MAX runs is skipped too."""
    global _outage_until
    counts = {"enriched": 0, "updated": 0}
    size = get_batch_size(conn)

    def attempt(ids: list[int]) -> bool:
        try:
            res = enrich(conn, ids, ai)
        except AiConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            attempt.error = str(exc)  # type: ignore[attr-defined]
            return False
        counts["enriched"] += len(ids)
        counts["updated"] += res["updated"]
        return True

    try:
        while batch := pending(conn, settled=True)[:size]:
            if attempt(batch):
                continue
            first, last = batch[0], batch[-1]
            if len(batch) > 1 and attempt([first]):
                continue  # the next round asks about the rest
            if len(batch) > 1 and attempt([last]):
                _skip(conn, first, attempt.error)  # type: ignore[attr-defined]
                continue
            _lone_failures[first] = _lone_failures.get(first, 0) + 1
            if _lone_failures[first] >= _LONE_FAILURES_MAX:
                _skip(conn, first, attempt.error)  # type: ignore[attr-defined]
            raise ValueError(attempt.error)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — an AI failure is an outage
        _outage_until = time.monotonic() + OUTAGE_BACKOFF_S
        left = len(pending(conn))
        log.warning("Enrichment stopped, %d product(s) left for later: %s", left, exc)
        counts["failed"] = left
    return counts


def _job() -> None:
    from ai_client import call_ai_json
    from database import get_db
    from main import DB_PATH

    result: dict = {}
    try:
        conn = get_db(DB_PATH)
        try:
            # Products created while a batch runs settle and are taken next.
            while True:
                time.sleep(DEBOUNCE_S)
                with BUSY:
                    counts = run_pending(conn, call_ai_json)
                result = {k: result.get(k, 0) + v for k, v in counts.items()}
                if "failed" in counts or not pending(conn):
                    break
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — a background run never raises
        log.warning("Enrichment run failed: %s", exc)
        result = {"failed": str(exc)}
    with _guard:
        _state.update(running=False, last_run={
            **result, "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    if result.get("enriched"):
        log.info("Enrichment: %s", result)


def start() -> bool:
    """Drain the queue in the background unless it is running, an outage is
    being waited out, or automatic enrichment is off or has no AI provider.
    True when started. Never raises: every caller must be able to trust this
    cannot fail their write."""
    try:
        from main import get_connection
        from routers.products import _autoplace_enabled

        conn = get_connection()
        if time.monotonic() < _outage_until or not _autoplace_enabled(conn) or not pending(conn):
            return False
        with _guard:
            if _state["running"]:
                return False
            _state["running"] = True
        threading.Thread(target=_job, daemon=True, name="enrich").start()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Enrichment did not start: %s", exc)
        return False
