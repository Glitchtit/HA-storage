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
