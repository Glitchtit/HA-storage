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
