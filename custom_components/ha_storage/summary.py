"""Todo summaries for Storage shopping rows — pure, no Home Assistant imports."""

from __future__ import annotations


def _amount_text(amount: float) -> str:
    text = f"{float(amount):.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def todo_summary(item: dict, product_names: dict[int, str]) -> str:
    """'Kerma 3,5 dl' for a recipe need, 'Maito ×2' for packages, 'Maito' for one.

    Uses the row's display block from Storage (qty_text, unit_dimension); rows
    from a Storage without one show a plain package count.
    """
    name = product_names.get(item["product_id"]) or item.get("ha_item_name") or "Unknown"
    display = item.get("display") or {}
    if display.get("qty_text") and display.get("unit_dimension") not in (None, "package"):
        return f"{name} {display['qty_text']}"
    amount = item.get("amount") or 1
    return name if float(amount) == 1 else f"{name} ×{_amount_text(amount)}"
