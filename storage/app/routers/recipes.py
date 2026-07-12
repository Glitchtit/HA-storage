"""Recipe CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

import tree
from cook_recipe import cook_recipe
from models import (
    CookRecipeRequest,
    CookRecipeResponse,
    Ingredient,
    IngredientCreate,
    IngredientDetail,
    IngredientUpdate,
    Recipe,
    RecipeCreate,
    RecipeDetail,
    RecipeUpdate,
)

router = APIRouter(tags=["recipes"])


def _get_db():
    from main import get_connection
    return get_connection()


@router.get("/recipes", response_model=list[Recipe])
def list_recipes():
    return _get_db().execute("SELECT * FROM recipes ORDER BY name").fetchall()


@router.get("/recipes/{recipe_id}", response_model=RecipeDetail)
def get_recipe(recipe_id: int):
    conn = _get_db()
    recipe = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not recipe:
        raise HTTPException(404, f"Recipe {recipe_id} not found")

    rows = conn.execute("""
        SELECT ri.*,
               p.name as product_name,
               u.abbreviation as unit_abbreviation,
               COALESCE((
                   SELECT SUM(s.amount) FROM stock s
                   WHERE s.product_id = ri.product_id
                      OR (
                          ri.specificity = 'loose'
                          AND s.product_id IN (
                              WITH RECURSIVE d(id) AS (
                                  SELECT id FROM products WHERE parent_id = ri.product_id
                                  UNION
                                  SELECT p.id FROM products p JOIN d ON p.parent_id = d.id
                              )
                              SELECT id FROM d
                          )
                      )
               ), 0) as stock_amount,
               p.unit_id as stock_unit_id
        FROM recipe_ingredients ri
        JOIN products p ON p.id = ri.product_id
        JOIN units u ON u.id = ri.unit_id
        WHERE ri.recipe_id = ?
        ORDER BY ri.sort_order, ri.id
    """, (recipe_id,)).fetchall()

    ingredients = [IngredientDetail(**r) for r in rows]
    return RecipeDetail(**recipe, ingredients=ingredients)


def _convert_amount(
    amount: float, from_unit: int, to_unit: int, product_id: int, conversions: list[dict]
) -> float | None:
    """BFS over global + product-specific conversions (port of HA-recipes logic)."""
    if from_unit == to_unit:
        return amount
    graph: dict[int, dict[int, float]] = {}
    for c in conversions:
        cpid = c["product_id"]
        if cpid is not None and int(cpid) != product_id:
            continue
        f, t, factor = int(c["from_unit_id"]), int(c["to_unit_id"]), float(c["factor"])
        graph.setdefault(f, {})[t] = factor
        if factor != 0:
            graph.setdefault(t, {})[f] = 1.0 / factor
    visited = {from_unit}
    queue = [(from_unit, amount)]
    while queue:
        unit, amt = queue.pop(0)
        if unit == to_unit:
            return amt
        for nxt, factor in graph.get(unit, {}).items():
            if nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, amt * factor))
    return None


@router.get("/recipes/{recipe_id}/availability")
def recipe_availability(recipe_id: int):
    """Per-ingredient stock status with recursive subtree aggregation.

    Status semantics (port of HA-recipes `_get_recipe_detail`):
    - staple product -> always green, available null.
    - amount_needed == 0 ("to taste") -> green if any subtree stock > 0 else yellow.
    - otherwise sum subtree stock converted into recipe units (same-unit in
      kpl uses pack_count; any other same-unit match uses 1:1; cross-unit
      uses per-product conversion BFS; unconvertible stock counts 0 toward
      available but marks the row).
    - available >= needed -> green, except yellow when total unopened pieces
      <= 1 and something is opened.
    - available < needed but subtree has unconvertible stock >= 1 piece ->
      yellow (have some, cannot verify amount).
    - else red.
    """
    conn = _get_db()
    if not conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone():
        raise HTTPException(404, f"Recipe {recipe_id} not found")

    kpl_row = conn.execute(
        "SELECT id FROM units WHERE abbreviation = 'kpl'"
    ).fetchone()
    kpl_unit_id = kpl_row["id"] if kpl_row else None

    conversions = [dict(r) for r in conn.execute("SELECT * FROM unit_conversions").fetchall()]
    rows = conn.execute(
        """
        SELECT ri.id AS ingredient_id, ri.product_id, ri.amount, ri.unit_id,
               ri.specificity, p.name AS product_name, p.parent_id,
               COALESCE(p.staple, 0) AS staple, u.abbreviation AS unit_abbrev
        FROM recipe_ingredients ri
        JOIN products p ON p.id = ri.product_id
        JOIN units u ON u.id = ri.unit_id
        WHERE ri.recipe_id = ?
        ORDER BY ri.sort_order, ri.id
        """,
        (recipe_id,),
    ).fetchall()

    out = []
    for ri in rows:
        pid = ri["product_id"]
        needed = ri["amount"] or 0
        subtree = [pid] + tree.descendant_ids(conn, pid)
        qmarks = ",".join("?" * len(subtree))
        stock_rows = conn.execute(
            f"""
            SELECT s.product_id, s.amount, s.amount_opened, p.unit_id, p.pack_count
            FROM stock s JOIN products p ON p.id = s.product_id
            WHERE s.product_id IN ({qmarks})
            """,
            subtree,
        ).fetchall()

        available: float | None = 0.0
        pieces = 0.0
        opened = 0.0
        unconvertible_pieces = 0.0
        for s in stock_rows:
            amt = s["amount"] or 0
            if amt <= 0:
                continue
            pieces += amt
            opened += s["amount_opened"] or 0
            if s["unit_id"] == ri["unit_id"]:
                # pack_count multiplies discrete piece counts (e.g. a 10-pack
                # of eggs) into the recipe's kpl need. Applying it to any
                # same-unit match (e.g. grams) would inflate stock that
                # merely shares a unit with the recipe by the pack size.
                multiplier = s["pack_count"] or 1 if ri["unit_id"] == kpl_unit_id else 1
                available += amt * multiplier
            else:
                conv = _convert_amount(
                    amt, s["unit_id"], ri["unit_id"], s["product_id"], conversions
                )
                if conv is not None:
                    available += conv
                else:
                    unconvertible_pieces += amt

        if ri["staple"]:
            status, available = "green", None
        elif needed == 0:
            status = "green" if pieces > 0 else "yellow"
        elif available >= needed:
            status = "yellow" if pieces <= 1 and opened >= 1 else "green"
        elif unconvertible_pieces >= 1:
            status = "yellow"
        else:
            status = "red"

        out.append({
            "ingredient_id": ri["ingredient_id"],
            "product_id": pid,
            "product_name": ri["product_name"],
            "parent_id": ri["parent_id"],
            "amount_needed": needed,
            "unit_id": ri["unit_id"],
            "unit_abbrev": ri["unit_abbrev"],
            "specificity": ri["specificity"] or "loose",
            "status": status,
            "available": available,
        })

    return {"recipe_id": recipe_id, "ingredients": out}


@router.post("/recipes", response_model=RecipeDetail, status_code=201)
def create_recipe(body: RecipeCreate):
    conn = _get_db()
    cur = conn.execute(
        """INSERT INTO recipes (name, description, source_url, servings, picture_filename)
           VALUES (?, ?, ?, ?, ?)""",
        (body.name, body.description, body.source_url, body.servings, body.picture_filename),
    )
    recipe_id = cur.lastrowid

    for idx, ing in enumerate(body.ingredients):
        conn.execute(
            """INSERT INTO recipe_ingredients (recipe_id, product_id, amount, unit_id, note, sort_order, specificity)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (recipe_id, ing.product_id, ing.amount, ing.unit_id, ing.note,
             ing.sort_order if ing.sort_order else idx,
             ing.specificity if ing.specificity in ("strict", "loose") else "loose"),
        )

    conn.commit()
    return get_recipe(recipe_id)


@router.put("/recipes/{recipe_id}", response_model=Recipe)
def update_recipe(recipe_id: int, body: RecipeUpdate):
    conn = _get_db()
    existing = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not existing:
        raise HTTPException(404, f"Recipe {recipe_id} not found")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return existing
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE recipes SET {set_clause} WHERE id = ?",
        list(updates.values()) + [recipe_id],
    )
    conn.commit()
    return conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()


@router.delete("/recipes/{recipe_id}", status_code=204)
def delete_recipe(recipe_id: int):
    conn = _get_db()
    if not conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone():
        raise HTTPException(404, f"Recipe {recipe_id} not found")
    conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
    conn.commit()


@router.post("/recipes/{recipe_id}/cook", response_model=CookRecipeResponse)
def cook(recipe_id: int, body: CookRecipeRequest | None = None):
    """Cook the recipe: deduct ingredients from stock (FIFO) and queue any
    shortfall on the shopping list with a "Reseptistä: <name>" note.

    Pass an optional ``servings`` count to scale the recipe up or down; defaults
    to the recipe's stored servings.
    """
    conn = _get_db()
    try:
        return cook_recipe(conn, recipe_id, servings=body.servings if body else None)
    except ValueError as exc:
        raise HTTPException(404 if "not found" in str(exc) else 400, str(exc)) from exc


@router.post("/recipes/{recipe_id}/to-shopping", status_code=201)
def recipe_to_shopping(recipe_id: int):
    """Add missing recipe ingredients to shopping list."""
    conn = _get_db()
    recipe = conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not recipe:
        raise HTTPException(404, f"Recipe {recipe_id} not found")

    ingredients = conn.execute(
        """SELECT ri.*, p.name AS product_name
           FROM recipe_ingredients ri
           JOIN products p ON p.id = ri.product_id
           WHERE ri.recipe_id = ?""",
        (recipe_id,),
    ).fetchall()

    added = 0
    for ing in ingredients:
        stock = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM stock WHERE product_id = ?",
            (ing["product_id"],),
        ).fetchone()

        needed = ing["amount"] - stock["total"]
        if needed > 0:
            conn.execute(
                # Cache ha_item_name so active-only shopping-list consumers can
                # render rows bound to inactive stub products (see cook_recipe).
                """INSERT INTO shopping_list (product_id, amount, unit_id, note, recipe_id, ha_item_name)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ing["product_id"], needed, ing["unit_id"], ing["note"], recipe_id, ing["product_name"]),
            )
            added += 1

    conn.commit()
    return {"added": added}


# ── Individual ingredient management ───────────────────────────────────────

@router.post("/recipes/{recipe_id}/ingredients", response_model=Ingredient, status_code=201)
def add_ingredient(recipe_id: int, body: IngredientCreate):
    conn = _get_db()
    if not conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone():
        raise HTTPException(404, f"Recipe {recipe_id} not found")
    cur = conn.execute(
        """INSERT INTO recipe_ingredients (recipe_id, product_id, amount, unit_id, note, sort_order, specificity)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (recipe_id, body.product_id, body.amount, body.unit_id, body.note, body.sort_order,
         body.specificity if body.specificity in ("strict", "loose") else "loose"),
    )
    conn.commit()
    return conn.execute("SELECT * FROM recipe_ingredients WHERE id = ?", (cur.lastrowid,)).fetchone()


@router.put("/recipes/{recipe_id}/ingredients/{ingredient_id}", response_model=Ingredient)
def update_ingredient(recipe_id: int, ingredient_id: int, body: IngredientUpdate):
    conn = _get_db()
    existing = conn.execute(
        "SELECT * FROM recipe_ingredients WHERE id = ? AND recipe_id = ?",
        (ingredient_id, recipe_id),
    ).fetchone()
    if not existing:
        raise HTTPException(404, f"Ingredient {ingredient_id} not found in recipe {recipe_id}")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return existing
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE recipe_ingredients SET {set_clause} WHERE id = ?",
        list(updates.values()) + [ingredient_id],
    )
    conn.commit()
    return conn.execute("SELECT * FROM recipe_ingredients WHERE id = ?", (ingredient_id,)).fetchone()


@router.delete("/recipes/{recipe_id}/ingredients/{ingredient_id}", status_code=204)
def delete_ingredient(recipe_id: int, ingredient_id: int):
    conn = _get_db()
    if not conn.execute(
        "SELECT id FROM recipe_ingredients WHERE id = ? AND recipe_id = ?",
        (ingredient_id, recipe_id),
    ).fetchone():
        raise HTTPException(404, f"Ingredient {ingredient_id} not found in recipe {recipe_id}")
    conn.execute("DELETE FROM recipe_ingredients WHERE id = ?", (ingredient_id,))
    conn.commit()
