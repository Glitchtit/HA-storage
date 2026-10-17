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


def test_substring_single_candidate_needs_judgement():
    # "kevyt" matches only "Kevytmaito": a partial hit is returned, not added.
    status, result = resolve_product_by_name("kevyt", PRODUCTS)
    assert status == "ambiguous"
    assert result == [{"id": 5, "name": "Kevytmaito"}]


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


def test_single_mid_word_match_needs_confirmation():
    # "egg" only appears inside "Reggiano": a weak hit, must not auto-add.
    prods = [_p(9, "Pirkka Parhaat Parmigiano Reggiano juustoraaste 80g")]
    status, result = resolve_product_by_name("Egg", prods)
    assert status == "ambiguous"
    assert result == [{"id": 9, "name": "Pirkka Parhaat Parmigiano Reggiano juustoraaste 80g"}]


def test_single_whole_word_match_is_not_added():
    # 2026-10-17: Jarvis's "butter" landed on popcorn because it was the only
    # product containing the word. A different product, so the agent must judge.
    prods = [_p(1663, "Sundlings butter popcorn 100g"), _p(681, "Voi")]
    status, result = resolve_product_by_name("butter", prods)
    assert status == "ambiguous"
    assert result == [{"id": 1663, "name": "Sundlings butter popcorn 100g"}]


def test_single_partial_match_message_offers_finnish_retry():
    cands = [{"id": 1663, "name": "Sundlings butter popcorn 100g"}]
    hist = [{"product_id": 1663, "product_name": "Sundlings butter popcorn 100g", "total_amount": 1, "event_count": 1}]
    msg = describe_add_failure("butter", "ambiguous", cands, hist)
    assert "only if it really is the item" in msg
    assert "butter = voi" in msg
    assert "usually buys Sundlings" not in msg


def test_ambiguous_message_offers_finnish_retry():
    cands = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
    msg = describe_add_failure("x", "ambiguous", cands, [])
    assert "Finnish word" in msg


def test_not_found_suggests_finnish_retry():
    msg = describe_add_failure("Eggs", "not_found", None, HISTORY)
    assert "Finnish" in msg
    assert "kananmuna" in msg  # concrete example the agent can follow


def test_ambiguous_without_history_uses_best_judgement():
    cands = [
        {"id": 1, "name": "Kultamuna vapaa kananmuna S/M10 530g"},
        {"id": 2, "name": "Pirkka vapaan kanan ruskea kananmuna 6 kpl/348g"},
    ]
    msg = describe_add_failure("kananmuna", "ambiguous", cands, [])
    low = msg.lower()
    assert "exact" not in low
    assert "Kultamuna" in msg and "Pirkka vapaan" in msg
    # The agent should choose itself, not bounce the choice back to the user.
    assert "without asking" in low
    assert "best judgement" in low
    assert "ask the user which" not in low


def test_not_found_with_history_does_not_invite_asking():
    msg = describe_add_failure("Eggs", "not_found", None, HISTORY)
    assert "only ask if none clearly fits" not in msg.lower()


def test_ambiguous_with_one_bought_candidate_recommends_it():
    hist = [{"product_id": 2, "product_name": "B", "total_amount": 3, "event_count": 3}]
    cands = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
    msg = describe_add_failure("x", "ambiguous", cands, hist)
    assert "B (bought 3×)" in msg
    assert "usually buys B" in msg


def test_ambiguous_lists_bought_candidates_first():
    cands = [{"id": i, "name": f"N{i}"} for i in range(12)]
    hist = [{"product_id": 11, "product_name": "N11", "total_amount": 2, "event_count": 2}]
    msg = describe_add_failure("x", "ambiguous", cands, hist)
    assert msg.index("N11 (bought 2×)") < msg.index("N0 (never bought)")


def test_an_exact_name_prefers_the_concept_over_a_sku():
    products = [{"id": 1, "name": "Maito", "kind": "sku"}, {"id": 2, "name": "Maito", "kind": "concept"}]
    assert resolve_product_by_name("maito", products) == ("added", {"id": 2, "name": "Maito"})


def test_ambiguous_candidates_list_concepts_first():
    products = [
        {"id": 1, "name": "Kermaviili", "kind": "sku"},
        {"id": 2, "name": "Vispikerma", "kind": "concept"},
        {"id": 3, "name": "Ruokakerma", "kind": "concept"},
    ]
    status, candidates = resolve_product_by_name("kerma", products)
    assert status == "ambiguous"
    assert [c["id"] for c in candidates] == [2, 3, 1]
