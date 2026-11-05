"""elarabygroup.com (El Araby Group) catalog, through the shop's own GraphQL API.

The maker of Tornado and the Egyptian agent of Toshiba, Sharp, Seiko and Alba, on Magento 2. `/graphql` (header
`Store: en`) answers from anywhere, the droplet too, though its HTML pages can answer 404 to anything but a browser.
An exact `total_count`, a price filter on the current price, inclusive at both ends, any page size.

**`price: {from: "0"}` finds nothing**, even inside a category, so a zero lower bound is left out. A category's
`product_count` is stale (Watches says 3,780 and holds 1,057): only `total_count` counts. Sold-out products stay listed
with their price (two thirds of the catalogue), and every product is simple: one SKU, one price. 42 sold-out products
show a price of 0, which means none.

`manufacturer` is the country of origin ("Egypt"), not the brand, so the brand is found in the title. Nearly nothing
has a crossed-out price (4 of 2,229). Categories are keyed by Magento's id (`category:155` = TVs). docs/ELARABY.md.
"""

import json
import re

from curl_cffi import requests

from deals.models import Blocked, Category, Listing, PageResult, Slice, UnknownTarget

GRAPHQL = "https://www.elarabygroup.com/graphql"
PRODUCT_URL = "https://www.elarabygroup.com/en/{key}"
PAGE_SIZE = 100
IMPERSONATE = "chrome"
# As the shop writes them in its titles. The first one in a title is the brand: a remote "TORNADO LED TV" is Tornado's.
BRANDS = ("TORNADO", "TOSHIBA", "SHARP", "HOOVER", "LA GERMANIA", "SONY", "CANDY", "HITACHI", "SEIKO", "ALBA", "HELLER",
          "DYNABOOK", "HAMA", "HIONE+", "BELKIN", "TCL", "KAJITO", "KARCHER", "BEKO", "FAST FORWARD", "NEC", "SAMSUNG", "LG")
_BRAND = re.compile(r"(?<![A-Z0-9])(" + "|".join(re.escape(b) for b in BRANDS) + r")(?![A-Z0-9])", re.I)
PRODUCT_FIELDS = """
  total_count
  page_info { total_pages }
  items {
    sku name url_key stock_status only_x_left_in_stock rating_summary review_count
    price_range { minimum_price { regular_price { value } final_price { value } } }
  }"""
CATEGORY_FIELDS = "id name children { id name product_count }"


class ElarabyEG:
    name = "elaraby_eg"
    max_slice_total = 2000  # no cap on the API; keeps a slice to 20 requests

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind == "deals":
            raise UnknownTarget(f"{self.name}: its offers are ordinary categories; use category:<id>")
        data = self._post(build_query(slice, page))
        if slice.kind == "category" and not data["category"]:
            raise UnknownTarget(f"{self.name}: no category with id {slice.value}")
        return parse_products(data, page)

    def _post(self, query: str) -> dict:
        r = self._session.post(GRAPHQL, json={"query": query}, timeout=60,
                               headers={"Store": "en", "Content-Type": "application/json"})
        if r.status_code in (403, 429) or (r.status_code == 200 and "json" not in r.headers.get("content-type", "")):
            raise Blocked(f"{self.name}: HTTP {r.status_code} from the API")
        r.raise_for_status()  # its Varnish answers 503 for its own troubles: retried, not a block
        body = r.json()
        if body.get("errors") or "data" not in body:
            raise ValueError(f"{self.name}: GraphQL error {json.dumps(body.get('errors'))[:300]} (API changed?)")
        return body["data"]


def build_query(slice: Slice, page: int) -> str:
    """The GraphQL query for one page: the products, plus the category's children on category slices."""
    if slice.kind == "search":
        selector = f"search: {json.dumps(slice.value)}, filter: {{{_price_filter(slice).lstrip(', ')}}}"
        category_query = ""
    else:
        if not slice.value.isdigit():
            raise UnknownTarget(f"elaraby_eg: category ids are numbers (e.g. category:155), got {slice.value!r}")
        selector = f"filter: {{category_id: {{eq: {json.dumps(slice.value)}}}{_price_filter(slice)}}}"
        category_query = f"category: categoryList(filters: {{ids: {{eq: {json.dumps(slice.value)}}}}}) {{{CATEGORY_FIELDS}}}"
    return (f"{{ products({selector}, pageSize: {PAGE_SIZE}, currentPage: {page}, "
            f"sort: {{name: ASC}}) {{{PRODUCT_FIELDS}}} {category_query} }}")


def _price_filter(slice: Slice) -> str:
    bounds = []
    if slice.price_min:  # from "0" would find nothing
        bounds.append(f"from: {json.dumps(str(slice.price_min))}")
    if slice.price_max is not None:
        bounds.append(f"to: {json.dumps(str(slice.price_max))}")
    return f", price: {{{', '.join(bounds)}}}" if bounds else ""


def parse_products(data: dict, page: int) -> PageResult:
    products = data["products"]
    listings: dict[str, Listing] = {}
    for item in products.get("items") or []:
        if item.get("sku") and item["sku"] not in listings:
            listings[item["sku"]] = _parse_item(item)
    category = data.get("category") or []
    return PageResult(
        listings=list(listings.values()),
        has_next=page < (products.get("page_info") or {}).get("total_pages", 0),
        total=products.get("total_count"),
        subcategories=[Category(str(c["id"]), f"{c['name'].strip()} ({c.get('product_count', 0)})")
                       for c in category[0].get("children") or []] if category else [],
    )


def _parse_item(p: dict) -> Listing:
    prices = (p.get("price_range") or {}).get("minimum_price") or {}
    final = _minor((prices.get("final_price") or {}).get("value"))
    regular = _minor((prices.get("regular_price") or {}).get("value"))
    list_price = regular if regular and final and regular > final else None
    title = (p.get("name") or "").strip()
    brand = _BRAND.search(title)
    reviews = p.get("review_count") or 0
    rating = p.get("rating_summary")  # 0-100
    return Listing(
        site=ElarabyEG.name,
        sku=p["sku"],
        title=title,
        url=PRODUCT_URL.format(key=p.get("url_key", "")),
        price=final,
        list_price=list_price,
        currency="EGP",
        in_stock=p.get("stock_status") == "IN_STOCK" and final is not None,
        list_price_kind="list" if list_price else None,
        brand=brand.group(1).upper() if brand else None,
        stock_left=p.get("only_x_left_in_stock") or None,
        rating=round(rating / 20, 1) if reviews and rating else None,
        reviews=reviews,
    )


def _minor(value) -> int | None:
    return round(float(value) * 100) if value not in (None, "", 0) else None  # 42 products show a price of 0: none yet
