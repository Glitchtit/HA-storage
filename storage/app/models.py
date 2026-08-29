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
    unit_price: float | None = None
    unit_price_currency: str = "EUR"
    pack_count: float | None = None
    staple: bool = False

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
    unit_price: float | None = None
    unit_price_currency: str | None = None
    pack_count: float | None = None
    staple: bool | None = None

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
    unit_price: float | None = None
    unit_price_currency: str | None = None
    pack_count: float | None = None
    staple: int | bool = 0
    created_at: str
    updated_at: str
    stock_amount: float = 0
    stock_opened: float = 0
    # Aggregated stock from immediate children (parent_id = this product's id).
    # Always 0 for non-parent rows. Kept separate from stock_amount so
    # callers that care about "this exact SKU" (low-stock alerts, shopping
    # list) keep their semantics; UI surfaces interested in category-level
    # totals (the Products list) add this in for display.
    children_stock_amount: float = 0
    children_stock_opened: float = 0
    # Per-store assortment availability (see routers/stores.py). Populated by
    # the products read endpoints from product_availability ⋈ stores.
    stores: list["ProductStoreInfo"] = []

class ProductDetail(Product):
    """Product with related data included."""
    children: list[Product] = []
    barcodes: list["Barcode"] = []
    matched_pack_size: float = 1.0

# ── Stores ─────────────────────────────────────────────────────────────────

class StoreUpsert(BaseModel):
    name: str

class Store(BaseModel):
    id: str
    name: str
    created_at: str
    updated_at: str

class AvailabilityEntry(BaseModel):
    store_id: str
    available: bool
    price: float | None = None
    price_currency: str = "EUR"

class ProductStoreInfo(BaseModel):
    store_id: str
    name: str
    available: bool
    price: float | None = None
    price_currency: str | None = None
    checked_at: str
    source: str = "scraper"

class ManualAvailabilityCreate(BaseModel):
    """Manually assert "this store carries the product". Exactly one of
    store_id (existing registry store) or name (free-text; auto-registers a
    manual-<slug> store) must be given. available=False records an explicit
    "does not carry" override."""
    store_id: str | None = None
    name: str | None = None
    available: bool = True

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
    purchased_date: str | None = None
    price_paid: float | None = None
    note: str = ""

class StockConsume(BaseModel):
    product_id: int
    amount: float = 1
    note: str = ""
    spoiled: bool = False

class StockCorrectPurchase(BaseModel):
    product_id: int
    amount: float = 1
    note: str = ""

class StockOpen(BaseModel):
    product_id: int
    amount: float = 1
    note: str = ""

class StockTransfer(BaseModel):
    product_id: int
    amount: float
    from_location_id: int
    to_location_id: int
    note: str = ""

class StockSpoilLot(BaseModel):
    amount: float | None = None
    note: str = ""

class StockEntry(BaseModel):
    id: int
    product_id: int
    location_id: int
    amount: float
    amount_opened: float
    unit_id: int
    best_before_date: str | None
    best_before_days: int | None
    purchased_date: str | None
    price_paid: float | None = None
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

# ── Stock History & Statistics ─────────────────────────────────────────────

class StockHistoryEntry(BaseModel):
    id: int
    product_id: int
    event_type: str  # 'purchase' | 'consume' | 'open' | 'transfer' | 'spoil'
    amount: float
    unit_id: int | None = None
    location_id: int | None = None
    from_location_id: int | None = None
    stock_id: int | None = None
    note: str = ""
    unit_price: float | None = None
    created_at: str

class StockHistoryEntryWithProduct(StockHistoryEntry):
    product_name: str

class StatsSummary(BaseModel):
    events_total: int
    events_7d: int
    events_30d: int
    products_purchased_30d: int
    products_consumed_30d: int
    spoiled_30d: int

class StatsTopItem(BaseModel):
    product_id: int
    product_name: str
    total_amount: float
    event_count: int

class StatsTimelinePoint(BaseModel):
    day: str  # YYYY-MM-DD
    amount: float
    event_count: int

class StatsWasteBreakdown(BaseModel):
    product_id: int | None = None
    product_name: str | None = None
    location_id: int | None = None
    location_name: str | None = None
    category_id: int | None = None
    category_name: str | None = None
    amount: float
    value: float

class StatsWasteSeriesPoint(BaseModel):
    week: str  # ISO week start date (YYYY-MM-DD, Monday)
    amount: float
    value: float

class StatsWasteResponse(BaseModel):
    days: int
    currency: str = "EUR"
    total_amount: float
    total_value: float
    by_product: list[StatsWasteBreakdown] = []
    by_location: list[StatsWasteBreakdown] = []
    by_category: list[StatsWasteBreakdown] = []
    series: list[StatsWasteSeriesPoint] = []

class StatsStockValueGroup(BaseModel):
    group_id: int | None = None
    group_name: str
    value: float

class StatsStockValueResponse(BaseModel):
    total_value: float
    currency: str = "EUR"
    priced_amount: float
    unpriced_amount: float
    by_group: list[StatsStockValueGroup] = []

class StatsPurchaseCostsProduct(BaseModel):
    product_id: int
    product_name: str
    amount: float
    value: float

class StatsPurchaseCostsSeriesPoint(BaseModel):
    month: str  # YYYY-MM
    value: float

class StatsPurchaseCostsResponse(BaseModel):
    year: int
    month: int
    currency: str = "EUR"
    total_value: float
    event_count: int
    by_product: list[StatsPurchaseCostsProduct] = []
    series: list[StatsPurchaseCostsSeriesPoint] = []

class PredictedRunout(BaseModel):
    product_id: int
    product_name: str
    unit_id: int
    current_qty: float
    avg_daily: float
    days_to_runout: float

class StatsRunoutsResponse(BaseModel):
    horizon: int
    runouts: list[PredictedRunout] = []

class DigestExpiring(BaseModel):
    lot_id: int
    product_id: int
    product_name: str
    amount: float
    best_before_date: str | None
    days_left: int | None

class DigestSpoiler(BaseModel):
    product_id: int
    product_name: str
    amount: float
    value: float

class StatsDigestResponse(BaseModel):
    generated_at: str
    days: int = 30
    currency: str = "EUR"
    expiring_this_week: list[DigestExpiring] = []
    predicted_runouts_14d: list[PredictedRunout] = []
    waste_value_30d: float = 0
    waste_amount_30d: float = 0
    top_spoilers_30d: list[DigestSpoiler] = []

class StatsProductSummary(BaseModel):
    product_id: int
    purchased_total: float = 0
    consumed_total: float = 0
    spoiled_total: float = 0
    purchase_count: int = 0
    consume_count: int = 0
    avg_days_between_consumes: float | None = None
    last_purchase: str | None = None
    last_consume: str | None = None

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
    specificity: str = "loose"

class IngredientUpdate(BaseModel):
    product_id: int | None = None
    amount: float | None = None
    unit_id: int | None = None
    note: str | None = None
    sort_order: int | None = None
    specificity: str | None = None

class Ingredient(BaseModel):
    id: int
    recipe_id: int
    product_id: int
    amount: float
    unit_id: int
    note: str | None = None
    sort_order: int
    specificity: str = "loose"

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

class CookRecipeRequest(BaseModel):
    servings: float | None = Field(
        None,
        description="Cook this many servings. Defaults to the recipe's stored servings count.",
    )

class CookedIngredient(BaseModel):
    product_id: int
    product_name: str
    amount: float
    unit_id: int

class UnmatchedIngredient(BaseModel):
    product_id: int
    product_name: str
    amount: float
    unit_id: int
    reason: str

class CookRecipeResponse(BaseModel):
    recipe_id: int
    recipe_name: str
    servings: float
    deducted: list[CookedIngredient]
    shortfall_added: list[CookedIngredient]
    unmatched: list[UnmatchedIngredient]

# ── Shopping List ──────────────────────────────────────────────────────────

class ShoppingItemCreate(BaseModel):
    product_id: int
    amount: float = 1
    unit_id: int | None = None
    note: str = ""
    recipe_id: int | None = None
    auto_added: bool = False
    pinned: bool = False

class ShoppingItemUpdate(BaseModel):
    amount: float | None = None
    done: bool | None = None
    note: str | None = None
    pinned: bool | None = None

class ShoppingItem(BaseModel):
    id: int
    product_id: int
    amount: float
    unit_id: int | None
    note: str
    done: bool
    recipe_id: int | None
    bundle_id: int | None = None
    auto_added: bool = False
    ha_item_name: str | None = None
    pinned: bool = False
    created_at: str

class ShoppingProposalItem(BaseModel):
    product_id: int
    product_name: str
    unit_id: int
    current_qty: float
    weekly_rate: float
    days_to_zero: float
    suggested_amount: float
    reasoning: str

class ShoppingProposalResponse(BaseModel):
    lookback_weeks: int
    horizon_days: int
    proposal: list[ShoppingProposalItem]

class CadenceSuggestionItem(BaseModel):
    product_id: int
    product_name: str
    unit_id: int
    current_qty: float
    purchase_count: int
    avg_interval_days: float
    days_since_last: float
    days_until_expected: float   # negative = overdue
    suggested_amount: float
    is_kept: bool                # min_stock_amount > 0
    reasoning: str

class CadenceSuggestionResponse(BaseModel):
    lookback_days: int
    window_days: int
    min_purchases: int
    suggestions: list[CadenceSuggestionItem]

# ── Bundles (quick-add shopping sets) ──────────────────────────────────────

class BundleItemCreate(BaseModel):
    product_id: int

class BundleCreate(BaseModel):
    name: str
    emoji: str = "🧺"
    sort_order: int = 0
    items: list[BundleItemCreate] = []

class BundleUpdate(BaseModel):
    name: str | None = None
    emoji: str | None = None
    sort_order: int | None = None
    items: list[BundleItemCreate] | None = None

class Bundle(BaseModel):
    id: int
    name: str
    emoji: str
    sort_order: int
    created_at: str
    item_count: int = 0

class BundleItemDetail(BaseModel):
    id: int
    product_id: int
    product_name: str = ""
    sort_order: int = 0
    stock_amount: float = 0
    on_list: bool = False

class BundleDetail(Bundle):
    items: list[BundleItemDetail] = []

class BundleToShoppingRequest(BaseModel):
    product_ids: list[int]

class BundleToShoppingResponse(BaseModel):
    added: int
    skipped: int

# ── Shopping reconcile (cross-brand fulfillment) ─────────────────────────────

class BasketItem(BaseModel):
    product_id: int
    amount: float = 1

class ReconcileRequest(BaseModel):
    basket: list[BasketItem]
    # product_ids already consumed by the real-time exact path this session;
    # excluded so the AI pass can never double-count them.
    exclude_product_ids: list[int] = []

class ReconcileMatch(BaseModel):
    shopping_row_id: int
    bought_product_id: int
    amount: float
    confidence: str            # "high" | "medium"
    shopping_name: str
    bought_name: str

class ReconcileResponse(BaseModel):
    proposals: list[ReconcileMatch]
    ai_available: bool

class ReconcileApplyRequest(BaseModel):
    matches: list[ReconcileMatch]

class ReconcileApplyResponse(BaseModel):
    applied: list[int]
    skipped: list[int]

# ── Receipt OCR ────────────────────────────────────────────────────────────

class ReceiptParseRequest(BaseModel):
    image_b64: str = Field(..., description="Base64-encoded image bytes (no data: prefix)")
    mime_type: str = Field("image/jpeg", description="Image MIME type, e.g. image/jpeg, image/png, image/webp")

class ReceiptLine(BaseModel):
    raw_text: str
    qty: float
    unit: str | None = None
    price: float | None = None
    suggested_product_id: int | None = None
    suggested_unit_id: int | None = None
    confidence: float = 0.0

class ReceiptParseResponse(BaseModel):
    store: str
    date: str | None
    lines: list[ReceiptLine]

class ReceiptCommitLine(BaseModel):
    product_id: int = Field(..., description="Matched product (user-confirmed)")
    amount: float = 1
    unit_id: int | None = None
    location_id: int | None = None
    price_paid: float | None = None
    note: str = ""

class ReceiptCommitRequest(BaseModel):
    lines: list[ReceiptCommitLine] = []

class ReceiptCommitResponse(BaseModel):
    added: int
    failed: int
    errors: list[str] = []

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
