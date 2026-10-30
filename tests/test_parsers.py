import json

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


def test_btech_body_tree_and_parse():
    from deals.models import BESTSELLER, Blocked
    from deals.sites.btech_eg import build_body, category_slug, guest_token, parse_category_tree, parse_products
    assert category_slug("tvs-projectors/televisions") == "televisions" == category_slug("/televisions/")
    body = build_body(Slice("category", "tvs-projectors/televisions", 3000, 10000), 2)
    assert body == {"page": 2, "page_size": 100, "sort_by": "price_asc",
                    "filters": {"in_stock": True, "categories": "televisions", "price_range": {"min": 3000, "max": 10000}}}
    body = build_body(Slice("search", "playstation 5", 8000, None), 1)
    assert body["search_query"] == "playstation 5" and body["filters"] == {"in_stock": True, "price_range": {"min": 8000}}
    assert "categories" not in body["filters"]

    result = parse_products(fixture_json("btech_products.json.gz"), 1)
    assert len(result.listings) == 100 and result.total == 320 and result.has_next
    assert len({x.sku for x in result.listings}) == 100
    own = next(x for x in result.listings if x.sku == "1TSKTVFS3222A000L007")  # B.TECH's own offer with a promotion
    assert (own.price, own.list_price, own.list_price_kind, own.in_stock, own.brand) == (424900, 449900, "was", True, "Skyline")
    assert own.seller is None and own.badges == (BESTSELLER,) and own.title.startswith("Skyline 32 Inch TV")
    assert own.url == "https://btech.com/en/p/06b30035-3d58-4d29-99fc-0f08ed2c3dcc?offering_id=4e0e2f2d-4246-48cf-8775-d36ba2c91345"
    third_party = [x for x in result.listings if x.seller]
    assert len(third_party) == 68 and all(x.list_price_kind in (None, "list") for x in third_party)
    readable = next(x for x in result.listings if x.sku == "GQQNJRYDNUNPIARRXBZL")
    assert readable.url.startswith("https://btech.com/en/p/evolve-32-inch-fhd-standard-led-frameless-tv?offering_id=")
    assert not parse_products({"items": [], "total_items": 0, "total_pages": 0}, 1).has_next

    tree = parse_category_tree(fixture_text("btech_category.html.gz"))
    assert len(tree) == 330 and tree["televisions"]["value"] == "Televisions" and tree["televisions"]["count"] == 320
    assert [c["id"] for c in tree["tvs-projectors"]["options"]][:3] == ["projectors", "satellite-equipment", "televisions"]
    assert "no-such-thing" not in tree

    cookie = "%7B%22accessToken%22%3A%22ory_st_x%22%2C%22JWT%22%3A%22eyJhbGciOiJIUzI1NiJ9.e30.sig%22%2C%22isGuest%22%3Atrue%7D"
    assert guest_token(cookie) == "eyJhbGciOiJIUzI1NiJ9.e30.sig"
    with pytest.raises(Blocked, match="guest token"):
        guest_token(None)


def test_btech_unknown_category_token_refresh_and_block(monkeypatch):
    from deals.models import Blocked, UnknownTarget
    from deals.sites import btech_eg

    page_html = fixture_text("btech_category.html.gz")
    cookie = "%7B%22JWT%22%3A%22tok%22%7D"
    calls = []

    class Response:
        def __init__(self, status_code, body=None, text="", ctype="application/json"):
            self.status_code, self._body, self.text = status_code, body, text
            self.headers = {"content-type": ctype}

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    class Cookies(dict):
        pass

    class Session:
        def __init__(self, impersonate):
            calls.append(("session", impersonate))
            self.cookies = Cookies()

        def get(self, url, timeout):
            calls.append(("get", url))
            self.cookies["btech-auth-session"] = cookie
            return Response(200, text=page_html, ctype="text/html")

        def post(self, url, json, timeout, headers):
            calls.append(("post", json["filters"].get("categories"), headers["authorization"]))
            return next_response()

    monkeypatch.setattr(btech_eg.requests, "Session", Session)
    ok = {"items": [], "total_items": 0, "total_pages": 0}
    next_response = lambda: Response(200, ok)
    site = btech_eg.BtechEG()
    # the slug is checked against the tree before the API is called; the tree is fetched once
    with pytest.raises(UnknownTarget, match="no-such-thing"):
        site.fetch_page(Slice("category", "no-such-thing", 3000, None), 1)
    assert [c[0] for c in calls] == ["session", "get"]
    result = site.fetch_page(Slice("category", "tvs-projectors/televisions", 3000, None), 1)
    assert result.total == 0 and [c.id for c in result.subcategories][:2] == ["smart-tvs", "standard-tvs"] or result.subcategories
    assert calls[-1] == ("post", "televisions", "Bearer tok")
    # a refused token: one fresh session (new cookie), then Blocked
    answers = iter([Response(401, {"message": "Unauthorized"}), Response(401, {"message": "Unauthorized"})])
    next_response = lambda: next(answers)
    calls.clear()
    with pytest.raises(Blocked, match="guest token twice"):
        site.fetch_page(Slice("search", "ps5", None, None), 1)
    assert [c[0] for c in calls] == ["post", "session", "get", "post"]
    # a page past the end answers HTTP 500: the slice shrank, treat it as empty
    next_response = lambda: Response(500, {"detail": "Error executing search with filters request"})
    assert site.fetch_page(Slice("search", "ps5", None, None), 3).listings == []
    with pytest.raises(ValueError, match="API changed"):
        site.fetch_page(Slice("search", "ps5", None, None), 1)


def test_dream2000_variants_fold_into_listings():
    from deals.sites.dream2000_eg import parse_products, product_listings
    data = fixture_json("dream2000_products.json.gz")
    result = parse_products(data, total=112)
    assert result.total == 112 and not result.has_next  # 111 products < 250: a single page
    assert len({x.sku for x in result.listings}) == len(result.listings) > 111
    a17 = [x for x in result.listings if x.sku.startswith("8255092588683")]
    # RAM and storage make separate listings; the four colours of each fold into one
    assert sorted(x.sku for x in a17) == ["8255092588683:4gb-128gb", "8255092588683:6gb-128gb", "8255092588683:8gb-256gb"]
    big = next(x for x in a17 if x.sku.endswith("8gb-256gb"))
    assert (big.price, big.list_price, big.list_price_kind, big.in_stock) == (2049000, 2460000, "list", True)
    assert big.title == "Samsung Galaxy A17 (8GB / 256GB)" and big.brand == "Samsung"
    assert big.url == "https://dream2000.com/en/products/galaxy-a17"
    small = next(x for x in a17 if x.sku.endswith("4gb-128gb"))
    assert small.price == 1333000 and not small.in_stock  # sold out, price still shown
    # a product whose only option is colour (or none at all) is one listing keyed by its id
    single = [x for x in result.listings if ":" not in x.sku]
    assert single and all(x.sku.isdigit() for x in single)
    # the cheapest *available* variant sets the price; sold-out ones only when nothing is available
    product = {"id": 1, "title": "Watch", "handle": "watch", "vendor": "Amazfit",
               "options": [{"name": "Color"}],
               "variants": [{"option1": "Black", "price": "1000.00", "compare_at_price": "1500.00", "available": False},
                            {"option1": "Pink", "price": "1200.00", "compare_at_price": None, "available": True}]}
    (watch,) = product_listings(product)
    assert (watch.sku, watch.price, watch.list_price, watch.in_stock, watch.title) == ("1", 120000, None, True, "Watch")
    product["variants"][1]["available"] = False
    (watch,) = product_listings(product)
    assert (watch.price, watch.list_price, watch.in_stock) == (100000, 150000, False)
    empty = parse_products({"products": []}, total=None)
    assert empty.total == 0 and not empty.has_next
    assert parse_products({"products": [product] * 250}).has_next


def test_dream2000_unknown_handle_search_and_block(monkeypatch):
    from deals.models import Blocked, UnknownTarget
    from deals.sites import dream2000_eg

    urls = []

    class Response:
        def __init__(self, status_code, body=None, ctype="application/json"):
            self.status_code, self._body, self.headers = status_code, body, {"content-type": ctype}

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    class Session:
        def __init__(self, impersonate):
            pass

        def get(self, url, timeout):
            urls.append(url)
            return next_response(url)

    monkeypatch.setattr(dream2000_eg.requests, "Session", Session)
    site = dream2000_eg.Dream2000EG()
    next_response = lambda url: Response(404, None, "text/html")
    with pytest.raises(UnknownTarget, match="no-such-xyz"):
        site.fetch_page(Slice("category", "no-such-xyz", 2500, None), 1)
    assert urls == ["https://dream2000.com/en/collections/no-such-xyz.json"]
    with pytest.raises(UnknownTarget, match="no product search"):
        site.fetch_page(Slice("search", "ps5", None, None), 1)
    # page 1 asks for the count, later pages don't; 250 products mean another page
    next_response = lambda url: Response(200, {"collection": {"products_count": 300}} if url.endswith(".json")
                                         else {"products": [{"id": i, "title": "x", "handle": "x", "options": [], "variants": [
                                             {"price": "10.00", "available": True}]} for i in range(250)]})
    urls.clear()
    page = site.fetch_page(Slice("category", "accessories", None, None), 1)
    assert page.total == 300 and page.has_next and len(page.listings) == 250
    assert urls == ["https://dream2000.com/en/collections/accessories.json",
                    "https://dream2000.com/en/collections/accessories/products.json?limit=250&page=1"]
    urls.clear()
    site.fetch_page(Slice("category", "accessories", None, None), 2)
    assert urls == ["https://dream2000.com/en/collections/accessories/products.json?limit=250&page=2"]
    next_response = lambda url: Response(430, None, "text/html")
    with pytest.raises(Blocked, match="HTTP 430"):
        site.fetch_page(Slice("category", "accessories", None, None), 2)


def test_noon_category_value_may_carry_extra_catalogue_filters():
    """`category:<path>?f[min_offer_price]=7_days` crawls noon's own price-drop feed (fast lane)."""
    from deals.sites.noon_eg import request_for

    path, params = request_for(Slice("category", "electronics-and-mobiles/tvs?f[min_offer_price]=7_days", 3000, None), 2)
    assert path == "electronics-and-mobiles/tvs"
    assert params == {"page": 2, "f[min_offer_price]": "7_days", "f[price][min]": 3000}
    path, params = request_for(Slice("category", "electronics-and-mobiles/tvs", None, 5000), 1)
    assert (path, params) == ("electronics-and-mobiles/tvs", {"page": 1, "f[price][max]": 5000})
    assert request_for(Slice("search", "ps5 console"), 1) == ("search", {"page": 1, "q": "ps5 console"})


def test_jumia_flash_sale_listings_carry_the_deal_badge():
    from deals.models import LIMITED_DEAL
    from deals.sites.jumia_eg import _parse_product

    card = {"sku": "AB1", "name": "Fryer", "brand": "Fresh", "url": "/fryer.html",
            "prices": {"rawPrice": "1999.00", "oldPrice": "EGP 2,999.00"}, "isBuyable": True,
            "stock": {"percent": 3, "text": "3 items left"}}
    flash = _parse_product(card, flash=True)
    assert flash.badges == (LIMITED_DEAL,) and flash.stock_left == 3 and flash.list_price == 299_900
    assert _parse_product(card).badges == ()


def test_amazon_deals_api_page():
    from deals.sites.amazon_eg import deals_api_url, parse_deals_api
    result = parse_deals_api(fixture_text("amazon_deals_api.json.gz"))
    assert not result.has_next and result.total is None  # 40 products: a short page is the last
    by_sku = {l.sku: l for l in result.listings}
    tv = by_sku["B0H17ZVHBY"]  # LG 55" on a 26% "List:" deal
    assert tv.price == 2119900 and tv.list_price == 2859900 and tv.list_price_kind == "list"
    assert tv.url.endswith("/dp/B0H17ZVHBY") and tv.in_stock
    assert any(l.list_price_kind == "was" for l in result.listings)
    assert any(LIMITED_DEAL in l.badges for l in result.listings)
    assert all(l.price for l in result.listings)  # products without a deal price are skipped

    url = deals_api_url(Slice.from_key("deals:21832982031|price:3000-"), 2)
    assert url.startswith("https://www.amazon.eg/d2b/api/v1/products/search?")
    assert "startIndex=200" in url and "%2221832982031%22" in url
    assert "rangeRefinementFilters=%5B%7B%22id%22%3A%22price%22%2C%22min%22%3A3000%2C%22max%22%3A10000000%7D%5D" in url
    assert "rangeRefinementFilters" not in deals_api_url(Slice.from_key("deals:1"), 1)
    assert Slice.from_key("deals:1|price:10-20").key() == "deals:1|price:10-20"
    with pytest.raises(ValueError):
        Slice.from_key("deal:1")


def _carrefour_page(data: dict) -> str:
    """A minimal Carrefour page: the listing object inside a Next.js RSC chunk."""
    return f'<html><script>self.__next_f.push([1,{json.dumps(json.dumps(data))}])</script></html>'


def test_carrefour_category_page():
    from deals.sites.carrefour_eg import page_url, parse_listing_page
    result = parse_listing_page(fixture_text("carrefour_category.html.gz"), 1)
    assert result.total == 2010 and result.has_next and len(result.listings) == 60
    assert ("NFEGY4080000", "TVs & Projectors (168)") in [(c.id, c.name) for c in result.subcategories]
    by_sku = {x.sku: x for x in result.listings}
    # Carrefour's own timed promotion: the crossed-out price is its regular price ("was")
    tv = by_sku["658120"]
    assert (tv.price, tv.list_price, tv.list_price_kind, tv.badges) == (2649900, 2799900, "was", (LIMITED_DEAL,))
    assert (tv.rating, tv.reviews, tv.brand, tv.seller, tv.in_stock) == (4.9, 23, "Samsung", None, True)
    assert tv.url == "https://www.carrefouregypt.com/mafegy/en/uhd-tv/samsung-led-tv-ua-65u8000-f-sm-4k/p/658120"
    assert tv.title == "Samsung 65-inch 4K Crystal UHD Smart TV - 65U8000F"
    # a marketplace seller's crossed-out price is only a claim
    washer = by_sku["529297"]
    assert (washer.seller, washer.list_price_kind, washer.stock_left) == ("Electro Zone", "list", 1)
    # "Only 4 left" is the order limit on an in-stock card, not a stock count
    kettle = by_sku["647686"]
    assert kettle.stock_left is None and kettle.badges == () and kettle.list_price is None
    assert not any(x.title.startswith("‎") for x in result.listings)
    # sorted by price, 0-based pages, inclusive price bounds, search by keyword
    assert page_url(Slice.from_key("category:NFEGY4080000|price:3000-5000"), 1) == (
        "https://www.carrefouregypt.com/mafegy/en/c/NFEGY4080000?sortBy=price_asc&minPrice=3000&maxPrice=5000")
    assert page_url(Slice.from_key("category:NFEGY4080000|price:3000-"), 3) == (
        "https://www.carrefouregypt.com/mafegy/en/c/NFEGY4080000?sortBy=price_asc&currentPage=2&minPrice=3000")
    assert page_url(Slice.from_key("search:playstation 5"), 1) == (
        "https://www.carrefouregypt.com/mafegy/en/search?keyword=playstation+5&sortBy=price_asc")


def test_carrefour_page_cap_empty_unknown_and_block(monkeypatch):
    from deals.models import Blocked, UnknownTarget
    from deals.sites import carrefour_eg

    card = {"uid": "master-product-card", "componentDTO": {"additionalAttributes": {
        "productId": "1", "productName": "TV", "sellingPrice": 4000, "markedPrice": 4000,
        "shopName": "Carrefour", "stock": {"stockLevelStatus": "inStock"}, "productUrl": "/mafegy/en/tv/p/1"}}}
    sponsored = {"uid": "master-product-card", "componentDTO": {"additionalAttributes": {
        "productId": "2", "productName": "Kettle", "sellingPrice": 300, "isSponsored": True}}}
    full = _carrefour_page({"topStickySections": [], "sections": [card, sponsored], "additionalAttributes": {},
                            "pagination": {"totalPages": 34, "totalResults": 2010, "currentPage": 0}})
    empty = _carrefour_page({"topStickySections": [], "sections": [], "additionalAttributes": {"maxColumns": 12}})
    # pages past currentPage=20 repeat page 20, so paging stops at page 21
    assert carrefour_eg.parse_listing_page(full, 20).has_next
    assert not carrefour_eg.parse_listing_page(full, 21).has_next
    assert [x.sku for x in carrefour_eg.parse_listing_page(full, 1).listings] == ["1"]  # sponsored card dropped
    with pytest.raises(ValueError, match="layout changed"):
        carrefour_eg.parse_listing_page("<html>Access Denied</html>")

    urls = []

    class Response:
        def __init__(self, status_code, text):
            self.status_code, self.text = status_code, text

        def raise_for_status(self):
            pass

    class Session:
        def __init__(self, impersonate):
            pass

        def get(self, url, timeout):
            urls.append(url)
            return answer(url)

    monkeypatch.setattr(carrefour_eg.requests, "Session", Session)
    site = carrefour_eg.CarrefourEG()
    # an empty price range of a real category is an empty slice (checked once against the bare category)
    answer = lambda url: Response(200, full if "minPrice" not in url else empty)
    result = site.fetch_page(Slice.from_key("category:NFEGY4080000|price:900000-"), 1)
    assert result.total == 0 and not result.listings and len(urls) == 2
    urls.clear()
    assert site.fetch_page(Slice.from_key("category:NFEGY4080000|price:800000-"), 1).total == 0
    assert len(urls) == 1  # the category is known now
    # an unknown id answers 200 with the same empty listing
    answer = lambda url: Response(200, empty)
    with pytest.raises(UnknownTarget, match="NFEGY999"):
        site.fetch_page(Slice.from_key("category:NFEGY999|price:3000-"), 1)
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice.from_key("category:nfegy4080000"), 1)  # ids are case-sensitive
    # an empty search or a page past the end is just empty
    assert site.fetch_page(Slice.from_key("search:zzqq"), 1).total == 0
    later = site.fetch_page(Slice.from_key("category:NFEGY4080000"), 3)
    assert later.listings == [] and later.total is None and not later.has_next
    # Akamai refuses twice (a fresh session in between): blocked
    answer = lambda url: Response(429, "<HTML>Access Denied</HTML>")
    urls.clear()
    with pytest.raises(Blocked, match="HTTP 429"):
        site.fetch_page(Slice.from_key("category:NFEGY4080000"), 1)
    assert len(urls) == 2


def test_dubaiphone_listing_and_traps():
    from deals.sites.dubaiphone_eg import page_url, parse_listing
    result = parse_listing(fixture_text("dubaiphone_mobiles.rsc.gz"))
    assert result.total == 120 and result.has_next
    assert ("honor", "Honor (15)") in [(c.id, c.name) for c in result.subcategories]
    by_sku = {x.sku: x for x in result.listings}
    phone = by_sku["1200"]
    assert (phone.price, phone.list_price, phone.in_stock) == (14600000, None, True)
    assert phone.title == "IPhone 17 Pro Max With FaceTime - 1TB, 12GB RAM"
    assert phone.url == "https://www.dubaiphone.net/en/shop/apple-iphone-17-pro-max-with-facetime-1tb-12gb-ram"
    # sold-out phones wait at 1 EGP for a real price: no price at all
    assert by_sku["2854"].price is None and not by_sku["2854"].in_stock
    # pre-orders are priced at the deposit (500 EGP) or before sale: skipped
    assert not {"2655", "2774", "2821", "2822"} & set(by_sku)
    # "coming soon" shows a price but can't be bought
    assert by_sku["2543"].price == 17600000 and not by_sku["2543"].in_stock
    assert len(result.listings) == 96  # 100 cards, 4 pre-orders
    # newest first (stable paging), inclusive price filter, the current minute past Vercel's cache
    assert page_url(Slice.from_key("category:tvs|price:3000-"), 2, now=600) == (
        "https://www.dubaiphone.net/en/category/tvs?per_page=100&sort=newest&page=2&price_min=3000&t=10")


def test_dubaiphone_crossed_out_price_html_page_and_errors(monkeypatch):
    from deals.models import Blocked, UnknownTarget
    from deals.sites import dubaiphone_eg

    item = {"id": 500, "slug": "anker", "name": "Anker MagGo", "price": {"amount": 5899}, "selling_price": {"amount": 5599},
            "has_special_price": True, "in_stock": True, "availability_type": "normal", "preorder": {"enabled": False}}
    same = {**item, "id": 501, "price": {"amount": 5599}}  # has_special_price with equal prices: no claim
    data = {"products": {"total": 2, "current_page": 1, "last_page": 1, "data": [item, same]}, "filters": {}}
    rsc = '1:["$","$L30",null,{"shopData":' + json.dumps(data) + "}]\n"
    html = f'<html><script>self.__next_f.push([1,{json.dumps(rsc)}])</script></html>'
    for body in (rsc, html):
        result = dubaiphone_eg.parse_listing(body)
        a, b = result.listings
        assert (a.price, a.list_price, a.list_price_kind) == (559900, 589900, "list")
        assert (b.price, b.list_price) == (559900, None) and not result.has_next

    class Response:
        def __init__(self, status_code, text, headers=None):
            self.status_code, self.text, self.headers = status_code, text, headers or {}

        def raise_for_status(self):
            pass

    urls = []

    class Session:
        def __init__(self, impersonate):
            pass

        def get(self, url, timeout, headers):
            assert headers == {"RSC": "1"}
            urls.append(url)
            return answer

    monkeypatch.setattr(dubaiphone_eg.requests, "Session", Session)
    site = dubaiphone_eg.DubaiPhoneEG()
    answer = Response(200, '0:["NEXT_HTTP_ERROR_FALLBACK;404"]')
    with pytest.raises(UnknownTarget, match="no-such"):
        site.fetch_page(Slice.from_key("category:no-such"), 1)
    with pytest.raises(UnknownTarget, match="search"):
        site.fetch_page(Slice.from_key("search:iphone"), 1)
    answer = Response(200, '0:["Something went wrong"]')  # e.g. per_page over 100
    with pytest.raises(ValueError, match="no shopData"):
        site.fetch_page(Slice.from_key("category:tvs"), 1)
    answer = Response(429, "challenge", {"x-vercel-mitigated": "challenge"})
    urls.clear()
    with pytest.raises(Blocked, match="Vercel challenge"):
        site.fetch_page(Slice.from_key("category:tvs"), 1)
    assert len(urls) == 2  # one fresh session first


def test_hardwaremarket_products():
    from deals.sites.hardwaremarket_eg import parse_products, products_url
    result = parse_products(fixture_json("hardwaremarket_headsets.json.gz"), 559, 6, 6)
    assert result.total == 559 and not result.has_next and len(result.listings) == 59
    by_sku = {x.sku: x for x in result.listings}
    g321 = by_sku["154226"]
    assert (g321.price, g321.list_price, g321.list_price_kind, g321.in_stock, g321.brand) == (
        309900, 349900, "list", True, "Logitech")
    assert g321.url == "https://hardwaremarket.net/product/logitech-g321-lightspeed-gaming-headset/"
    # HTML entities in titles; brand from an "ONIKUMA | X92" SKU when the shop set none
    x92 = by_sku["158133"]
    assert x92.title.startswith("ONIKUMA X92 RGB Gaming Headset – 50mm") and x92.brand == "Onikuma"
    assert by_sku["169314"].brand == "Microsoft"
    assert sum(not x.in_stock for x in result.listings) == 27  # sold-out products stay visible
    assert parse_products([], 0, 0, 1).total == 0
    # every stock status, paged by id, price filter in piastres (inclusive whole pounds)
    url = products_url(Slice.from_key("category:39|price:800-1500"), 2)
    assert url.startswith("https://hardwaremarket.net/wp-json/wc/store/v1/products?_fields=id%2Cname%2Csku")
    assert "per_page=100&page=2&orderby=id&order=asc&category=39&min_price=80000&max_price=150099" in url
    assert url.endswith("stock_status%5B%5D=instock&stock_status%5B%5D=outofstock&stock_status%5B%5D=onbackorder")
    deals = products_url(Slice.from_key("deals:41|price:1000-"), 1)
    assert "per_page=50&orderby=modified&order=desc&category=41&min_price=100000" in deals
    assert "search=redragon" in products_url(Slice.from_key("search:redragon"), 1)
    assert "category=" not in products_url(Slice.from_key("category:all"), 1)


def test_hardwaremarket_categories_deals_lane_and_block(monkeypatch):
    from deals.models import Blocked, UnknownTarget
    from deals.sites import hardwaremarket_eg

    categories = [{"id": 39, "slug": "headsets", "name": "Headsets", "parent": 0, "count": 71},
                  {"id": 72, "slug": "wired-headsets", "name": "Wired", "parent": 39, "count": 57}]
    product = {"id": 1, "name": "Mouse &amp; Pad", "sku": "X", "permalink": "https://hardwaremarket.net/product/x/",
               "prices": {"price": "100000", "regular_price": "100000", "currency_code": "EGP"}, "is_in_stock": True}

    class Response:
        def __init__(self, status_code, body, headers=None):
            self.status_code, self._body = status_code, body
            self.headers = {"content-type": "application/json", **(headers or {})}
            self.text = json.dumps(body)

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    urls = []

    class Session:
        def __init__(self, impersonate):
            pass

        def get(self, url, timeout):
            urls.append(url)
            return answer(url)

    monkeypatch.setattr(hardwaremarket_eg.requests, "Session", Session)
    site = hardwaremarket_eg.HardwareMarketEG()
    answer = lambda url: Response(200, categories) if "/categories" in url else Response(
        200, [product], {"x-wp-total": "120", "x-wp-totalpages": "2"})
    page = site.fetch_page(Slice.from_key("category:headsets"), 1)
    assert page.total == 120 and page.has_next and page.listings[0].title == "Mouse & Pad"
    assert [(c.id, c.name) for c in page.subcategories] == [("72", "Wired (57)")]
    assert page.listings[0].list_price is None
    with pytest.raises(UnknownTarget, match="9999"):
        site.fetch_page(Slice.from_key("category:9999"), 1)
    assert sum("/categories" in u for u in urls) == 1  # the tree is read once
    # the fast lane is one page of recently modified products, no count
    lane = site.fetch_page(Slice.from_key("deals:39"), 1)
    assert lane.total is None and not lane.has_next and len(lane.listings) == 1
    assert site.fetch_page(Slice.from_key("deals:39"), 2).listings == []
    # Cloudflare's challenge is HTML, twice: blocked
    answer = lambda url: Response(403, None, {"content-type": "text/html", "cf-mitigated": "challenge"})
    urls.clear()
    with pytest.raises(Blocked, match="HTTP 403"):
        site.fetch_page(Slice.from_key("category:all"), 1)
    assert len(urls) == 2
