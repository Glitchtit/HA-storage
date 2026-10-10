"""Effective product facts and catalog lookups, loaded once per request.

`Catalog(conn)` snapshots units, products, product-specific conversion rows
and (lazily) stock lots, then answers the questions every stock computation
asks: which unit is this, what are this product's facts, what is under it in
the tree, and how should a quantity attached to it be read.

Fact rules:
- content and pack_count belong to one retail item, so they are never
  inherited. density and piece weight are properties of the food, so a
  product without its own value uses its nearest ancestor's.
- piece weight derived from content ÷ pack_count (eggs: 580 g / 10) is exact
  and beats any estimate.
- Legacy product-specific unit_conversions rows still count: a SKU's
  "1 kpl = 65 g" row is content, a mass↔volume row is density, a concept's
  "1 kpl = 60 g" row is piece weight. Column values win over rows.
"""
from __future__ import annotations

import statistics
import sqlite3
from collections import defaultdict
from typing import Iterable

from quantity import (
    COUNT, MASS, PACKAGE, VAGUE, VOLUME, Facts, Unit, plausible_density,
)


class Catalog:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.units: dict[int, Unit] = {}
        self._units_by_abbr: dict[str, Unit] = {}
        for r in conn.execute(
            "SELECT id, abbreviation, dimension, base_factor FROM units"
        ).fetchall():
            dim = r["dimension"] or VAGUE
            factor = r["base_factor"] if dim != VAGUE else None
            unit = Unit(r["id"], r["abbreviation"], dim, factor)
            self.units[r["id"]] = unit
            self._units_by_abbr[r["abbreviation"]] = unit

        self.products: dict[int, dict] = {
            r["id"]: r
            for r in conn.execute(
                "SELECT id, name, parent_id, kind, unit_id, staple, active, "
                "content_amount, content_unit_id, pack_count, density_g_ml, "
                "piece_weight_g FROM products"
            ).fetchall()
        }
        self._children: dict[int, list[int]] = defaultdict(list)
        for p in self.products.values():
            if p["parent_id"] is not None:
                self._children[p["parent_id"]].append(p["id"])

        self._legacy = self._fold_legacy_rows(conn.execute(
            "SELECT product_id, from_unit_id, to_unit_id, factor "
            "FROM unit_conversions WHERE product_id IS NOT NULL"
        ).fetchall())
        self._facts: dict[int, Facts] = {}
        self._lots: dict[int, list[dict]] | None = None

    # ── units ──────────────────────────────────────────────────────────────
    def unit(self, unit_id: int | None) -> Unit | None:
        return self.units.get(unit_id) if unit_id is not None else None

    def unit_by_abbr(self, abbr: str) -> Unit | None:
        return self._units_by_abbr.get(abbr)

    # ── tree ───────────────────────────────────────────────────────────────
    def product(self, pid: int) -> dict | None:
        return self.products.get(pid)

    def is_sku(self, pid: int) -> bool:
        p = self.products.get(pid)
        return bool(p) and p["kind"] != "concept"

    def children(self, pid: int) -> list[int]:
        return list(self._children.get(pid, []))

    def descendants(self, pid: int) -> list[int]:
        """All descendants, excluding pid. Terminates on cyclic legacy data."""
        out: list[int] = []
        seen = {pid}
        stack = list(self._children.get(pid, []))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            out.append(cur)
            stack.extend(self._children.get(cur, []))
        return out

    def ancestors(self, pid: int) -> list[int]:
        """Parent, grandparent, ... nearest first. Terminates on cycles."""
        out: list[int] = []
        seen = {pid}
        cur = self.products.get(pid)
        while cur and cur["parent_id"] is not None and cur["parent_id"] not in seen:
            seen.add(cur["parent_id"])
            out.append(cur["parent_id"])
            cur = self.products.get(cur["parent_id"])
        return out

    def stock_dim(self, pid: int, unit: Unit) -> str:
        """How a quantity attached to product `pid` reads: a count unit on a
        SKU (its lots, its shopping rows, its own unit) means retail items."""
        if unit.dimension == COUNT and self.is_sku(pid):
            return PACKAGE
        return unit.dimension

    # ── facts ──────────────────────────────────────────────────────────────
    def facts(self, pid: int) -> Facts:
        cached = self._facts.get(pid)
        if cached is not None:
            return cached
        p = self.products.get(pid)
        if p is None:
            return Facts()
        legacy = self._legacy.get(pid, {})
        content_amount, content_unit = p["content_amount"], self.unit(p["content_unit_id"])
        if (content_amount is None or content_unit is None) and self.is_sku(pid) and "content" in legacy:
            content_amount, content_unit = legacy["content"]
        pack_count = p["pack_count"] or None

        piece_weight, exact = None, False
        if content_amount and content_unit and content_unit.dimension == MASS and pack_count:
            piece_weight, exact = content_amount * content_unit.base_factor / pack_count, True
        if piece_weight is None:
            piece_weight = self._own_or_inherited(pid, "piece_weight_g")
        density = self._own_or_inherited(pid, "density_g_ml")
        facts = Facts(
            content_amount=content_amount,
            content_unit=content_unit,
            pack_count=pack_count,
            density_g_ml=density,
            piece_weight_g=piece_weight,
            piece_weight_exact=exact,
        )
        self._facts[pid] = facts
        return facts

    def _own_or_inherited(self, pid: int, column: str) -> float | None:
        for node in [pid, *self.ancestors(pid)]:
            p = self.products.get(node)
            if p is None:
                continue
            value = p[column]
            if column == "density_g_ml" and not plausible_density(value):
                value = None
            if value:
                return float(value)
            legacy = self._legacy.get(node, {}).get(column)
            if legacy:
                return legacy
        return None

    def _fold_legacy_rows(self, rows: Iterable[dict]) -> dict[int, dict]:
        densities: dict[int, list[float]] = defaultdict(list)
        out: dict[int, dict] = defaultdict(dict)
        for r in rows:
            src, dst = self.unit(r["from_unit_id"]), self.unit(r["to_unit_id"])
            factor = r["factor"]
            if src is None or dst is None or not factor or factor <= 0:
                continue
            if not src.base_factor or not dst.base_factor:
                continue
            pid = r["product_id"]
            dims = (src.dimension, dst.dimension)
            if dims == (MASS, VOLUME):
                densities[pid].append(src.base_factor / (factor * dst.base_factor))
            elif dims == (VOLUME, MASS):
                densities[pid].append(factor * dst.base_factor / src.base_factor)
            elif src.dimension == COUNT and dst.dimension in (MASS, VOLUME):
                # On a SKU "1 kpl = 65 g" is one retail item; on a concept it
                # is one natural piece.
                if self.is_sku(pid):
                    out[pid].setdefault("content", (factor, dst))
                elif dst.dimension == MASS:
                    out[pid].setdefault("piece_weight_g", factor * dst.base_factor)
            elif dst.dimension == COUNT and src.dimension in (MASS, VOLUME):
                if self.is_sku(pid):
                    out[pid].setdefault("content", (1.0 / factor, src))
                elif src.dimension == MASS:
                    out[pid].setdefault("piece_weight_g", src.base_factor / factor)
        for pid, values in densities.items():
            plausible = [v for v in values if plausible_density(v)]
            if plausible:
                out[pid]["density_g_ml"] = statistics.median(plausible)
        return out

    # ── stock ──────────────────────────────────────────────────────────────
    def lots(self, pids: Iterable[int]) -> list[dict]:
        """Lots with amount > 0 for the given products, FIFO ordered."""
        if self._lots is None:
            self._lots = defaultdict(list)
            for r in self.conn.execute(
                "SELECT id, product_id, amount, amount_opened, unit_id, "
                "best_before_date, purchased_date FROM stock WHERE amount > 0"
            ).fetchall():
                self._lots[r["product_id"]].append(r)
        # Filter at call time: cook mutates cached lots as it deducts them.
        out = [lot for pid in pids for lot in self._lots.get(pid, []) if lot["amount"] > 0]
        out.sort(key=lambda r: (
            r["best_before_date"] is None, r["best_before_date"] or "",
            r["purchased_date"] or "", r["id"],
        ))
        return out
