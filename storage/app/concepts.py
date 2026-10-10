"""Concept identity: name normalization, aliases and concept lookup.

A concept is the generic ingredient that recipes, generic shopping rows and
voice adds refer to ("Vehnäjauho"). Aliases map every spelling seen so far
("vehnäjauhot", "vetemjöl", "wheat flour") to one concept, so a known word
binds without AI. The first mapping of a normalized alias wins.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata

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
