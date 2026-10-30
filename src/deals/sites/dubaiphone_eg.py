"""dubaiphone.net (Dubai Phone) category listings, from the pages' React Server Components data.

A Next.js storefront on Vercel. Each category page (`/en/category/<slug>`) is rendered
on the server from a `shopData` object: up to 100 products a page (`per_page`), an exact
`total`, the selling price, the crossed-out price and stock. Asked with the `RSC: 1`
header the page answers only that data stream (about 40% of the HTML). `/en/category/all`
holds the whole shop (about 1,300 products).

Parameters: `sort=newest` (by id, so pages don't shift when prices change; the default is
price high to low) and `page`. The shop's `price_min`/`price_max` filter is **not used**: it
silently drops every sold-out product (Smart Phone from 2,500 EGP: 103 products, all in stock;
unfiltered: 120, 45 sold out), so a sell-out would look like the product vanishing. Categories
are one or two pages, so a slice is a whole category and the coordinator applies the group's
minimum price itself (as for Dream 2000). A `per_page` above 100 or an unknown sort renders a page without `shopData`; an
unknown category renders Next's not-found page (`NEXT_HTTP_ERROR_FALLBACK;404`), both with
HTTP 200. Vercel caches every page for 10 minutes, so the URL carries the current minute
(`t=`) to read the shop's prices as they are now rather than up to 10 minutes old.

Vercel's bot challenge (HTTP 429, `x-vercel-mitigated: challenge`) stops plain clients and
every datacenter IP; Chrome's TLS fingerprint passes from a home connection. Traps: sold-out
phones listed at 1 EGP until their price is set, pre-orders listed at their deposit (500 EGP),
"coming soon" products that can't be bought. docs/DUBAIPHONE.md.
"""

import html
import json
import time
from urllib.parse import urlencode

from curl_cffi import requests

from deals.models import Blocked, Category, Listing, PageResult, Slice, UnknownTarget
from deals.sites.btech_eg import _rsc_payload

BASE = "https://www.dubaiphone.net/en"
PER_PAGE = 100  # the shop's maximum
IMPERSONATE = "chrome"
PLACEHOLDER_PRICE = 1000  # piastres (10 EGP); sold-out products sit at 1 EGP until they get a real price
NOT_FOUND = "NEXT_HTTP_ERROR_FALLBACK;404"


class DubaiPhoneEG:
    name = "dubaiphone_eg"
    max_slice_total = 10_000  # the price filter is not used, so the planner must never split

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind != "category":
            raise UnknownTarget(f"{self.name}: no server-side search; use category:<slug> (e.g. mobiles-all-2)")
        body = self._get(page_url(slice, page))
        if NOT_FOUND in body and '"shopData":' not in body:
            raise UnknownTarget(f"{self.name}: no category with slug {slice.value!r} "
                                f"(see https://www.dubaiphone.net/sitemap-categories.xml)")
        return parse_listing(body)

    def _get(self, url: str) -> str:
        r = self._session.get(url, timeout=30, headers={"RSC": "1"})
        if r.status_code in (403, 429, 503):
            # Vercel's challenge; one fresh session, then give up.
            self._session = requests.Session(impersonate=IMPERSONATE)
            r = self._session.get(url, timeout=30, headers={"RSC": "1"})
            if r.status_code in (403, 429, 503):
                raise Blocked(f"{self.name}: HTTP {r.status_code} from {url}"
                              + (" (Vercel challenge)" if r.headers.get("x-vercel-mitigated") else ""))
        r.raise_for_status()
        return r.text


def page_url(slice: Slice, page: int, now: float | None = None) -> str:
    params: dict[str, str | int] = {"per_page": PER_PAGE, "sort": "newest"}
    if page > 1:
        params["page"] = page
    # No price_min/price_max: the shop's filter hides sold-out products (the coordinator applies the minimum)
    params["t"] = int((time.time() if now is None else now) // 60)  # past Vercel's 10-minute page cache
    return f"{BASE}/category/{slice.value.strip('/')}?{urlencode(params)}"


def shop_data(body: str) -> dict:
    """The `shopData` object, from an RSC stream (`RSC: 1`) or from the HTML that embeds one."""
    if body.lstrip().startswith("<"):
        body = _rsc_payload(body)
    start = body.find('"shopData":')
    if start < 0:
        raise ValueError("dubaiphone_eg: no shopData in the page (bad parameter, a shop error, or the layout changed)")
    return json.JSONDecoder().raw_decode(body, body.index("{", start))[0]


def parse_listing(body: str) -> PageResult:
    data = shop_data(body)
    products = data.get("products") or {}
    listings: dict[str, Listing] = {}
    for item in products.get("data") or []:
        listing = _parse_item(item)
        if listing and listing.sku not in listings:
            listings[listing.sku] = listing
    filters = (data.get("filters") or {}).get("categories") or []
    return PageResult(
        listings=list(listings.values()),
        has_next=(products.get("current_page") or 1) < (products.get("last_page") or 1),
        total=products.get("total"),
        subcategories=[Category(c["slug"], f"{_unescape(c.get('name', ''))} ({c.get('products_count', 0)})")
                       for c in filters if c.get("slug")],
    )


def _parse_item(item: dict) -> Listing | None:
    title = (item.get("name") or "").strip()
    availability = item.get("availability_type") or "normal"
    if availability == "preorder" or (item.get("preorder") or {}).get("enabled") \
            or title.lower().startswith("pre order"):
        return None  # priced at the deposit (500 EGP) or at a price that isn't on sale yet
    price = _minor(item.get("selling_price"))
    shown = _minor(item.get("price"))
    if price is not None and price < PLACEHOLDER_PRICE:
        price = None  # 1 EGP: no real price yet
    # `has_special_price` is set on hundreds of products whose two prices are equal: only a higher
    # `price` is a crossed-out price, and it is the shop's own claim.
    list_price = shown if shown and price and shown > price else None
    return Listing(
        site=DubaiPhoneEG.name,
        sku=str(item["id"]),
        title=title,
        url=f"{BASE}/shop/{item.get('slug', '')}",
        price=price,
        list_price=list_price,
        currency="EGP",
        in_stock=bool(item.get("in_stock")) and availability == "normal" and price is not None,
        list_price_kind="list" if list_price else None,
    )


def _minor(value) -> int | None:
    """{"amount": 72999, "formatted": "72,999 EGP"} -> 7299900."""
    amount = (value or {}).get("amount") if isinstance(value, dict) else value
    return round(float(amount) * 100) if amount not in (None, "") else None


def _unescape(text: str) -> str:
    return html.unescape(text).strip()
