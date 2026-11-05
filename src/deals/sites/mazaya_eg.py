"""mazaya.eg (Mazaya): perfumes, makeup and skin care, through the Magento GraphQL API its own pages call.

`GET /graphql?query=...&variables=...` with `store: en`: 100 products a page, `total_count` exact, `category_uid` is the
category's id in base64 (children included), `price: {from, to}` inclusive on the current price. The category pages hold no
products: the browser fetches them here. **`robots.txt` disallows /graphql**; the user chose to crawl it anyway (5 Nov 2026),
slowly and with alerts kept at first. docs/MAZAYA.md.

A listing's price is the cheapest of its variants, so sizes must be read one by one: 132 of 1,513 perfumes come in sizes at
different prices (100 ML 22,900, 50 ML 15,000). Each size is its own listing. Shades fold into one when they cost the same (79
of 87 in makeup do) and are their own listings when they don't. Sold-out products stay listed. A page past the end is a
GraphQL error, and an unknown category an empty answer, told apart from an empty one by asking for the category.
"""

import base64
import json
import re
from urllib.parse import urlencode

from curl_cffi import requests

from deals.models import Blocked, Listing, PageResult, Slice, UnknownTarget

BASE = "https://mazaya.eg/en"
API = "https://mazaya.eg/graphql"
PER_PAGE = 100
IMPERSONATE = "chrome"
SIZE_OPTIONS = {"size"}  # always a separate listing
_PRICE = "price_range{minimum_price{final_price{value}regular_price{value}}}"
_QUERY = ("query P($page:Int$pageSize:Int$filter:ProductAttributeFilterInput={}$sort:ProductAttributeSortInput={}){"
          "products(currentPage:$page pageSize:$pageSize filter:$filter sort:$sort){total_count page_info{total_pages} "
          f"items{{id name sku url_key stock_status brand{{name}} {_PRICE} ...on ConfigurableProduct{{"
          f"variants{{attributes{{code label}} product{{stock_status {_PRICE}}}}}}}}}}}}}")
_CATEGORY = "query C($id:String){categories(filters:{ids:{eq:$id}}){items{id}}}"
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


class MazayaEG:
    name = "mazaya_eg"
    max_slice_total = 5000  # 50 pages; bigger than any category it has, and the price filter narrows anything bigger

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind != "category":
            raise UnknownTarget(f"{self.name}: use category:<id>")
        result = parse_products(self._call(_QUERY, variables(slice, page)), page)
        if page == 1 and result.total == 0 and slice.price_min in (None, 0) and slice.price_max is None:
            if not self._call(_CATEGORY, {"id": slice.value})["categories"]["items"]:
                raise UnknownTarget(f"{self.name}: no category {slice.value!r}")
        return result

    def _call(self, query: str, values: dict) -> dict:
        url = f"{API}?{urlencode({'query': query, 'variables': json.dumps(values, separators=(',', ':'))})}"
        r = self._session.get(url, headers={"store": "en", "content-type": "application/json"}, timeout=40)
        if r.status_code in (403, 429, 503) or "json" not in r.headers.get("content-type", ""):
            raise Blocked(f"{self.name}: HTTP {r.status_code} from /graphql")
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise ValueError(f"{self.name}: GraphQL error: {body['errors'][0].get('message')}")
        return body["data"]


def variables(slice: Slice, page: int) -> dict:
    wanted: dict = {"category_uid": {"eq": base64.b64encode(slice.value.encode()).decode()}}
    if slice.price_min is not None or slice.price_max is not None:
        wanted["price"] = {k: str(v) for k, v in (("from", slice.price_min), ("to", slice.price_max)) if v is not None}
    return {"page": page, "pageSize": PER_PAGE, "sort": {"name": "ASC"}, "filter": wanted}  # by name: nothing repeats


def parse_products(data: dict, page: int = 1) -> PageResult:
    found = data["products"]
    listings: dict[str, Listing] = {}
    for item in found.get("items") or []:
        for listing in _item_listings(item):
            listings.setdefault(listing.sku, listing)
    return PageResult(listings=list(listings.values()), has_next=page < (found.get("page_info") or {}).get("total_pages", 0),
                      total=found.get("total_count") if page == 1 else None)


def _item_listings(item: dict) -> list[Listing]:
    variants = item.get("variants") or []
    if not variants:
        return [_listing(item, "", [item])]
    sizes: dict[str, list[dict]] = {}
    for v in variants:
        size = " ".join(a["label"] for a in v.get("attributes") or [] if a.get("code") in SIZE_OPTIONS)
        sizes.setdefault(size, []).append(v)
    out = []
    for size, group in sizes.items():
        if len({_prices(v["product"]) for v in group}) == 1:
            out.append(_listing(item, size, [v["product"] for v in group]))
        else:  # shades at different prices: folding them would make a restock look like a price drop
            for v in group:
                shade = " ".join(a["label"] for a in v.get("attributes") or [])
                out.append(_listing(item, shade, [v["product"]]))
    return out


def _listing(item: dict, suffix: str, offers: list[dict]) -> Listing:
    available = [o for o in offers if o.get("stock_status") == "IN_STOCK"]
    price, regular = min(map(_prices, available or offers), key=lambda p: p[0] or 0)
    list_price = regular if regular and price and regular > price else None
    suffix = suffix.strip()
    return Listing(
        site=MazayaEG.name,
        sku=str(item["id"]) + (f":{_NOT_SLUG.sub('-', suffix.lower()).strip('-')}" if suffix else ""),
        title=(item.get("name") or "").strip() + (f" ({suffix})" if suffix else ""),
        url=f"{BASE}/{item.get('url_key', '')}",
        price=price,
        list_price=list_price,
        currency="EGP",
        in_stock=bool(available) and price is not None,
        list_price_kind="list" if list_price else None,
        brand=((item.get("brand") or {}).get("name") or "").strip() or None,
    )


def _prices(offer: dict) -> tuple[int | None, int | None]:
    low = (offer.get("price_range") or {}).get("minimum_price") or {}
    return _minor(low.get("final_price")), _minor(low.get("regular_price"))


def _minor(money: dict | None) -> int | None:
    value = (money or {}).get("value")
    return round(float(value) * 100) if value else None
