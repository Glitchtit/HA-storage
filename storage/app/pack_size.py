"""Parse package size out of Finnish product names and store it as SKU facts.

"Pirkka babypinaatti 65g"            -> content 65 g
"Pirkka Luomu kananmunia 10kpl/580g" -> content 580 g, pack_count 10
"Valio kuohukerma 3,3 dl"            -> content 3.3 dl
"Pepsi Max 6x0,33l"                  -> content 1.98 l, pack_count 6

Regex-only on purpose: runs synchronously inside product creation and must
never depend on AI. The reconcile sweep re-runs it as a backfill. Facts land
in products.content_amount / content_unit_id / pack_count with provenance
"name" in facts_meta, and never overwrite a value that is already set.
"""
from __future__ import annotations

import json
import re
import sqlite3

_UNIT = r"(kg|g|ml|dl|cl|l)"
_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*" + _UNIT + r"(?![\wäö])", re.IGNORECASE)
_MULTI_RE = re.compile(
    r"(\d+)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*" + _UNIT + r"(?![\wäö])", re.IGNORECASE
)
_COUNT_RE = re.compile(r"(\d+)\s*(?:kpl|rl|st)(?![\wäö])", re.IGNORECASE)


def _num(raw: str) -> float:
    return float(raw.replace(",", "."))


def parse_pack_size(name: str) -> dict:
    """Return {"amount", "unit", "count"} parsed from a product name.

    `amount`/`unit` is the net content of one retail item; `count` is the
    number of natural pieces in it. A multipack token ("6x0,33l") yields the
    total content and the piece count.
    """
    name = (name or "").replace("\xa0", " ")
    out: dict = {"amount": None, "unit": None, "count": None}
    multi = _MULTI_RE.findall(name)
    if multi:
        n, size, unit = multi[-1]
        out["count"] = int(n)
        out["amount"] = round(int(n) * _num(size), 6)
        out["unit"] = unit.lower()
        return out
    sizes = _SIZE_RE.findall(name)
    if sizes:
        raw, unit = sizes[-1]  # last match is the most specific ("2x200g 400g")
        out["amount"] = _num(raw)
        out["unit"] = unit.lower()
    counts = _COUNT_RE.findall(name)
    if counts:
        out["count"] = int(counts[-1])
    return out


def ensure_pack_facts(conn: sqlite3.Connection, product_id: int) -> bool:
    """Store parsed content / pack_count on a SKU whose facts are still empty.

    Concepts are skipped (they have no package). Returns True if anything was
    written. Commits its own write; rolls back on error.
    """
    prod = conn.execute(
        "SELECT id, name, kind, content_amount, pack_count, facts_meta "
        "FROM products WHERE id = ?",
        (product_id,),
    ).fetchone()
    if not prod or prod["kind"] != "sku":
        return False
    parsed = parse_pack_size(prod["name"])
    try:
        meta = json.loads(prod["facts_meta"] or "{}")
        if not isinstance(meta, dict):
            meta = {}
    except ValueError:
        meta = {}
    sets: dict = {}
    if prod["content_amount"] is None and parsed["amount"] and parsed["unit"]:
        unit = conn.execute(
            "SELECT id FROM units WHERE abbreviation = ?", (parsed["unit"],)
        ).fetchone()
        if unit:
            sets["content_amount"] = parsed["amount"]
            sets["content_unit_id"] = unit["id"]
            meta["content"] = "name"
    if prod["pack_count"] is None and parsed["count"]:
        sets["pack_count"] = float(parsed["count"])
        meta["pack_count"] = "name"
    if not sets:
        return False
    sets["facts_meta"] = json.dumps(meta, sort_keys=True)
    try:
        conn.execute(
            f"UPDATE products SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
            [*sets.values(), product_id],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True
