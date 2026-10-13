"""Shopping notes written with the old Finnish recipe prefix are rewritten to
the English one once at startup, so the UI shows "From recipe: …"."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database
from database import get_db, init_db


def test_finnish_recipe_notes_are_rewritten_once(tmp_path):
    conn = get_db(tmp_path / "notes.db")
    init_db(conn)
    kpl = conn.execute("SELECT id FROM units WHERE abbreviation = 'kpl'").fetchone()["id"]
    pid = conn.execute("INSERT INTO products (name, unit_id) VALUES ('Vehnäjauho', ?)", (kpl,)).lastrowid
    rows = {
        note: conn.execute("INSERT INTO shopping_list (product_id, note) VALUES (?, ?)", (pid, note)).lastrowid
        for note in ("Reseptistä: Pasta Carbonara, Pannukakku", "From recipe: Taco night", "ostetaan Lidlistä", "")
    }
    conn.execute("DELETE FROM _meta WHERE key = 'english_recipe_notes_v1'")
    conn.commit()

    database.english_recipe_notes(conn)

    notes = {rid: conn.execute("SELECT note FROM shopping_list WHERE id = ?", (rid,)).fetchone()["note"]
             for rid in rows.values()}
    assert notes[rows["Reseptistä: Pasta Carbonara, Pannukakku"]] == "From recipe: Pasta Carbonara, Pannukakku"
    assert notes[rows["From recipe: Taco night"]] == "From recipe: Taco night"
    assert notes[rows["ostetaan Lidlistä"]] == "ostetaan Lidlistä"  # user notes are left alone
    assert notes[rows[""]] == ""
    assert conn.execute("SELECT 1 FROM _meta WHERE key = 'english_recipe_notes_v1'").fetchone()

    # Runs once: a legacy note written later (old client) is left for the merge path.
    conn.execute("UPDATE shopping_list SET note = 'Reseptistä: X' WHERE id = ?", (rows[""],))
    database.english_recipe_notes(conn)
    assert conn.execute("SELECT note FROM shopping_list WHERE id = ?", (rows[""],)).fetchone()["note"] == "Reseptistä: X"
