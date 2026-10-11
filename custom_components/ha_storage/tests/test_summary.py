"""Todo summaries show the quantity the way Storage displays it — pure, no Home Assistant import."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from summary import todo_summary


def _item(amount, display=None, **extra):
    item = {"id": 1, "product_id": 7, "amount": amount, "ha_item_name": "Tallennettu nimi", **extra}
    if display is not None:
        item["display"] = display
    return item


def _display(qty_text, unit_abbr, dimension):
    return {"qty_text": qty_text, "unit_abbr": unit_abbr, "unit_dimension": dimension}


def test_a_requirement_shows_its_quantity():
    item = _item(3.5, _display("3,5 dl", "dl", "volume"))
    assert todo_summary(item, {7: "Kerma"}) == "Kerma 3,5 dl"


def test_natural_pieces_show_their_unit():
    item = _item(6, _display("6 kpl", "kpl", "count"))
    assert todo_summary(item, {7: "Kananmuna"}) == "Kananmuna 6 kpl"


def test_one_package_is_just_the_name():
    assert todo_summary(_item(1, _display("1", None, "package")), {7: "Maito"}) == "Maito"


def test_several_packages_show_a_count_without_the_unit():
    item = _item(2, _display("2 kpl", "kpl", "package"))
    assert todo_summary(item, {7: "Maito"}) == "Maito ×2"


def test_fractional_packages_use_a_decimal_comma():
    assert todo_summary(_item(1.5, _display("1,5", None, "package")), {7: "Banaani"}) == "Banaani ×1,5"


def test_rows_from_an_older_storage_without_display_show_a_count():
    assert todo_summary(_item(2), {7: "Kerma"}) == "Kerma ×2"


def test_a_product_missing_from_the_list_uses_the_stored_name():
    assert todo_summary(_item(1, _display("1", None, "package")), {}) == "Tallennettu nimi"


def test_a_generic_row_names_the_product_to_buy():
    display = {**_display("1", None, "package"), "buy_product_id": 9,
               "buy_product_name": "Pirkka creme fraiche 150g"}
    assert todo_summary(_item(1, display), {7: "Crème fraîche"}) == "Pirkka creme fraiche 150g"
    need = {**_display("300 g", "g", "mass"), "buy_product_id": 9, "buy_product_name": "Tomaatti 500g"}
    assert todo_summary(_item(300, need), {7: "Tomaatti"}) == "Tomaatti 500g 300 g"
    own = {**_display("1", None, "package"), "buy_product_id": 7, "buy_product_name": "Maito"}
    assert todo_summary(_item(1, own), {7: "Maito"}) == "Maito"
