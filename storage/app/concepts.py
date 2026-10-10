"""Concept identity: name normalization, aliases and concept lookup.

A concept is the generic ingredient that recipes, generic shopping rows and
voice adds refer to ("Vehnäjauho"). Aliases map every spelling seen so far
("vehnäjauhot", "vetemjöl", "wheat flour") to one concept, so a known word
binds without AI. The first mapping of a normalized alias wins.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import unicodedata

from locks import CATALOG_LOCK

log = logging.getLogger(__name__)

_KEEP = frozenset("åäö")


def norm(text: str | None) -> str:
    """Normalize a name for matching (spec §4.6).

    NFKC, casefold, NBSP to a space and U+2011 to '-', diacritics stripped
    except å/ä/ö, punctuation to spaces, whitespace collapsed:
    "Crème fraîche" and "creme-fraiche" both become "creme fraiche".
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text)).replace("\xa0", " ").replace("‑", "-").casefold()
    out = []
    for ch in s:
        if ch in _KEEP:
            out.append(ch)
        else:
            out.append("".join(c for c in unicodedata.normalize("NFD", ch)
                               if not unicodedata.combining(c)))
    s = re.sub(r"[^\w\s]|_", " ", "".join(out))
    return re.sub(r"\s+", " ", s).strip()


def add_alias(conn: sqlite3.Connection, alias: str | None, product_id: int, source: str) -> bool:
    """Map alias -> product unless the normalized alias is already taken.
    Returns True when a row was written. Does not commit."""
    key = norm(alias)
    if not key:
        return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO product_aliases (alias_norm, product_id, source) VALUES (?, ?, ?)",
        (key, product_id, source),
    )
    return cur.rowcount == 1


def aliases_of(conn: sqlite3.Connection, product_id: int) -> list[dict]:
    return [
        {"alias": r["alias_norm"], "source": r["source"]}
        for r in conn.execute(
            "SELECT alias_norm, source FROM product_aliases WHERE product_id = ? ORDER BY alias_norm",
            (product_id,),
        ).fetchall()
    ]


def concept_index(conn: sqlite3.Connection) -> dict[str, int]:
    """Normalized name -> concept id: every alias of a concept, then every
    concept name not already taken (the lowest id wins among same names)."""
    index: dict[str, int] = {}
    for r in conn.execute(
        "SELECT a.alias_norm, a.product_id FROM product_aliases a "
        "JOIN products p ON p.id = a.product_id WHERE p.kind = 'concept'"
    ).fetchall():
        index[r["alias_norm"]] = int(r["product_id"])
    for r in conn.execute(
        "SELECT id, name FROM products WHERE kind = 'concept' ORDER BY id"
    ).fetchall():
        index.setdefault(norm(r["name"]), int(r["id"]))
    index.pop("", None)
    return index


def find_concept(conn: sqlite3.Connection, name: str | None) -> int | None:
    """Concept bound to `name` by alias, else by exact normalized name.
    SKUs never match."""
    key = norm(name)
    return concept_index(conn).get(key) if key else None


def display_name(name: str) -> str:
    """Catalog spelling for a new concept: trimmed, first letter upper-case
    ("crème fraîche" -> "Crème fraîche")."""
    name = " ".join((name or "").split())
    return name[:1].upper() + name[1:]


def create_concept(conn: sqlite3.Connection, name: str, unit_id: int,
                   parent_id: int | None = None) -> int:
    """Insert an active concept. A variant takes its parent's aisle group.
    Does not commit; callers hold CATALOG_LOCK."""
    group_id = None
    if parent_id is not None:
        row = conn.execute(
            "SELECT product_group_id FROM products WHERE id = ?", (parent_id,)
        ).fetchone()
        group_id = row["product_group_id"] if row else None
    cur = conn.execute(
        "INSERT INTO products (name, unit_id, kind, active, parent_id, product_group_id) "
        "VALUES (?, ?, 'concept', 1, ?, ?)",
        (display_name(name), unit_id, parent_id, group_id),
    )
    return int(cur.lastrowid)


_REFERENCE_SQL = """
    SELECT (SELECT COUNT(*) FROM recipe_ingredients WHERE product_id = :id)
         + (SELECT COUNT(*) FROM shopping_list WHERE product_id = :id)
         + (SELECT COUNT(*) FROM bundle_items WHERE product_id = :id)
         + (SELECT COUNT(*) FROM products WHERE parent_id = :id)
         + (SELECT COUNT(*) FROM stock WHERE product_id = :id)
         + (SELECT COUNT(*) FROM barcodes WHERE product_id = :id) AS n
"""


def _references(conn: sqlite3.Connection, pid: int) -> int:
    return int(conn.execute(_REFERENCE_SQL, {"id": pid}).fetchone()["n"])


def _twin_groups(conn: sqlite3.Connection) -> list[list[int]]:
    """Concepts whose normalized names collide, or whose name is another
    concept's name or former name (aliases from 'name' or 'merge'). Aliases
    learned from recipes, the AI or a manual edit only bind words; they never
    make two concepts twins. Each group is sorted by id."""
    names = {r["id"]: norm(r["name"]) for r in conn.execute(
        "SELECT id, name FROM products WHERE kind = 'concept'").fetchall()}
    parent = {pid: pid for pid in names}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    by_name: dict[str, int] = {}
    for pid, key in sorted(names.items()):
        if key in by_name:
            union(pid, by_name[key])
        elif key:
            by_name[key] = pid
    for r in conn.execute(
        "SELECT alias_norm, product_id FROM product_aliases WHERE source IN ('name', 'merge')"
    ).fetchall():
        other = by_name.get(r["alias_norm"])
        if other is not None and r["product_id"] in names and r["product_id"] != other:
            union(r["product_id"], other)
    groups: dict[int, list[int]] = {}
    for pid in names:
        groups.setdefault(find(pid), []).append(pid)
    return [sorted(g) for g in groups.values() if len(g) > 1]


def dedup_concepts(conn: sqlite3.Connection) -> list[tuple[int, int]]:
    """Merge each group of twin concepts into one survivor (spec §4.7).

    The survivor is the topmost member when one twin sits under another (a
    same-name child stub folds into its parent), otherwise the twin with the
    most references (ingredients, shopping rows, bundles, children, lots,
    barcodes), the lowest id on a tie. A survivor without a parent takes over
    the parent of a twin it absorbs. A twin that holds stock or barcodes is
    left alone (merge refuses it). Returns the (source, target) pairs merged.
    """
    import tree
    from merge import merge_products

    merged: list[tuple[int, int]] = []
    with CATALOG_LOCK:
        for group in _twin_groups(conn):
            members = set(group)
            top = [m for m in group if not members & set(tree.ancestor_ids(conn, m))]
            survivor = max(top, key=lambda m: (_references(conn, m), -m))
            for source in sorted(members - {survivor},
                                 key=lambda m: -len(tree.ancestor_ids(conn, m))):
                src = conn.execute(
                    "SELECT parent_id FROM products WHERE id = ?", (source,)).fetchone()
                if src is None:
                    continue
                try:
                    merge_products(conn, source, survivor)
                except (ValueError, LookupError) as exc:
                    log.info("Dedup: left %s apart from %s: %s", source, survivor, exc)
                    continue
                merged.append((source, survivor))
                inherited = src["parent_id"]
                row = conn.execute(
                    "SELECT parent_id FROM products WHERE id = ?", (survivor,)).fetchone()
                if row["parent_id"] is None and inherited is not None and inherited != survivor:
                    try:
                        tree.prepare_parent(conn, survivor, inherited)
                        conn.execute("UPDATE products SET parent_id = ? WHERE id = ?",
                                     (inherited, survivor))
                        conn.commit()
                    except ValueError:
                        conn.rollback()
    return merged
