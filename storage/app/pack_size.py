"""Parse package size out of Finnish product names and persist it.

"Pirkka babypinaatti 65g"            -> 1 kpl = 65 g   (unit_conversions row)
"Pirkka Luomu kananmunia 10kpl/580g" -> pack_count=10 AND 1 kpl = 580 g
"Valio kuohukerma 3,3 dl"            -> 1 kpl = 3.3 dl

Regex-only on purpose: runs synchronously inside product creation, must never
depend on AI availability. The linker sweep re-runs it as backfill.
"""
from __future__ import annotations

import re
import sqlite3

_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g|ml|dl|cl|l)(?![\wäö])", re.IGNORECASE)
_COUNT_RE = re.compile(r"(\d+)\s*(?:kpl|rl|st)(?![\wäö])", re.IGNORECASE)


def parse_pack_size(name: str) -> dict:
    name = (name or "").replace("\xa0", " ")
    out: dict = {"amount": None, "unit": None, "count": None}
    sizes = _SIZE_RE.findall(name)
    if sizes:
        raw, unit = sizes[-1]  # last match is the most specific ("2x200g 400g")
        out["amount"] = float(raw.replace(",", "."))
        out["unit"] = unit.lower()
    counts = _COUNT_RE.findall(name)
    if counts:
        out["count"] = int(counts[-1])
    return out


def ensure_pack_conversions(conn: sqlite3.Connection, product_id: int) -> bool:
    """Idempotently persist parsed pack size. Returns True if anything was written."""
    prod = conn.execute(
        "SELECT id, name, pack_count FROM products WHERE id = ?", (product_id,)
    ).fetchone()
    if not prod:
        return False
    parsed = parse_pack_size(prod["name"])
    units = {
        u["abbreviation"]: u["id"]
        for u in conn.execute("SELECT id, abbreviation FROM units").fetchall()
    }
    piece_id = units.get("kpl")
    wrote = False

    try:
        if parsed["amount"] and parsed["unit"] and piece_id:
            to_unit = units.get(parsed["unit"])
            if to_unit and to_unit != piece_id:
                exists = conn.execute(
                    "SELECT 1 FROM unit_conversions WHERE product_id = ? "
                    "AND from_unit_id = ? AND to_unit_id = ?",
                    (product_id, piece_id, to_unit),
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO unit_conversions (from_unit_id, to_unit_id, factor, product_id) "
                        "VALUES (?, ?, ?, ?)",
                        (piece_id, to_unit, parsed["amount"], product_id),
                    )
                    wrote = True

        if parsed["count"] and not prod["pack_count"]:
            conn.execute(
                "UPDATE products SET pack_count = ? WHERE id = ?",
                (float(parsed["count"]), product_id),
            )
            wrote = True

        if wrote:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    return wrote
