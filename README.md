# HA-Storage

SQLite-backed central data store for the HA-apps ecosystem. Replaces Grocy + Barcode Buddy.

## Features

- **Product management** with parent/child hierarchy, barcodes, and pack sizes
- **Stock tracking** with FIFO consumption, locations, best-before dates
- **Recipe management** with ingredients, stock status, and shopping list generation
- **Unit system** with conversion graph resolution (BFS)
- **Barcode queue** (replaces Barcode Buddy)
- **AI key sharing** — stores Gemini API key for sister apps
- **Grocy migration** — one-time import from existing Grocy instance

## Architecture

- Python + FastAPI on port 8099 (HA ingress)
- SQLite database at `/data/storage.db`
- s6-overlay managed service
- Image storage at `/data/images/`

## Install

This repo ships **two** things — install both for the full experience:

1. **The Storage add-on** (system of record). Add this repo as a Supervisor add-on store: Settings → Add-ons → Add-on Store → ⋮ → Repositories → add `https://github.com/Glitchtit/HA-storage`, then install **Storage**.
2. **The Storage HACS integration** (`ha_storage`). Adds a sidebar panel, sensors, a shopping list todo entity, and services that talk to the add-on. Install via HACS:
   - HACS → Integrations → ⋮ → Custom repositories → add `https://github.com/Glitchtit/HA-storage` (category: Integration), then install **Storage**.
   - Restart Home Assistant, then add the integration via Settings → Devices & Services → Add Integration → Storage.

The integration auto-discovers the add-on URL via Supervisor when it can; otherwise enter it manually (e.g. `http://homeassistant.local:8099`).

### Entities

- `sensor.storage_products_total` — total active products
- `sensor.storage_low_stock` — products at/below their `min_stock_amount`
- `sensor.storage_expiring_soon` — stock entries expiring within N days (default 7, configurable)
- `sensor.storage_expired` — stock entries past their best-before date
- `sensor.storage_shopping_pending` — unchecked shopping list items
- `sensor.storage_barcode_queue` — entries in the barcode queue
- `sensor.storage_optimize_status` — current AI optimize state (`idle` / `running` / `done` / `error`)
- `todo.storage_shopping_list` — the shopping list as a HA todo entity (check, uncheck, delete)

### Services

- `ha_storage.add_to_shopping_list` — add a product to the shopping list
- `ha_storage.consume_stock` — consume N of a product (FIFO)
- `ha_storage.run_optimize` — kick off the AI optimize background job
