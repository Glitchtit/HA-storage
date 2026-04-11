"""Recipe CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from models import (
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
                      OR s.product_id IN (SELECT id FROM products WHERE parent_id = ri.product_id)
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
            """INSERT INTO recipe_ingredients (recipe_id, product_id, amount, unit_id, note, sort_order)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (recipe_id, ing.product_id, ing.amount, ing.unit_id, ing.note,
             ing.sort_order if ing.sort_order else idx),
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


@router.post("/recipes/{recipe_id}/to-shopping", status_code=201)
def recipe_to_shopping(recipe_id: int):
    """Add missing recipe ingredients to shopping list."""
    conn = _get_db()
    recipe = conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not recipe:
        raise HTTPException(404, f"Recipe {recipe_id} not found")

    ingredients = conn.execute(
        "SELECT * FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,)
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
                """INSERT INTO shopping_list (product_id, amount, unit_id, note, recipe_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (ing["product_id"], needed, ing["unit_id"], ing["note"], recipe_id),
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
        """INSERT INTO recipe_ingredients (recipe_id, product_id, amount, unit_id, note, sort_order)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (recipe_id, body.product_id, body.amount, body.unit_id, body.note, body.sort_order),
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
