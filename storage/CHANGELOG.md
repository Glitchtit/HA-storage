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
