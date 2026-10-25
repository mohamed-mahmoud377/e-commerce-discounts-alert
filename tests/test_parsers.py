from conftest import fixture_json, fixture_text

from deals.models import LIMITED_DEAL, LOWEST_30D, MEGA_DEAL, Slice
from deals.planner import split_price
from deals.sites.amazon_eg import parse_search_page
from deals.sites.base import parse_count, parse_money
from deals.sites.noon_eg import parse_catalog


def test_money_and_counts():
    assert parse_money("EGP\xa01,999.50") == 199950
    assert parse_money("Was: EGP 275.00") == 27500
    assert parse_money(None) is None
    assert parse_count("16.6K") == 16600
    assert parse_count("1,234") == 1234


def test_slice_keys_round_trip():
    for key in ["category:123", "search:laptop|price:1000-2000", "category:a/b|price:100000-", "category:x|price:-50"]:
        assert Slice.from_key(key).key() == key


def test_split_price_bands_then_halves():
    first = split_price(Slice.from_key("category:1|price:3000-"))
    assert [s.key() for s in first][:2] == ["category:1|price:3000-5000", "category:1|price:5000-10000"]
    assert first[-1].price_max is None
    assert [s.key() for s in split_price(Slice.from_key("category:1|price:3000-5000"))] == [
        "category:1|price:3000-4000", "category:1|price:4000-5000"]
    assert split_price(Slice.from_key("category:1|price:10-11")) == []


def test_amazon_search_page():
    result = parse_search_page(fixture_text("amazon_search.html.gz"))
    assert len(result.listings) >= 40
    assert len({x.sku for x in result.listings}) == len(result.listings)  # sponsored duplicates removed
    sony = next(x for x in result.listings if x.sku == "B0BS1PRC4L")
    assert (sony.price, sony.list_price, sony.list_price_kind) == (199900, 261900, "list")
    assert sony.rating == 4.2 and sony.reviews == 16600 and sony.recent_sales == 300
    assert any(x.stock_left for x in result.listings)
    assert any(LIMITED_DEAL in x.badges for x in result.listings)
    assert any(x.list_price_kind == "was" for x in result.listings)
    assert result.has_next


def test_amazon_node_page_children():
    result = parse_search_page(fixture_text("amazon_node.html.gz"), "21832868031")
    assert [c.name for c in result.subcategories] == ["Accessories", "Mobile Broadband", "Mobile Phones", "Smartwatches"]
    assert result.subcategories[2].id == "21832883031"


def test_noon_catalog():
    result = parse_catalog(fixture_json("noon_category.json.gz"), 1, "electronics-and-mobiles")
    assert len(result.listings) == 50 and result.total > 300000 and result.has_next
    assert any(MEGA_DEAL in x.badges for x in result.listings)
    assert any(LOWEST_30D in x.badges for x in result.listings)
    discounted = [x for x in result.listings if x.list_price]
    assert discounted and all(x.list_price > x.price and x.list_price_kind == "list" for x in discounted)
    assert result.subcategories[0].id == "electronics-and-mobiles/mobiles-and-accessories"
