"""miamicenters.com (Miami Center) catalog, through WooCommerce's public Store API.

The same API as Hardware Market (deals/sites/hardwaremarket_eg.py): 100 products a page, the exact count in `X-WP-Total`,
`category=<id>` with its children. **Its prices are whole pounds, not piastres** (`currency_minor_unit` 0), in answers and
in `min_price`/`max_price` alike, so every value goes through the unit the answer states. A page takes 5 s to build.

Sold-out products are listed without being asked for, but asked for anyway. Every product is `simple` (2,991 on 5 Nov 2026),
one price per id. `on_sale` means nothing: 602 are flagged, at a median 0.2% off. Pre-orders are skipped: their page says
"Estimated preorder price", so a change in it is no price drop (18 on 5 Nov 2026). docs/MIAMI.md.
"""

import html
from urllib.parse import urlencode

from curl_cffi import requests

from deals.models import Blocked, Category, Listing, PageResult, Slice, UnknownTarget

BASE = "https://miamicenters.com"
API = f"{BASE}/wp-json/wc/store/v1"
PER_PAGE = 100  # the Store API's maximum
IMPERSONATE = "chrome"
FIELDS = "id,name,sku,permalink,prices,is_in_stock,low_stock_remaining,brands"
STOCK = [("stock_status[]", s) for s in ("instock", "outofstock", "onbackorder")]


class MiamiEG:
    name = "miami_eg"
    max_slice_total = 1000  # 10 pages at 5 s each to build; the price filter narrows anything bigger

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)
        self._categories: list[dict] | None = None

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind != "category":
            raise UnknownTarget(f"{self.name}: use category:<id>")
        category = self._category(slice.value)
        items, total, pages = self._get(products_url(slice, page))
        result = parse_products(items, total, pages, page)
        if page == 1:
            result.subcategories.extend(Category(str(c["id"]), f"{_text(c.get('name'))} ({c.get('count', 0)})")
                                        for c in self._categories or [] if c.get("parent") == category["id"])
        return result

    def _category(self, value: str) -> dict:
        """The category by id or slug; UnknownTarget when the shop has none such (the API would answer the whole shop)."""
        if self._categories is None:
            found, page, pages = [], 1, 1
            while page <= pages:
                data, _, pages = self._get(f"{API}/products/categories?per_page=100&page={page}")
                found += data
                page += 1
            self._categories = found
        match = next((c for c in self._categories if value in (str(c.get("id")), c.get("slug"))), None)
        if match is None:
            raise UnknownTarget(f"{self.name}: no category {value!r} ({API}/products/categories?per_page=100)")
        return match

    def _get(self, url: str) -> tuple[list, int | None, int]:
        r = self._session.get(url, timeout=60)
        if r.status_code in (403, 429, 503) or "json" not in r.headers.get("content-type", ""):
            raise Blocked(f"{self.name}: HTTP {r.status_code} from {url}")
        if r.status_code == 400:
            raise ValueError(f"{self.name}: the Store API refused {url}: {r.text[:300]}")
        r.raise_for_status()
        return r.json(), _int(r.headers.get("x-wp-total")), _int(r.headers.get("x-wp-totalpages")) or 0


def products_url(slice: Slice, page: int) -> str:
    params: list[tuple[str, str | int]] = [("_fields", FIELDS), ("per_page", PER_PAGE), ("page", page),
                                           ("orderby", "id"), ("order", "asc"), ("category", slice.value)]
    if slice.price_min is not None:
        params.append(("min_price", slice.price_min))  # whole pounds here
    if slice.price_max is not None:
        params.append(("max_price", slice.price_max))
    return f"{API}/products?{urlencode(params + STOCK)}"


def parse_products(items: list, total: int | None, pages: int | None, page: int) -> PageResult:
    listings: dict[str, Listing] = {}
    for item in items:
        if _text(item.get("name")).lower().startswith("pre order"):
            continue
        if item.get("id") and str(item["id"]) not in listings:
            listings[str(item["id"])] = _parse_item(item)
    return PageResult(listings=list(listings.values()), has_next=page < (pages or 0),
                      total=total if total is not None else (0 if not items else None))


def _parse_item(p: dict) -> Listing:
    prices = p.get("prices") or {}
    scale = 10 ** (2 - int(prices.get("currency_minor_unit", 0)))  # to piastres, whatever unit the answer is in
    price = (_int(prices.get("price")) or 0) * scale or None
    regular = (_int(prices.get("regular_price")) or 0) * scale or None
    list_price = regular if regular and price and regular > price else None
    brands = p.get("brands") or []
    return Listing(
        site=MiamiEG.name,
        sku=str(p["id"]),
        title=_text(p.get("name")),
        url=p.get("permalink") or f"{BASE}/?p={p['id']}",
        price=price,
        list_price=list_price,
        currency=prices.get("currency_code") or "EGP",
        in_stock=bool(p.get("is_in_stock")) and bool(price),
        list_price_kind="list" if list_price else None,
        brand=_text(brands[0].get("name")) if brands else None,
        stock_left=p.get("low_stock_remaining") or None,
    )


def _text(value) -> str:
    return html.unescape(str(value or "")).strip()


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
