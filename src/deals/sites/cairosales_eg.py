"""cairosales.com (Cairo Sales Stores) listing pages.

A PrestaShop 1.6 shop behind Cloudflare: plain `curl` gets "Attention Required!",
Chrome's TLS fingerprint (curl_cffi) passes from a home IP; datacenter IPs are
refused whatever the fingerprint (like noon and Jumia). Category pages are
`/en/<slug>/` with `?n=60` (products per page) and `?p=N`; the "Amazzing filter"
module takes `price=<min>-<max>` in the URL. Every page says exactly how many
products match ("Showing 1 - 60 of 820 items"), and there is no page cap.

Out-of-stock products stay listed but hide their price (no price, a "View" button
instead of "Add to cart"): they come back as price None, in_stock False. An unknown
slug redirects to the homepage (HTTP 200), so the served URL is checked.
"""

import re
from urllib.parse import urlencode, urlparse

from curl_cffi import requests
from selectolax.lexbor import LexborHTMLParser

from deals.models import Blocked, Category, Listing, PageResult, Slice, UnknownTarget
from deals.sites.base import parse_money

BASE = "https://cairosales.com"
PER_PAGE = 60  # the biggest "n" the shop offers (12, 24, 60)
OPEN_MAX = 10_000_000  # the price filter needs both ends (EGP)
IMPERSONATE = "chrome"
CHALLENGE_MARKERS = ("_cf_chl_opt", "challenge-platform", "<title>Just a moment", "Attention Required!")
SHOWING = re.compile(r"Showing \d+ - \d+ of ([\d,]+) items")
THERE_ARE = re.compile(r"There (?:is|are) ([\d,]+) products?")


class CairoSalesEG:
    name = "cairosales_eg"
    max_slice_total = 3000  # no page cap; 50 pages of 60 keeps one slice crawlable in a few minutes

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        path, url = page_url(slice, page)
        served = self._get(url)
        if served is None:
            # One fresh session (new Cloudflare cookies), then give up: the coordinator
            # pauses this IP on the shop for a while.
            self._session = requests.Session(impersonate=IMPERSONATE)
            served = self._get(url)
            if served is None:
                raise Blocked(f"{self.name}: Cloudflare challenge")
        html, served_path = served
        if served_path.rstrip("/") != path.rstrip("/"):
            # An unknown slug lands on the homepage (HTTP 200); a renamed one elsewhere.
            raise UnknownTarget(f"{self.name}: {path} redirected to {served_path}")
        current = slice.value.strip("/") if slice.kind == "category" else None
        return parse_listing_page(html, current)

    def _get(self, url: str) -> tuple[str, str] | None:
        """(page, served path), or None for a Cloudflare challenge."""
        r = self._session.get(url, timeout=30, allow_redirects=True)
        if r.status_code in (403, 429, 503) or any(m in r.text[:20000] for m in CHALLENGE_MARKERS):
            return None
        if r.status_code == 404:
            raise UnknownTarget(f"{self.name}: no such page {urlparse(url).path} (HTTP 404)")
        r.raise_for_status()
        return r.text, urlparse(str(r.url)).path


def page_url(slice: Slice, page: int) -> tuple[str, str]:
    """(path we expect to be served, full URL) of one listing page."""
    # Sorted by price so that paging is stable: the default order ("sales") is
    # shuffled with a daily random seed, which would move products between pages.
    params: dict[str, str | int] = {"n": PER_PAGE, "orderby": "price", "orderway": "asc"}
    if slice.kind == "search":
        path = "/en/search"
        params = {"controller": "search", "search_query": slice.value, **params}
    else:  # category slug, e.g. tvs-screens or small-appliances/fryers
        path = f"/en/{slice.value.strip('/')}/"
    if slice.has_price:
        high = OPEN_MAX if slice.price_max is None else slice.price_max
        params["price"] = f"{slice.price_min or 0}-{high}"
    if page > 1:
        params["p"] = page
    return path, f"{BASE}{path}?{urlencode(params)}"


def parse_listing_page(html: str, current_category: str | None = None) -> PageResult:
    tree = LexborHTMLParser(html)
    grid = tree.css_first("ul.product_list")
    listings: dict[str, Listing] = {}
    for card in grid.css("li.ajax_block_product") if grid else []:
        listing = _parse_card(card)
        if listing and listing.sku not in listings:
            listings[listing.sku] = listing
    count = SHOWING.search(html) or THERE_ARE.search(html)
    total = int(count.group(1).replace(",", "")) if count else (0 if not listings else None)
    # The shop moves out-of-stock products (no price) behind every in-stock one, and the
    # dead back-catalogue is huge (55 of 60 phones on a page): once a whole page is
    # unpriced, every later page is too, so the crawl stops here.
    all_unpriced = bool(listings) and all(x.price is None for x in listings.values())
    return PageResult(
        listings=list(listings.values()),
        has_next=tree.css_first('a[rel="next"]') is not None and not all_unpriced,
        total=total,
        subcategories=_subcategories(tree) if current_category else [],
    )


def _parse_card(card) -> Listing | None:
    sku = card.attributes.get("data-id-product")
    name = card.css_first("a.product-name")
    if not sku or name is None:
        return None
    price_el = card.css_first(".content_price .price.product-price")
    # Out-of-stock products hide their price (product page: productShowPrice=false,
    # quantityAvailable=0) and show a "View" button instead of "Add to cart".
    price = parse_money(price_el.text()) if price_el else None
    old_el = card.css_first(".content_price .old-price")
    old = parse_money(old_el.text()) if old_el else None
    list_price = old if old and price and old > price else None
    availability = " ".join(a.text(strip=True) for a in card.css(".availability")).lower()
    in_stock = price is not None and "out of stock" not in availability
    logo = card.css_first(".m-logo img")
    brand = (logo.attributes.get("title") or "").strip() if logo else ""
    return Listing(
        site=CairoSalesEG.name,
        sku=sku,
        title=name.text(strip=True),
        url=name.attributes.get("href") or "",
        price=price,
        list_price=list_price,
        currency="EGP",
        in_stock=in_stock,
        # The crossed-out price is the shop's own "regular" price before its discount:
        # a claim, not proof of an earlier selling price.
        list_price_kind="list" if list_price else None,
        brand=brand or None,
    )


def _subcategories(tree: LexborHTMLParser) -> list[Category]:
    """The 'Subcategories' block of a category page (children only)."""
    out: dict[str, Category] = {}
    for a in tree.css("#subcategories a.subcategory-name"):
        slug = urlparse(a.attributes.get("href") or "").path.removeprefix("/en/").strip("/")
        if slug and slug not in out:
            out[slug] = Category(slug, a.text(strip=True))
    return list(out.values())
