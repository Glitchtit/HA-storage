"""Background AI follow-ups that keep concepts connected (spec §4.6 steps 6-7).

place           a concept with nothing under it is merged into the concept it
                duplicates ("Spaghetti" -> "Spagetti") or moved under the
                broader concept it is a kind of ("Terttutomaatti" -> "Tomaatti").
adopt           retail products (SKUs) that ARE the concept move under it.
assign_groups   a concept takes its descendants' majority aisle group, else
                its parent's, else one the AI picks from the existing groups.
estimate_facts  density or piece weight that a recipe unit needs, if unknown.

Each step makes one AI call per batch. High confidence acts; medium only
proposes (link_proposals) or does nothing; an AI failure changes nothing.
Writes run under CATALOG_LOCK, AI calls never do. run_followups chains the
steps for one import, and schedule_followups runs that on a daemon thread
with its own connection. run_sweep does the same for the whole catalog
(spec §6 steps 9-10); it runs once in the background after an upgrade and
on demand from POST /api/concepts/sweep.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Callable

import tree
from concepts import norm
from locks import CATALOG_LOCK
from quantity import COUNT, MASS, VOLUME, plausible_density

log = logging.getLogger(__name__)

AiFn = Callable[..., Any]
_BATCH = 15
_MAX_CANDIDATES = 25


def _ask(ai: AiFn | None, prompt: str, conn: sqlite3.Connection) -> list[dict]:
    """Rows of the AI's JSON array answer; [] on any failure."""
    if ai is None:
        return []
    try:
        answer = ai(prompt, conn)
    except Exception as exc:
        log.warning("Concept AI call failed: %s", exc)
        return []
    return [r for r in answer if isinstance(r, dict)] if isinstance(answer, list) else []


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _concept(conn: sqlite3.Connection, pid: int):
    return conn.execute(
        "SELECT * FROM products WHERE id = ? AND kind = 'concept'", (pid,)
    ).fetchone()


def _propose(conn: sqlite3.Connection, product_id: int, parent_id: int) -> bool:
    cur = conn.execute(
        "INSERT OR IGNORE INTO link_proposals (product_id, proposed_parent_id, confidence, status) "
        "VALUES (?, ?, 'medium', 'pending')",
        (product_id, parent_id),
    )
    return cur.rowcount == 1


# ── adopt ────────────────────────────────────────────────────────────────

_ADOPT_PROMPT = """You keep a household grocery catalog tidy.
Each CONCEPT is a generic ingredient with a Finnish name.
Decide which candidate products ARE the concept: any brand, size or package of
the same thing counts ("Pirkka vehnäjauho 2kg" is "Vehnäjauho"). A different
thing does not ("Voileipäkeksi" is not "Voi", "Kermaviili" is not "Kerma").
When unsure, use lower confidence.

Concepts and their candidates (JSON):
{batch_json}

Return ONLY a JSON array of matches:
[{{"concept_id": <id>, "product_id": <candidate id>, "confidence": "high"|"medium"|"low"}}]
"""


def _words(name: str) -> set[str]:
    """The concept's words worth matching: letters only, three or more."""
    return {w for w in norm(name).split() if len(w) >= 3 and w.isalpha()}


def _mentions(sku_name: str, words: set[str]) -> bool:
    """A word of four or more letters may sit inside a compound ("hillosokeri"
    in "Dansukker hillosokeri"); a three-letter word must stand alone ("voi"
    in "Valio voi 500g", not in "Voileipäkeksi")."""
    name = norm(sku_name)
    return any(w in name if len(w) >= 4 else re.search(rf"(?<!\w){w}(?!\w)", name)
               for w in words)


def _candidates(concept, skus: list) -> list[dict]:
    """SKUs whose name mentions one of the concept's words, then SKUs that
    sit under the concept's own parent."""
    words = _words(concept["name"])
    named, siblings = [], []
    for s in skus:
        if s["parent_id"] == concept["id"]:
            continue
        if words and _mentions(s["name"], words):
            named.append(s)
        elif concept["parent_id"] is not None and s["parent_id"] == concept["parent_id"]:
            siblings.append(s)
    return [{"id": s["id"], "name": s["name"]} for s in (named + siblings)[:_MAX_CANDIDATES]]


def adopt(conn: sqlite3.Connection, concept_ids: list[int], ai: AiFn | None) -> dict:
    """Move SKUs that are the concept under it. A SKU only moves when it is
    unparented or sits under one of the concept's ancestors (a move deeper
    into its own branch); it is never taken from another concept."""
    from linker import apply_link

    stats = {"adopted": 0, "proposed": 0}
    skus = conn.execute(
        "SELECT id, name, parent_id FROM products WHERE kind = 'sku'"
    ).fetchall()
    batch = []
    for cid in concept_ids:
        concept = _concept(conn, cid)
        if concept is not None:
            cands = _candidates(concept, skus)
            if cands:
                batch.append({"concept_id": cid, "concept": concept["name"], "candidates": cands})
    for i in range(0, len(batch), _BATCH):
        chunk = batch[i:i + _BATCH]
        offered = {(b["concept_id"], c["id"]) for b in chunk for c in b["candidates"]}
        rows = _ask(ai, _ADOPT_PROMPT.format(batch_json=json.dumps(chunk, ensure_ascii=False)), conn)
        for row in rows:
            cid, sku = _int(row.get("concept_id")), _int(row.get("product_id"))
            conf = str(row.get("confidence", "")).lower()
            if (cid, sku) not in offered or conf not in ("high", "medium"):
                continue
            with CATALOG_LOCK:
                concept = _concept(conn, cid)
                prod = conn.execute(
                    "SELECT parent_id, kind FROM products WHERE id = ?", (sku,)
                ).fetchone()
                if concept is None or prod is None or prod["kind"] != "sku":
                    continue
                if prod["parent_id"] is not None and prod["parent_id"] not in tree.ancestor_ids(conn, cid):
                    continue
                try:
                    if conf == "high":
                        apply_link(conn, sku, cid, note=f"adopted by {concept['name']}")
                        stats["adopted"] += 1
                    elif _propose(conn, sku, cid):
                        conn.commit()
                        stats["proposed"] += 1
                except ValueError as exc:
                    conn.rollback()
                    log.warning("Adopt %s -> %s refused: %s", sku, cid, exc)
    return stats


# ── place ────────────────────────────────────────────────────────────────

_PLACE_PROMPT = """These generic ingredient CONCEPTS in a household grocery
catalog have no products under them yet. Decide how each relates to the
existing concepts:
- "same": the same ingredient as an existing concept, in another spelling or
  language -> that concept's id
- "kind_of": a more specific kind of an existing concept ("Terttutomaatti" is
  a kind of "Tomaatti") -> that concept's id
- "none": neither

Concepts to place (JSON):
{todo_json}

Existing concepts (JSON):
{existing_json}

Return ONLY a JSON array:
[{{"id": <concept to place>, "relation": "same"|"kind_of"|"none", "target_id": <existing concept id or null>, "confidence": "high"|"medium"|"low"}}]
"""


def _alike(a: str, b: str) -> bool:
    """Names close enough for a 'same' merge: another spelling, not another
    ingredient. Guards the merge against a slipped or hallucinated id."""
    na, nb = norm(a), norm(b)
    return bool(na and nb) and (na in nb or nb in na or SequenceMatcher(None, na, nb).ratio() >= 0.6)


def place(conn: sqlite3.Connection, concept_ids: list[int], ai: AiFn | None) -> dict:
    """Merge or re-parent concepts that have nothing under them. A target
    must be one of the existing concepts the prompt listed, and a 'same'
    merge also needs alike names; every merge is logged at WARNING."""
    from linker import apply_link
    from merge import merge_products

    stats = {"merged": 0, "reparented": 0, "proposed": 0}
    todo = []
    for cid in concept_ids:
        concept = _concept(conn, cid)
        if concept is not None and not tree.descendant_ids(conn, cid):
            todo.append({"id": cid, "name": concept["name"]})
    if not todo:
        return stats
    names = {r["id"]: r for r in conn.execute(
        "SELECT id, name, parent_id FROM products WHERE kind = 'concept'").fetchall()}
    todo_ids = {t["id"] for t in todo}
    existing = [
        {"id": cid, "name": r["name"],
         **({"parent": names[r["parent_id"]]["name"]} if r["parent_id"] in names else {})}
        for cid, r in sorted(names.items()) if cid not in todo_ids
    ][:1500]
    for i in range(0, len(todo), _BATCH):
        chunk = todo[i:i + _BATCH]
        rows = _ask(ai, _PLACE_PROMPT.format(
            todo_json=json.dumps(chunk, ensure_ascii=False),
            existing_json=json.dumps(existing, ensure_ascii=False)), conn)
        asked = {t["id"] for t in chunk}
        offered = {e["id"] for e in existing}
        for row in rows:
            cid, target = _int(row.get("id")), _int(row.get("target_id"))
            relation = str(row.get("relation", "")).lower()
            conf = str(row.get("confidence", "")).lower()
            if cid not in asked or target not in offered or target == cid:
                continue
            with CATALOG_LOCK:
                concept, tgt = _concept(conn, cid), _concept(conn, target)
                if concept is None or tgt is None:
                    continue
                try:
                    if relation == "same" and conf == "high":
                        if not _alike(concept["name"], tgt["name"]):
                            log.warning("Concept placement: left %r apart from %r (names differ)",
                                        concept["name"], tgt["name"])
                            continue
                        merge_products(conn, cid, target)
                        log.warning("Concept placement merged %r (#%s) into %r (#%s)",
                                    concept["name"], cid, tgt["name"], target)
                        stats["merged"] += 1
                    elif relation == "kind_of" and conf == "high":
                        if concept["parent_id"] is None or concept["parent_id"] in tree.ancestor_ids(conn, target):
                            apply_link(conn, cid, target, note=f"placed under {tgt['name']}")
                            stats["reparented"] += 1
                    elif relation == "kind_of" and conf == "medium":
                        if _propose(conn, cid, target):
                            conn.commit()
                            stats["proposed"] += 1
                except ValueError as exc:
                    conn.rollback()
                    log.warning("Placing concept %s failed: %s", cid, exc)
    return stats


# ── aisle groups ─────────────────────────────────────────────────────────

_GROUPS_PROMPT = """Put each grocery ingredient CONCEPT of a household catalog
into the best aisle group. Use only the groups listed.

Concepts (JSON):
{concepts_json}

Groups (JSON):
{groups_json}

Return ONLY a JSON array:
[{{"id": <concept id>, "group_id": <group id or null>}}]
"""


def assign_groups(conn: sqlite3.Connection, concept_ids: list[int], ai: AiFn | None) -> int:
    """Give ungrouped concepts an aisle group. Returns how many changed."""
    groups = {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM product_groups WHERE name != 'Group master'").fetchall()}
    changed = 0
    ask = []
    with CATALOG_LOCK:
        for cid in concept_ids:
            concept = _concept(conn, cid)
            if concept is None or concept["product_group_id"] in groups:
                continue
            desc = tree.descendant_ids(conn, cid)
            counts: Counter = Counter()
            if desc:
                marks = ",".join("?" * len(desc))
                counts = Counter(
                    r["product_group_id"] for r in conn.execute(
                        f"SELECT product_group_id FROM products WHERE id IN ({marks})", desc
                    ).fetchall()
                    if r["product_group_id"] in groups
                )
            gid = counts.most_common(1)[0][0] if counts else None
            if gid is None and concept["parent_id"] is not None:
                parent = conn.execute(
                    "SELECT product_group_id FROM products WHERE id = ?", (concept["parent_id"],)
                ).fetchone()
                if parent and parent["product_group_id"] in groups:
                    gid = parent["product_group_id"]
            if gid is None:
                ask.append({"id": cid, "name": concept["name"]})
                continue
            conn.execute("UPDATE products SET product_group_id = ? WHERE id = ?", (gid, cid))
            changed += 1
        conn.commit()
    if not ask or not groups:
        return changed
    group_list = [{"id": gid, "name": name} for gid, name in sorted(groups.items())]
    for i in range(0, len(ask), 50):
        chunk = ask[i:i + 50]
        rows = _ask(ai, _GROUPS_PROMPT.format(
            concepts_json=json.dumps(chunk, ensure_ascii=False),
            groups_json=json.dumps(group_list, ensure_ascii=False)), conn)
        asked = {c["id"] for c in chunk}
        with CATALOG_LOCK:
            for row in rows:
                cid, gid = _int(row.get("id")), _int(row.get("group_id"))
                if cid in asked and gid in groups:
                    cur = conn.execute(
                        "UPDATE products SET product_group_id = ? WHERE id = ? AND kind = 'concept' "
                        "AND (product_group_id IS NULL OR product_group_id NOT IN "
                        "(SELECT id FROM product_groups WHERE name != 'Group master'))",
                        (gid, cid),
                    )
                    changed += cur.rowcount
            conn.commit()
    return changed


# ── facts ────────────────────────────────────────────────────────────────

_FACTS_PROMPT = """Estimate kitchen facts for these Finnish grocery ingredients.
- density_g_ml: grams per millilitre as measured in a kitchen (vehnäjauho
  about 0.55, sokeri about 0.85, voi about 0.95, maito about 1.03); null if
  unknown.
- piece_weight_g: typical weight in grams of one natural piece (kananmuna
  about 60, sipuli about 100, valkosipulinkynsi about 5); null if it has no
  natural piece.

Ingredients (JSON):
{items_json}

Return ONLY a JSON array:
[{{"id": <id>, "density_g_ml": <number or null>, "piece_weight_g": <number or null>}}]
"""


def _with_meta(meta_json: str | None, key: str) -> str:
    try:
        meta = json.loads(meta_json or "{}")
        if not isinstance(meta, dict):
            meta = {}
    except ValueError:
        meta = {}
    meta[key] = "ai"
    return json.dumps(meta, sort_keys=True)


def estimate_facts(conn: sqlite3.Connection, needs: list[tuple[int, str]], ai: AiFn | None) -> int:
    """needs: (concept id, dimension of a recipe need in it). Estimates the
    density a mass/volume need lacks and the piece weight a count need lacks
    (own or inherited). Never overwrites a value. Returns facts written."""
    from facts import Catalog

    cat = Catalog(conn)
    want: dict[int, set[str]] = {}
    for cid, dim in needs:
        prod = cat.product(cid)
        if not prod or prod["kind"] != "concept":
            continue
        f = cat.facts(cid)
        if dim in (MASS, VOLUME) and f.density_g_ml is None:
            want.setdefault(cid, set()).add("density")
        if dim == COUNT and f.piece_weight_g is None:
            want.setdefault(cid, set()).add("piece_weight")
    if not want or ai is None:
        return 0
    items = [{"id": cid, "name": cat.product(cid)["name"],
              "need": "both" if len(w) == 2 else next(iter(w))}
             for cid, w in sorted(want.items())]
    written = 0
    for i in range(0, len(items), 50):
        rows = _ask(ai, _FACTS_PROMPT.format(
            items_json=json.dumps(items[i:i + 50], ensure_ascii=False)), conn)
        with CATALOG_LOCK:
            for row in rows:
                cid = _int(row.get("id"))
                if cid not in want:
                    continue
                for key, col, ok in (
                    ("density", "density_g_ml", plausible_density),
                    ("piece_weight", "piece_weight_g", lambda v: v is not None and 0 < v <= 10000),
                ):
                    if key not in want[cid]:
                        continue
                    try:
                        value = float(row.get(col))
                    except (TypeError, ValueError):
                        continue
                    if not ok(value):
                        continue
                    meta = conn.execute(
                        "SELECT facts_meta FROM products WHERE id = ?", (cid,)
                    ).fetchone()["facts_meta"]
                    cur = conn.execute(
                        f"UPDATE products SET {col} = ?, facts_meta = ? WHERE id = ? AND {col} IS NULL",
                        (round(value, 4), _with_meta(meta, col), cid),
                    )
                    written += cur.rowcount
            conn.commit()
    return written


# ── orchestration ────────────────────────────────────────────────────────

def run_followups(conn: sqlite3.Connection, created: list[int],
                  needs: list[tuple[int, str]], ai: AiFn | None) -> dict:
    """After one import: place new top-level concepts, adopt SKUs for every
    new concept, group them, then fill missing facts for every bound need."""
    if ai is None:
        return {}

    def alive(ids):
        return [c for c in ids if _concept(conn, c) is not None]

    top = [c for c in alive(created) if _concept(conn, c)["parent_id"] is None]
    out = {"place": place(conn, top, ai)}
    out["adopt"] = adopt(conn, alive(created), ai)
    out["grouped"] = assign_groups(conn, alive(created), ai)
    out["facts"] = estimate_facts(conn, [(c, d) for c, d in needs if _concept(conn, c)], ai)
    return out


def _followup_job(created: list[int], needs: list[tuple[int, str]]) -> None:
    from ai_client import call_ai_json
    from database import get_db
    from main import DB_PATH
    from routers.products import _ai_configured

    conn = get_db(DB_PATH)
    try:
        if _ai_configured(conn):
            log.info("Concept follow-ups: %s", run_followups(conn, created, needs, call_ai_json))
    except Exception as exc:
        log.warning("Concept follow-ups failed: %s", exc)
    finally:
        conn.close()


def schedule_followups(created: list[int], needs: list[tuple[int, str]]) -> None:
    """Run run_followups off the request path, on its own connection."""
    if created or needs:
        threading.Thread(target=_followup_job, args=(list(created), list(needs)),
                         daemon=True).start()


# ── whole-catalog sweep ──────────────────────────────────────────────────

SWEEP_FLAG = "concepts_sweep_v1"
_sweep_state: dict = {"running": False, "result": None, "error": None, "finished_at": None}
_sweep_guard = threading.Lock()


def _isolated(conn: sqlite3.Connection) -> list[int]:
    return [r["id"] for r in conn.execute(
        "SELECT p.id FROM products p WHERE p.kind = 'concept' "
        "AND NOT EXISTS (SELECT 1 FROM products c WHERE c.parent_id = p.id) ORDER BY p.id"
    ).fetchall()]


def run_sweep(conn: sqlite3.Connection, ai: AiFn | None) -> dict:
    """Place, then adopt, every concept with nothing under it; group every
    ungrouped concept; estimate the facts recipe needs lack; finally merge
    any twins the placement revealed."""
    from concepts import dedup_concepts

    out = {"place": place(conn, _isolated(conn), ai)}
    out["adopt"] = adopt(conn, _isolated(conn), ai)
    # Twins that were both childless could not target each other above; once
    # one of them has adopted products it is an existing concept to merge into.
    out["place_again"] = place(conn, _isolated(conn), ai)
    out["grouped"] = assign_groups(conn, [r["id"] for r in conn.execute(
        "SELECT id FROM products WHERE kind = 'concept' ORDER BY id").fetchall()], ai)
    needs = [(r["product_id"], r["dimension"]) for r in conn.execute(
        "SELECT DISTINCT ri.product_id, u.dimension FROM recipe_ingredients ri "
        "JOIN units u ON u.id = ri.unit_id JOIN products p ON p.id = ri.product_id "
        "WHERE p.kind = 'concept' AND ri.amount > 0"
    ).fetchall()]
    out["facts"] = estimate_facts(conn, needs, ai)
    out["merged"] = len(dedup_concepts(conn))
    return out


def _sweep_job() -> None:
    from ai_client import call_ai_json
    from database import get_db
    from main import DB_PATH
    from routers.products import _ai_configured

    result, error = None, None
    try:
        conn = get_db(DB_PATH)
        try:
            if not _ai_configured(conn):
                result = {"skipped": "no AI provider"}
            else:
                result = run_sweep(conn, call_ai_json)
                conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)",
                             (SWEEP_FLAG, json.dumps(result)))
                conn.commit()
                log.info("Concept sweep: %s", result)
        finally:
            conn.close()
    except Exception as exc:
        log.warning("Concept sweep failed: %s", exc)
        error = str(exc)
    with _sweep_guard:
        _sweep_state.update(running=False, result=result, error=error,
                            finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def start_sweep() -> bool:
    """Start the whole-catalog sweep on a background thread unless one is
    already running. Returns True when started."""
    with _sweep_guard:
        if _sweep_state["running"]:
            return False
        _sweep_state.update(running=True, result=None, error=None)
    threading.Thread(target=_sweep_job, daemon=True, name="concept-sweep").start()
    return True


def start_sweep_if_due() -> bool:
    """Start the sweep once per upgrade: until it has completed with an AI
    provider configured, every start tries again."""
    from main import get_connection

    if get_connection().execute("SELECT 1 FROM _meta WHERE key = ?", (SWEEP_FLAG,)).fetchone():
        return False
    return start_sweep()


def sweep_status() -> dict:
    with _sweep_guard:
        return dict(_sweep_state)
