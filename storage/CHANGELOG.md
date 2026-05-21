## 0.14.0
- Products page: add a picture to a product by hand — from both the "New Product" modal and the edit form. Pick an image file or take a photo (📷 uses the device camera on mobile), preview it inline, and replace or remove it later. Pictures upload only when you save, so cancelling never leaves stray files; replacing or removing a picture deletes the previous file from disk. Manually-added images are stored as `product_<token>.<ext>` so they never clash with scraper-fetched `<ean>` images. Frontend-only; no API or schema change.

## 0.13.0
- New `GET /api/shopping-list/cadence-suggestions` endpoint: suggests re-buys from **purchase cadence** rather than consumption velocity. For products kept in stock (`min_stock_amount > 0`, always considered) or bought ≥ `min_purchases` times in the last `lookback_days`, it learns the mean interval between `purchase` events in `stock_history` and surfaces a product when today is within ±`window_days` of its expected next purchase (last purchase + mean interval). Defaults: `lookback_days=180`, `window_days=7`, `min_purchases=3`. Needs ≥ 2 purchases in the window to derive an interval. Excludes items already on the shopping list and items still well-stocked (current qty at/above the keep-in-stock threshold, or — for non-staples — at/above the typical purchase amount). Overdue-by-more-than-a-week items drop off and resurface once the next purchase resets the anchor. Complements, and is independent of, the consumption-velocity `/shopping-list/proposal`. No schema change.

## 0.12.9
- Fix: manually adding a product to the shopping list and then scanning it after purchase no longer leaves the row behind. `consume_shopping_for_purchase` previously matched on `(row.unit_id IS NULL AND call.unit_id IS NULL) OR row.unit_id = call.unit_id`, which never fired for the realistic flow: the HA-stock frontend omits `unit_id` on the shopping-list POST (stored as NULL), but `add_stock` resolves a missing `unit_id` on the scan POST to the product's default (non-NULL). Asymmetric NULL handling → no match → row untouched. The match is now `row.unit_id IS NULL OR row.unit_id = call.unit_id`: a row that didn't pin a specific unit matches any purchase of the same product. Existing behaviour for rows with an explicit unit is unchanged (a `unit_id=L` row still won't be cleared by a `unit_id=kpl` scan). Bug affected every manual shopping-list entry since the helper was introduced — no existing test caught it because the suite always passed `unit_id` on both sides.

## 0.12.8
- `Product` model gains `children_stock_amount` and `children_stock_opened` — the sum of stock entries on immediate child products. Surfaces in `GET /api/products` (CTE-joined per row) and `GET /api/products/{id}`. Lets the Products list UI show a parent's category total (own + children) so a "Punasipuli" parent with stocked SKU children renders e.g. `2 ↓` instead of `–`, with the breakdown in a tooltip. `stock_amount` semantics are unchanged (still own-only) so low-stock alerts, shopping list, and other consumers that care about the specific SKU keep their current behaviour. Pure-leaf rows have `children_stock_amount=0` and render exactly as before.

## 0.12.7
- `GET /api/products` now includes aggregated `stock_amount` and `stock_opened` per row (LEFT JOIN onto the `stock` table). The endpoint previously returned raw `products` rows with no stock data, so the frontend's product list rendered `–` in the Stock column for every product even when `GET /api/products/{id}` showed real stock. The base `Product` model gains `stock_amount: float = 0` and `stock_opened: float = 0` (additive — no breaking changes for existing consumers).

## 0.12.6
- Revert 0.12.5: bind uvicorn back to `0.0.0.0`. The Supervisor container has `bindv6only=1`, so the dual-stack `::` bind only listened on IPv6 — the local nginx wait loop and upstream proxy (both targeting `http://127.0.0.1:8100`) got connection-refused on every health check, leaving Storage looking dead to its own ingress. The original IPv6 upstream-refused churn in sibling addons will be addressed client-side instead.

## 0.12.5
- uvicorn now binds to `::` (dual-stack) instead of `0.0.0.0`. Docker's embedded DNS returns both A and AAAA records for the add-on container, so IPv6-capable clients (nginx in HA-stock / HA-recipes / HA-print, plus any HACS integration) were repeatedly hitting `connect() failed (111: Connection refused)` against the AAAA endpoint and silently falling back to IPv4 — clean in the response but loud in the upstream logs. Binding dual-stack makes the AAAA endpoint answer too, eliminating the connection-refused churn.

## 0.12.4
- Buying a tracked product now clears matching **manual** shopping list rows automatically. `POST /api/stock/add` decrements every non-done manual row (`auto_added = 0`) for that product whose unit matches the purchase, oldest-first; rows that reach amount 0 are hard-deleted, and any leftover spills into the next matching row. Unit mismatches are skipped — no automatic conversion. Auto-added rows continue to be managed by the existing `sync_auto_shopping()` against `min_stock_amount`, so the two mechanisms cover disjoint sets.

## 0.12.3
- Per-ingredient `specificity` (`loose` | `strict`) on recipe ingredients. **Loose** (default, prior behavior) lets a child of the linked parent product satisfy the recipe — any cheese under a "Juusto" parent covers a generic "cheese" call. **Strict** requires the exact linked product, so a recipe specifying parmesan no longer reports as in-stock just because gouda is on hand. Stock-amount aggregation in `GET /api/recipes/{id}` is gated on this flag; the new column is added via lazy `ALTER TABLE` and a one-shot heuristic backfill upgrades loose rows to strict when the stored note text matches an existing child product name (e.g. "parmesan" under a "Juusto" parent → relink to Parmesan child + strict).
- `IngredientCreate` / `IngredientUpdate` / `Ingredient` Pydantic models accept and return `specificity`. Recipe detail UI gains a per-row strict/loose toggle.

## 0.12.2
- Add **"What's new"** popup — when you open Storage after an update, a dismissable modal shows the changelog entries for every version released since your last visit. Markers persist per-browser via `localStorage` (`storage_whatsnew_lastSeen`); first visit silently marks the current version as seen so users don't get a wall of historical changelog on first install

## 0.12.1
- Dashboard "Low Stock Alerts" now correctly lists products whose stock is below `min_stock_amount`, including those that are completely out of stock. The `/stock` endpoint drops products with zero `total_amount` via its `HAVING` clause, so the dashboard now joins the product list against the stock map locally instead of relying on `/stock` alone.

## 0.12.0
- Predictive insights. `GET /api/stats/runouts?horizon=N` returns products predicted to deplete within N days, using the same 8-week consumption-velocity model that drives the shopping proposal — but without the proposal's "only opted-in products" filter so the dashboard can show all upcoming runouts. New helper `consumption_stats.predicted_runouts` is the shared core.
- `GET /api/stats/digest` returns a single bundle for HA notifications: monetary waste (30d), expiring lots (7d), predicted runouts (14d), and top spoilers. Backed by `consumption_stats.weekly_digest`.
- New helper `consumption_stats.expiring_within(days)` mirrors the FIFO order of `/stock/entries` for digest consumption.
- HACS integration (manifest 0.3.0): coordinator fetches `/api/stats/digest` and exposes three new sensors — `sensor.storage_waste_value_30d`, `sensor.storage_expiring_this_week`, `sensor.storage_predicted_runouts` — with item-level breakdowns in attributes. New HA service `ha_storage.get_weekly_digest` returns the digest as a service response, ready to pipe into `notify.*` automations. Older add-ons missing `/stats/digest` degrade gracefully (sensors stay at 0, no UpdateFailed).
- Insights tab "Will run out next 14 days" and "Expiring this week" cards activate against the new endpoints.

## 0.11.0
- Monetary waste tracking. Products gain an optional `unit_price` (and `unit_price_currency`, default EUR). Stock lots gain a `price_paid` snapshot taken at add time so historic waste valuation doesn't drift when product defaults change — same invariant as the 0.9.0 `best_before_days` snapshot.
- `stock_history` rows now snapshot a per-event `unit_price` too: `purchase` events get the lot's paid price; lot-targeted `spoil` events get the lot's `price_paid` (falling back to the product's `unit_price` at event time); aggregate `consume`-as-spoil events get the product's current `unit_price`. NULL means "valuation unknown" — the waste endpoint counts these toward amount but not value.
- New `GET /api/stats/waste?days=N` returns total amount + total value + breakdowns by product, location, and category, plus a weekly value-lost series. Joins `stock_history.unit_price` preferred, with `products.unit_price` fallback for older rows.
- `POST /api/receipts/commit` accepts an optional `price_paid` per line (treated as the *total* for that line; divided back to per-unit before snapshotting onto the lot).
- Frontend: Insights tab's waste tile activates and shows "wasted €X this window" with by-product / by-location breakdowns and a sparkline. Product form gains a unit price input; Stock add form gains a per-lot price-paid input.
- Schema migration is purely additive — existing rows return NULL and the UI handles that as "price unknown".

## 0.10.0
- New `📈 Insights` tab. The Dashboard kept its quick-action role; Insights is the analytical view.
- Time-window selector (7d / 30d / 90d / 1y) drives every section on the page.
- Sections: top consumed, top purchased, spoilage breakdown (with daily trend sparkline), and a per-product event timeline drill-down. Two scaffold sections — monetary waste and predicted runouts — appear empty for now and light up automatically when the matching backend endpoints land in upcoming releases.
- Charts use `recharts`; styling follows the brand palette (Cobalt for primary series, International Orange for highlights, danger red for spoilage). First shared `src/components/charts/` directory establishes the pattern for further frontend modularization.
- No backend changes in this release — uses the existing `/api/stats/*` endpoints.

## 0.9.4
- Dashboard "Expiring Soon" panel now actually works. It was reading from the aggregated `/stock` endpoint which has no per-lot `best_before_date`, so the filter always evaluated to false and the panel was permanently stuck on "No products expiring 👍". Now reads from `/stock/entries?expiring_within_days=7`.
- Expired items (past best-before date) show up in the same panel — they are more urgent than upcoming-expiry items, not less. Renamed to "Expiring or expired" with appropriate red styling for already-past-due lots.
- Backend `GET /stock/entries?expiring_within_days=N` dropped its lower-bound clause; the filter now means "best_before_date ≤ today + N" (includes already-expired). The `expired=true` flag still works for "strictly past due" queries.

## 0.9.3
- **Semantic switch:** `best_before_days` is now the authoritative per-lot value; `best_before_date` is a derived/cached function of `purchased_date + best_before_days`. This guarantees the displayed columns are always internally consistent — `Scanned + BB (days) == Expires`, always.
- `POST /stock/add`: a user-supplied `best_before_date` is converted into a per-lot `bb_days` value (`override − purchased`) and stored. The override date is preserved exactly; `bb_days` reflects the realized interval for this lot, not the product default. Negative or zero diffs are accepted ("expires today" / "already expired on import").
- New always-on, idempotent migration pass: rows where `best_before_date != purchased_date + best_before_days` are realigned in place. Self-heals legacy import drift on every init; no-op once consistent. Replaces the two one-shot repair passes from 0.9.1 and 0.9.2 (which trusted the stored date over the days — wrong direction).

## 0.9.2
- Rewrote sentinel `best_before_date` values from pre-0.9.0 imports. Lots whose stored expiry is past the year 2100 (e.g. `2999-12-31`, a common "no real expiry" sentinel from receipt OCR / barcode lookup paths) now get both their expiry date AND `best_before_days` rewritten to `purchased_date + product.default_best_before_days`. The product's stated policy is more trustworthy than a clearly-bogus stored value.
- One-shot, gated by `_meta.bbd_sentinel_repair_v1`, bounded by a `created_at` cutoff. Lots with plausible (under ~75-year) expiry dates are untouched.

## 0.9.1
- Fixed migration backfill of `best_before_days` for pre-0.9.0 stock rows. Previously the column was stamped from the product's current default, which produced misleading values for lots with an imported or user-set `best_before_date` (e.g. a `2999-12-31` sentinel showed `BB (days) = 365` while `Expires = 31.12.2999` — three columns telling three stories).
- `best_before_days` is now derived from the lot's own `(purchased_date, best_before_date)` pair when both are present, falling back to the product default only when the lot has no expiry date.
- One-shot repair pass on first init after upgrade: rows where `best_before_days` disagrees with the date pair get recomputed in place. Bounded by a `created_at` cutoff so post-upgrade lots are never touched. Tracked via `_meta.bb_days_repaired_v1`.

## 0.9.0
- Strengthened expiry tracking. Each stock lot now snapshots `(purchased_date, best_before_days)` at add time, so a later edit to a product's `default_best_before_days` does not retroactively shift existing stock.
- Fixed FIFO ordering. `consume`, `open`, and `transfer` now sort by `best_before_date ASC` with NULL dates LAST (was first), tie-breaking by `purchased_date` then `id`. Previously a no-expiry lot would be eaten before a real one.
- `POST /stock/spoil/{lot_id}` — new endpoint. Targets a specific lot (whole or partial amount) and logs a `spoil` history event tied to that lot.
- `POST /stock/add` accepts an optional `purchased_date` override, for receipt imports and manual backfill.
- `StockEntry` responses gain `best_before_days`.
- Storage frontend lot inspector now shows `Scanned`, `BB (days)`, `Days left`, plus a per-lot Spoil button. The Dashboard "Expiring soon" panel labels each row with "scanned Nd ago".
- Migration is automatic and idempotent: new column added to `stock`, existing rows backfilled from each product's current `default_best_before_days`, FIFO index created. Live `best_before_date` values are not recomputed.

## 0.8.2
- Scraper auto-detection: storage's s6 startup script and nginx proxy now look for the new `scraper` slug (matches HA-scraper 2.0.0) instead of the old `grocy_scraper`. Missed in 0.8.1
- Pure infra change; the `/api/migrate/grocy` endpoint is unchanged

## 0.8.1
- Docs: dropped legacy "Replaces Grocy + Barcode Buddy" framing from README and copilot-instructions — the codebase is its own thing, not a migration target
- Docs: removed vestigial Barcode Buddy mentions; the barcode queue is described on its own merits
- Code unchanged. The `/api/migrate/grocy` endpoint and `migrate_from_grocy` helper stay — they're the import path FOR users coming from a Grocy install, where the word is accurate

## 0.8.0
- Feature: cook a recipe. New `POST /api/recipes/{id}/cook` deducts ingredients from stock (FIFO by best-before, same path `/stock/consume` uses) and queues any shortfall on the shopping list with a "Reseptistä: <name>" note linked back to the recipe
- Optional `servings` body param scales every ingredient amount proportionally to the recipe's stored servings count
- Unit conversion: each ingredient's amount is converted from the recipe's unit to the product's default stock unit via the existing `unit_conversions` BFS. Ingredients with no conversion path land in `unmatched` rather than being silently skipped
- Per-ingredient `consume` events are logged to `stock_history` so the predictive shopping proposal (0.6.0) sees recipe-driven consumption in its velocity model

## 0.7.0
- Feature: receipt OCR. New `POST /api/receipts/parse` accepts a base64-encoded receipt image and returns parsed line items via Claude vision, each enriched with a suggested product match (token-overlap + SequenceMatcher) and confidence score
- Feature: `POST /api/receipts/commit` batch-adds confirmed lines to stock (creates entries via the existing `/stock/add` semantics, logs `purchase` events to `stock_history`)
- AI: new `call_ai_vision_json` in `ai_client.py` — Claude-only for now; raises `503` on the parse endpoint when `ai_provider != claude` or `claude_api_key` is unset
- Models: new `ReceiptParseRequest`, `ReceiptParseResponse`, `ReceiptCommitRequest`, `ReceiptCommitResponse`
- Prompt embedded in `receipt_parser.py` is Finnish-grocery-aware (K-Ruoka / S-market / Lidl / Prisma); skips totals, discounts, loyalty rows; preserves Finnish capitalisation in `raw_text`

## 0.6.0
- Feature: predictive shopping proposal. New `GET /api/shopping-list/proposal` returns products predicted to deplete within a configurable horizon based on consumption velocity over a lookback window
- For every active product with `min_stock_amount > 0`, the proposal computes mean weekly consume rate from `stock_history` consume events and predicts days-to-zero from the current total stock
- Products already on the shopping list (done = 0) and products with no consume history in the lookback window are excluded
- Query params: `lookback_weeks` (1–52, default 8), `horizon_days` (1–30, default 7); response includes `weekly_rate`, `days_to_zero`, `suggested_amount` (max of `min_stock_amount` and two weeks at current rate), and a Finnish reasoning string per row sorted by urgency

## 0.5.5
- Fix: shopping list auto-sync now runs server-side every 5 minutes, independent of the HA integration. Previously the only triggers were `/stock/*` writes and the integration coordinator, so users without the integration could see stale auto-added rows linger forever after restocking via the UI's stock-amount picker or after lowering `min_stock_amount`
- Fix: editing `min_stock_amount` or `active` on a product now triggers an immediate sync — raising the threshold adds rows, lowering it (or deactivating the product) removes auto-added rows for products that are now considered fine
- Background task: a `_periodic_shopping_sync` coroutine starts in the FastAPI lifespan, runs the first reconciliation 15s after startup, then every 300s

## 0.5.4
- Fix: shopping list now correctly removes auto-added rows when stock is replenished. Previously items would only be added when stock dropped below `min_stock_amount` and lingered on the list forever, even after restocking
- Feature: backend-side shopping list reconciliation. Adds a row (with `auto_added = 1`) for every active product whose total stock is below `min_stock_amount` and has no existing shopping_list entry, and deletes auto-added rows whose product is back at or above the threshold (and not yet checked off)
- API: `POST /api/shopping-list/sync` runs the reconciliation on demand and returns `{added, removed}` counts
- Hook: `/stock/add`, `/stock/consume` and `/stock/{id}` (delete with spoil reason) automatically trigger the sync, so scanning a barcode or consuming stock keeps the list in sync without polling
- Integration: HA-Storage custom component pings `/shopping-list/sync` at the start of every coordinator refresh so the periodic 5-minute poll catches drift even when no scan happens

## 0.5.3
- Optimize page: new "Ungrouped only" checkbox runs an incremental AI optimize that targets only the products that currently have no `product_group_id`, seeding the AI with the existing groups and parent products so each ungrouped item is slotted into the best-fitting existing group instead of inventing new ones
- API: `POST /api/ai/optimize` accepts `{"ungrouped_only": true}` — server resolves the candidate set server-side; returns 400 when there are no ungrouped products
- UI: "Fresh naming" checkbox is disabled while "Ungrouped only" is on (no-op for incremental runs) so the two modes don't appear conflicting

## 0.5.2
- Fix: AI optimize incremental run could leave a newly-added product ungrouped even though it returned a category. The `product_group_id` write was nested inside the "has external parent" branch, so it was skipped whenever the AI returned `group_name: null` (truly unique product) or a parent name that resolved to the product itself (self-parent skip — common for a single freshly-added product). The category assignment is now persisted independently of parent linkage so single-fire optimize properly slots new products into the best-fitting existing product group, and still parents them when applicable

## 0.5.1
- API: `POST /api/stock/consume` now accepts a `spoiled: true` flag — when set, the operation is recorded in history as a `spoil` event instead of `consume` so spoilage statistics include quantity-based discards (not just full-row deletes)

## 0.5.0
- Feature: stock movement history — every purchase, consume, open, transfer and spoil event is now recorded in a new `stock_history` table
- API: `GET /api/history`, `GET /api/history/product/{id}`, `DELETE /api/history/{id}` for the audit log (filters: product_id, event_type, since, until, limit)
- API: `GET /api/stats/summary`, `/stats/top-consumed`, `/stats/top-purchased`, `/stats/spoilage`, `/stats/timeline`, `/stats/product/{id}` for analytics
- API: `DELETE /api/stock/{id}?reason=spoiled` now logs a `spoil` event with the discarded amount
- API: `note` field accepted on `/api/stock/add`, `/consume`, `/open`, `/transfer` and stored on the matching history event
- UI: new History tab with filter UI (product, event type, date range)
- UI: Dashboard now shows Top Consumed (30d) and Recent Purchases widgets
- UI: Product detail panel includes a Recent History section
- Migration: existing stock rows are backfilled as `purchase` events on first start (gated via `_meta.history_backfilled`)

## 0.4.1
- API: `POST /api/shopping-list` now accepts and persists an `auto_added` flag (default `false`); previously the column was always written as `0` even when callers asked for `true`, so the flag did not survive a re-fetch

## 0.4.0
- Companion HACS integration `ha_storage` now ships side-by-side at the repo root — install via HACS to get the Storage sidebar panel, sensors (`products_total`, `low_stock`, `expiring_soon`, `expired`, `shopping_pending`, `barcode_queue`, `optimize_status`), a `todo.storage_shopping_list` entity, and the `ha_storage.add_to_shopping_list` / `consume_stock` / `run_optimize` services
- API: new `GET /api/stock/entries` endpoint returns all stock entries joined with product name; supports `expiring_within_days` and `expired` filters
- API: new `GET /api/ai/optimize` endpoint (no task id) returns the running or most recent optimize task summary, or `idle`
- Breaking: removed the legacy in-add-on HA bridge (`ha_sync.py`) and the `/api/shopping-list/ha-sync`, `/api/shopping-list/ha-status`, `/api/stock-list/ha-sync`, `/api/stock-list/ha-status` endpoints — use the new HACS integration instead

## 0.3.32
- `ai_provider` schema changed to dropdown (list) — renders as radio buttons in HA add-on options instead of free text

## 0.3.31
- Apply GlitchyRee design system: brand orange active tabs, cobalt primary "Add Product" button, self-hosted Space Grotesk/Inter/JetBrains Mono
- Add CSS design tokens at src/styles/design-tokens.css
- Wire Tailwind theme.extend to expose brand.* / semantic.* / font-display utilities

## 0.3.30
- Feature: "Add to Stock" and "Transfer" product selector replaced with fuzzy-search combobox — type to filter with instant suggestions, keyboard-navigable, replaces the full product dropdown

## 0.3.29
- Feature: Product image thumbnail endpoint `GET /api/files/products/thumb/{filename}` — serves a 128×128 JPEG compressed thumbnail; generated lazily on first request and cached; backwards compatible with all existing product images; thumbnails also generated eagerly on new uploads; thumbnail removed automatically when original is deleted

## 0.3.28
- Fix: recipe ingredients no longer break after optimize — fixed name_to_product collision where duplicate-name parents caused recipe-linked parents to be lost from the lookup, resulting in new parent IDs that orphaned recipe references
- Fix: Phase 3 recipe repair now re-links stale recipe ingredient product_ids to current parent products when IDs change after optimize restructuring
- Fix: Phase 3 re-link handles deduplication (won't create duplicate recipe_id + product_id rows)

## 0.3.26
- Feature: "Fresh naming" checkbox in Optimize — when checked, the AI invents all parent product names from scratch instead of being seeded with previous parent names

## 0.3.25
- Fix: Phase 2 optimizer now returns a JSON object example in prompt to prevent Gemini from returning a list
- Fix: Phase 1 + Phase 2 now recover automatically when AI returns a list with "id" fields (list→dict reshape) instead of silently skipping the batch
- Fix: Phase 1 recovery path logs "recovered list→dict (N entries)" so the behavior is visible in logs

## 0.3.24
- Stability: single-flight optimize guard — rejects concurrent optimize requests with HTTP 409 instead of running overlapping jobs
- Stability: SQLite busy_timeout set to 5s (prevents "database is locked" under thread contention)
- Stability: explicit SAVEPOINT/ROLLBACK for pack merge, pack rename, stub merge, orphan repair, and dedup cleanup in optimizer
- Stability: factory reset now uses savepoint to prevent partial wipes on error, with guaranteed foreign_keys restoration in finally block
- Logging: optimize task failures now logged with full traceback

## 0.3.23
- Optimizer Phase 3: recipe integrity repair — merges recipe stubs into matching parent products, fixes orphaned recipe ingredients, deduplicates entries
- Fix: recipe-linked group-master products now preserved in parent name lookup (prevents duplicate parent creation after optimize)
- Fix: case-insensitive parent product matching in optimizer (e.g. "sitruuna" reuses "Sitruuna")
- Fix: pack merge now moves recipe_ingredients to base product before deletion (prevents silent cascade-delete of recipe links)

## 0.3.22
- Optimizer: ALL drinks of any kind always assigned to Fridge/refrigerator (explicit rule, no exceptions)

## 0.3.21
- Fix: optimizer rename-in-place pack conversion now multiplies stock entries by pack_size (e.g. 1 box of 10 eggs → 10 eggs in stock)
- Safety cap: pack_size > 24 is ignored for stock multiplication (prevents package-content numbers like "cotton swabs 200 kpl" from inflating stock)

## 0.3.20
- Fix: optimizer pack conversion now transfers stock to base product (amount × pack_size) before deleting the multi-pack product; previously stock was lost due to CASCADE DELETE

## 0.3.19
- Optimizer: group-master parent products referenced by recipe ingredients are no longer deleted during clean-slate pass (they are still deactivated and excluded from the AI feed)

## 0.3.18
- Fix: remove redundant sub_filter_types text/html in nginx.conf (duplicate MIME type warning)

## 0.3.17
- Fix: optimizer full-mode now seeds Phase 1 with old parent names (collected before deletion) so AI reuses consistent group names across batches instead of inventing fresh ones
- Fix: optimizer loads existing categories as initial context in full mode (same as incremental mode)
- Fix: Phase 2 now logs diagnostic messages when a product is skipped (null group, missing parent ID, self-parenting)
- Fix: set PYTHONUNBUFFERED=1 in s6 run script so optimizer logs appear in HA APP log immediately
- Fix: explicitly pin INFO level on optimizer/ai_client loggers so uvicorn startup config cannot silence them

## 0.3.16
- Optimizer now deactivates ALL parent/group-master products before the AI runs (not just inactive ones)
- Optimizer-created group-master products are deleted immediately before AI batches (fewer tokens wasted)
- Fix: purge deleted parent IDs from name_to_product so Phase 1 creates fresh parents (fixes FOREIGN KEY constraint errors)
- Recipe scraper stub products are unaffected (they have no product_group_id)

## 0.3.15
- Move optimize_batch_size setting to HA add-on config (HA Settings → Add-ons → Storage → Configuration), default 100
- Synced from options.json at startup via sync_from_options
- Removed editable batch size field from Settings web UI

## 0.3.14
- Fix: Settings page config values (batch size, HA entity IDs) never loaded correctly — GET /config returns an array but was read as object; now converted with Object.fromEntries

## 0.3.13
- Smart Stock List: mirror all in-stock products to a HA Local To-do entity (default: todo.smart_stock_list)
- Stock items show name + amount/unit as description; updated on every stock mutation
- Settings: new "Home Assistant Stock List" card with entity ID edit and manual sync button
- Stock list syncs automatically on startup and on every add/consume/transfer/delete stock action
- New API endpoints: GET/POST /stock-list/ha-status and /stock-list/ha-sync

## 0.3.11
- Settings: editable optimize batch size (10–500, default 100) in AI config card
- Batch size is persisted in the config DB and read dynamically at optimize time


- Optimize log now shows AI token usage (in/out) and response time per batch call
- Token/timing lines rendered in cyan in the web UI log view


- Settings: AI card is now read-only (provider + model display only); configure via HA add-on options
- Fix: sync_from_options() now called at startup so HA add-on config always takes effect


- Fix: HA interface AI provider/model settings now always take effect on addon restart (INSERT OR REPLACE instead of INSERT OR IGNORE)
- Fix: Claude can now be selected as AI provider in Settings WebUI
- Fix: Settings WebUI correctly saves Claude API key and model
- Fix: Settings display correctly shows Claude key/model when provider is claude

## 0.3.7
- Optimize: user-defined enforced categories panel (tag/pill UI with add/remove)
- Categories stored in config table as JSON under key optimize_categories
- New API: GET/PUT /api/ai/optimize/categories
- optimizer.py: run_optimize() accepts enforced_categories param; AI strongly prefers them in Phase 1; groups are created even if no products are assigned

## 0.3.6
- AI Optimize tab: full 2-phase AI product optimization moved from Scraper to Storage app
- New backend modules: ai_client.py (Gemini/Claude/Ollama calling), optimizer.py (2-phase pipeline)
- New endpoint: POST /api/ai/optimize (fire-and-poll background job), GET /api/ai/optimize/{task_id}
- New frontend component: Optimize tab with live log streaming and result summary
- Added requests and anthropic to requirements

## 0.3.5
- Persistent service health monitoring: background loop never stops; re-detects scraper if it goes down or moves, reloads nginx only when URL changes

# Changelog

## 0.3.4
- Persistent service probing: if Scraper addon is not found at startup, retry every 30 s in background; reload nginx automatically when found

## 0.3.3
- Fix version always showing 0.0.0: copy config.json into Docker image at /config.json

## 0.3.2
- Fix HA To-do sync: add `homeassistant_api: true` to config.json (was missing, blocked all HA Core API calls)
- Remove unreliable config flow auto-creation of To-do entity (not supported via HA API)
- Settings UI: show HA connection status banner — ✅ Connected or ⚠️ with step-by-step setup instructions
- Add `GET /api/shopping-list/ha-status` endpoint returning token availability + entity existence
- Sync button refreshes status after sync

## 0.3.1
- Smart Shopping List: products below `min_stock_amount` auto-added to shopping list; removed when restocked
- HA To-do sync: shopping list synced to a Home Assistant To-do entity (`todo.smart_shopping_list` by default)
- Auto-creates the HA To-do entity on first use (no manual HA setup needed)
- `POST /api/shopping-list/ha-sync` endpoint for manual full-sync trigger
- 🤖 badge on auto-added shopping list items in the UI
- New "Home Assistant Shopping List" section in Settings with entity config + sync button
- `shopping_list` table: new `auto_added` and `ha_item_name` columns (auto-migrated)

## 0.3.0
- Add Claude AI provider support: `claude_api_key` + `claude_model` in config.json, run script, `_seed_config()`, `/config/ai` endpoint, and `/config/ai-key` endpoint

## 0.2.9
- Add `ai_provider`, `ollama_url`, `ollama_model` to addon config.json schema + options
- `_seed_config()` now seeds Ollama env vars (AI_PROVIDER, OLLAMA_URL, OLLAMA_MODEL)
  into SQLite using INSERT OR IGNORE so Settings UI edits survive restarts

## 0.2.8
- VERSION now read dynamically from config.json instead of hardcoded "0.1.0"

## 0.2.7
- Add `GET /api/config/ai` endpoint returning provider-agnostic AI config
  (`provider`, `api_key`, `model`, `ollama_url`, `ollama_model`)
- Update `GET /api/config/ai-key` to return gracefully when provider is Ollama
- Settings UI: AI provider toggle (Gemini / Ollama); Ollama shows URL + model fields

## 0.2.6
- Barcode lookup (`GET /products/by-barcode/{barcode}`) now returns `matched_pack_size`
  so clients (e.g. Stock app) know how many units to add when scanning a multi-pack barcode

## 0.2.5
- Add Factory Reset button in Settings: wipes all user data (products, stock, barcodes,
  recipes, images) and re-seeds default units, locations, and conversions

## 0.2.4

- Fix Pydantic validation errors: description/note fields now accept NULL from database
- Rewrite Grocy migration: imports barcodes and stock amounts only (products created via Scraper discover)
- Add import_stock_amount field to barcode queue for preserving stock during migration
- Schema migration adds import_stock_amount column to existing databases
- Auto-detect and proxy to Scraper addon — import triggers discover automatically
- Settings UI shows live progress during import + discover pipeline

## 0.2.3

- Add "Include inactive" toggle to Products page
- Parent product dropdown now shows all products (including inactive)

## 0.2.2

- Fix product image upload returning 404 (nginx static asset regex was intercepting /api/files/ requests)
- Fix product delete failing silently when product has recipe ingredients or shopping list entries
- Show error toast when product delete fails

## 0.2.1

- English UI (all Finnish labels translated)
- Dark mode matching Stock and Recipe apps (bg-gray-900/800, emerald accents)

## 0.2.0

- Ingress web UI for database management (React + Vite + Tailwind)
- Multi-stage Docker build: Node 20 builds frontend, nginx serves SPA
- s6-overlay dual service: nginx (port 8099) + FastAPI (port 8100)
- Dashboard with stats, low-stock alerts, expiring-soon
- Full CRUD for products, stock, recipes, shopping list
- Units & conversions, locations, product groups management
- Barcode queue viewer with manual entry
- Settings page with AI config and Grocy migration
- Finnish UI labels throughout
- 10 tab navigation with health-check spinner

## 0.1.0

- Initial release
- SQLite database with products, stock, units, conversions, barcodes, recipes, shopping list
- FastAPI REST API on ingress port 8099
- Grocy migration endpoint
- Image file storage for products and recipes
- Centralized AI key management
