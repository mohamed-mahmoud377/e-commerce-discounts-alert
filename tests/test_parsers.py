from conftest import fixture_json, fixture_text
import pytest

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
    # tracked per seller offer, so different sellers' prices never mix in one history
    assert all("@" in x.sku and x.seller for x in result.listings)


def test_amazon_decoy_page_is_flagged():
    page = fixture_text("amazon_search.html.gz")
    assert parse_search_page(page).decoy is False
    # amazon.eg's soft block: a listing of random products with no result-count bar
    decoy = page.replace('data-component-type="s-result-info-bar"', 'data-component-type="x-gone"')
    result = parse_search_page(decoy)
    assert result.decoy is True and result.total is None and result.listings
    # a genuinely empty page (no cards) is not a decoy
    assert parse_search_page("<html><body></body></html>").decoy is False


def test_amazon_site_treats_decoy_as_block(tmp_path, monkeypatch):
    from deals.models import Blocked, Slice
    from deals.sites.amazon_eg import AmazonEG
    site = AmazonEG(tmp_path)
    decoy = fixture_text("amazon_search.html.gz").replace('data-component-type="s-result-info-bar"', 'x')
    monkeypatch.setattr(site, "_get", lambda url: decoy)
    refreshed = []
    monkeypatch.setattr(site, "_bootstrap_cookies", lambda: refreshed.append(1))
    with pytest.raises(Blocked, match="decoy"):
        site.fetch_page(Slice("search:playstation 5 console", 8000, None), 1)
    assert refreshed == [1]  # a fresh cookie session was tried once before giving up
    # ...and if the fresh session gets a real page, that page is used
    pages = iter([decoy, fixture_text("amazon_search.html.gz")])
    monkeypatch.setattr(site, "_get", lambda url: next(pages))
    assert len(site.fetch_page(Slice("search:playstation 5 console", 8000, None), 1).listings) >= 40


def test_amazon_no_results_page_is_empty_not_a_failure():
    result = parse_search_page(fixture_text("amazon_no_results.html.gz"), "21833039031")
    assert result.listings == [] and result.total == 0 and not result.has_next and not result.decoy
