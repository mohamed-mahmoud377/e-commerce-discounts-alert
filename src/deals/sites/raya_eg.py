"""rayashop.com (Raya Shop) catalog, through the shop's own GraphQL API.

Raya runs Magento 2 behind a Nuxt storefront; the storefront reads its products from a GraphQL
endpoint on a Fastly host (`api-rayashop.global.ssl.fastly.net`, header `Store: en`) that answers
from anywhere, the droplet too. An exact `total_count` (it stops at 9,999, which only the whole
catalogue reaches), a price filter on the current price that is inclusive at both ends, and any
page size. Only products in stock are returned, so one that sells out disappears from its category.

It is a marketplace: most products belong to third-party sellers (`marketplace_seller` is a number,
the API gives no name), each with its own SKU, so one key has one price. The crossed-out price is
the shop's claim (kind `list`): 20% off exactly, regular = price x 1.25, is the commonest "discount".
"Pre Booking" products cost a 10,000 EGP deposit, not their price, so they are skipped.

Categories are keyed by Magento's numeric id (`category:643` = Smartphones). Introspection is off
and a whole-tree query takes 15 s: ask for one category's children at a time. docs/RAYA.md.
"""

import json

from curl_cffi import requests

from deals.models import Blocked, Category, Listing, PageResult, Slice, UnknownTarget

GRAPHQL = "https://api-rayashop.global.ssl.fastly.net/graphql"
PRODUCT_URL = "https://www.rayashop.com/en/{key}"
PAGE_SIZE = 100
IMPERSONATE = "chrome"
PRODUCT_FIELDS = """
  total_count
  page_info { current_page total_pages }
  items {
    sku name url_key stock_status only_x_left_in_stock rating_summary review_count
    brand { name }
    price_range { minimum_price { regular_price { value } final_price { value } } }
  }"""
CATEGORY_FIELDS = "id name children { id name product_count }"


class RayaEG:
    name = "raya_eg"
    max_slice_total = 2000  # no cap on the API; keeps a slice to 20 requests

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind == "deals":
            raise UnknownTarget(f"{self.name}: its offer categories are ordinary categories; use category:<id>")
        data = self._post(build_query(slice, page))
        if slice.kind == "category" and not data["category"]:
            raise UnknownTarget(f"{self.name}: no category with id {slice.value}")
        return parse_products(data, page)

    def _post(self, query: str) -> dict:
        r = self._session.post(GRAPHQL, json={"query": query}, timeout=60,
                               headers={"Store": "en", "Content-Type": "application/json",
                                        "Origin": "https://www.rayashop.com"})
        if r.status_code in (403, 429) or (r.status_code == 200 and "json" not in r.headers.get("content-type", "")):
            raise Blocked(f"{self.name}: HTTP {r.status_code} from the API")
        r.raise_for_status()
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
            raise UnknownTarget(f"raya_eg: category ids are numbers (e.g. category:643), got {slice.value!r}")
        selector = f"filter: {{category_id: {{eq: {json.dumps(slice.value)}}}{_price_filter(slice)}}}"
        category_query = f"category: categoryList(filters: {{ids: {{eq: {json.dumps(slice.value)}}}}}) {{{CATEGORY_FIELDS}}}"
    return (f"{{ products({selector}, pageSize: {PAGE_SIZE}, currentPage: {page}, "
            f"sort: {{price: ASC}}) {{{PRODUCT_FIELDS}}} {category_query} }}")


def _price_filter(slice: Slice) -> str:
    if not slice.has_price:
        return ""
    bounds = []
    if slice.price_min is not None:
        bounds.append(f"from: {json.dumps(str(slice.price_min))}")
    if slice.price_max is not None:
        bounds.append(f"to: {json.dumps(str(slice.price_max))}")
    return f", price: {{{', '.join(bounds)}}}"


def parse_products(data: dict, page: int) -> PageResult:
    products = data["products"]
    listings: dict[str, Listing] = {}
    for item in products.get("items") or []:
        if item.get("sku") and item["sku"] not in listings and not is_deposit(item):
            listings[item["sku"]] = _parse_item(item)
    category = data.get("category") or []
    return PageResult(
        listings=list(listings.values()),
        has_next=page < (products.get("page_info") or {}).get("total_pages", 0),
        total=products.get("total_count"),
        subcategories=[Category(str(c["id"]), f"{c['name'].strip()} ({c.get('product_count', 0)})")
                       for c in category[0].get("children") or []] if category else [],
    )


def is_deposit(item: dict) -> bool:
    """A pre-order's deposit ("Pre Booking - Samsung Galaxy Z Fold8 Ultra" at 10,000 EGP): not a price."""
    return (item.get("name") or "").lower().startswith("pre booking")


def _parse_item(p: dict) -> Listing:
    prices = (p.get("price_range") or {}).get("minimum_price") or {}
    final = _minor((prices.get("final_price") or {}).get("value"))
    regular = _minor((prices.get("regular_price") or {}).get("value"))
    list_price = regular if regular and final and regular > final else None
    reviews = p.get("review_count") or 0
    rating = p.get("rating_summary")  # 0-100
    return Listing(
        site=RayaEG.name,
        sku=p["sku"],
        title=(p.get("name") or "").strip(),
        url=PRODUCT_URL.format(key=p.get("url_key", "")),
        price=final,
        list_price=list_price,
        currency="EGP",
        in_stock=p.get("stock_status", "IN_STOCK") == "IN_STOCK" and final is not None,
        list_price_kind="list" if list_price else None,
        brand=((p.get("brand") or {}).get("name") or "").strip() or None,
        stock_left=p.get("only_x_left_in_stock"),
        rating=round(rating / 20, 1) if reviews and rating else None,
        reviews=reviews,
    )


def _minor(value) -> int | None:
    return round(float(value) * 100) if value not in (None, "") else None
