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


def test_jumia_category_page():
    from deals.sites.jumia_eg import parse_catalog_page
    result = parse_catalog_page(fixture_text("jumia_category.html.gz"), "/televisions/", 1, "televisions")
    assert len(result.listings) == 40 and result.total == 454 and result.has_next
    assert len({x.sku for x in result.listings}) == 40
    lg = next(x for x in result.listings if x.sku == "LG178EA3AHODVNAFAMZ")
    assert (lg.price, lg.list_price, lg.list_price_kind, lg.in_stock) == (1529900, 1999900, "list", True)
    assert (lg.brand, lg.rating, lg.reviews) == ("LG", 3.8, 10)
    assert lg.url.startswith("https://www.jumia.com.eg/lg-led-smart-tv-43-inch") and lg.title.startswith("LG LED Smart TV")
    hoho = next(x for x in result.listings if x.sku == "HO179EL1K701HNAFAMZ")
    assert hoho.stock_left == 1 and hoho.list_price is None  # "1 item left" lives on the variant
    # the sidebar lists ancestors, the current category and its children; only children count
    assert [c.id for c in result.subcategories] == ["led-lcd-tvs", "oled-tvs", "category-url-en-smart-tvs",
                                                    "large-screen-tv"]


def test_jumia_redirect_to_parent_is_an_empty_slice_or_an_unknown_category():
    from deals.models import UnknownTarget
    from deals.sites.jumia_eg import parse_catalog_page
    page = fixture_text("jumia_category.html.gz")
    # An empty price range of a child (here OLED TVs) is answered with its parent: empty, not 454 TVs.
    empty = parse_catalog_page(page, "/oled-tvs/", 1, "oled-tvs")
    assert empty.listings == [] and empty.total == 0 and not empty.has_next
    # A page past the last one redirects the same way (the slice shrank during the crawl).
    past_end = parse_catalog_page(page, "/led-lcd-tvs/", 5, "led-lcd-tvs")
    assert past_end.listings == [] and not past_end.has_next
    # Landing anywhere unrelated means the slug is wrong or renamed: fail loudly.
    with pytest.raises(UnknownTarget, match="redirected"):
        parse_catalog_page(page, "/some-renamed-category/", 1, "some-renamed-category")


def test_jumia_challenge_is_a_block(monkeypatch):
    from deals.models import Blocked
    from deals.sites import jumia_eg

    class Challenge:
        status_code, text = 403, "<html><title>Just a moment...</title><script>window._cf_chl_opt={}</script>"

    sessions = []

    class Session:
        def __init__(self, impersonate):
            sessions.append(impersonate)

        def get(self, url, timeout):
            return Challenge()

    monkeypatch.setattr(jumia_eg.requests, "Session", Session)
    with pytest.raises(Blocked, match="Cloudflare"):
        jumia_eg.JumiaEG().fetch_page(Slice("category", "televisions", 3000, None), 1)
    assert sessions == ["safari", "safari"]  # one fresh session was tried before giving up


def test_cairosales_category_page():
    from deals.sites.cairosales_eg import page_url, parse_listing_page
    path, url = page_url(Slice("category", "tvs-screens", 3000, None), 2)
    assert path == "/en/tvs-screens/" and url.endswith("/en/tvs-screens/?n=60&orderby=price&orderway=asc&price=3000-10000000&p=2")
    assert page_url(Slice("search", "playstation 5", 8000, 12000), 1)[1].endswith(
        "/en/search?controller=search&search_query=playstation+5&n=60&orderby=price&orderway=asc&price=8000-12000")
    result = parse_listing_page(fixture_text("cairosales_category.html.gz"), "tvs-screens")
    assert len(result.listings) == 60 and result.total == 820 and result.has_next
    assert len({x.sku for x in result.listings}) == 60
    first = result.listings[0]
    assert (first.sku, first.price, first.list_price, first.list_price_kind, first.in_stock) == ("31774", 494900, 549900, "list", True)
    assert first.title == "Ultra 32 Inch HD LED TV FSKE32H" and first.url.startswith("https://cairosales.com/en/tvs-screens/ultra-32")
    assert first.brand and first.stock_left is None and first.currency == "EGP"
    assert [c.id for c in result.subcategories][:3] == ["tvs-screens/25-32-inch", "tvs-screens/43-54-inch", "tvs-screens/55-65-inch"]
    assert all(c.id.startswith("tvs-screens/") for c in result.subcategories) and len(result.subcategories) == 17


def test_cairosales_out_of_stock_products_hide_their_price():
    from deals.sites.cairosales_eg import parse_listing_page
    result = parse_listing_page(fixture_text("cairosales_mobiles.html.gz"), "mobiles-tablets/mobiles")
    assert result.total == 263 and len(result.listings) == 60 and result.has_next
    hidden = [x for x in result.listings if x.price is None]
    assert len(hidden) == 55 and not any(x.in_stock for x in hidden)
    assert all(x.in_stock for x in result.listings if x.price is not None)
    assert result.subcategories == []  # a leaf category has no "Subcategories" block
    # out-of-stock products sort last, so a page with no price at all ends the crawl early
    dead = ('<ul class="product_list"><li class="ajax_block_product" data-id-product="1"><a class="product-name" '
            'href="https://cairosales.com/en/x.html">Old phone</a></li></ul><a rel="next" href="?p=3">Next</a>'
            '<p>Showing 61 - 120 of 263 items</p>')
    page = parse_listing_page(dead)
    assert page.total == 263 and len(page.listings) == 1 and not page.has_next


def test_cairosales_empty_page_redirect_and_challenge(monkeypatch):
    from deals.models import Blocked, UnknownTarget
    from deals.sites import cairosales_eg
    from deals.sites.cairosales_eg import parse_listing_page
    empty = parse_listing_page("<html><ul class='product_list'></ul><p>No products</p></html>", "x")
    assert empty.listings == [] and empty.total == 0 and not empty.has_next

    answers = []

    class Response:
        def __init__(self, status_code, text, url):
            self.status_code, self.text, self.url = status_code, text, url

        def raise_for_status(self):
            pass

    class Session:
        def __init__(self, impersonate):
            answers.append(impersonate)

        def get(self, url, timeout, allow_redirects):
            return next_response(url)

    monkeypatch.setattr(cairosales_eg.requests, "Session", Session)
    # an unknown slug is answered with the homepage after a redirect
    next_response = lambda url: Response(200, "<html>home</html>", "https://cairosales.com/en/")
    with pytest.raises(UnknownTarget, match="redirected to /en/"):
        cairosales_eg.CairoSalesEG().fetch_page(Slice("category", "no-such-thing", 3000, None), 1)
    # a Cloudflare challenge: one fresh session, then Blocked
    next_response = lambda url: Response(403, "<html><title>Attention Required! | Cloudflare</title></html>", url)
    answers.clear()
    with pytest.raises(Blocked, match="Cloudflare"):
        cairosales_eg.CairoSalesEG().fetch_page(Slice("category", "tvs-screens", 3000, None), 1)
    assert answers == ["chrome", "chrome"]


def test_2b_products_query_and_parse():
    from deals.models import UnknownTarget
    from deals.sites.twob_eg import build_query, parse_products
    q = build_query(Slice("category", "131", 3000, 10000), 2)
    assert 'filter: {category_id: {eq: "131"}, price: {from: "3000", to: "10000"}}' in q
    assert "currentPage: 2" in q and 'categories(filters: {ids: {eq: "131"}})' in q
    q = build_query(Slice("search", "playstation 5", 8000, None), 1)
    assert 'search: "playstation 5", filter: {price: {from: "8000"}}' in q and "categories(" not in q
    with pytest.raises(UnknownTarget, match="numbers"):
        build_query(Slice("category", "tvs", None, None), 1)

    result = parse_products(fixture_json("2b_products.json.gz"), 1)
    assert len(result.listings) == 96 and result.total == 96 and not result.has_next
    tv = next(x for x in result.listings if x.sku == "TV442")
    assert (tv.price, tv.list_price, tv.list_price_kind, tv.in_stock, tv.brand) == (1169900, 1599900, "list", True, "Xiaomi")
    assert tv.url == "https://2b.com.eg/en/xiaomi-tv-43-inch-led-fhd-smart-google-tv-with-built-in-receiver-l43mb-afme-2026.html"
    rated = next(x for x in result.listings if x.sku == "TV118")
    assert (rated.rating, rated.reviews) == (5.0, 1)
    assert sum(x.stock_left is not None for x in result.listings) == 54  # "only N left" is everywhere
    assert all(x.rating is None for x in result.listings if not x.reviews)
    assert result.subcategories[0].id == "2167" and result.subcategories[0].name == "LG (11)"


def test_2b_challenge_and_unknown_category(monkeypatch):
    from deals.models import Blocked, UnknownTarget
    from deals.sites import twob_eg

    class Response:
        def __init__(self, status_code, body, ctype="application/json"):
            self.status_code, self._body, self.headers = status_code, body, {"content-type": ctype}

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    sessions = []

    class Session:
        def __init__(self, impersonate):
            sessions.append(impersonate)

        def post(self, url, json, timeout, headers):
            return next_response()

    monkeypatch.setattr(twob_eg.requests, "Session", Session)
    next_response = lambda: Response(403, None, "text/html")
    with pytest.raises(Blocked, match="Cloudflare"):
        twob_eg.TwoBEG().fetch_page(Slice("category", "131", 3000, None), 1)
    assert sessions == ["chrome", "chrome"]
    next_response = lambda: Response(200, {"data": {"products": {"total_count": 0, "page_info": {"total_pages": 0}, "items": []},
                                                     "category": {"items": []}}})
    with pytest.raises(UnknownTarget, match="no category with id 999"):
        twob_eg.TwoBEG().fetch_page(Slice("category", "999", None, None), 1)
    next_response = lambda: Response(200, {"errors": [{"message": "Cannot query field x"}]})
    with pytest.raises(ValueError, match="GraphQL error"):
        twob_eg.TwoBEG().fetch_page(Slice("category", "131", None, None), 1)
