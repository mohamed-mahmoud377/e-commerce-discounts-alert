"""hardwaremarket.net (Hardware Market) catalog, through WooCommerce's public Store API.

A WordPress + WooCommerce shop for gaming peripherals and accessories. The Store API
(`/wp-json/wc/store/v1/products`) answers JSON without a login: up to 100 products a
page, the exact count in the `X-WP-Total` header, `category=<id or slug>` (children
included), `min_price`/`max_price` in piastres on the current price (inclusive) and
`search=`. Prices are strings in piastres already. `_fields` trims an answer from 4 MB
to about 100 KB a page (the full objects carry HTML descriptions).

Sold-out products are hidden unless every `stock_status` is asked for, so they are: a
product that sells out stays visible with `is_in_stock` false (three quarters of the
catalogue is sold out). Paging is by id, which doesn't shift when prices change.

`deals:<category>` is the fast lane: the category's 50 most recently modified products
(`orderby=modified`); a price edit moves a product to the top. Nearly every product
shows a crossed-out "regular" price (98%), so it is only the shop's claim.

Cloudflare in front: a home connection passes even without a browser fingerprint, the
droplet gets the challenge (HTTP 403, `cf-mitigated: challenge`). docs/HARDWAREMARKET.md.
"""

import html
from urllib.parse import urlencode

from curl_cffi import requests

from deals.models import Blocked, Category, Listing, PageResult, Slice, UnknownTarget

BASE = "https://hardwaremarket.net"
API = f"{BASE}/wp-json/wc/store/v1"
PER_PAGE = 100  # the Store API's maximum
DEALS_PAGE = 50  # recently modified products read by the fast lane
IMPERSONATE = "chrome"
FIELDS = "id,name,sku,permalink,prices,is_in_stock,low_stock_remaining,brands"
STOCK = [("stock_status[]", s) for s in ("instock", "outofstock", "onbackorder")]


class HardwareMarketEG:
    name = "hardwaremarket_eg"
    max_slice_total = 1000  # 10 pages; each costs the shop about 3 s to build, so bigger slices are split

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)
        self._categories: list[dict] | None = None

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind == "deals" and page > 1:
            return PageResult(listings=[], has_next=False)
        category = None
        if slice.kind in ("category", "deals") and slice.value != "all":
            category = self._category(slice.value)
        items, total, pages = self._get(products_url(slice, page))
        result = parse_products(items, total, pages, page)
        if slice.kind == "deals":
            return PageResult(listings=result.listings, has_next=False, total=None)
        if category is not None and page == 1:
            children = [c for c in self._categories or [] if c.get("parent") == category["id"]]
            result.subcategories.extend(Category(str(c["id"]), f"{_text(c.get('name'))} ({c.get('count', 0)})")
                                        for c in children)
        return result

    def _category(self, value: str) -> dict:
        """The category (by id or slug); UnknownTarget when the shop has none such."""
        if self._categories is None:
            data, _, _ = self._get(f"{API}/products/categories?per_page=100")
            self._categories = data
        found = next((c for c in self._categories if value in (str(c.get("id")), c.get("slug"))), None)
        if found is None:
            raise UnknownTarget(f"{self.name}: no category {value!r} (ids and slugs: {API}/products/categories?per_page=100)")
        return found

    def _get(self, url: str) -> tuple[list, int | None, int | None]:
        """The JSON list with X-WP-Total and X-WP-TotalPages; Blocked on a Cloudflare challenge."""
        r = self._session.get(url, timeout=60)
        if _refused(r):
            self._session = requests.Session(impersonate=IMPERSONATE)  # fresh Cloudflare cookies, once
            r = self._session.get(url, timeout=60)
            if _refused(r):
                raise Blocked(f"{self.name}: HTTP {r.status_code} from {url}")
        if r.status_code == 400:
            raise ValueError(f"{self.name}: the Store API refused {url}: {r.text[:300]}")
        r.raise_for_status()
        return r.json(), _int(r.headers.get("x-wp-total")), _int(r.headers.get("x-wp-totalpages"))


def _refused(r) -> bool:
    return r.status_code in (403, 429, 503) or "json" not in r.headers.get("content-type", "")


def products_url(slice: Slice, page: int) -> str:
    params: list[tuple[str, str | int]] = [("_fields", FIELDS)]
    if slice.kind == "deals":
        params += [("per_page", DEALS_PAGE), ("orderby", "modified"), ("order", "desc")]
    else:
        params += [("per_page", PER_PAGE), ("page", page), ("orderby", "id"), ("order", "asc")]
    if slice.kind == "search":
        params.append(("search", slice.value))
    elif slice.value != "all":
        params.append(("category", slice.value))
    if slice.price_min is not None:
        params.append(("min_price", slice.price_min * 100))
    if slice.price_max is not None:
        params.append(("max_price", slice.price_max * 100 + 99))  # whole pounds, inclusive
    return f"{API}/products?{urlencode(params + STOCK)}"


def parse_products(items: list, total: int | None, pages: int | None, page: int) -> PageResult:
    listings: dict[str, Listing] = {}
    for item in items:
        if item.get("id") and str(item["id"]) not in listings:
            listings[str(item["id"])] = _parse_item(item)
    return PageResult(
        listings=list(listings.values()),
        has_next=page < (pages or 0),
        total=total if total is not None else (0 if not items else None),
    )


def _parse_item(p: dict) -> Listing:
    prices = p.get("prices") or {}
    price = _int(prices.get("price"))
    regular = _int(prices.get("regular_price"))
    list_price = regular if regular and price and regular > price else None
    sku = _text(p.get("sku"))
    brands = p.get("brands") or []
    # The shop's brand when it set one, else the first word of an "ONIKUMA | X92" style SKU
    brand = _text(brands[0].get("name")) if brands else (sku.split("|")[0].split() or [""])[0] if "|" in sku else ""
    return Listing(
        site=HardwareMarketEG.name,
        sku=str(p["id"]),
        title=_text(p.get("name")),
        url=p.get("permalink") or f"{BASE}/?p={p['id']}",
        price=price or None,
        list_price=list_price,
        currency=prices.get("currency_code") or "EGP",
        in_stock=bool(p.get("is_in_stock")) and bool(price),
        list_price_kind="list" if list_price else None,
        brand=brand.title() if brand.isupper() and len(brand) > 3 else brand or None,
        stock_left=p.get("low_stock_remaining") or None,
    )


def _text(value) -> str:
    return html.unescape(str(value or "")).strip()


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
