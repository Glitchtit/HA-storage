<p align="center">
  <img src="https://raw.githubusercontent.com/Glitchtit/HA-storage/main/storage/logo.png" alt="Storage" height="72">
</p>

<p align="center">
  <b>Products, stock, recipes and the shopping list in one database</b><br>
  Part of Glitchtit's HA apps — the system of record the other apps read and write through.
</p>

<p align="center">
  <a href="https://github.com/Glitchtit/HA-apps"><img alt="Home Assistant add-on" src="https://img.shields.io/badge/Home%20Assistant-add--on-0047AB?style=flat-square&logo=homeassistant&logoColor=white"></a>
  <img alt="Version" src="https://img.shields.io/badge/dynamic/json?style=flat-square&color=FF4F00&label=version&url=https%3A%2F%2Fraw.githubusercontent.com%2FGlitchtit%2FHA-storage%2Fmain%2Fstorage%2Fconfig.json&query=%24.version">
  <a href="https://hacs.xyz"><img alt="HACS custom" src="https://img.shields.io/badge/HACS-custom-1F2937?style=flat-square"></a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Glitchtit/HA-storage/main/docs/screenshots/hero.png" alt="Storage screenshot" width="100%">
</p>

## ✨ Features

- **Generic items and the products you buy** — a generic item ("Ateriakastike") holds the real products under it, at any depth. Products carry barcodes, pictures, pack sizes, aisle group, location and best-before default; spellings seen in recipes become aliases of one item, and duplicates can be merged.
- **Stock in lots** — every purchase is a lot with its location, best-before date and price. Using stock takes the oldest lot first; lots can be opened, moved, marked spoiled or corrected, and every change lands in the history.
- **Recipes against real stock** — availability converts units (a 2 kg flour bag counts as about 33 dl) and counts everything under each ingredient. Cooking deducts the stock, and missing amounts go to the shopping list without doubling rows.
- **A shopping list that keeps itself** — products below their minimum stock are added and shrunk every 5 minutes, each row says which product to buy and how many packages, and buying clears the rows it covers. Named bundles ("Taco night") add several products at once, and a generic item with no product gets one found on K-Ruoka through the Scraper add-on.
- **Insights** — stock value by product group, monthly spend with a 12-month trend, waste, top consumed and purchased, lots expiring this week and products predicted to run out in 14 days.
- **AI with Gemini, Claude or Ollama** — Optimize assigns aisle groups, locations and best-before days in batches, new generic items are placed in the tree in the background, and a receipt photo can be read into stock (Claude only).
- **Grocy import** — Settings can pull barcodes and stock amounts from an existing Grocy install into the barcode queue, where the Scraper creates the products.

## 📸 Screenshots

<table>
  <tr>
    <td width="50%"><img src="https://raw.githubusercontent.com/Glitchtit/HA-storage/main/docs/screenshots/insights.png" alt="Stock value by product group, monthly costs and top products"></td>
    <td width="50%"><img src="https://raw.githubusercontent.com/Glitchtit/HA-storage/main/docs/screenshots/products.png" alt="Product table with generic items and their products nested under them"></td>
  </tr>
  <tr>
    <td align="center"><sub>Insights: stock value by product group, monthly spend and top products</sub></td>
    <td align="center"><sub>Products: generic items with the products you buy nested under them</sub></td>
  </tr>
</table>

## 🚀 Installation

### Add-on

1. In Home Assistant open **Settings → Add-ons → Add-on Store → ⋮ → Repositories** and add `https://github.com/Glitchtit/HA-apps`
2. Install **Storage**
3. Optional: set `ai_provider` and its API key or URL in the add-on configuration to turn on Optimize, receipt reading and the background item placement
4. Start it and open **Storage** from the sidebar

### Integration (HACS)

1. In HACS open **⋮ → Custom repositories**, add `https://github.com/Glitchtit/HA-storage` with category **Integration**
2. Download **Storage** and restart Home Assistant
3. **Settings → Devices & services → Add integration → Storage** (the add-on URL is filled in from the Supervisor when it can be found; otherwise enter it, e.g. `http://homeassistant.local:8099`, and choose how many days count as expiring soon, default 7)

## ⚙️ Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `ai_provider` | `gemini` | AI used for Optimize, item placement and receipts: `gemini`, `claude` or `ollama` |
| `gemini_api_key` / `gemini_model` | `""` / `gemini-2.0-flash` | Gemini API key and model |
| `claude_api_key` / `claude_model` | `""` / `claude-3-5-haiku-20241022` | Claude API key and model; reading receipt photos needs Claude |
| `ollama_url` / `ollama_model` | `""` / `llama3` | Address of an Ollama server and the model to use |
| `scraper_url` | `""` | Scraper add-on URL; empty finds it automatically |
| `optimize_batch_size` | `100` | Products sent to the AI per Optimize batch (10–500) |
| `debug` | `false` | Verbose logging |

The options are copied into Storage's settings at start, and the **Settings** tab writes to the same values; the latest change wins.

## 🏠 In Home Assistant

The integration polls the add-on every 5 minutes and creates:

- Sensors, each named **Storage …**: Products Total, Low Stock, Expiring Soon, Expired, Shopping Pending, Barcode Queue, Optimize Status, Waste Value 30d, Expiring This Week and Predicted Runouts
- `todo.storage_shopping_list` — the shopping list as a to-do list: tick, untick, delete, and add by name (the name must match a Storage product)

| Action | What it does |
|--------|--------------|
| `ha_storage.add_to_shopping_list_by_name` | Adds a product by name, matched against existing products; never creates one |
| `ha_storage.add_to_shopping_list` | Adds a product by `product_id`, with optional amount, unit and note |
| `ha_storage.consume_stock` | Uses an amount of a product from stock, oldest lot first |
| `ha_storage.get_weekly_digest` | Returns waste value, expiring lots and predicted runouts, for a notification |
| `ha_storage.run_optimize` | Starts the AI Optimize job, for all products or a list of IDs |

`add_to_shopping_list_by_name` responds with `status` (`added`, `ambiguous` with candidates, `not_found` or `error`); `get_weekly_digest` always returns the digest as its response.

## 🧩 The family

<p align="center">
  <a href="https://github.com/Glitchtit/HA-storage"><img src="https://raw.githubusercontent.com/Glitchtit/HA-storage/main/storage/icon.png" width="56" alt="Storage"></a>&nbsp;
  <a href="https://github.com/Glitchtit/HA-scraper"><img src="https://raw.githubusercontent.com/Glitchtit/HA-scraper/main/addon/icon.png" width="56" alt="Scraper"></a>&nbsp;
  <a href="https://github.com/Glitchtit/HA-stock"><img src="https://raw.githubusercontent.com/Glitchtit/HA-stock/main/stock/icon.png" width="56" alt="Stock"></a>&nbsp;
  <a href="https://github.com/Glitchtit/HA-recipes"><img src="https://raw.githubusercontent.com/Glitchtit/HA-recipes/main/recipes/icon.png" width="56" alt="Recipes"></a>&nbsp;
  <a href="https://github.com/Glitchtit/HA-chores"><img src="https://raw.githubusercontent.com/Glitchtit/HA-chores/main/chores/icon.png" width="56" alt="Chores"></a>&nbsp;
  <a href="https://github.com/Glitchtit/HA-lists"><img src="https://raw.githubusercontent.com/Glitchtit/HA-lists/main/lists/icon.png" width="56" alt="Lists"></a>&nbsp;
  <a href="https://github.com/Glitchtit/HA-print"><img src="https://raw.githubusercontent.com/Glitchtit/HA-print/main/print/icon.png" width="56" alt="Print"></a>
</p>

<p align="center"><sub>Scraper, Stock and Recipes keep their products, stock and recipes in Storage; Print reads its shopping list from here.</sub></p>

## 🛠️ Development

```bash
# Backend tests
cd storage && python -m pytest app/tests/ -v

# Frontend dev server
cd storage/frontend && npm install && npm run dev
```
