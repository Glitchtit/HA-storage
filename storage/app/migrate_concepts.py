"""One-off data migration for concept identity (spec §6 steps 6-8).

Each step is idempotent and guarded by a _meta flag, so init_db calls run()
on every start, after migrate_quantity.

Steps:
  concepts_demote_v1   a "concept" whose name carries a pack size and that no
                       recipe or bundle uses is a retail product something was
                       linked under by mistake ("Pepsi Max 0,33l 18-pack"):
                       it becomes a SKU and its children are released for the
                       linker to place again
  concepts_visible_v1  concepts become active, take their descendants'
                       majority aisle group (else their parent's) and a
                       capitalised name; "Group master" is retired
  concepts_aliases_v1  every concept name, and the generic word of every loose
                       recipe row ("prep — generic"), becomes an alias
  concepts_dedup_v1    twin concepts merge, same-name child stubs fold into
                       their parent (concepts.dedup_concepts)

The AI steps (adopt, placement, facts; spec §6 steps 9-10) need a provider
and run in the background sweep instead (concept_jobs.run_sweep).
"""
from __future__ import annotations

import logging
import sqlite3
from collections import Counter

import tree
from concepts import add_alias, dedup_concepts
from pack_size import ensure_pack_facts, parse_pack_size

log = logging.getLogger(__name__)


def demote_mislabelled(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """SELECT p.id, p.name FROM products p
           WHERE p.kind = 'concept'
             AND NOT EXISTS (SELECT 1 FROM recipe_ingredients ri WHERE ri.product_id = p.id)
             AND NOT EXISTS (SELECT 1 FROM bundle_items bi WHERE bi.product_id = p.id)"""
    ).fetchall()
    changed = 0
    for row in rows:
        parsed = parse_pack_size(row["name"])
        if not (parsed["amount"] or parsed["count"]):
            continue
        conn.execute("UPDATE products SET parent_id = NULL WHERE parent_id = ?", (row["id"],))
        conn.execute("UPDATE products SET kind = 'sku' WHERE id = ?", (row["id"],))
        conn.commit()
        ensure_pack_facts(conn, row["id"])
        log.info("Demoted mislabelled concept %d %r to a SKU.", row["id"], row["name"])
        changed += 1
    return changed


def make_visible(conn: sqlite3.Connection) -> int:
    gm = conn.execute("SELECT id FROM product_groups WHERE name = 'Group master'").fetchone()
    gm_id = gm["id"] if gm else None
    valid = {r["id"] for r in conn.execute("SELECT id FROM product_groups").fetchall()} - {gm_id}
    concepts = conn.execute(
        "SELECT id, name, parent_id, product_group_id, active FROM products WHERE kind = 'concept'"
    ).fetchall()
    # Parents first, so a childless variant can take its parent's new group.
    concepts = sorted(concepts, key=lambda r: len(tree.ancestor_ids(conn, r["id"])))
    group_of = {r["id"]: r["product_group_id"] for r in conn.execute(
        "SELECT id, product_group_id FROM products").fetchall()}
    changed = 0
    for c in concepts:
        sets: dict = {}
        if not c["active"]:
            sets["active"] = 1
        name = c["name"] or ""
        if name[:1].islower():
            sets["name"] = name[:1].upper() + name[1:]
        if group_of.get(c["id"]) not in valid:
            counts = Counter(group_of.get(d) for d in tree.descendant_ids(conn, c["id"]))
            counts = Counter({k: v for k, v in counts.items() if k in valid})
            gid = counts.most_common(1)[0][0] if counts else None
            if gid is None and group_of.get(c["parent_id"]) in valid:
                gid = group_of[c["parent_id"]]
            sets["product_group_id"] = gid
            group_of[c["id"]] = gid
        if sets:
            conn.execute(
                f"UPDATE products SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                [*sets.values(), c["id"]],
            )
            changed += 1
    if gm_id is not None:
        conn.execute("UPDATE products SET product_group_id = NULL WHERE product_group_id = ?", (gm_id,))
        conn.execute("DELETE FROM product_groups WHERE id = ?", (gm_id,))
    conn.commit()
    return changed


def seed_aliases(conn: sqlite3.Connection) -> int:
    written = 0
    for r in conn.execute("SELECT id, name FROM products WHERE kind = 'concept' ORDER BY id").fetchall():
        written += add_alias(conn, r["name"], r["id"], "name")
    for r in conn.execute(
        """SELECT ri.product_id, ri.note FROM recipe_ingredients ri
           JOIN products p ON p.id = ri.product_id
           WHERE p.kind = 'concept' AND ri.specificity = 'loose'
             AND ri.note IS NOT NULL AND ri.note != ''
           ORDER BY ri.id"""
    ).fetchall():
        generic = r["note"].split("—")[-1].strip()
        written += add_alias(conn, generic, r["product_id"], "recipe")
    conn.commit()
    return written


def merge_twins(conn: sqlite3.Connection) -> int:
    return len(dedup_concepts(conn))


STEPS = (
    ("concepts_demote_v1", demote_mislabelled),
    ("concepts_visible_v1", make_visible),
    ("concepts_aliases_v1", seed_aliases),
    ("concepts_dedup_v1", merge_twins),
)


def run(conn: sqlite3.Connection, *, force: bool = False) -> dict[str, int]:
    """Run every step whose _meta flag is missing (all of them with force)."""
    done: dict[str, int] = {}
    for flag, step in STEPS:
        if not force and conn.execute("SELECT 1 FROM _meta WHERE key = ?", (flag,)).fetchone():
            continue
        count = step(conn)
        conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)", (flag, str(count)))
        conn.commit()
        done[flag] = count
        log.info("Concept migration %s: %d row(s).", flag, count)
    return done
