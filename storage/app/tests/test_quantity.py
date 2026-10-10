"""Pure tests for the quantity engine (no database, no app import)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from quantity import (
    COUNT, MASS, PACKAGE, VOLUME, Facts, Unit, convert, format_qty,
    normalize_unit_text,
)

G = Unit(1, "g", MASS, 1.0)
KG = Unit(2, "kg", MASS, 1000.0)
ML = Unit(3, "ml", VOLUME, 1.0)
DL = Unit(4, "dl", VOLUME, 100.0)
TL = Unit(6, "tl", VOLUME, 5.0)
KPL = Unit(8, "kpl", COUNT, 1.0)
RS = Unit(9, "rs", "vague", None)
PRK = Unit(12, "prk", PACKAGE, 1.0)


def approx_eq(a, b, tol=1e-6):
    return abs(a - b) <= tol


class TestSameDimension:
    def test_kg_to_g(self):
        c = convert(1.5, KG, G)
        assert approx_eq(c.amount, 1500) and c.approx is False

    def test_tl_to_dl(self):
        c = convert(4, TL, DL)
        assert approx_eq(c.amount, 0.2)

    def test_vague_never_converts(self):
        assert convert(1, RS, G) is None
        assert convert(1, G, RS) is None


class TestPackageContent:
    def test_package_to_mass_via_content(self):
        facts = Facts(content_amount=2, content_unit=KG)
        c = convert(1, KPL, DL, Facts(content_amount=2, content_unit=KG, density_g_ml=0.6), from_dim=PACKAGE)
        # 1 pkg = 2000 g = 3333.3 ml = 33.3 dl, approximate because of density
        assert approx_eq(c.amount, 2000 / 0.6 / 100) and c.approx is True
        exact = convert(1, KPL, G, facts, from_dim=PACKAGE)
        assert approx_eq(exact.amount, 2000) and exact.approx is False

    def test_mass_to_packages(self):
        facts = Facts(content_amount=400, content_unit=G)
        c = convert(500, G, KPL, facts, to_dim=PACKAGE)
        assert approx_eq(c.amount, 1.25) and c.approx is False

    def test_zero_content_is_ignored(self):
        facts = Facts(content_amount=0, content_unit=G)
        assert convert(1, KPL, G, facts, from_dim=PACKAGE) is None


class TestPieces:
    def test_egg_carton_pack_count(self):
        facts = Facts(content_amount=580, content_unit=G, pack_count=10)
        c = convert(1, KPL, KPL, facts, from_dim=PACKAGE, to_dim=COUNT)
        assert approx_eq(c.amount, 10) and c.approx is False

    def test_potato_bag_via_piece_weight(self):
        facts = Facts(content_amount=1, content_unit=KG, piece_weight_g=150)
        c = convert(1, KPL, KPL, facts, from_dim=PACKAGE, to_dim=COUNT)
        assert approx_eq(c.amount, 1000 / 150) and c.approx is True

    def test_exact_piece_weight_is_not_approx(self):
        facts = Facts(piece_weight_g=58, piece_weight_exact=True)
        c = convert(2, KPL, G, facts)
        assert approx_eq(c.amount, 116) and c.approx is False

    def test_fallback_one_piece_per_package_is_last_resort(self):
        c = convert(3, KPL, KPL, Facts(), from_dim=PACKAGE, to_dim=COUNT)
        assert approx_eq(c.amount, 3) and c.approx is True

    def test_count_to_mass_without_facts_fails(self):
        assert convert(2, KPL, G, Facts()) is None


class TestDensity:
    def test_flour_dl_to_g(self):
        c = convert(1, DL, G, Facts(density_g_ml=0.6))
        assert approx_eq(c.amount, 60) and c.approx is True

    def test_implausible_density_ignored(self):
        assert convert(1, DL, G, Facts(density_g_ml=10309.0)) is None


class TestPackageUnits:
    def test_prk_need_vs_sku_packages(self):
        c = convert(2, KPL, PRK, Facts(), from_dim=PACKAGE)
        assert approx_eq(c.amount, 2) and c.approx is False


class TestNormalizeUnitText:
    @pytest.mark.parametrize("text,expected", [
        ("msk", ("rkl", 1.0)), ("Tsk", ("tl", 1.0)), ("krm", ("mm", 1.0)),
        ("st", ("kpl", 1.0)), ("burk", ("prk", 1.0)), ("förp", ("pkt", 1.0)),
        ("påse", ("pss", 1.0)), ("kruka", ("ruukku", 1.0)), ("knippe", ("nippu", 1.0)),
        ("cup", ("dl", 2.4)), ("oz", ("g", 28.35)), ("dl.", ("dl", 1.0)),
        ("  KG ", ("kg", 1.0)),
    ])
    def test_known(self, text, expected):
        assert normalize_unit_text(text) == expected

    @pytest.mark.parametrize("text", [None, "", "handful", "nyppy x"])
    def test_unknown(self, text):
        assert normalize_unit_text(text) is None


class TestFormatQty:
    @pytest.mark.parametrize("amount,unit,text", [
        (3.5, "dl", "3,5 dl"), (2.0, None, "2"), (0.25, "tl", "0,25 tl"),
        (1.0 / 3, "dl", "0,33 dl"), (200, "g", "200 g"), (0.0001, "g", "0 g"),
    ])
    def test_format(self, amount, unit, text):
        assert format_qty(amount, unit) == text
