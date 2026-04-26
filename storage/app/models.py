"""Pydantic models for request/response validation."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── Units ──────────────────────────────────────────────────────────────────

class UnitCreate(BaseModel):
    name: str
    abbreviation: str
    name_plural: str = ""

class Unit(UnitCreate):
    id: int

# ── Locations ──────────────────────────────────────────────────────────────

class LocationCreate(BaseModel):
    name: str
    description: str = ""

class Location(LocationCreate):
    id: int

# ── Product Groups ─────────────────────────────────────────────────────────

class ProductGroupCreate(BaseModel):
    name: str
    description: str = ""

class ProductGroup(ProductGroupCreate):
    id: int

# ── Products ───────────────────────────────────────────────────────────────

class ProductCreate(BaseModel):
    name: str
    description: str = ""
    parent_id: int | None = None
    location_id: int | None = None
    product_group_id: int | None = None
    unit_id: int = Field(..., description="Default unit for this product")
    default_best_before_days: int = 60
    min_stock_amount: float = 0
    picture_filename: str | None = None
    active: bool = True

class ProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    parent_id: int | None = None
    location_id: int | None = None
    product_group_id: int | None = None
    unit_id: int | None = None
    default_best_before_days: int | None = None
    min_stock_amount: float | None = None
    picture_filename: str | None = None
    active: bool | None = None

class Product(BaseModel):
    id: int
    name: str
    description: str | None = None
    parent_id: int | None
    location_id: int | None
    product_group_id: int | None
    unit_id: int
    default_best_before_days: int
    min_stock_amount: float
    picture_filename: str | None
    active: bool
    created_at: str
    updated_at: str

class ProductDetail(Product):
    """Product with related data included."""
    children: list[Product] = []
    barcodes: list["Barcode"] = []
    stock_amount: float = 0
    stock_opened: float = 0
    matched_pack_size: float = 1.0

# ── Barcodes ───────────────────────────────────────────────────────────────

class BarcodeCreate(BaseModel):
    product_id: int
    barcode: str
    pack_size: float = 1
    pack_unit_id: int | None = None

class BarcodeUpdate(BaseModel):
    product_id: int | None = None
    pack_size: float | None = None
    pack_unit_id: int | None = None

class Barcode(BaseModel):
    id: int
    product_id: int
    barcode: str
    pack_size: float
    pack_unit_id: int | None
    created_at: str

# ── Stock ──────────────────────────────────────────────────────────────────

class StockAdd(BaseModel):
    product_id: int
    amount: float = 1
    unit_id: int | None = None
    location_id: int | None = None
    best_before_date: str | None = None

class StockConsume(BaseModel):
    product_id: int
    amount: float = 1

class StockOpen(BaseModel):
    product_id: int
    amount: float = 1

class StockTransfer(BaseModel):
    product_id: int
    amount: float
    from_location_id: int
    to_location_id: int

class StockEntry(BaseModel):
    id: int
    product_id: int
    location_id: int
    amount: float
    amount_opened: float
    unit_id: int
    best_before_date: str | None
    purchased_date: str | None
    created_at: str

class StockSummary(BaseModel):
    """Aggregated stock view per product."""
    product_id: int
    product_name: str
    amount: float
    amount_opened: float
    min_stock_amount: float
    product: Product

class StockEntryWithProduct(StockEntry):
    """Stock entry joined with its product name — used by aggregate listings."""
    product_name: str

# ── Unit Conversions ───────────────────────────────────────────────────────

class ConversionCreate(BaseModel):
    from_unit_id: int
    to_unit_id: int
    factor: float
    product_id: int | None = None

class Conversion(ConversionCreate):
    id: int

class ConversionResolve(BaseModel):
    from_unit_id: int
    to_unit_id: int
    product_id: int | None = None

class ConversionResult(BaseModel):
    factor: float
    path: list[int]  # unit IDs in the conversion chain

# ── Recipes ────────────────────────────────────────────────────────────────

class IngredientCreate(BaseModel):
    product_id: int
    amount: float = 1
    unit_id: int
    note: str = ""
    sort_order: int = 0

class IngredientUpdate(BaseModel):
    product_id: int | None = None
    amount: float | None = None
    unit_id: int | None = None
    note: str | None = None
    sort_order: int | None = None

class Ingredient(BaseModel):
    id: int
    recipe_id: int
    product_id: int
    amount: float
    unit_id: int
    note: str | None = None
    sort_order: int

class RecipeCreate(BaseModel):
    name: str
    description: str = ""
    source_url: str | None = None
    servings: float = 4
    picture_filename: str | None = None
    ingredients: list[IngredientCreate] = []

class RecipeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    source_url: str | None = None
    servings: float | None = None
    picture_filename: str | None = None

class Recipe(BaseModel):
    id: int
    name: str
    description: str | None = None
    source_url: str | None
    servings: float
    picture_filename: str | None
    created_at: str

class RecipeDetail(Recipe):
    """Recipe with ingredients and stock status."""
    ingredients: list["IngredientDetail"] = []

class IngredientDetail(Ingredient):
    """Ingredient with product and stock info."""
    product_name: str = ""
    unit_abbreviation: str = ""
    stock_amount: float = 0
    stock_unit_id: int | None = None

# ── Shopping List ──────────────────────────────────────────────────────────

class ShoppingItemCreate(BaseModel):
    product_id: int
    amount: float = 1
    unit_id: int | None = None
    note: str = ""
    recipe_id: int | None = None

class ShoppingItemUpdate(BaseModel):
    amount: float | None = None
    done: bool | None = None
    note: str | None = None

class ShoppingItem(BaseModel):
    id: int
    product_id: int
    amount: float
    unit_id: int | None
    note: str
    done: bool
    recipe_id: int | None
    auto_added: bool = False
    ha_item_name: str | None = None
    created_at: str

# ── Barcode Queue ──────────────────────────────────────────────────────────

class BarcodeQueueCreate(BaseModel):
    barcode: str
    source: str = "scan"
    import_stock_amount: float | None = None

class BarcodeQueueUpdate(BaseModel):
    status: str | None = None
    result_product_id: int | None = None
    error_message: str | None = None

class BarcodeQueueEntry(BaseModel):
    id: int
    barcode: str
    source: str
    status: str
    result_product_id: int | None
    error_message: str | None
    import_stock_amount: float | None = None
    created_at: str

# ── Config ─────────────────────────────────────────────────────────────────

class ConfigEntry(BaseModel):
    key: str
    value: str

# ── Migration ──────────────────────────────────────────────────────────────

class GrocyMigrationRequest(BaseModel):
    grocy_url: str
    api_key: str

class MigrationResult(BaseModel):
    barcodes_queued: int = 0
    barcodes_skipped: int = 0
    errors: list[str] = []

# ── Health ─────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    db_tables: int = 0


# Rebuild forward refs for nested models
ProductDetail.model_rebuild()
RecipeDetail.model_rebuild()
