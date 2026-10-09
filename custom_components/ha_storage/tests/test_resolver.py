"""Unit tests for resolve_product_by_name — pure, no Home Assistant import."""

from __future__ import annotations

import sys
from pathlib import Path

# Import the pure resolver module directly (parent dir = the integration package
# dir). No `homeassistant` import, so this runs in a bare pytest environment.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resolver import resolve_product_by_name


def _p(pid: int, name: str) -> dict:
    return {"id": pid, "name": name}


PRODUCTS = [
    _p(1, "Sprite"),
    _p(2, "Coca-Cola"),
    _p(3, "Coca-Cola Zero"),
    _p(4, "Maito"),
    _p(5, "Kevytmaito"),
]


def test_exact_match_case_insensitive():
    status, result = resolve_product_by_name("sprite", PRODUCTS)
    assert status == "added"
    assert result == {"id": 1, "name": "Sprite"}


def test_exact_match_wins_over_substring():
    # "Maito" is an exact match AND a substring of "Kevytmaito"; exact must win.
    status, result = resolve_product_by_name("Maito", PRODUCTS)
    assert status == "added"
    assert result == {"id": 4, "name": "Maito"}


def test_substring_single_candidate_is_added():
    # "kevyt" matches only "Kevytmaito" as a substring -> single candidate -> added.
    status, result = resolve_product_by_name("kevyt", PRODUCTS)
    assert status == "added"
    assert result == {"id": 5, "name": "Kevytmaito"}


def test_ambiguous_multiple_candidates():
    # "coca" is a substring of two products -> ambiguous, candidates returned.
    status, result = resolve_product_by_name("coca", PRODUCTS)
    assert status == "ambiguous"
    assert result == [
        {"id": 2, "name": "Coca-Cola"},
        {"id": 3, "name": "Coca-Cola Zero"},
    ]


def test_not_found():
    status, result = resolve_product_by_name("Fanta", PRODUCTS)
    assert status == "not_found"
    assert result is None


def test_casefold_finnish_compare():
    # Casefold handles case-insensitive Finnish names. "MAITO" exact-matches "Maito".
    status, result = resolve_product_by_name("MAITO", PRODUCTS)
    assert status == "added"
    assert result == {"id": 4, "name": "Maito"}


def test_blank_name_is_not_found():
    status, result = resolve_product_by_name("   ", PRODUCTS)
    assert status == "not_found"
    assert result is None


# --- describe_add_failure ---------------------------------------------------

from resolver import describe_add_failure  # noqa: E402

HISTORY = [
    {"product_id": 7, "product_name": "Pepsi Max virvoitusjuoma 0,33l 18-pack", "total_amount": 36.0, "event_count": 2},
    {"product_id": 1396, "product_name": "Valio Eila kevytmaitojuoma 2,5dl laktoositon UHT", "total_amount": 15.0, "event_count": 15},
    {"product_id": 1477, "product_name": "Stratos Milky Joy suklaapatukka 35g", "total_amount": 1.0, "event_count": 1},
]


def test_failure_lists_usual_purchases_by_frequency():
    msg = describe_add_failure("Milk", "not_found", None, HISTORY)
    assert "'Milk'" in msg
    # Ordered by how often it is bought (event_count), not total amount.
    assert msg.index("Valio Eila kevytmaitojuoma") < msg.index("Pepsi Max")
    assert "15×" in msg
    # Tells the agent to act instead of asking when the match is obvious.
    assert "without asking" in msg


def test_failure_ambiguous_marks_candidate_purchase_counts():
    cands = [
        {"id": 1818, "name": "Palmolive Naturals nestesaippua 500ml Olive Milk täyttöpussi"},
        {"id": 1477, "name": "Stratos Milky Joy suklaapatukka 35g"},
    ]
    msg = describe_add_failure("Milk", "ambiguous", cands, HISTORY)
    assert "Palmolive Naturals" in msg
    assert "Stratos Milky Joy suklaapatukka 35g (bought 1×)" in msg
    assert "Palmolive Naturals nestesaippua 500ml Olive Milk täyttöpussi (never bought)" in msg


def test_failure_without_history_still_readable():
    msg = describe_add_failure("Dragon fruit", "not_found", None, [])
    assert "'Dragon fruit'" in msg
    assert "usually buys" not in msg


def test_failure_caps_history_length():
    many = [
        {"product_id": i, "product_name": f"P{i}", "total_amount": 1, "event_count": 1000 - i}
        for i in range(200)
    ]
    msg = describe_add_failure("x", "not_found", None, many, limit=50)
    assert "P49 " in msg and "P50 " not in msg
