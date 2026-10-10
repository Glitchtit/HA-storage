"""Recipe CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from availability import EPS, need_status
from cook_recipe import cook_recipe
from facts import Catalog
from recipe_rows import add_recipe_need, recipe_already_listed
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
    RecipeToShoppingRequest,
    RecipeUpdate,
)

router = APIRouter(tags=["recipes"])


def _get_db():
    from main import get_connection
    return get_connection()


@router.get("/recipes", response_model=list[Recipe])
def list_recipes():
    return _get_db().execute("SELECT * FROM recipes ORDER BY name").fetchall()


_INGREDIENT_ROWS_SQL = """
    SELECT ri.*, p.name AS product_name, p.parent_id,
           u.abbreviation AS unit_abbreviation
    FROM recipe_ingredients ri
    JOIN products p ON p.id = ri.product_id
    JOIN units u ON u.id = ri.unit_id
    WHERE ri.recipe_id = ?
    ORDER BY ri.sort_order, ri.id
"""


@router.get("/recipes/{recipe_id}", response_model=RecipeDetail)
def get_recipe(recipe_id: int):
    conn = _get_db()
    recipe = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not recipe:
        raise HTTPException(404, f"Recipe {recipe_id} not found")

    cat = Catalog(conn)
    ingredients = []
    for row in conn.execute(_INGREDIENT_ROWS_SQL, (recipe_id,)).fetchall():
        st = need_status(cat, row["product_id"], row["amount"] or 0, cat.unit(row["unit_id"]))
        ingredients.append(IngredientDetail(
            **row,
            stock_amount=st["available"] or 0,
            stock_unit_id=row["unit_id"],
            status=st["status"],
            approx=st["approx"],
        ))
    return RecipeDetail(**recipe, ingredients=ingredients)


@router.get("/recipes/{recipe_id}/availability")
def recipe_availability(recipe_id: int):
    """Per-ingredient stock status over the bound product's whole subtree.

    `available` is expressed in the ingredient's own unit (None for staples).
    See availability.need_status for the green/yellow/red rules.
    """
    conn = _get_db()
    if not conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone():
        raise HTTPException(404, f"Recipe {recipe_id} not found")

    cat = Catalog(conn)
    out = []
    for ri in conn.execute(_INGREDIENT_ROWS_SQL, (recipe_id,)).fetchall():
        st = need_status(cat, ri["product_id"], ri["amount"] or 0, cat.unit(ri["unit_id"]))
        out.append({
            "ingredient_id": ri["id"],
            "product_id": ri["product_id"],
            "product_name": ri["product_name"],
            "parent_id": ri["parent_id"],
            "amount_needed": ri["amount"] or 0,
            "unit_id": ri["unit_id"],
            "unit_abbrev": ri["unit_abbreviation"],
            "specificity": ri["specificity"] or "loose",
            "status": st["status"],
            "available": st["available"],
            "available_unopened": st["available_unopened"],
            "approx": st["approx"],
            "unknown_lots": st["unknown_lots"],
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
def recipe_to_shopping(recipe_id: int, body: RecipeToShoppingRequest | None = None):
    """Put a recipe's needs on the shopping list.

    mode "missing" adds each ingredient's shortfall against whole-subtree
    stock; "missing_and_opened" also adds what only opened packs cover; "all"
    adds every full need. Staples are never added; a to-taste ingredient is
    added as one package when nothing of it is in stock (or in mode "all").
    Pressing the button again does not double rows that are already listed.
    """
    body = body or RecipeToShoppingRequest()
    if body.mode not in ("missing", "missing_and_opened", "all"):
        raise HTTPException(400, "mode must be missing, missing_and_opened or all")
    conn = _get_db()
    recipe = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not recipe:
        raise HTTPException(404, f"Recipe {recipe_id} not found")
    ratio = 1.0
    if body.servings:
        ratio = float(body.servings) / float(recipe["servings"] or 1)

    cat = Catalog(conn)
    created = merged = 0
    for ing in conn.execute(_INGREDIENT_ROWS_SQL, (recipe_id,)).fetchall():
        pid = ing["product_id"]
        if (cat.product(pid) or {}).get("staple"):
            continue
        if recipe_already_listed(conn, pid, recipe_id, recipe["name"]):
            continue
        unit = cat.unit(ing["unit_id"])
        need = float(ing["amount"] or 0) * ratio
        st = need_status(cat, pid, need, unit)
        if st["to_taste"]:
            if body.mode == "all" or st["have"].lots == 0:
                _, was_merged = add_recipe_need(
                    conn, cat, product_id=pid, amount=1, unit_id=None,
                    recipe_id=recipe_id, recipe_name=recipe["name"])
                merged += was_merged
                created += not was_merged
            continue
        h = st["have"]
        if body.mode == "all":
            gap = need
        elif h.amount <= EPS and h.unknown_lots:
            gap = 0.0  # something is in stock, amount unknown: do not guess
        elif body.mode == "missing_and_opened":
            gap = need - h.unopened
        else:
            gap = need - h.amount
        if gap > EPS:
            _, was_merged = add_recipe_need(
                conn, cat, product_id=pid, amount=gap, unit_id=ing["unit_id"],
                recipe_id=recipe_id, recipe_name=recipe["name"])
            merged += was_merged
            created += not was_merged
    conn.commit()
    return {"added": created + merged, "created": created, "merged": merged}


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
