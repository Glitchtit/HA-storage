"""One-off data migration for the quantity model (spec §6 steps 2-5).

Each step is idempotent and guarded by a _meta flag, so init_db can call
run() on every start. Legacy unit_conversions rows that become product facts
are copied to unit_conversions_backup_qty1 before they are deleted.

Steps:
  qty_kind_v1           products with children, "Group master" members and
                        recipe-referenced auto-stubs become kind='concept'
  qty_content_v1        SKU "1 kpl = X" rows -> content; concept "1 kpl = X g"
                        rows -> piece weight; name parser fills the rest
  qty_density_v1        mass<->volume rows -> density_g_ml (plausible median),
                        then concepts without one take their descendants' median
  qty_concept_units_v1  concepts with a nonsense display unit (g from the old
                        optimizer, tl/rkl/rs from recipe stubs) get dl/g/kpl
"""
from __future__ import annotations

import json
import logging
import sqlite3
import statistics
from collections import Counter

import tree
from pack_size import ensure_pack_facts
from quantity import CANONICAL_UNIT, COUNT, MASS, VOLUME, plausible_density

log = logging.getLogger(__name__)

_BACKUP_DDL = """
CREATE TABLE IF NOT EXISTS unit_conversions_backup_qty1 (
    id           INTEGER,
    from_unit_id INTEGER,
    to_unit_id   INTEGER,
    factor       REAL,
    product_id   INTEGER,
    reason       TEXT,
    backed_up_at TEXT DEFAULT (datetime('now'))
)
"""


def _units(conn) -> dict[int, dict]:
    return {
        r["id"]: r
        for r in conn.execute(
            "SELECT id, abbreviation, dimension, base_factor FROM units"
        ).fetchall()
    }


def _with_meta(meta_json: str | None, key: str, source: str) -> str:
    try:
        meta = json.loads(meta_json or "{}")
        if not isinstance(meta, dict):
            meta = {}
    except ValueError:
        meta = {}
    meta[key] = source
    return json.dumps(meta, sort_keys=True)


def _retire_row(conn, row, reason: str) -> None:
    conn.execute(_BACKUP_DDL)
    conn.execute(
        "INSERT INTO unit_conversions_backup_qty1 "
        "(id, from_unit_id, to_unit_id, factor, product_id, reason) VALUES (?, ?, ?, ?, ?, ?)",
        (row["id"], row["from_unit_id"], row["to_unit_id"], row["factor"], row["product_id"], reason),
    )
    conn.execute("DELETE FROM unit_conversions WHERE id = ?", (row["id"],))


def classify_kind(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        """
        UPDATE products SET kind = 'concept'
         WHERE kind != 'concept' AND (
               EXISTS (SELECT 1 FROM products c WHERE c.parent_id = products.id)
            OR product_group_id IN (SELECT id FROM product_groups WHERE name = 'Group master')
            OR (COALESCE(description, '') LIKE '%Auto-created%'
                AND EXISTS (SELECT 1 FROM recipe_ingredients ri WHERE ri.product_id = products.id))
         )
        """
    )
    return cur.rowcount


def migrate_content(conn: sqlite3.Connection) -> int:
    units = _units(conn)
    rows = conn.execute(
        """
        SELECT uc.id, uc.from_unit_id, uc.to_unit_id, uc.factor, uc.product_id
        FROM unit_conversions uc
        JOIN products p ON p.id = uc.product_id
        ORDER BY uc.id
        """
    ).fetchall()
    changed = 0
    for row in rows:
        src, dst = units.get(row["from_unit_id"]), units.get(row["to_unit_id"])
        if not src or not dst or not row["factor"] or row["factor"] <= 0:
            continue
        if src["dimension"] == COUNT and dst["dimension"] in (MASS, VOLUME):
            amount, unit = float(row["factor"]), dst
        elif dst["dimension"] == COUNT and src["dimension"] in (MASS, VOLUME):
            amount, unit = 1.0 / float(row["factor"]), src
        else:
            continue
        prod = conn.execute(
            "SELECT kind, content_amount, piece_weight_g, facts_meta FROM products WHERE id = ?",
            (row["product_id"],),
        ).fetchone()
        if prod["kind"] == "sku":
            if prod["content_amount"] is None:
                conn.execute(
                    "UPDATE products SET content_amount = ?, content_unit_id = ?, facts_meta = ? WHERE id = ?",
                    (round(amount, 6), unit["id"], _with_meta(prod["facts_meta"], "content", "name"),
                     row["product_id"]),
                )
                changed += 1
            _retire_row(conn, row, "content")
        elif unit["dimension"] == MASS:
            if prod["piece_weight_g"] is None:
                conn.execute(
                    "UPDATE products SET piece_weight_g = ?, facts_meta = ? WHERE id = ?",
                    (round(amount * unit["base_factor"], 6),
                     _with_meta(prod["facts_meta"], "piece_weight_g", "name"), row["product_id"]),
                )
                changed += 1
            _retire_row(conn, row, "piece_weight")
    conn.commit()
    for r in conn.execute(
        "SELECT id FROM products WHERE kind = 'sku' "
        "AND (content_amount IS NULL OR pack_count IS NULL)"
    ).fetchall():
        if ensure_pack_facts(conn, r["id"]):
            changed += 1
    return changed


def migrate_density(conn: sqlite3.Connection) -> int:
    units = _units(conn)
    rows = conn.execute(
        "SELECT id, from_unit_id, to_unit_id, factor, product_id FROM unit_conversions "
        "WHERE product_id IS NOT NULL ORDER BY id"
    ).fetchall()
    values: dict[int, list[float]] = {}
    for row in rows:
        src, dst = units.get(row["from_unit_id"]), units.get(row["to_unit_id"])
        if not src or not dst or not src["base_factor"] or not dst["base_factor"]:
            continue
        dims = (src["dimension"], dst["dimension"])
        if dims not in ((MASS, VOLUME), (VOLUME, MASS)):
            continue
        factor = float(row["factor"] or 0)
        if factor > 0:
            if dims == (MASS, VOLUME):
                g_ml = src["base_factor"] / (factor * dst["base_factor"])
            else:
                g_ml = factor * dst["base_factor"] / src["base_factor"]
            values.setdefault(row["product_id"], []).append(g_ml)
        _retire_row(conn, row, "density")
    changed = 0
    for pid, vals in values.items():
        plausible = [v for v in vals if plausible_density(v)]
        prod = conn.execute(
            "SELECT density_g_ml, facts_meta FROM products WHERE id = ?", (pid,)
        ).fetchone()
        if plausible and prod["density_g_ml"] is None:
            conn.execute(
                "UPDATE products SET density_g_ml = ?, facts_meta = ? WHERE id = ?",
                (round(statistics.median(plausible), 4),
                 _with_meta(prod["facts_meta"], "density_g_ml", "ai"), pid),
            )
            changed += 1
    for concept in conn.execute(
        "SELECT id, facts_meta FROM products WHERE kind = 'concept' AND density_g_ml IS NULL"
    ).fetchall():
        desc = tree.descendant_ids(conn, concept["id"])
        if not desc:
            continue
        marks = ",".join("?" * len(desc))
        found = [
            r["density_g_ml"]
            for r in conn.execute(
                f"SELECT density_g_ml FROM products WHERE id IN ({marks}) AND density_g_ml IS NOT NULL",
                desc,
            ).fetchall()
            if plausible_density(r["density_g_ml"])
        ]
        if found:
            conn.execute(
                "UPDATE products SET density_g_ml = ?, facts_meta = ? WHERE id = ?",
                (round(statistics.median(found), 4),
                 _with_meta(concept["facts_meta"], "density_g_ml", "ai"), concept["id"]),
            )
            changed += 1
    conn.commit()
    return changed


def fix_concept_units(conn: sqlite3.Connection) -> int:
    units = _units(conn)
    by_abbr = {u["abbreviation"]: u["id"] for u in units.values()}
    changed = 0
    for concept in conn.execute(
        "SELECT id, unit_id FROM products WHERE kind = 'concept'"
    ).fetchall():
        dims = Counter(
            r["dimension"]
            for r in conn.execute(
                "SELECT u.dimension FROM recipe_ingredients ri JOIN units u ON u.id = ri.unit_id "
                "WHERE ri.product_id = ? AND ri.amount > 0",
                (concept["id"],),
            ).fetchall()
            if r["dimension"] in (MASS, VOLUME, COUNT)
        )
        if not dims:
            desc = tree.descendant_ids(conn, concept["id"])
            if desc:
                marks = ",".join("?" * len(desc))
                dims = Counter(
                    r["dimension"]
                    for r in conn.execute(
                        f"SELECT u.dimension FROM products p JOIN units u ON u.id = p.content_unit_id "
                        f"WHERE p.id IN ({marks})",
                        desc,
                    ).fetchall()
                    if r["dimension"] in (MASS, VOLUME, COUNT)
                )
        target = dims.most_common(1)[0][0] if dims else COUNT
        current = units.get(concept["unit_id"])
        bogus = (
            current is None
            or current["dimension"] != target
            or current["abbreviation"] in ("tl", "rkl", "mm", "rs")
        )
        new_unit = by_abbr.get(CANONICAL_UNIT[target])
        if bogus and new_unit is not None and new_unit != concept["unit_id"]:
            conn.execute("UPDATE products SET unit_id = ? WHERE id = ?", (new_unit, concept["id"]))
            changed += 1
    conn.commit()
    return changed


STEPS = (
    ("qty_kind_v1", classify_kind),
    ("qty_content_v1", migrate_content),
    ("qty_density_v1", migrate_density),
    ("qty_concept_units_v1", fix_concept_units),
)


def run(conn: sqlite3.Connection, *, force: bool = False) -> dict[str, int]:
    """Run every step whose _meta flag is missing (all of them with force)."""
    done: dict[str, int] = {}
    for flag, step in STEPS:
        if not force and conn.execute(
            "SELECT 1 FROM _meta WHERE key = ?", (flag,)
        ).fetchone():
            continue
        count = step(conn)
        conn.execute(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)", (flag, str(count))
        )
        conn.commit()
        done[flag] = count
        log.info("Quantity migration %s: %d row(s).", flag, count)
    return done
