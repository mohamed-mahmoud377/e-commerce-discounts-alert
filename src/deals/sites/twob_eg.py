"""2b.com.eg (2B Egypt) catalog, through the shop's own GraphQL API.

2B runs Magento 2, whose public GraphQL endpoint (`/graphql`, header `Store: en`)
answers the same product data the pages render from, as JSON: an exact
`total_count`, a price filter that is inclusive at both ends, any page size, and a
category tree with product counts. Only in-stock products are returned (the shop
hides the rest), so a product that sells out simply disappears from its category.

Cloudflare sits in front: Chrome's TLS fingerprint passes from a home IP, every
datacenter IP gets the challenge (like noon and Jumia). Categories are keyed by
Magento's numeric id (`category:131` = TVs) because the readable `url_path` of a
category often differs from the path in the site's menu.
"""

import json

from curl_cffi import requests

from deals.models import Blocked, Category, Listing, PageResult, Slice, UnknownTarget

GRAPHQL = "https://2b.com.eg/graphql"
PRODUCT_URL = "https://2b.com.eg/en/{key}{suffix}"
PAGE_SIZE = 100  # the API accepts far more; 100 keeps one answer at a few hundred KB
IMPERSONATE = "chrome"
PRODUCT_FIELDS = """
  total_count
  page_info { current_page total_pages }
  items {
    sku name url_key url_suffix brand_name
    stock_status only_x_left_in_stock rating_summary review_count
    price_range { minimum_price { regular_price { value } final_price { value } } }
  }"""
CATEGORY_FIELDS = "items { id name children { id name product_count } }"


class TwoBEG:
    name = "2b_eg"
    max_slice_total = 2000  # no cap on the API; keeps a slice to 20 requests

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        query = build_query(slice, page)
        data = self._post(query)
        if data is None:
            self._session = requests.Session(impersonate=IMPERSONATE)  # fresh Cloudflare cookies, once
            data = self._post(query)
            if data is None:
                raise Blocked(f"{self.name}: Cloudflare challenge")
        if slice.kind == "category" and not data["category"]["items"]:
            raise UnknownTarget(f"{self.name}: no category with id {slice.value}")
        return parse_products(data, page)

    def _post(self, query: str) -> dict | None:
        """The GraphQL `data`, or None for a Cloudflare challenge."""
        r = self._session.post(GRAPHQL, json={"query": query}, timeout=30,
                               headers={"Store": "en", "Content-Type": "application/json"})
        if r.status_code in (403, 429, 503) or "json" not in r.headers.get("content-type", ""):
            return None
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
            raise UnknownTarget(f"2b_eg: category ids are numbers (e.g. category:131), got {slice.value!r}")
        selector = f"filter: {{category_id: {{eq: {json.dumps(slice.value)}}}{_price_filter(slice)}}}"
        category_query = (f"category: categories(filters: {{ids: {{eq: {json.dumps(slice.value)}}}}}) "
                          f"{{{CATEGORY_FIELDS}}}")
    return (f"{{ products({selector}, pageSize: {PAGE_SIZE}, currentPage: {page}, "
            f"sort: {{position: ASC}}) {{{PRODUCT_FIELDS}}} {category_query} }}")


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
        if item.get("sku") and item["sku"] not in listings:
            listings[item["sku"]] = _parse_item(item)
    category = (data.get("category") or {}).get("items") or []
    return PageResult(
        listings=list(listings.values()),
        has_next=page < (products.get("page_info") or {}).get("total_pages", 0),
        total=products.get("total_count"),
        subcategories=[Category(str(c["id"]), f"{c['name'].strip()} ({c.get('product_count', 0)})")
                       for c in category[0]["children"]] if category else [],
    )


def _parse_item(p: dict) -> Listing:
    prices = (p.get("price_range") or {}).get("minimum_price") or {}
    final = _minor((prices.get("final_price") or {}).get("value"))
    regular = _minor((prices.get("regular_price") or {}).get("value"))
    list_price = regular if regular and final and regular > final else None
    reviews = p.get("review_count") or 0
    rating = p.get("rating_summary")  # 0-100
    return Listing(
        site=TwoBEG.name,
        sku=p["sku"],
        title=(p.get("name") or "").strip(),
        url=PRODUCT_URL.format(key=p.get("url_key", ""), suffix=p.get("url_suffix") or ".html"),
        price=final,
        # The crossed-out ("was") price is the shop's regular price before its own
        # discount: a claim, not proof of an earlier selling price.
        list_price=list_price,
        currency="EGP",
        in_stock=p.get("stock_status", "IN_STOCK") == "IN_STOCK" and final is not None,
        list_price_kind="list" if list_price else None,
        brand=(p.get("brand_name") or "").strip() or None,
        stock_left=p.get("only_x_left_in_stock"),
        rating=round(rating / 20, 1) if reviews and rating else None,
        reviews=reviews,
    )


def _minor(value) -> int | None:
    return round(float(value) * 100) if value not in (None, "") else None
