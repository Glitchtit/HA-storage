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
