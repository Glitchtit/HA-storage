"""Resolve parsed recipe lines into concepts (spec §4.6).

Used by POST /api/ingredients/resolve. Per line, cheapest step first:

1. Units. The unit text goes through quantity.normalize_unit_text, so a
   container word ("burk", "paketti") becomes a package unit. A number with no
   unit text counts natural pieces (kpl). An unknown unit word also becomes
   kpl, flagged needs_review, so a bad unit is visible instead of silently
   turning into grams. No amount means "to taste" (amount 0); an unknown
   unit word there is flagged too.
2. Generic name: alias, then exact normalized concept name.
3. Specific variant, matched the same way. A variant that is the generic
   concept (or its own name) binds loose on the generic, so no same-name
   child is ever made. A known variant whose generic is unknown binds
   strict on the variant without creating the generic.
4. One AI call matches the names still unknown against every concept. A
   high-confidence match binds and is remembered as an alias.
5. Anything left becomes a new concept: a variant under its generic, a
   generic at top level. The generic, the variant and the source-language
   word are recorded as aliases.
6. A preferred variant (flavour, form, variety the recipe names beyond the
   specific: "ruokakerma kolme juustoa") binds 'prefer', found like any name
   or made under the node bound so far, which becomes its fallback.

The resolver never creates SKUs, never changes a unit and never deletes.
Catalog writes run under CATALOG_LOCK. The AI call runs outside it, and
every name is looked up again under the lock before a concept is created,
so two imports at once cannot create the same concept twice.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Callable

from concepts import add_alias, concept_index, create_concept, norm
from locks import CATALOG_LOCK
from quantity import MASS, UNIT_CATALOG, VOLUME, normalize_unit_text

log = logging.getLogger(__name__)

AiFn = Callable[..., Any]


def quantity_of(amount: Any, unit_text: str | None) -> tuple[float, str, bool]:
    """(amount, catalog unit abbreviation, needs_review) for one line."""
    try:
        value = float(amount) if amount is not None else 0.0
    except (TypeError, ValueError):
        value = 0.0
    text = (unit_text or "").strip()
    found = normalize_unit_text(text) if text else None
    if value <= 0:
        return 0.0, (found[0] if found else "rs"), bool(text) and found is None
    if not text:
        return value, "kpl", False
    if found is None:
        return value, "kpl", True
    abbr, multiplier = found
    return round(value * multiplier, 4), abbr, False


def _display_unit(abbr: str) -> str:
    """Display unit for a concept created from a line in `abbr`."""
    dimension = UNIT_CATALOG.get(abbr, ("", "", None, None))[2]
    return {MASS: "g", VOLUME: "dl"}.get(dimension, "kpl")


def _names(item: dict) -> tuple[str, str]:
    """(generic, specific) as given; a line with only a variant uses it as
    its generic. The specific is '' when absent or the same word."""
    generic = (item.get("name") or "").strip()
    specific = (item.get("specific") or "").strip()
    if not norm(generic):
        generic, specific = specific, ""
    if norm(specific) == norm(generic):
        specific = ""
    return generic, specific


def _unknown_names(items: list[dict], index: dict[str, int]) -> list[str]:
    seen: dict[str, str] = {}
    for item in items:
        for name in (*_names(item), (item.get("preferred") or "").strip()):
            key = norm(name)
            if key and key not in index:
                seen.setdefault(key, name)
    return list(seen.values())


_MATCH_PROMPT = """You match recipe ingredient names to a household grocery catalog.
Catalog entries are generic ingredient CONCEPTS with Finnish names. An entry
may have a parent (it is a variant of that parent) and known aliases.

A name matches a concept only if it is the SAME ingredient. Another spelling,
another language (Swedish, English) or another grammatical form is the same
ingredient. A broader or narrower ingredient is NOT: "kerma" is not
"vispikerma", "juusto" is not "parmesaani", "pasta" is not "spagetti".
When unsure, answer null or a lower confidence.

Names to match (JSON list):
{names_json}

Catalog concepts (JSON):
{concepts_json}

Return ONLY a JSON array with one entry per name:
[{{"name": "<name exactly as given>", "id": <concept id or null>, "confidence": "high"|"medium"|"low"}}]
"""


def _catalog_for_prompt(conn: sqlite3.Connection) -> list[dict]:
    names = {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM products WHERE kind = 'concept'").fetchall()}
    aliases: dict[int, list[str]] = {}
    for r in conn.execute("SELECT alias_norm, product_id FROM product_aliases ORDER BY alias_norm"):
        if r["product_id"] in names and len(aliases.setdefault(r["product_id"], [])) < 5:
            aliases[r["product_id"]].append(r["alias_norm"])
    parents = {r["id"]: r["parent_id"] for r in conn.execute(
        "SELECT id, parent_id FROM products WHERE kind = 'concept'").fetchall()}
    out = []
    for cid, name in sorted(names.items()):
        entry: dict = {"id": cid, "name": name}
        if parents.get(cid) in names:
            entry["parent"] = names[parents[cid]]
        if aliases.get(cid):
            entry["aliases"] = aliases[cid]
        out.append(entry)
    return out


def match_with_ai(conn: sqlite3.Connection, names: list[str], ai: AiFn | None,
                  catalog: list[dict]) -> dict[str, int]:
    """normalized name -> concept id for every high-confidence AI match
    against `catalog` (a _catalog_for_prompt snapshot taken under the lock).
    Any failure or malformed answer simply matches nothing."""
    if not names or ai is None or not catalog:
        return {}
    prompt = _MATCH_PROMPT.format(
        names_json=json.dumps(names, ensure_ascii=False),
        concepts_json=json.dumps(catalog[:1500], ensure_ascii=False),
    )
    try:
        answer = ai(prompt, conn)
    except Exception as exc:
        log.warning("Ingredient AI match failed: %s", exc)
        return {}
    if not isinstance(answer, list):
        return {}
    asked = {norm(n) for n in names}
    valid = {c["id"] for c in catalog}
    hits: dict[str, int] = {}
    for entry in answer:
        if not isinstance(entry, dict) or str(entry.get("confidence", "")).lower() != "high":
            continue
        key = norm(str(entry.get("name") or ""))
        try:
            cid = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        if key in asked and cid in valid:
            hits[key] = cid
    return hits


def resolve(conn: sqlite3.Connection, items: list[dict], *, ai: AiFn | None = None) -> dict:
    """Bind every line; returns {"items": [...], "created": [concept ids]}."""
    # Every read and write of the shared connection happens under the lock;
    # two resolves must never use it at the same time.
    with CATALOG_LOCK:
        units = {r["abbreviation"]: r["id"] for r in conn.execute(
            "SELECT id, abbreviation FROM units").fetchall()}
        unknown = _unknown_names(items, concept_index(conn))
        catalog = _catalog_for_prompt(conn) if unknown and ai is not None else []
    hits = match_with_ai(conn, unknown, ai, catalog) if unknown else {}

    created: list[int] = []
    out: list[dict] = []
    with CATALOG_LOCK:
        try:
            index = concept_index(conn)  # again: another import may have added some
            alive = set(index.values()) | {r["id"] for r in conn.execute(
                "SELECT id FROM products WHERE kind = 'concept'").fetchall()}
            hits = {k: v for k, v in hits.items() if v in alive}
            for i, item in enumerate(items):
                out.append(_bind(conn, i, item, index, hits, created, units))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"items": out, "created": created}


def _bind(conn, i: int, item: dict, index: dict[str, int], hits: dict[str, int],
          created: list[int], units: dict[str, int]) -> dict:
    amount, abbr, review = quantity_of(item.get("amount"), item.get("unit_text"))
    if abbr not in units:
        abbr, review = "kpl", True
    generic, specific = _names(item)
    result = {"index": i, "product_id": None, "product_name": None, "specificity": "loose",
              "amount": amount, "unit_id": units.get(abbr), "unit": abbr,
              "created": False, "needs_review": review}
    if not norm(generic):
        result["needs_review"] = True
        return result

    def concept_for(name: str, parent_id: int | None) -> int:
        key = norm(name)
        pid, source = index.get(key), "recipe"
        if pid is None and key in hits:
            pid, source = hits[key], "ai"
        if pid is None:
            pid = create_concept(conn, name, units[_display_unit(abbr)], parent_id)
            created.append(pid)
            result["created"] = True
        index[key] = pid
        add_alias(conn, name, pid, source)
        return pid

    def known(name: str) -> bool:
        return norm(name) in index or norm(name) in hits

    if specific and known(specific) and not known(generic):
        # A known variant needs no generic; making one here left orphans
        # such as "Liemi" beside an existing "Kanaliemi".
        pid, specificity = concept_for(specific, None), "strict"
    else:
        gen_id = concept_for(generic, None)
        pid, specificity = gen_id, "loose"
        if specific:
            spec_id = concept_for(specific, gen_id)
            gen_name = conn.execute("SELECT name FROM products WHERE id = ?", (gen_id,)).fetchone()["name"]
            if spec_id != gen_id and norm(specific) != norm(gen_name):
                pid, specificity = spec_id, "strict"
    preferred = (item.get("preferred") or "").strip()
    bound_name = conn.execute("SELECT name FROM products WHERE id = ?", (pid,)).fetchone()["name"]
    if norm(preferred) and norm(preferred) not in {norm(generic), norm(specific), norm(bound_name)}:
        # The exact variant the recipe names ("ruokakerma kolme juustoa"):
        # bound first, with the node above as an acceptable substitute. A new
        # one is made under the current binding; a known one keeps its parent.
        pref_id = concept_for(preferred, pid)
        if pref_id != pid:
            pid, specificity = pref_id, "prefer"
    if item.get("source_name"):
        add_alias(conn, item["source_name"], pid, "recipe")
    row = conn.execute("SELECT name FROM products WHERE id = ?", (pid,)).fetchone()
    result.update(product_id=pid, product_name=row["name"], specificity=specificity)
    return result
