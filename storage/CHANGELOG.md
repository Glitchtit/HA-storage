# Changelog

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
