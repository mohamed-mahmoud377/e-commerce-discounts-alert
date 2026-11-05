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


def test_amazon_now_queries_carry_its_root_node(tmp_path):
    from urllib.parse import parse_qs, urlparse
    from deals.models import UnknownTarget
    from deals.sites.amazon_now_eg import AmazonNowEG
    site = AmazonNowEG(tmp_path)

    def query(key: str, page: int = 1) -> dict:
        return {k: v[0] for k, v in parse_qs(urlparse(site.search_url(Slice.from_key(key), page)).query).items()}

    # without the root a child node lists the regular Amazon offers, not Amazon Now's
    assert query("category:18020637031|price:20-40") == {"rh": "n:211761638031,n:18020637031,p_36:2000-4000"}
    assert query("category:211761638031", 3) == {"rh": "n:211761638031,p_36:0-", "page": "3"}
    assert query("category:211761638031,p_n_deal_type:26901036031")["rh"] == \
        "n:211761638031,p_n_deal_type:26901036031,p_36:0-"
    assert query("search:milk") == {"k": "milk", "rh": "n:211761638031"}
    with pytest.raises(UnknownTarget):  # the Today's Deals API has nothing under Amazon Now
        site.fetch_page(Slice.from_key("deals:211761638031"), 1)
    # amazon_eg's own queries are unchanged by the refactor
    from deals.sites.amazon_eg import AmazonEG
    assert AmazonEG(tmp_path).search_url(Slice.from_key("category:18020637031"), 1) == \
        "https://www.amazon.eg/-/en/s?rh=n%3A18020637031%2Cp_36%3A0-"


def test_amazon_now_pages(tmp_path, monkeypatch):
    from deals.sites.amazon_now_eg import AmazonNowEG
    site = AmazonNowEG(tmp_path)
    # The Amazon Now root (3 Nov 2026): "over 1,000", departments as children, the Amazon Now
    # price of an ASIN that regular Amazon sold for EGP 275.95 at the same moment
    monkeypatch.setattr(site, "_get", lambda url: fixture_text("amazon_now_root.html.gz"))
    root = site.fetch_page(Slice.from_key("category:211761638031"), 1)
    assert root.total is None and root.has_next and not root.decoy
    assert ("18020637031", "Grocery") in [(c.id, c.name) for c in root.subcategories]
    milk = next(x for x in root.listings if x.sku == "B0CVW9DPCB")
    assert (milk.site, milk.price, milk.list_price, milk.list_price_kind) == ("amazon_now_eg", 28975, 34000, "list")
    assert milk.url == "https://www.amazon.eg/-/en/dp/B0CVW9DPCB?almBrandId=sAuWWBROaG&fpw=alm"

    # Grocery, 20-40 EGP: an exact total, Grocery's children, 33 cards a page, a sold-out card with no price
    monkeypatch.setattr(site, "_get", lambda url: fixture_text("amazon_now_node.html.gz"))
    node = site.fetch_page(Slice.from_key("category:18020637031|price:20-40"), 1)
    assert node.total == 965 and node.has_next and len(node.listings) == 33
    assert ("21840237031", "Baby Foods") in [(c.id, c.name) for c in node.subcategories]
    assert all(x.site == "amazon_now_eg" for x in node.listings)
    assert any(not x.in_stock and x.price is None for x in node.listings)

    # An unserved delivery location (the droplet's) gets "no results" for the whole store: a block, not an empty shop
    from deals.models import Blocked
    monkeypatch.setattr(site, "_get", lambda url: fixture_text("amazon_no_results.html.gz"))
    with pytest.raises(Blocked, match="not served"):
        site.fetch_page(Slice.from_key("category:211761638031"), 1)
    assert site.fetch_page(Slice.from_key("category:18020637031|price:20-40"), 1).total == 0  # a narrow slice may be empty


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
    # a search with only the group's floor goes without the filter (Cloudflare refuses some of those)
    assert page_url(Slice("search", "nintendo switch", 8000, None), 1)[1].endswith(
        "/en/search?controller=search&search_query=nintendo+switch&n=60&orderby=price&orderway=asc")
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
    # search pages: the next link's first rel is "nofollow"
    priced = ('<ul class="product_list"><li class="ajax_block_product" data-id-product="2"><a class="product-name" '
              'href="https://cairosales.com/en/x.html">Xbox</a><div class="content_price"><span class="price product-price">EGP 25,000</span></div></li></ul>'
              '<li id="pagination_next_bottom" class="pagination_next"><a rel="nofollow" href="?p=2" rel="next">Next</a></li>')
    assert parse_listing_page(priced).has_next


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
    assert big.url == "https://dream2000.com/en/products/galaxy-a17?variant=47061098037387"  # its cheapest in stock
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
    assert watch.url == "https://dream2000.com/en/products/watch"  # one listing: no variant to pick
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

    # A node whose deals ended (kitchen appliances 2000+, 31 Oct 2026): empty, not a parse failure
    empty = parse_deals_api(fixture_text("amazon_deals_api_empty.json.gz"))
    assert empty.total == 0 and not empty.listings and not empty.has_next
    assert parse_deals_api('{"startIndex":0,"items":[]}').total is None  # unknown shape: layout check
    assert parse_deals_api('{"startIndex":40,"products":[]}').total is None  # past the first page

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
    # newest first (stable paging), the current minute past Vercel's cache, and no price filter
    # (the shop's hides sold-out products; the coordinator applies the group minimum)
    assert page_url(Slice.from_key("category:tvs|price:3000-"), 2, now=600) == (
        "https://www.dubaiphone.net/en/category/tvs?per_page=100&sort=newest&page=2&t=10")


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

    # Stock comes from the price-filtered listing (it holds exactly the products in stock): the plain
    # listing calls 501 sold out (its first colour is), the filtered one lists it, so it is in stock;
    # 500 is flagged in stock but missing from the filtered listing, so it is sold out.
    plain = {"products": {"total": 2, "current_page": 1, "last_page": 1,
                          "data": [item, {**same, "in_stock": False}]}}
    stocked = {"products": {"total": 1, "current_page": 1, "last_page": 1, "data": [same]}}
    stream = lambda d: '1:["$","$L30",null,{"shopData":' + json.dumps(d) + "}]\n"

    def get(self, url, timeout, headers):
        urls.append(url)
        return Response(200, stream(stocked if "price_min=1" in url else plain))

    monkeypatch.setattr(Session, "get", get)
    site = dubaiphone_eg.DubaiPhoneEG()
    urls.clear()
    page = site.fetch_page(Slice.from_key("category:mobiles-all-2|price:2500-"), 1)
    assert {x.sku: x.in_stock for x in page.listings} == {"500": False, "501": True}
    assert len(urls) == 2 and "price_min=1" in urls[1] and "price_min" not in urls[0]
    site.fetch_page(Slice.from_key("category:mobiles-all-2|price:2500-"), 2)
    assert len(urls) == 3  # later pages reuse the ids read with page 1


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


def test_raya_products():
    from deals.sites.raya_eg import build_query, parse_products
    result = parse_products(fixture_json("raya_tvs.json.gz")["data"], 1)
    assert result.total == 70 and not result.has_next and len(result.listings) == 70
    by_sku = {x.sku: x for x in result.listings}
    jac = by_sku["50JB734-Teba HA"]
    assert (jac.price, jac.list_price, jac.list_price_kind, jac.in_stock, jac.brand, jac.stock_left) == (
        2019300, 2524125, "list", True, "JAC", 1)
    assert jac.url == "https://www.rayashop.com/en/jac-50-inch-4k-uhd-smart-led-tv-50jb734"
    assert by_sku["58US1500E"].brand == "TORNADO"
    query = build_query(Slice("category", "534", 3000, None), 2)
    assert 'category_id: {eq: "534"}, price: {from: "3000"}' in query and "currentPage: 2" in query
    assert 'search: "lg tv", filter: {price: {to: "9000"}}' in build_query(Slice("search", "lg tv", None, 9000), 1)


def test_raya_skips_prebooking_deposits():
    """Ten 'Pre Booking' phones at exactly 10,000 EGP: the shop counts them, we don't record them."""
    from deals.sites.raya_eg import parse_products
    result = parse_products(fixture_json("raya_deposits.json.gz")["data"], 1)
    assert result.total == 10 and result.listings == []


def _response(status_code, body, content_type="application/json"):
    class Response:
        def __init__(self):
            self.status_code, self._body = status_code, body
            self.headers = {"content-type": content_type}
            self.text = json.dumps(body)

        def json(self):
            return self._body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")
    return Response()


def test_raya_unknown_category_and_block(monkeypatch):
    from deals.models import Blocked, UnknownTarget
    from deals.sites.raya_eg import RayaEG
    site = RayaEG()
    answers = [_response(200, {"data": {"products": {"total_count": 0, "items": []}, "category": []}}),
               _response(403, {}), _response(200, {"errors": [{"message": "Cannot query field"}]})]
    monkeypatch.setattr(site._session, "post", lambda *a, **k: answers.pop(0))
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("category", "99999"), 1)
    with pytest.raises(Blocked):
        site.fetch_page(Slice("category", "534"), 1)
    with pytest.raises(ValueError, match="API changed"):
        site.fetch_page(Slice("category", "534"), 1)
    with pytest.raises(UnknownTarget):  # not a number: no request at all
        site.fetch_page(Slice("category", "tvs"), 1)
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("deals", "534"), 1)


def test_vodafone_products():
    from deals.sites.vodafone_eg import VodafoneEG, parse_products, search_url
    result = parse_products(fixture_json("vodafone_phones.json.gz"))
    assert result.total == 136 and not result.has_next and len(result.listings) == 136
    assert all(x.in_stock for x in result.listings)  # only what is in stock is asked for
    by_sku = {x.sku: x for x in result.listings}
    # a phone with no `sku` in the JSON is still read: the key is the product's id
    bundle = by_sku["01K05CP63M0QP100468WQJ0YWS"]
    assert (bundle.title, bundle.price, bundle.list_price, bundle.list_price_kind) == (
        "HONOR X9C Smart + Earbuds X6 - White", 1445000, None, None)
    a07 = by_sku["01M4N5VZ7SXFV91AQK9YWV1K33"]
    assert a07.url == "https://eshop.vodafone.com.eg/en/prod/samsung-galaxy-a07s-4g-64gb" and a07.brand == "Samsung"
    assert any(x.list_price and x.list_price_kind == "list" and x.list_price > x.price for x in result.listings)
    url = search_url("/smart-phones", 3)
    assert "%2Fsmart-phones" in url and "page=2" in url and "hideOutOfStock" in url and "sort=sku%2Casc" in url
    assert VodafoneEG()._session is not None


def test_vodafone_unknown_category_and_block(monkeypatch):
    from deals.models import Blocked, UnknownTarget
    from deals.sites.vodafone_eg import VodafoneEG
    site = VodafoneEG()
    answers = [_response(404, {}), _response(403, {})]
    monkeypatch.setattr(site._session, "get", lambda *a, **k: answers.pop(0))
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("category", "nope"), 1)
    with pytest.raises(Blocked):
        site.fetch_page(Slice("category", "tv"), 1)
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("search", "tv"), 1)


def test_dokkantech_products():
    from deals.sites.dokkantech_eg import parse_products
    result = parse_products(fixture_json("dokkantech_keyboards.json.gz"), 54)
    assert result.total == 54 and not result.has_next and len(result.listings) == 68
    by_sku = {x.sku: x for x in result.listings}
    assert all(x.site == "dokkantech_eg" for x in result.listings)
    legion = by_sku["8848478568597"]
    assert (legion.price, legion.list_price, legion.list_price_kind, legion.brand) == (449900, 499900, "list", "Legion")
    assert legion.url == ("https://www.dokkantech.com/products/"
                          "lenovo-legion-k510-mini-pro-gaming-mechanical-keyboard-arabic-english")
    # "Box" looks like packaging but is a price: each box is its own listing, the sold-out one stays visible
    asia, mena = by_sku["8634306953365:asia-box-english"], by_sku["8634306953365:mena-box-arabic"]
    assert (asia.price, asia.in_stock, mena.price, mena.in_stock) == (449900, True, 499900, False)
    assert "?variant=" in asia.url


def test_dokkantech_unknown_collection_and_block(monkeypatch):
    from deals.models import Blocked, UnknownTarget
    from deals.sites.dokkantech_eg import DokkanTechEG
    site = DokkanTechEG()
    answers = [_response(404, {}), _response(430, {}), _response(200, {}, "text/html")]
    monkeypatch.setattr(site._session, "get", lambda *a, **k: answers.pop(0))
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("category", "nope"), 1)
    with pytest.raises(Blocked):
        site.fetch_page(Slice("category", "mouse"), 1)
    with pytest.raises(Blocked):  # a challenge page is not JSON
        site.fetch_page(Slice("category", "mouse"), 2)
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("search", "mouse"), 1)


def test_etisal_products():
    from deals.sites.etisal_eg import parse_products, query
    result = parse_products(fixture_json("etisal_wearables.json.gz"), 1)
    assert result.total == 53 and result.has_next and len(result.listings) == 10
    bip = {x.sku: x for x in result.listings}["7086"]
    assert (bip.price, bip.list_price, bip.list_price_kind, bip.in_stock, bip.brand, bip.stock_left) == (
        449900, 655900, "list", True, "Amazfit", 1)
    assert bip.gtins == ("6978099570152",)  # its SKU is the barcode
    assert bip.url == "https://etisalstore.com/en/amazfit-bip-max-smart-watch-207-inch-amoled-carbon-gray"
    assert not parse_products(fixture_json("etisal_wearables.json.gz"), 6).has_next  # 6 pages
    assert query(Slice("category", "1372", 1200, 2000), 3) == "page=3&category_id=1372&price_from=1200&price_to=2000"
    assert query(Slice("category", "s1382"), 1) == "page=1&sub_categories%5B%5D=1382"


def test_etisal_token_unknown_category_and_block(monkeypatch):
    from deals.models import Blocked, UnknownTarget
    from deals.sites.etisal_eg import EtisalEG
    site = EtisalEG()
    calls = []
    home = _response(200, {"data": [{"id": 1081}]})
    products = _response(200, {"data": {"total": 0, "total_products": 0, "products": []}})

    def get(url, **kw):
        calls.append((url, kw["headers"].get("authorization")))
        if "initialize" in url:
            r = _response(200, {})
            r.text = '{"token":"eyJabc.def.ghi"}'
            return r
        return home if "home/categories" in url else products

    monkeypatch.setattr(site._session, "get", get)
    assert site.fetch_page(Slice("category", "1081"), 1).total == 0
    assert site.fetch_page(Slice("category", "s1382"), 1).total == 0  # sub-categories can't be checked
    assert sum("initialize" in c[0] for c in calls) == 1  # one guest customer per session
    assert calls[-1][1] == "Bearer eyJabc.def.ghi"
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("category", "99999"), 1)
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("category", "laptops"), 1)
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("deals", "1081"), 1)
    monkeypatch.setattr(site._session, "get", lambda *a, **k: _response(403, {}))
    with pytest.raises(Blocked):
        site.fetch_page(Slice("category", "1081"), 1)


def test_orange_products():
    from deals.sites.orange_eg import parse_products
    result = parse_products(fixture_json("orange_tablets.json.gz"), 1)
    assert result.total == 17 and not result.has_next and len(result.listings) == 17
    by_sku = {x.sku: x for x in result.listings}
    pro = by_sku["289"]  # sold out ("غير متوفر" on its page) but still listed with its price
    assert (pro.title, pro.price, pro.list_price, pro.list_price_kind, pro.in_stock) == (
        "iPad Pro 11 Inch M5", 6199900, 7099900, "list", False)
    assert pro.url == "https://eshop.orange.eg/en/tablets/p/ipad-pro-11-inch-m5"
    assert (by_sku["507"].price, by_sku["507"].list_price, by_sku["507"].in_stock) == (5649900, None, True)
    assert sum(not x.in_stock for x in result.listings) == 3


def test_orange_empty_unknown_and_block(monkeypatch):
    from deals.models import Blocked, UnknownTarget
    from deals.sites.orange_eg import OrangeEG
    site = OrangeEG()
    calls = []

    def http(path, headers, data=None):
        calls.append(path)
        if path == "/Auth/token":
            return 200, {"Data": {"AccessToken": "tok"}}
        if "seName=nope" in path:
            return 400, {"Success": False, "Message": "Not Found => Category details", "Data": None}
        if "GetCategoryDetails" in path:
            return 200, {"Data": {"Id": 1033}}
        return 400, {"Success": False, "Message": "Not Found => Homepage Feature products", "Data": None}

    monkeypatch.setattr(site, "_http", http)
    empty = site.fetch_page(Slice("category", "smart-phones"), 1)
    assert (empty.total, empty.listings, empty.has_next) == (0, [], False)  # empty, not an alarm
    assert site.fetch_page(Slice("category", "smart-phones"), 2).total is None  # past the end
    assert sum("GetCategoryDetails" in c for c in calls) == 1 and sum(c == "/Auth/token" for c in calls) == 1
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("category", "nope"), 1)
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("search", "phone"), 1)
    monkeypatch.setattr(site, "_token", None)
    monkeypatch.setattr(site, "_http", lambda *a, **k: (200, {"Data": {}}))
    with pytest.raises(Blocked):  # no token: refused
        site.fetch_page(Slice("category", "x"), 1)


def test_lcwaikiki_listing():
    from deals.sites.lcwaikiki_eg import page_url, parse_listing
    result = parse_listing(fixture_text("lcwaikiki_shoes.html.gz"))
    assert result.total == 169 and result.has_next and len(result.listings) == 100
    by_sku = {x.sku: x for x in result.listings}
    # the sale price is in CampaignBadges, not DiscountedPriceValue (always 0)
    sale = by_sku["5451025"]
    assert (sale.title, sale.price, sale.list_price, sale.list_price_kind, sale.in_stock, sale.brand) == (
        "Suede Look Women's Sport Shoe", 79900, 99900, "list", True, "LCW STEPS")
    assert sale.url == "https://www.lcwaikiki.eg/en/suede-look-womens-sport-shoe-white-o-5451025"
    assert (by_sku["5206072"].price, by_sku["5206072"].list_price) == (249900, None)
    assert page_url(Slice("category", "women-clothing-t-10", 500, 900), 3) == (
        "https://www.lcwaikiki.eg/en/women-clothing-t-10?MinPrice=500&PageIndex=3")  # MaxPrice doesn't hold


def test_lcwaikiki_empty_result_is_not_its_52_filler_products():
    from deals.sites.lcwaikiki_eg import parse_listing
    result = parse_listing(fixture_text("lcwaikiki_empty.html.gz"))
    assert (result.total, result.listings, result.has_next) == (0, [], False)
    with pytest.raises(ValueError, match="layout changed"):
        parse_listing("<html>a challenge page</html>")


def test_lcwaikiki_unknown_and_block(monkeypatch):
    from deals.models import Blocked, UnknownTarget
    from deals.sites.lcwaikiki_eg import LcWaikikiEG
    site = LcWaikikiEG()
    answers = [_response(404, {}), _response(403, {})]
    monkeypatch.setattr(site._session, "get", lambda *a, **k: answers.pop(0))
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("category", "nope-t-1"), 1)
    with pytest.raises(Blocked):
        site.fetch_page(Slice("category", "women-clothing-t-10"), 1)
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("search", "dress"), 1)


def test_defacto_listing():
    from deals.sites.defacto_eg import page_url, parse_listing
    result = parse_listing(fixture_text("defacto_woman_discount.html.gz"))
    assert result.total == 5328 and result.has_next and len(result.listings) == 23  # 24 cards, one a promotion
    by_sku = {x.sku: x for x in result.listings}
    assert "9000213" not in by_sku  # "kargobedava", free shipping: no price
    cardigan = by_sku["3566797"]  # 999, "November - Net %20 Discount" 799.20
    assert (cardigan.title, cardigan.price, cardigan.list_price, cardigan.list_price_kind, cardigan.in_stock) == (
        "Slim Fit V-Neck Knitwear Cardigan", 79920, 99900, "list", True)
    assert cardigan.url == "https://www.defacto.com.eg/en-eg/fitted-v-yaka-triko-hirka-3566797"
    belts = parse_listing(fixture_text("defacto_man_belts.html.gz"))
    assert belts.total == 44
    assert (lambda x: (x.price, x.list_price, x.stock_left))({b.sku: b for b in belts.listings}["3559868"]) == (39900, None, 3)
    # the filter needs both ends, and page=1 next to it finds nothing
    assert page_url(Slice("category", "woman-dress", 500, None), 1) == (
        "https://www.defacto.com.eg/en-eg/woman-dress?filter=price:500-99999")
    assert page_url(Slice("category", "woman-dress", 500, 900), 2).endswith("?filter=price:500-900&page=2")
    assert page_url(Slice("category", "woman-dress"), 3).endswith("/woman-dress?page=3")


def test_defacto_paging_ends_and_refusals(monkeypatch):
    from deals.models import Blocked, UnknownTarget
    from deals.sites.defacto_eg import DeFactoEG, parse_listing
    # a page past the end: no cards, no count; an empty range: count 0
    past = parse_listing("<div class=catalog-products></div>")
    assert (past.listings, past.has_next, past.total) == ([], False, None)
    empty = parse_listing("<input id=totalCount type=hidden value=0><div class=catalog-products></div>")
    assert (empty.listings, empty.has_next, empty.total) == ([], False, 0)
    with pytest.raises(ValueError, match="layout changed"):
        parse_listing("<html>nothing</html>")
    site = DeFactoEG()
    answers = [_response(404, {}), _response(403, {})]
    monkeypatch.setattr(site._session, "get", lambda *a, **k: answers.pop(0))
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("category", "nope"), 1)
    with pytest.raises(Blocked):
        site.fetch_page(Slice("category", "woman-dress"), 1)
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("search", "dress"), 1)


def test_elaraby_products():
    from deals.sites.elaraby_eg import build_query, parse_products
    result = parse_products(fixture_json("elaraby_ducts.json.gz")["data"], 1)
    assert result.total == 3 and not result.has_next and len(result.listings) == 3
    duct = {x.sku: x for x in result.listings}["GB-X18BME.."]
    assert (duct.price, duct.list_price, duct.list_price_kind, duct.in_stock, duct.brand) == (
        5834000, 6077000, "list", True, "SHARP")
    assert duct.url == "https://www.elarabygroup.com/en/sharp-duct-air-conditioner-2-25-hp-cool-heat-inverter-digital-gb-x18bme"
    assert parse_products(fixture_json("elaraby_televisions.json.gz")["data"], 1).total == 33
    query = build_query(Slice("category", "155", 3000, 9000), 2)
    assert 'category_id: {eq: "155"}, price: {from: "3000", to: "9000"}' in query and "currentPage: 2" in query
    # from "0" finds nothing on this shop, even inside a category
    assert "price" not in build_query(Slice("category", "155", 0, None), 1).split("pageSize")[0]
    assert 'price: {to: "500"}' in build_query(Slice("category", "155", 0, 500), 1)


def test_elaraby_zero_price_is_no_price():
    from deals.sites.elaraby_eg import _parse_item
    zero = {"value": 0}
    item = _parse_item({"sku": "QHL097L", "name": "SEIKO Alarm Clock Digital QHL097L", "url_key": "x", "stock_status": "OUT_OF_STOCK",
                        "price_range": {"minimum_price": {"regular_price": zero, "final_price": zero}}})
    assert (item.price, item.list_price, item.in_stock, item.brand) == (None, None, False, "SEIKO")


def test_elaraby_unknown_category_and_refusals(monkeypatch):
    from deals.models import Blocked, UnknownTarget
    from deals.sites.elaraby_eg import ElarabyEG
    site = ElarabyEG()
    answers = [_response(200, {"data": {"products": {"total_count": 0, "items": []}, "category": []}}),
               _response(403, {}), _response(200, {"errors": [{"message": "Cannot query field"}]}), _response(503, {}, "text/html")]
    monkeypatch.setattr(site._session, "post", lambda *a, **k: answers.pop(0))
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("category", "99999"), 1)
    with pytest.raises(Blocked):
        site.fetch_page(Slice("category", "155"), 1)
    with pytest.raises(ValueError, match="API changed"):
        site.fetch_page(Slice("category", "155"), 1)
    with pytest.raises(RuntimeError):  # its Varnish's 503: retried, not a block
        site.fetch_page(Slice("category", "155"), 1)
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice("deals", "155"), 1)


class _Answer:
    """A shop's answer, for the adapters' refusal and paging branches."""
    def __init__(self, status=200, text="", data=None, ctype="application/json", headers=None):
        self.status_code, self.text, self._data = status, text, data
        self.headers = {"content-type": ctype, **(headers or {})}
        self.url = "https://shop.example/page"

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    def __init__(self, *answers):
        self.answers, self.urls = list(answers), []

    def get(self, url, **_):
        self.urls.append(url)
        return self.answers.pop(0)


def test_miami_prices_are_whole_pounds():
    from deals.sites.miami_eg import parse_products
    result = parse_products(fixture_json("miami_tablets.json.gz"), 84, 1, 1)
    by = {x.sku: x for x in result.listings}
    lenovo = by["241171"]
    assert lenovo.price == 5_300_000 and lenovo.list_price is None and lenovo.in_stock and lenovo.brand == "Lenovo"
    assert lenovo.url.startswith("https://miamicenters.com/product/lenovo-yoga-tab-plus")
    assert by["223046"].price == 1_648_100 and not by["223046"].in_stock
    assert result.total == 84 and result.has_next is False


def test_miami_scales_by_the_unit_the_answer_states_and_skips_preorders():
    from deals.sites.miami_eg import parse_products
    item = fixture_json("miami_tablets.json.gz")[0]
    piastres = {**item, "id": 1, "prices": {**item["prices"], "currency_minor_unit": 2, "price": "12345", "regular_price": "20000"}}
    preorder = {**item, "id": 2, "name": "Pre Order &#8211; Samsung Galaxy Z Fold8 Ultra"}
    free = {**item, "id": 3, "name": "Miami Product Protection", "prices": {**item["prices"], "price": "0", "regular_price": "0"}}
    listings = {x.sku: x for x in parse_products([piastres, preorder, free], 3, 1, 1).listings}
    assert set(listings) == {"1", "3"}  # no pre-order: its page says "Estimated preorder price"
    assert (listings["1"].price, listings["1"].list_price, listings["1"].list_price_kind) == (12345, 20000, "list")
    assert listings["3"].price is None and not listings["3"].in_stock


def test_miami_url_and_refusals():
    from deals.models import Blocked, Slice, UnknownTarget
    from deals.sites.miami_eg import MiamiEG, products_url
    url = products_url(Slice.from_key("category:52|price:2500-3000"), 2)
    assert "category=52" in url and "min_price=2500&" in url and "max_price=3000&" in url and "page=2" in url  # pounds, not piastres
    assert "orderby=id" in url and "stock_status" in url
    site = MiamiEG()
    site._categories = [{"id": 52, "slug": "mobiles-tablets", "parent": 0}]
    with pytest.raises(UnknownTarget):  # the API itself answers an empty 200
        site.fetch_page(Slice.from_key("category:99999"), 1)
    site._session = _Session(_Answer(403, ctype="text/html"))
    with pytest.raises(Blocked):
        site.fetch_page(Slice.from_key("category:52"), 1)


def test_sigma_listing():
    from deals.sites.sigma_eg import parse_listing
    result = parse_listing(fixture_text("sigma_laptops.html.gz"))
    by = {x.sku: x for x in result.listings}
    assert len(by) == 16 and result.total == 587 and result.has_next  # 393 sold out + 194 in stock
    lenovo = by["a318b61b-0c16-43ff-b271-c8913d5a8d8c"]
    assert (lenovo.price, lenovo.list_price, lenovo.list_price_kind, lenovo.brand) == (4_599_900, 4_700_000, "list", "Lenovo")
    assert lenovo.url.startswith("https://www.sigma-computer.com/en/item?id=lenovo-ideapad-slim-3")
    last = parse_listing(fixture_text("sigma_laptops.html.gz"), 37)
    assert not last.has_next and last.total is None
    past = parse_listing(fixture_text("sigma_past_end.html.gz"), 500)
    assert past.listings == [] and not past.has_next


def test_sigma_always_sorts_and_checks_the_category():
    from deals.models import Blocked, Slice, UnknownTarget
    from deals.sites.sigma_eg import SigmaEG, listing_url
    assert "sortBy=createdAt" in listing_url("x", 1) and "sortBy=createdAt&page=2" in listing_url("x", 2)  # 15% missed without it
    site = SigmaEG()
    site._categories = [{"id": "known"}]
    with pytest.raises(UnknownTarget):  # any id gives an empty 200
        site.fetch_page(Slice.from_key("category:nope"), 1)
    site._session = _Session(_Answer(403, ctype="text/html"))
    with pytest.raises(Blocked):
        site.fetch_page(Slice.from_key("category:known"), 1)
    with pytest.raises(ValueError, match="layout changed"):
        from deals.sites.sigma_eg import parse_listing
        parse_listing("<html>no next data</html>")


def test_elnekhely_listing():
    from deals.sites.elnekhely_eg import parse_listing
    result = parse_listing(fixture_text("elnekhely_gpu.html.gz"))
    by = {x.sku: x for x in result.listings}
    assert len(by) == 100 and result.total == 118 and result.has_next
    aorus = by["2664"]
    assert aorus.price == 5_999_900 and aorus.list_price is None and aorus.in_stock and aorus.brand == "Gigabyte"
    assert aorus.url.startswith("https://www.elnekhelytechnology.com/graphics-card/aorus-") and "limit" not in aorus.url
    sale = by["2973"]
    assert (sale.price, sale.list_price, sale.list_price_kind) == (4_899_900, 5_299_900, "list")
    assert by["3379"].price is None and not by["3379"].in_stock
    assert not parse_listing(fixture_text("elnekhely_gpu.html.gz"), 2).has_next


def test_elnekhely_refusals():
    from deals.models import Blocked, Slice, UnknownTarget
    from deals.sites.elnekhely_eg import ElnekhelyEG, parse_listing
    site = ElnekhelyEG()
    site._session = _Session(_Answer(404, ctype="text/html"), _Answer(403, ctype="text/html"))
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice.from_key("category:nope"), 1)
    with pytest.raises(Blocked):
        site.fetch_page(Slice.from_key("category:ram"), 1)
    with pytest.raises(ValueError, match="layout changed"):
        parse_listing("<html></html>")


def test_shopify_shops_fold_what_was_counted():
    from deals.sites.azzam_eg import parse_products as azzam
    from deals.sites.gcshop_eg import parse_products as gc
    from deals.sites.wayup_eg import parse_products as wayup
    nike = {x.sku: x for x in wayup(fixture_json("wayup_sale.json.gz")).listings}["8871750303982"]  # sizes XS to XL, one listing
    assert (nike.price, nike.list_price, nike.list_price_kind, nike.brand) == (160_000, 319_900, "list", "Nike") and nike.in_stock
    assert nike.url == "https://wayupsports.com/products/r-hj2855-370"
    casio = {x.sku: x for x in gc(fixture_json("gcshop_products.json.gz")).listings}["8241768202310"]
    assert (casio.price, casio.list_price, casio.in_stock) == (2_064_000, 2_294_000, False)  # sold out, still listed
    bentley = {x.sku: x for x in azzam(fixture_json("azzam_products.json.gz")).listings}["7608041668666"]
    assert (bentley.price, bentley.list_price, bentley.brand) == (2_799_500, None, "Bentley")
    colours = {"id": 1, "handle": "w", "title": "Watch", "vendor": "X", "options": [{"name": "Color"}], "variants": [
        {"id": 2, "option1": "Black", "price": "100.00", "available": True}, {"id": 3, "option1": "Gold", "price": "200.00", "available": True}]}
    assert len(azzam({"products": [colours]}).listings) == 2  # priced differently on 4 of 10: never folded here
    assert len(wayup({"products": [colours]}).listings) == 1


def test_shopify_stops_at_its_limit_and_always_gives_the_planner_a_number():
    from deals.models import Slice, UnknownTarget
    from deals.sites.shopify import MAX_PAGES
    from deals.sites.wayup_eg import WayUpEG
    full = {"products": [{"id": i, "handle": f"p{i}", "title": "T", "variants": [{"id": i, "price": "900.00", "available": True}]}
                         for i in range(250)]}
    site = WayUpEG()
    target = Slice.from_key("category:all-sale")
    site._session = _Session(_Answer(data={"collection": {"products_count": 12_434}}), _Answer(data=full))
    first = site.fetch_page(target, 1)
    assert first.total == 12_434 and first.has_next and "/collections/all-sale/products.json" in site._session.urls[1]
    site._session = _Session(_Answer(data=full))
    assert site.fetch_page(target, MAX_PAGES).has_next is False  # page 101 is an HTTP 400
    site._session = _Session()
    assert site.fetch_page(target, MAX_PAGES + 1).listings == [] and site._session.urls == []
    # a count of 0 with products: the planner must not drop the root as empty
    site._session = _Session(_Answer(data={"collection": {"products_count": 0}}), _Answer(data=full))
    assert site.fetch_page(target, 1).total == 250
    site._session = _Session(_Answer(data={"collection": {"products_count": 8_500}}), _Answer(data=full))
    site.fetch_page(Slice.from_key("category:all"), 1)
    assert site._session.urls[1].startswith("https://wayupsports.com/products.json")  # whole shop, stable order
    site._session = _Session(_Answer(404, ctype="text/html"))
    with pytest.raises(UnknownTarget):
        site.fetch_page(Slice.from_key("category:nope"), 1)
