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
