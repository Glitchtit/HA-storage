"""How much of a product (and everything under it) is in stock, in any unit.

One function answers "have vs need" for every flow: recipe detail and
availability, to-shopping, cook, min-stock sync, bundles and product subtree
totals. Conversion goes through quantity.convert with each lot's own SKU
facts, so a 2 kg flour bag counts as ~33 dl and a 10-egg carton as 10 eggs.
"""
from __future__ import annotations

from dataclasses import dataclass

from facts import Catalog
from quantity import VAGUE, Unit, convert

EPS = 1e-9


@dataclass
class Have:
    amount: float = 0.0       # convertible stock, in the requested unit
    unopened: float = 0.0     # part of `amount` held in unopened units
    approx: bool = False      # an estimate (density, piece weight) was used
    unknown_lots: int = 0     # lots that could not be converted
    lots: int = 0             # lots with amount > 0 in the subtree

    @property
    def opened(self) -> float:
        return max(0.0, self.amount - self.unopened)


def have(cat: Catalog, product_id: int, unit: Unit, *, target_dim: str | None = None) -> Have:
    """Stock of product_id and all its descendants, expressed in `unit`.

    `target_dim` overrides the unit's dimension (pass cat.stock_dim(...) when
    asking in a product's own unit; leave None for recipe needs, where kpl
    means natural pieces).
    """
    out = Have()
    subtree = [product_id, *cat.descendants(product_id)]
    for lot in cat.lots(subtree):
        out.lots += 1
        lot_unit = cat.unit(lot["unit_id"])
        if lot_unit is None:
            out.unknown_lots += 1
            continue
        conv = convert(
            lot["amount"], lot_unit, unit, cat.facts(lot["product_id"]),
            from_dim=cat.stock_dim(lot["product_id"], lot_unit), to_dim=target_dim,
        )
        if conv is None:
            out.unknown_lots += 1
            continue
        amount = float(lot["amount"])
        opened = min(max(float(lot["amount_opened"] or 0), 0.0), amount)
        out.amount += conv.amount
        out.unopened += conv.amount * (amount - opened) / amount if amount > 0 else 0.0
        out.approx = out.approx or conv.approx
    return out


def need_status(cat: Catalog, product_id: int, need: float, unit: Unit | None) -> dict:
    """Status of one recipe need against the product's subtree stock.

    green  enough (or staple, or to-taste with something in stock)
    yellow some but not enough, amount unknown, to-taste with nothing in stock,
           or covered only by opened packs and the need is over half of them
    red    nothing at all
    """
    product = cat.product(product_id) or {}
    to_taste = need <= EPS or unit is None or unit.dimension == VAGUE
    h = have(cat, product_id, unit) if unit is not None and not to_taste else None
    if h is None:
        h = Have(lots=len(cat.lots([product_id, *cat.descendants(product_id)])))

    if product.get("staple"):
        status = "green"
    elif to_taste:
        status = "green" if h.lots else "yellow"
    elif h.amount >= need - EPS:
        relies_on_opened = h.unopened < need - EPS
        status = "yellow" if relies_on_opened and (need - h.unopened) > 0.5 * h.opened else "green"
    elif h.amount > EPS or h.unknown_lots:
        status = "yellow"
    else:
        status = "red"

    return {
        "status": status,
        "available": None if product.get("staple") else round(h.amount, 3),
        "available_unopened": round(h.unopened, 3),
        "approx": h.approx,
        "unknown_lots": h.unknown_lots,
        "to_taste": to_taste,
        "have": h,
    }


def own_unit_have(cat: Catalog, product_id: int) -> Have:
    """Subtree stock in the product's own unit: retail packages for a SKU
    counted in kpl, natural pieces / grams / dl for a concept."""
    product = cat.product(product_id) or {}
    unit = cat.unit(product.get("unit_id"))
    if unit is None:
        return Have()
    return have(cat, product_id, unit, target_dim=cat.stock_dim(product_id, unit))
