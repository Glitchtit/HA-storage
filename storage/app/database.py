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
    purchased_date   TEXT DEFAULT (date('now')),
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
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id  INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    amount     REAL NOT NULL DEFAULT 1,
    unit_id    INTEGER NOT NULL REFERENCES units(id),
    note       TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0
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
