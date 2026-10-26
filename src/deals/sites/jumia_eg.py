"""jumia.com.eg listing pages.

Jumia sits behind Cloudflare. A request with Chrome's or Firefox's TLS fingerprint
gets the "Just a moment..." challenge; Safari's passes from a home connection (cloud
IPs are refused whatever the fingerprint, like noon). Every catalog page embeds its
products as JSON (`window.__STORE__`): 40 per page, at most 50 pages (2,000 products
per query), so big categories are split into price ranges.

Two traps: a price range with no products, or a page past the last one, *redirects
to the parent category* (a different, bigger listing), and a decimal price filter
does the same. The served URL is therefore checked on every page.
"""

import json
import re
from urllib.parse import urlencode, urlparse

from curl_cffi import requests
from selectolax.lexbor import LexborHTMLParser

from deals.models import Blocked, Category, Listing, PageResult, Slice, UnknownTarget
from deals.sites.base import parse_money

BASE = "https://www.jumia.com.eg"
# The price filter needs both ends; an open-ended slice gets this ceiling (EGP).
OPEN_MAX = 10_000_000
IMPERSONATE = "safari"  # chrome/firefox fingerprints get the Cloudflare challenge
CHALLENGE_MARKERS = ("_cf_chl_opt", "challenge-platform", "<title>Just a moment")
PRODUCTS_FOUND = re.compile(r"([\d,]+) products? found")
STOCK_LEFT = re.compile(r"(\d+) items? left")
INDENT = re.compile(r"-ph(x+)l\b")


class JumiaEG:
    name = "jumia_eg"
    max_slice_total = 2000  # 50 pages x 40 products
    # Many products sit on a category itself and in none of its children (Air
    # Conditioners: 135 of 1,274), so a sub-category split would lose them; price
    # ranges cover everything.
    split_by_subcategory = False

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        params: dict[str, str | int] = {}
        if slice.kind == "search":
            path = "/catalog/"
            params["q"] = slice.value
        else:  # category slug, e.g. televisions
            path = f"/{slice.value.strip('/')}/"
        if slice.has_price:
            high = OPEN_MAX if slice.price_max is None else slice.price_max
            params["price"] = f"{slice.price_min or 0}-{high}"
        if page > 1:
            params["page"] = page
        url = BASE + path + (f"?{urlencode(params)}" if params else "")

        html = self._get(url)
        if html is None:
            # A fresh session (new Cloudflare cookies) once, then give up: the coordinator
            # pauses this IP on Jumia for a while.
            self._session = requests.Session(impersonate=IMPERSONATE)
            html = self._get(url)
            if html is None:
                raise Blocked(f"{self.name}: Cloudflare challenge")
        current = slice.value.strip("/") if slice.kind == "category" else None
        return parse_catalog_page(html, path, page, current)

    def _get(self, url: str) -> str | None:
        """The page, or None for a Cloudflare challenge."""
        r = self._session.get(url, timeout=30)
        if r.status_code in (403, 429, 503) or any(m in r.text[:20000] for m in CHALLENGE_MARKERS):
            return None
        if r.status_code == 404:
            raise UnknownTarget(f"{self.name}: no such page {urlparse(url).path} (HTTP 404)")
        r.raise_for_status()
        return r.text


def parse_catalog_page(html: str, requested_path: str, page: int = 1,
                       current_category: str | None = None) -> PageResult:
    store = _store(html)
    tree = LexborHTMLParser(html)
    served_path = urlparse(store.get("url") or "").path
    if served_path != requested_path:
        # Jumia answers an empty price range or a page past the end with its parent
        # category. That is an empty slice when the page we got lists the requested
        # category among its sub-categories; anything else is a wrong or renamed slug.
        if page > 1 or (current_category and current_category in {c.id for c in _category_links(tree)}):
            return PageResult(listings=[], has_next=False, total=0)
        raise UnknownTarget(f"jumia_eg: {requested_path} redirected to {served_path}")

    listings: dict[str, Listing] = {}
    for product in store.get("products") or []:
        if product.get("sku") and not product.get("isSponsored") and product["sku"] not in listings:
            listings[product["sku"]] = _parse_product(product)
    count = PRODUCTS_FOUND.search(html)
    total = int(count.group(1).replace(",", "")) if count else (0 if not listings else None)
    return PageResult(
        listings=list(listings.values()),
        has_next=tree.css_first('a.pg[aria-label="Next Page"]') is not None,
        total=total,
        subcategories=_child_categories(tree, current_category) if current_category else [],
    )


def _store(html: str) -> dict:
    """The JSON the page renders from: window.__STORE__ = {...};"""
    start = html.find("window.__STORE__")
    if start < 0:
        raise ValueError("jumia_eg: no __STORE__ JSON on the page (layout changed?)")
    start = html.index("{", start)
    end = html.index("</script>", start)
    return json.loads(html[start:end].rstrip().rstrip(";"))


def _parse_product(p: dict) -> Listing:
    prices = p.get("prices") or {}
    raw = prices.get("rawPrice")
    price = round(float(raw) * 100) if raw not in (None, "") else None
    # The crossed-out price is set by the seller (88% of cards show one), so it is an
    # RRP-like claim, never proof of an earlier selling price.
    old = parse_money(prices.get("oldPrice"))
    list_price = old if old and price and old > price else None

    # "N items left" sits on the variant (simple). 45% of cards say 3 or fewer.
    stock_left = None
    selected = p.get("selectedVariation")
    simples = sorted(p.get("simples") or [], key=lambda s: s.get("sku") != selected)
    for text in [(p.get("stock") or {}).get("text")] + [s.get("stockText") for s in simples]:
        if text and (m := STOCK_LEFT.search(text)):
            stock_left = int(m.group(1))
            break

    rating = p.get("rating") or {}
    reviews = rating.get("totalRatings")
    return Listing(
        site=JumiaEG.name,
        sku=p["sku"],
        title=_title(p),
        url=BASE + p.get("url", ""),
        price=price,
        list_price=list_price,
        currency="EGP",
        in_stock=bool(p.get("isBuyable", True)) and price is not None,
        list_price_kind="list" if list_price else None,
        brand=p.get("brand") or None,
        stock_left=stock_left,
        rating=rating.get("average") if reviews else None,
        reviews=reviews,
    )


def _title(p: dict) -> str:
    """The name with the brand in front, without repeating it ("Sony Sony PlayStation 5")."""
    name, brand = p.get("name") or "", p.get("brand") or ""
    return name if not brand or brand.lower() in name.lower() else f"{brand} {name}"


def _category_links(tree: LexborHTMLParser) -> list[Category]:
    return [c for c, _ in _sidebar(tree)]


def _sidebar(tree: LexborHTMLParser) -> list[tuple[Category, int]]:
    """The 'Category' filter box: (category, indent level) in page order.

    It lists the ancestors, the current category (bold, class -m) and its children
    (indented one step more), or for a leaf, its siblings."""
    for article in tree.css("article"):
        heading = article.css_first("h2")
        if heading is None or heading.text(strip=True).lower() != "category":
            continue
        out = []
        for a in article.css("a"):
            slug = urlparse(a.attributes.get("href") or "").path.strip("/")
            indent = INDENT.search(a.attributes.get("class") or "")
            if slug:
                out.append((Category(slug, a.text(strip=True)), len(indent.group(1)) if indent else 0))
        return out
    return []


def _child_categories(tree: LexborHTMLParser, current: str) -> list[Category]:
    links = _sidebar(tree)
    at = next((i for i, (c, _) in enumerate(links) if c.id == current), None)
    if at is None:
        return []
    level = links[at][1]
    children = []
    for category, indent in links[at + 1:]:
        if indent <= level:
            break
        children.append(category)
    return children
