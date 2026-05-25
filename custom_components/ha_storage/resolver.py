"""Pure, deterministic product-name resolution for the by-name shopping service.

No Home Assistant imports live here so the resolver is unit-testable in a bare
pytest environment. The handler in services.py supplies the product list.
"""

from __future__ import annotations


def resolve_product_by_name(name: str | None, products: list[dict]) -> tuple[str, dict | list[dict] | None]:
    """Resolve a free-text product name against existing Storage products.

    Strategy (deterministic, casefold for Finnish names):
      1. Case-insensitive EXACT match on `name` -> ("added", {id, name}).
      2. Else collect substring candidates (the query appears anywhere in the
         product name, casefolded). Exactly one -> ("added", {id, name}).
      3. Multiple candidates -> ("ambiguous", [{id, name}, ...]).
      4. Zero candidates -> ("not_found", None).

    `products` is the list of Storage product dicts (each has at least `id` and
    `name`). The returned match/candidate dicts are trimmed to `{id, name}`.
    """
    query = (name or "").strip()
    if not query:
        return ("not_found", None)
    needle = query.casefold()

    candidates = []
    for product in products:
        pname = product.get("name") or ""
        folded = pname.casefold()
        slim = {"id": product["id"], "name": pname}
        if folded == needle:
            # First exact match wins; exact short-circuits any substring logic.
            return ("added", slim)
        if needle in folded:
            candidates.append(slim)

    if len(candidates) == 1:
        return ("added", candidates[0])
    if len(candidates) > 1:
        return ("ambiguous", candidates)
    return ("not_found", None)
