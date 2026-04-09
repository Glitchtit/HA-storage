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
    product_id INTEGER NOT NULL REFERENCES products(id),
    amount     REAL NOT NULL DEFAULT 1,
    unit_id    INTEGER NOT NULL REFERENCES units(id),
    note       TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS shopping_list (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    amount     REAL NOT NULL DEFAULT 1,
    unit_id    INTEGER REFERENCES units(id),
    note       TEXT DEFAULT '',
    done       INTEGER DEFAULT 0,
    recipe_id  INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS barcode_queue (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode           TEXT NOT NULL,
    source            TEXT DEFAULT 'scan',
    status            TEXT DEFAULT 'pending',
    result_product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    error_message     TEXT,
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist and seed initial data."""
    conn.executescript(_SCHEMA_SQL)

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
