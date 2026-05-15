"""SQLite database initialization, schema, and seed data."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS _meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS units (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    abbreviation TEXT NOT NULL UNIQUE,
    name_plural  TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS locations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS product_groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS products (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,
    description             TEXT DEFAULT '',
    parent_id               INTEGER REFERENCES products(id) ON DELETE SET NULL,
    location_id             INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    product_group_id        INTEGER REFERENCES product_groups(id) ON DELETE SET NULL,
    unit_id                 INTEGER NOT NULL REFERENCES units(id),
    default_best_before_days INTEGER DEFAULT 60,
    min_stock_amount        REAL DEFAULT 0,
    picture_filename        TEXT,
    active                  INTEGER DEFAULT 1,
    unit_price              REAL,
    unit_price_currency     TEXT DEFAULT 'EUR',
    created_at              TEXT DEFAULT (datetime('now')),
    updated_at              TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS barcodes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id   INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    barcode      TEXT NOT NULL UNIQUE,
    pack_size    REAL DEFAULT 1,
    pack_unit_id INTEGER REFERENCES units(id) ON DELETE SET NULL,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS stock (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id       INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    location_id      INTEGER NOT NULL REFERENCES locations(id),
    amount           REAL NOT NULL DEFAULT 0,
    amount_opened    REAL DEFAULT 0,
    unit_id          INTEGER NOT NULL REFERENCES units(id),
    best_before_date TEXT,
    best_before_days INTEGER,
    purchased_date   TEXT DEFAULT (date('now')),
    price_paid       REAL,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS unit_conversions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    from_unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    to_unit_id   INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    factor       REAL NOT NULL,
    product_id   INTEGER REFERENCES products(id) ON DELETE CASCADE,
    UNIQUE(from_unit_id, to_unit_id, product_id)
);

CREATE TABLE IF NOT EXISTS recipes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    description      TEXT DEFAULT '',
    source_url       TEXT,
    servings         REAL DEFAULT 4,
    picture_filename TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id   INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    amount      REAL NOT NULL DEFAULT 1,
    unit_id     INTEGER NOT NULL REFERENCES units(id),
    note        TEXT DEFAULT '',
    sort_order  INTEGER DEFAULT 0,
    specificity TEXT NOT NULL DEFAULT 'loose'
);

CREATE TABLE IF NOT EXISTS shopping_list (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    amount     REAL NOT NULL DEFAULT 1,
    unit_id    INTEGER REFERENCES units(id),
    note       TEXT DEFAULT '',
    done       INTEGER DEFAULT 0,
    recipe_id  INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS barcode_queue (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode              TEXT NOT NULL,
    source               TEXT DEFAULT 'scan',
    status               TEXT DEFAULT 'pending',
    result_product_id    INTEGER REFERENCES products(id) ON DELETE SET NULL,
    error_message        TEXT,
    import_stock_amount  REAL,
    created_at           TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id       INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    event_type       TEXT NOT NULL,
    amount           REAL NOT NULL,
    unit_id          INTEGER REFERENCES units(id) ON DELETE SET NULL,
    location_id      INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    from_location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    stock_id         INTEGER,
    note             TEXT DEFAULT '',
    unit_price       REAL,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_products_parent ON products(parent_id);
CREATE INDEX IF NOT EXISTS idx_products_group ON products(product_group_id);
CREATE INDEX IF NOT EXISTS idx_products_location ON products(location_id);
CREATE INDEX IF NOT EXISTS idx_stock_product ON stock(product_id);
CREATE INDEX IF NOT EXISTS idx_stock_location ON stock(location_id);
CREATE INDEX IF NOT EXISTS idx_barcodes_product ON barcodes(product_id);
CREATE INDEX IF NOT EXISTS idx_barcodes_barcode ON barcodes(barcode);
CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_recipe ON recipe_ingredients(recipe_id);
CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_product ON recipe_ingredients(product_id);
CREATE INDEX IF NOT EXISTS idx_unit_conversions_from ON unit_conversions(from_unit_id);
CREATE INDEX IF NOT EXISTS idx_unit_conversions_product ON unit_conversions(product_id);
CREATE INDEX IF NOT EXISTS idx_shopping_list_product ON shopping_list(product_id);
CREATE INDEX IF NOT EXISTS idx_barcode_queue_status ON barcode_queue(status);
CREATE INDEX IF NOT EXISTS idx_stock_history_product ON stock_history(product_id);
CREATE INDEX IF NOT EXISTS idx_stock_history_created ON stock_history(created_at);
CREATE INDEX IF NOT EXISTS idx_stock_history_event ON stock_history(event_type);
"""

# Standard Finnish measurement units
_SEED_UNITS: list[tuple[str, str, str]] = [
    ("Gramma", "g", "Grammaa"),
    ("Kilogramma", "kg", "Kilogrammaa"),
    ("Millilitra", "ml", "Millilitraa"),
    ("Desilitra", "dl", "Desilitraa"),
    ("Litra", "l", "Litraa"),
    ("Teelusikka", "tl", "Teelusikkaa"),
    ("Ruokalusikka", "rkl", "Ruokalusikkaa"),
    ("Kappale", "kpl", "Kappaletta"),
    ("Ripaus", "rs", "Ripausta"),
]

# Global unit conversions (from → to, factor: 1 from = factor to)
_SEED_CONVERSIONS: list[tuple[str, str, float]] = [
    ("kg", "g", 1000),
    ("l", "dl", 10),
    ("l", "ml", 1000),
    ("dl", "ml", 100),
    ("rkl", "ml", 15),
    ("tl", "ml", 5),
]

# Default locations
_SEED_LOCATIONS: list[tuple[str, str]] = [
    ("Fridge", "Jääkaappi"),
    ("Pantry", "Kuivakaappi"),
    ("Freezer", "Pakastin"),
]


def _row_factory(cursor: sqlite3.Cursor, row: tuple) -> dict:
    """Return rows as dicts."""
    cols = [col[0] for col in cursor.description]
    return dict(zip(cols, row))


def get_db(db_path: str | Path) -> sqlite3.Connection:
    """Open (or create) the database and return a connection."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = _row_factory
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply incremental schema migrations for existing databases."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(barcode_queue)").fetchall()}
    if "import_stock_amount" not in cols:
        conn.execute("ALTER TABLE barcode_queue ADD COLUMN import_stock_amount REAL")
        conn.commit()
        log.info("Added import_stock_amount column to barcode_queue.")

    sl_cols = {r["name"] for r in conn.execute("PRAGMA table_info(shopping_list)").fetchall()}
    if "auto_added" not in sl_cols:
        conn.execute("ALTER TABLE shopping_list ADD COLUMN auto_added INTEGER DEFAULT 0")
        conn.commit()
        log.info("Added auto_added column to shopping_list.")
    if "ha_item_name" not in sl_cols:
        conn.execute("ALTER TABLE shopping_list ADD COLUMN ha_item_name TEXT")
        conn.commit()
        log.info("Added ha_item_name column to shopping_list.")

    # stock_history table for older databases that pre-date it
    has_history = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='stock_history'"
    ).fetchone()
    if not has_history:
        conn.executescript("""
            CREATE TABLE stock_history (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id       INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                event_type       TEXT NOT NULL,
                amount           REAL NOT NULL,
                unit_id          INTEGER REFERENCES units(id) ON DELETE SET NULL,
                location_id      INTEGER REFERENCES locations(id) ON DELETE SET NULL,
                from_location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,
                stock_id         INTEGER,
                note             TEXT DEFAULT '',
                created_at       TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX idx_stock_history_product ON stock_history(product_id);
            CREATE INDEX idx_stock_history_created ON stock_history(created_at);
            CREATE INDEX idx_stock_history_event ON stock_history(event_type);
        """)
        conn.commit()
        log.info("Created stock_history table.")

    # One-shot backfill of existing stock rows as 'purchase' events
    backfilled = conn.execute(
        "SELECT value FROM _meta WHERE key = 'history_backfilled'"
    ).fetchone()
    if not backfilled:
        rows = conn.execute(
            "SELECT id, product_id, location_id, amount, unit_id, "
            "       COALESCE(purchased_date || ' 00:00:00', created_at) AS ts "
            "FROM stock WHERE amount > 0"
        ).fetchall()
        for r in rows:
            conn.execute(
                "INSERT INTO stock_history "
                "(product_id, event_type, amount, unit_id, location_id, stock_id, note, created_at) "
                "VALUES (?, 'purchase', ?, ?, ?, ?, 'backfill', ?)",
                (r["product_id"], r["amount"], r["unit_id"], r["location_id"], r["id"], r["ts"]),
            )
        conn.execute(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('history_backfilled', ?)",
            (str(len(rows)),),
        )
        conn.commit()
        if rows:
            log.info("Backfilled %d stock rows into stock_history.", len(rows))

    # Add best_before_days column for pre-existing databases.
    stock_cols = {r["name"] for r in conn.execute("PRAGMA table_info(stock)").fetchall()}
    if "best_before_days" not in stock_cols:
        conn.execute("ALTER TABLE stock ADD COLUMN best_before_days INTEGER")
        conn.commit()
        log.info("Added best_before_days column to stock.")

    # Backfill purchased_date first so it's available for the next UPDATE.
    pd_filled = conn.execute("""
        UPDATE stock SET purchased_date = date(created_at)
        WHERE purchased_date IS NULL
    """).rowcount
    # When the lot already has both date fields, prefer the realized interval —
    # it's strictly more accurate than the product's current default for lots
    # that were added with an explicit/imported best_before_date.
    bbd_filled = conn.execute("""
        UPDATE stock SET best_before_days = CASE
            WHEN best_before_date IS NOT NULL AND purchased_date IS NOT NULL
                THEN CAST(julianday(best_before_date) - julianday(purchased_date) AS INTEGER)
            ELSE COALESCE(
                (SELECT default_best_before_days FROM products WHERE products.id = stock.product_id),
                0
            )
        END
        WHERE best_before_days IS NULL
    """).rowcount
    conn.commit()
    if bbd_filled or pd_filled:
        log.info(
            "Backfilled %d stock row(s) best_before_days and %d purchased_date.",
            bbd_filled, pd_filled,
        )

    # Enforce the per-lot invariant: best_before_date = purchased_date + best_before_days.
    # best_before_days is authoritative (snapshot of product policy or user-set per-lot
    # override); the date column is a derived/cached value of that math. Always-on and
    # idempotent — once consistent, the WHERE clause matches nothing. Self-heals any
    # row that drifts (legacy imports, sentinels, manual SQL edits).
    realigned = conn.execute("""
        UPDATE stock
        SET best_before_date = date(purchased_date, '+' || best_before_days || ' days')
        WHERE purchased_date IS NOT NULL
          AND best_before_days IS NOT NULL
          AND best_before_days > 0
          AND best_before_date IS NOT date(purchased_date, '+' || best_before_days || ' days')
    """).rowcount
    conn.commit()
    if realigned:
        log.info(
            "Realigned best_before_date for %d stock row(s) (date is derived from purchased_date + best_before_days).",
            realigned,
        )

    # Canonical FIFO index. Idempotent — safe on every init.
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_stock_fifo
          ON stock(product_id, best_before_date, purchased_date, id)
    """)
    conn.commit()

    # 0.11.0 — monetary waste tracking. Additive: existing rows return NULL
    # and the waste endpoint falls back to the product's current default.
    product_cols = {r["name"] for r in conn.execute("PRAGMA table_info(products)").fetchall()}
    if "unit_price" not in product_cols:
        conn.execute("ALTER TABLE products ADD COLUMN unit_price REAL")
        conn.commit()
        log.info("Added unit_price column to products.")
    if "unit_price_currency" not in product_cols:
        conn.execute("ALTER TABLE products ADD COLUMN unit_price_currency TEXT DEFAULT 'EUR'")
        conn.commit()
        log.info("Added unit_price_currency column to products.")

    if "price_paid" not in stock_cols:
        # stock_cols was read above before any stock migrations; re-read to be sure.
        stock_cols = {r["name"] for r in conn.execute("PRAGMA table_info(stock)").fetchall()}
    if "price_paid" not in stock_cols:
        conn.execute("ALTER TABLE stock ADD COLUMN price_paid REAL")
        conn.commit()
        log.info("Added price_paid column to stock.")

    history_cols = {r["name"] for r in conn.execute("PRAGMA table_info(stock_history)").fetchall()}
    if "unit_price" not in history_cols:
        conn.execute("ALTER TABLE stock_history ADD COLUMN unit_price REAL")
        conn.commit()
        log.info("Added unit_price column to stock_history.")

    # Per-ingredient specificity: 'loose' (default — substituting a child of the
    # linked parent product is acceptable) or 'strict' (the recipe needs that
    # exact product; siblings under the same parent are not interchangeable).
    ri_cols = {r["name"] for r in conn.execute("PRAGMA table_info(recipe_ingredients)").fetchall()}
    if "specificity" not in ri_cols:
        conn.execute(
            "ALTER TABLE recipe_ingredients ADD COLUMN specificity TEXT NOT NULL DEFAULT 'loose'"
        )
        conn.commit()
        log.info("Added specificity column to recipe_ingredients.")

    # One-shot heuristic backfill: upgrade rows to 'strict' when the stored
    # note text matches an existing child product's name exactly. That captures
    # rows scraped before specificity tracking existed where the recipe author
    # named a specific variant (parmesan, gouda, …) but the matcher linked to
    # the parent. We move product_id to the child and flip specificity to strict.
    flag = conn.execute(
        "SELECT value FROM _meta WHERE key = 'specificity_backfilled'"
    ).fetchone()
    if not flag:
        upgraded = _backfill_recipe_specificity(conn)
        conn.execute(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('specificity_backfilled', ?)",
            (str(upgraded),),
        )
        conn.commit()
        if upgraded:
            log.info("Backfilled %d recipe_ingredients row(s) to specificity=strict.", upgraded)


def _backfill_recipe_specificity(conn: sqlite3.Connection) -> int:
    """Heuristic: for each loose ingredient whose `note` mentions a name that
    matches an existing child product of the linked product, relink to that
    child and mark the row strict. Returns the number of rows upgraded.

    The note field stores '<prep note> — <finnish ingredient name>' so we scan
    each tokenized note segment for a child name match. Only rows where the
    linked product currently has children are considered.
    """
    rows = conn.execute("""
        SELECT ri.id, ri.product_id, ri.note
        FROM recipe_ingredients ri
        WHERE ri.specificity = 'loose'
          AND ri.note IS NOT NULL AND ri.note != ''
          AND EXISTS (
              SELECT 1 FROM products WHERE parent_id = ri.product_id
          )
    """).fetchall()
    if not rows:
        return 0

    parent_children: dict[int, list[dict]] = {}
    upgraded = 0
    for row in rows:
        parent_id = int(row["product_id"])
        if parent_id not in parent_children:
            parent_children[parent_id] = conn.execute(
                "SELECT id, name FROM products WHERE parent_id = ?",
                (parent_id,),
            ).fetchall()
        children = parent_children[parent_id]
        if not children:
            continue
        # Split the note on common delimiters and compare each segment, lowercased
        segments = [s.strip().lower() for s in row["note"].replace("—", "|").replace(",", "|").split("|") if s.strip()]
        child_match: dict | None = None
        for seg in segments:
            for child in children:
                if child["name"].lower().strip() == seg:
                    child_match = child
                    break
            if child_match:
                break
        if child_match:
            conn.execute(
                "UPDATE recipe_ingredients SET product_id = ?, specificity = 'strict' WHERE id = ?",
                (int(child_match["id"]), int(row["id"])),
            )
            upgraded += 1
    return upgraded


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist and seed initial data."""
    conn.executescript(_SCHEMA_SQL)

    # Schema migrations for existing databases
    _migrate_schema(conn)

    # Check if already seeded
    row = conn.execute(
        "SELECT value FROM _meta WHERE key = 'schema_version'"
    ).fetchone()
    if row:
        log.info("Database already initialized (schema v%s).", row["value"])
        return

    log.info("Seeding database with standard units, conversions, and locations...")

    # Seed units
    for name, abbrev, plural in _SEED_UNITS:
        conn.execute(
            "INSERT OR IGNORE INTO units (name, abbreviation, name_plural) VALUES (?, ?, ?)",
            (name, abbrev, plural),
        )

    # Build abbreviation → id map for conversions
    units = {
        r["abbreviation"]: r["id"]
        for r in conn.execute("SELECT id, abbreviation FROM units").fetchall()
    }

    # Seed global conversions
    for from_abbrev, to_abbrev, factor in _SEED_CONVERSIONS:
        from_id = units.get(from_abbrev)
        to_id = units.get(to_abbrev)
        if from_id and to_id:
            conn.execute(
                "INSERT OR IGNORE INTO unit_conversions (from_unit_id, to_unit_id, factor) "
                "VALUES (?, ?, ?)",
                (from_id, to_id, factor),
            )
            # Also insert the reverse conversion
            conn.execute(
                "INSERT OR IGNORE INTO unit_conversions (from_unit_id, to_unit_id, factor) "
                "VALUES (?, ?, ?)",
                (to_id, from_id, 1.0 / factor),
            )

    # Seed locations
    for name, desc in _SEED_LOCATIONS:
        conn.execute(
            "INSERT OR IGNORE INTO locations (name, description) VALUES (?, ?)",
            (name, desc),
        )

    # Mark as seeded
    conn.execute(
        "INSERT INTO _meta (key, value) VALUES ('schema_version', ?)",
        (str(_SCHEMA_VERSION),),
    )
    conn.commit()
    log.info("Database seeded successfully.")


# ---------------------------------------------------------------------------
# HA Supervisor options sync
# ---------------------------------------------------------------------------

_OPTIONS_FILE = Path("/data/options.json")

_OPTIONS_CONFIG_MAP = {
    "ai_provider": "ai_provider",
    "gemini_api_key": "gemini_api_key",
    "gemini_model": "gemini_model",
    "ollama_url": "ollama_url",
    "ollama_model": "ollama_model",
    "claude_api_key": "claude_api_key",
    "claude_model": "claude_model",
    "scraper_url": "scraper_url",
    "optimize_batch_size": "optimize_batch_size",
}


def sync_from_options(conn: sqlite3.Connection) -> None:
    """Read /data/options.json (HA Supervisor add-on config) and upsert non-empty
    values into the config table so settings set in the HA UI take effect.
    """
    import json as _json

    if not _OPTIONS_FILE.exists():
        return
    try:
        opts = _json.loads(_OPTIONS_FILE.read_text())
    except Exception as exc:
        log.warning("Could not read options.json: %s", exc)
        return

    synced = 0
    for opt_key, config_key in _OPTIONS_CONFIG_MAP.items():
        val = opts.get(opt_key)
        if val is None or val == "":
            continue
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (config_key, str(val)),
        )
        synced += 1

    if synced:
        conn.commit()
        log.info("Synced %d config value(s) from options.json.", synced)
