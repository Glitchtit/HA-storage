"""Pure, deterministic product-name resolution for the by-name shopping service.

No Home Assistant imports live here so the resolver is unit-testable in a bare
pytest environment. The handler in services.py supplies the product list.
"""

from __future__ import annotations

import re


def _starts_a_word(needle: str, haystack: str) -> bool:
    """True if `needle` occurs in `haystack` at the start of a word."""
    return re.search(r"(?<!\w)" + re.escape(needle), haystack) is not None


def resolve_product_by_name(name: str | None, products: list[dict]) -> tuple[str, dict | list[dict] | None]:
    """Resolve a free-text product name against existing Storage products.

    Strategy (deterministic, casefold for Finnish names):
      1. Case-insensitive EXACT match on `name` -> ("added", {id, name}).
      2. Else collect substring candidates (the query appears anywhere in the
         product name, casefolded). Exactly one that starts at a word
         boundary -> ("added", {id, name}). A single hit buried mid-word
         ("egg" in "Reggiano") is too weak to act on -> ("ambiguous", [it]).
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
        only = candidates[0]
        if _starts_a_word(needle, only["name"].casefold()):
            return ("added", only)
        return ("ambiguous", candidates)
    if len(candidates) > 1:
        return ("ambiguous", candidates)
    return ("not_found", None)


def describe_add_failure(
    name: str,
    status: str,
    candidates: list[dict] | None,
    history: list[dict],
    limit: int = 60,
) -> str:
    """Build the error text an agent sees when a by-name add can't resolve.

    `history` is /api/stats/top-purchased rows ({product_id, product_name,
    event_count, ...}). It is re-sorted by how often each product is bought
    (event_count) so an agent can map a generic word like "milk" to the
    product this household usually buys, then retry with its exact name.
    """
    counts = {h["product_id"]: h.get("event_count", 0) for h in history}

    def _bought(pid: int) -> str:
        n = counts.get(pid, 0)
        return f"bought {n}×" if n else "never bought"

    if status == "ambiguous" and candidates:
        ranked = sorted(candidates, key=lambda c: counts.get(c["id"], 0), reverse=True)
        listed = "; ".join(f"{c['name']} ({_bought(c['id'])})" for c in ranked[:8])
        msg = f"'{name}' did not pick out one Storage product. Possible matches: {listed}."
        bought = [c for c in candidates if counts.get(c["id"])]
        if len(bought) == 1:
            msg += (
                f" The household usually buys {bought[0]['name']}; add that one by"
                " repeating the add with that full name, without asking, and say"
                " which product you added."
            )
            return msg
        msg += (
            " If one of them is clearly the item meant, add it by repeating the add"
            " with its full name. Otherwise ask the user which of these they want,"
            " naming the options in short form."
        )
    else:
        msg = (
            f"No Storage product name contains '{name}'. Product names are Finnish:"
            " retry the add once with the Finnish word for the item, singular basic"
            " form (e.g. eggs = kananmuna, milk = maito, tomatoes = tomaatti,"
            " bread = leipä)."
        )

    usual = sorted(history, key=lambda h: h.get("event_count", 0), reverse=True)[:limit]
    if usual:
        listed = "; ".join(f"{h['product_name']} ({h.get('event_count', 0)}×)" for h in usual)
        msg += (
            f" Products this household usually buys, most frequent first: {listed}."
            " If one of these is clearly what was meant, prefer it: add it by its"
            " full name without asking, and say which product you added."
        )
    return msg
