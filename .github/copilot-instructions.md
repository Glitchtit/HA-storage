# Copilot Instructions for HA-Storage

## Overview

Central SQLite-backed data store for the HA-apps ecosystem. Replaces Grocy + Barcode Buddy. Provides a REST API and web UI for database management.

- **Add-on name**: Storage
- **Slug**: `ha_storage`
- **Startup**: `system` — starts before other app addons

## Architecture

Two s6-overlay services:

1. **storage** (nginx on port 8099) — serves React SPA and proxies API requests
2. **storage-api** (FastAPI/Python on port 8100) — REST API, SQLite database

nginx proxy routes:
- `/api/*` → FastAPI (localhost:8100)
- Everything else → React SPA (`/var/www/html`)
- Ingress path injected into `<meta>` tag via `sub_filter`

The nginx `run` script waits for the API health check before starting.

Dockerfile: multi-stage build — Node 20 builds React SPA → Alpine runtime with Python + nginx.

SQLite database at `/data/storage.db`.

## Config Options

```json
{
  "gemini_api_key": "",           // Centralized AI key for all apps
  "gemini_model": "gemini-2.0-flash",
  "debug": false
}
```

## Build, Test, Lint

```bash
# API tests (54 tests)
cd storage
python -m pytest app/tests/ -v

# Single test
python -m pytest app/tests/test_api.py::TestProducts -v

# Frontend
cd storage/frontend
npm install
npm run dev      # dev server
npm run build    # production build to dist/
```

No linter or formatter configured.

## API Structure

FastAPI app in `app/main.py`. Database in `app/database.py`. Models in `app/models.py`. 11 routers in `app/routers/`:

| Router | Endpoints |
|---|---|
| `products.py` | CRUD products, product detail with children/barcodes/stock |
| `stock.py` | Stock summary, add/consume/open/transfer, FIFO |
| `barcodes.py` | CRUD barcodes, pack sizes |
| `units.py` | CRUD units, BFS-based unit conversion resolver |
| `conversions.py` | CRUD conversions, resolve endpoint |
| `locations.py` | CRUD locations |
| `product_groups.py` | CRUD product groups |
| `recipes.py` | CRUD recipes + ingredients, recipe-to-shopping |
| `shopping.py` | CRUD shopping list, clear done |
| `barcode_queue.py` | CRUD barcode queue (replaces Barcode Buddy) |
| `config.py` | Key-value config, AI key endpoint |
| `files.py` | Product/recipe image upload/download |
| `migrate.py` | One-shot Grocy → Storage migration |

## Database Schema

11 tables: products, stock, barcodes, units, unit_conversions, locations, product_groups, recipes, recipe_ingredients, shopping_list, barcode_queue, config

Key design decisions:
- Single `unit_id` per product (not 4 like Grocy)
- Pack sizes stored on barcode records
- CASCADE deletes throughout
- Seeded data: 9 Finnish units (g, kg, ml, dl, l, tl, rkl, kpl, rs), 12 conversions, 3 locations (Fridge, Pantry, Freezer)

## Web UI

React 18 + Vite + Tailwind. English UI. Dark mode (bg-gray-900, bg-gray-800, emerald accents).

10 tabs: Dashboard, Products, Stock, Recipes, Shopping List, Units, Locations, Groups, Barcode Queue, Settings.

Frontend source in `storage/frontend/src/`:
- `App.jsx` — Tab navigation shell
- `api.js` — Centralized API client
- `components/` — 10 component files (Dashboard, Products, Stock, etc.)

## Key Conventions

- **Ingress-aware**: Uses `<meta name="ingress-path">` for URL prefixing
- **Dark mode**: Consistent with Stock and Recipe apps
- **Health endpoint**: `GET /health` returns `{status, version, db_tables}`
- **Pydantic models**: 30+ models in `models.py` for request/response validation
- **Product names in Finnish**: Seeded units/locations have Finnish names

## HA Add-on Structure

- Add-on in `storage/`, matching slug in `config.json`
- `config.json`: metadata, options schema, ingress settings
- `build.json`: architecture → base image mapping

## Versioning

Bump both on user-facing changes:

| File | Field |
|---|---|
| `storage/config.json` | `"version": "X.Y.Z"` |
| `storage/CHANGELOG.md` | New `## X.Y.Z` section |

CHANGELOG format: plain `## x.y.z` headers, flat bullet list, no dates, no categories. Newest first.
