"""Shopping rows carry a buy hint: which retail product to pick up and how
many packages of it (spec §4.8)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "products"), exist_ok=True)
os.makedirs(os.path.join(os.environ["DATA_DIR"], "images", "recipes"), exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from main import app, get_connection

client = TestClient(app)


def _u(abbr):
    return next(u["id"] for u in client.get("/api/units").json() if u["abbreviation"] == abbr)


def _mk(name, unit="kpl", **extra):
    r = client.post("/api/products", json={"name": name, "unit_id": _u(unit), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _bought(product_id, *, days_ago, times=1):
    conn = get_connection()
    for _ in range(times):
        conn.execute(
            "INSERT INTO stock_history (product_id, event_type, amount, created_at) "
            "VALUES (?, 'purchase', 1, datetime('now', ?))",
            (product_id, f"-{days_ago} days"),
        )
    conn.commit()


def _add(product_id, amount=1, unit=None, **extra):
    body = {"product_id": product_id, "amount": amount, **extra}
    if unit:
        body["unit_id"] = _u(unit)
    r = client.post("/api/shopping-list", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _display(row_id):
    return next(i for i in client.get("/api/shopping-list").json() if i["id"] == row_id)["display"]


def _prefer(row_id, product_id):
    conn = get_connection()
    conn.execute("UPDATE shopping_list SET preferred_product_id = ? WHERE id = ?", (product_id, row_id))
    conn.commit()


class TestBuyProduct:
    def test_concept_row_hints_the_sku_bought_most_in_half_a_year(self):
        concept = _mk("Maito h1", kind="concept")
        rare = _mk("Harvinainen maito h1", parent_id=concept)
        usual = _mk("Arkimaito 1 l h1", parent_id=concept)
        _bought(rare, days_ago=3)
        _bought(usual, days_ago=20, times=3)
        d = _display(_add(concept, 2))
        assert (d["buy_product_id"], d["buy_product_name"], d["buy_count"], d["approx"]) == (
            usual, "Arkimaito 1 l h1", 2, False)

    def test_recent_purchase_breaks_a_tie_and_old_purchases_count_last(self):
        concept = _mk("Leipä h2", kind="concept")
        older = _mk("Vanhempi leipä h2", parent_id=concept)
        newer = _mk("Uudempi leipä h2", parent_id=concept)
        long_ago = _mk("Kauan sitten leipä h2", parent_id=concept)
        _bought(older, days_ago=40, times=2)
        _bought(newer, days_ago=10, times=2)
        _bought(long_ago, days_ago=400, times=9)
        assert _display(_add(concept))["buy_product_id"] == newer

    def test_without_recent_purchases_the_last_one_bought_wins(self):
        concept = _mk("Sinappi h3", kind="concept")
        first = _mk("Sinappi A h3", parent_id=concept)
        last = _mk("Sinappi B h3", parent_id=concept)
        _bought(first, days_ago=300)
        _bought(last, days_ago=200)
        assert _display(_add(concept))["buy_product_id"] == last

    def test_skus_further_down_the_tree_count(self):
        concept = _mk("Juusto h4", kind="concept")
        sub = _mk("Kypsytetty juusto h4", kind="concept", parent_id=concept)
        sku = _mk("Parmesaani 200 g h4", parent_id=sub)
        _bought(sku, days_ago=5)
        assert _display(_add(concept))["buy_product_id"] == sku

    def test_inactive_skus_are_not_suggested(self):
        concept = _mk("Mehu h5", kind="concept")
        gone = _mk("Lopetettu mehu h5", parent_id=concept, active=False)
        _bought(gone, days_ago=5, times=4)
        d = _display(_add(concept))
        assert (d["buy_product_id"], d["buy_count"], d["approx"]) == (None, None, False)

    def test_concept_with_no_products_has_no_hint(self):
        concept = _mk("Kardemumma h6", kind="concept")
        d = _display(_add(concept))
        assert (d["buy_product_id"], d["buy_product_name"], d["buy_count"]) == (None, None, None)

    def test_sku_row_buys_itself(self):
        sku = _mk("Kaurajuoma 1 l h7")
        d = _display(_add(sku, 3))
        assert (d["buy_product_id"], d["buy_count"], d["approx"]) == (sku, 3, False)

    def test_preferred_product_wins_over_the_usual_one(self):
        concept = _mk("Kahvi h8", kind="concept")
        usual = _mk("Arkikahvi h8", parent_id=concept)
        chosen = _mk("Juhlakahvi h8", parent_id=concept)
        _bought(usual, days_ago=5, times=5)
        row = _add(concept)
        _prefer(row, chosen)
        assert _display(row)["buy_product_id"] == chosen

    def test_preferred_product_outside_the_concept_is_ignored(self):
        concept = _mk("Tee h9", kind="concept")
        usual = _mk("Arkitee h9", parent_id=concept)
        stranger = _mk("Kahvi muualla h9")
        _bought(usual, days_ago=5)
        row = _add(concept)
        _prefer(row, stranger)
        assert _display(row)["buy_product_id"] == usual

    def test_deleting_the_preferred_product_keeps_the_row(self):
        concept = _mk("Riisi h10", kind="concept")
        chosen = _mk("Jasmiiniriisi h10", parent_id=concept)
        row = _add(concept)
        _prefer(row, chosen)
        assert client.delete(f"/api/products/{chosen}?force=true").status_code in (200, 204)
        item = next(i for i in client.get("/api/shopping-list").json() if i["id"] == row)
        assert item["preferred_product_id"] is None and item["display"]["buy_product_id"] is None

    def test_merging_the_preferred_product_moves_the_preference(self):
        concept = _mk("Pasta h11", kind="concept")
        dup = _mk("Spagetti 500 g h11", parent_id=concept)
        keep = _mk("Spaghetti 500 g h11", parent_id=concept)
        row = _add(concept)
        _prefer(row, dup)
        assert client.post(f"/api/products/{dup}/merge", json={"target_id": keep}).status_code == 200
        item = next(i for i in client.get("/api/shopping-list").json() if i["id"] == row)
        assert item["preferred_product_id"] == keep and item["display"]["buy_product_id"] == keep


class TestBuyCount:
    def test_volume_need_counts_whole_packages(self):
        concept = _mk("Kerma h12", unit="dl", kind="concept")
        sku = _mk("Vispikerma 2 dl h12", parent_id=concept, content_amount=2, content_unit_id=_u("dl"))
        _bought(sku, days_ago=5)
        d = _display(_add(concept, 3, unit="dl", note="From recipe: X"))
        assert (d["qty_text"], d["buy_product_id"], d["buy_count"], d["approx"]) == ("3 dl", sku, 2, False)

    def test_density_makes_the_count_approximate(self):
        concept = _mk("Vehnäjauho h13", unit="dl", kind="concept", density_g_ml=0.6)
        sku = _mk("Vehnäjauho 2 kg h13", parent_id=concept, content_amount=2, content_unit_id=_u("kg"))
        _bought(sku, days_ago=5)
        d = _display(_add(concept, 5, unit="dl", note="From recipe: Y"))
        assert (d["buy_count"], d["approx"]) == (1, True)

    def test_count_need_uses_the_pack_size(self):
        concept = _mk("Kananmuna h14", kind="concept")
        sku = _mk("Kananmunat 10 kpl h14", parent_id=concept, pack_count=10)
        _bought(sku, days_ago=5)
        d = _display(_add(concept, 12, unit="kpl", note="From recipe: Z"))
        assert (d["qty_text"], d["buy_count"], d["approx"]) == ("12 kpl", 2, False)

    def test_missing_facts_buy_one_package_marked_approximate(self):
        concept = _mk("Juustoraaste h15", unit="g", kind="concept")
        sku = _mk("Juustoraaste pussi h15", parent_id=concept)
        _bought(sku, days_ago=5)
        d = _display(_add(concept, 200, unit="g", note="From recipe: W"))
        assert (d["buy_product_id"], d["buy_count"], d["approx"]) == (sku, 1, True)

    def test_packages_on_a_concept_row_are_counted_as_given(self):
        concept = _mk("Banaani h16", kind="concept")
        sku = _mk("Banaanit 1 kg h16", parent_id=concept)
        _bought(sku, days_ago=5)
        d = _display(_add(concept, 2.5))
        assert (d["buy_count"], d["approx"]) == (3, False)

    def test_pieces_from_a_bag_sold_by_weight_buy_one_bag_marked_approximate(self):
        # "1,5 perunaa" must not become two 1 kg bags: without a piece weight
        # nothing says how many potatoes a bag holds.
        concept = _mk("Peruna h17", kind="concept")
        sku = _mk("Peruna 1 kg h17", parent_id=concept, content_amount=1, content_unit_id=_u("kg"))
        _bought(sku, days_ago=5)
        d = _display(_add(concept, 1.5, unit="kpl", note="From recipe: V"))
        assert (d["buy_product_id"], d["buy_count"], d["approx"]) == (sku, 1, True)

    def test_whole_spices_from_a_jar_buy_one_jar(self):
        concept = _mk("Maustepippuri h18", kind="concept")
        sku = _mk("Maustepippuri 17 g h18", parent_id=concept, content_amount=17, content_unit_id=_u("g"))
        _bought(sku, days_ago=5)
        d = _display(_add(concept, 4, unit="kpl", note="From recipe: U"))
        assert (d["buy_count"], d["approx"]) == (1, True)

    def test_pieces_with_a_piece_weight_count_the_bags(self):
        concept = _mk("Sipuli h19", kind="concept")
        sku = _mk("Sipuli 500 g h19", parent_id=concept, content_amount=500, content_unit_id=_u("g"),
                  piece_weight_g=100)
        _bought(sku, days_ago=5)
        d = _display(_add(concept, 7, unit="kpl", note="From recipe: T"))
        assert (d["buy_count"], d["approx"]) == (2, True)

    def test_a_product_sold_by_the_piece_counts_pieces(self):
        concept = _mk("Kurkku h20", kind="concept")
        sku = _mk("Kurkku irto h20", parent_id=concept)
        _bought(sku, days_ago=5)
        d = _display(_add(concept, 2, unit="kpl", note="From recipe: S"))
        assert (d["buy_count"], d["approx"]) == (2, True)


def _store(store_id):
    conn = get_connection()
    conn.execute("INSERT OR IGNORE INTO stores (id, name) VALUES (?, ?)", (store_id, store_id))
    conn.commit()


def _carried(product_id, store_id="T1", available=1):
    _store(store_id)
    conn = get_connection()
    conn.execute("INSERT OR REPLACE INTO product_availability (product_id, store_id, available) "
                 "VALUES (?, ?, ?)", (product_id, store_id, available))
    conn.commit()


class TestNeverBought:
    def test_a_variant_sold_in_a_store_beats_a_priced_one_and_the_oldest(self):
        concept = _mk("Kerma nb1", kind="concept")
        _mk("Vanhin kerma nb1", parent_id=concept)
        _mk("Hinnoiteltu kerma nb1", parent_id=concept, unit_price=1.5)
        sold = _mk("Myytävä kerma nb1", parent_id=concept)
        _carried(sold)
        assert _display(_add(concept))["buy_product_id"] == sold

    def test_a_priced_variant_beats_the_oldest(self):
        concept = _mk("Kerma nb2", kind="concept")
        _mk("Vanhin kerma nb2", parent_id=concept)
        priced = _mk("Hinnoiteltu kerma nb2", parent_id=concept, unit_price=1.5)
        assert _display(_add(concept))["buy_product_id"] == priced

    def test_without_ranks_the_oldest_variant_is_picked(self):
        concept = _mk("Kerma nb3", kind="concept")
        oldest = _mk("Vanhin kerma nb3", parent_id=concept)
        _mk("Uudempi kerma nb3", parent_id=concept)
        d = _display(_add(concept))
        assert (d["buy_product_id"], d["buy_product_name"]) == (oldest, "Vanhin kerma nb3")

    def test_a_bought_variant_still_wins(self):
        concept = _mk("Kerma nb4", kind="concept")
        bought = _mk("Ostettu kerma nb4", parent_id=concept)
        sold = _mk("Myytävä kerma nb4", parent_id=concept, unit_price=2)
        _carried(sold)
        _bought(bought, days_ago=5)
        assert _display(_add(concept))["buy_product_id"] == bought

    def test_inactive_variant_is_never_picked(self):
        concept = _mk("Kerma nb5", kind="concept")
        gone = _mk("Poistunut kerma nb5", parent_id=concept)
        r = client.put(f"/api/products/{gone}", json={"active": False})
        assert r.status_code == 200, r.text
        assert _display(_add(concept))["buy_product_id"] is None
