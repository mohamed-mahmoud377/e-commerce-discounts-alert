"""aghezty.com (Aghezty) catalog, from the JSON its category pages carry.

Server-rendered pages (Bootstrap, 12 products each, about 290 KB) with `window.AGHEZTY = {...}` holding each card's id,
price, crossed-out price, both names, model and stock. The exact count is in `#catalog-results-count` ("Showing 1 - 12 of
116 products available"; "No results found" when a filter leaves nothing). `?min_price=&max_price=` are whole pounds,
inclusive at both ends (asking 5,299 to 5,299 gives the one product at 5,299). Sold-out products are listed unless
`available=1` is asked for.

Past the end a page repeats the last one, so paging stops on the count. An unknown category is an HTTP 404. A price of 0
is "ask us" (16 De'Longhi products on 5 Nov 2026). `/en/flash-sale/` holds the offers (28 products, one page): the
fast lane. docs/AGHEZTY.md.
"""

import html
import json
import re
from urllib.parse import urlencode

from curl_cffi import requests

from deals.models import Blocked, Listing, PageResult, Slice, UnknownTarget

BASE = "https://aghezty.com"
IMPERSONATE = "chrome"
PAGE_SIZE = 12
_STATE = re.compile(r"window\.AGHEZTY = (\{.*?\});?\s*</script>", re.S)
_COUNT = re.compile(r'id="catalog-results-count"[^>]*>([^<]*)<')
_OF = re.compile(r"\bof\s+([\d,]+)")


class AgheztyEG:
    name = "aghezty_eg"
    max_slice_total = 2000  # a whole category pages fine (Large Appliances, 2,074, reads to its end); the filter narrows anything bigger

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind not in ("category", "deals"):
            raise UnknownTarget(f"{self.name}: only category:<path> or deals:flash-sale")
        r = self._session.get(page_url(slice, page), timeout=60)
        if r.status_code == 404:
            raise UnknownTarget(f"{self.name}: no category {slice.value!r} (HTTP 404)")
        if r.status_code in (403, 429, 503) or "Just a moment" in r.text[:2000]:
            raise Blocked(f"{self.name}: HTTP {r.status_code}")
        r.raise_for_status()
        return parse_page(r.text, page, feed=slice.kind == "deals")


def page_url(slice: Slice, page: int) -> str:
    query = {}
    if slice.kind == "deals":  # the offers page is the whole feed: a group's minimum is applied to it later
        return f"{BASE}/en/{slice.value.strip('/')}/"
    if slice.price_min is not None:
        query["min_price"] = slice.price_min
    if slice.price_max is not None:
        query["max_price"] = slice.price_max
    if page > 1:
        query["page"] = page
    return f"{BASE}/en/{slice.value.strip('/')}/" + (f"?{urlencode(query)}" if query else "")


def parse_page(body: str, page: int = 1, feed: bool = False) -> PageResult:
    state = _STATE.search(body)
    if not state:
        raise ValueError("aghezty_eg: no product data in the page (layout changed?)")
    products = json.loads(state.group(1)).get("products") or []
    listings = [_listing(p) for p in products]
    if feed:  # a fast lane reads the offers' one page
        return PageResult(listings=listings, has_next=False, total=len(listings))
    shown = _COUNT.search(body)
    count = _OF.search(shown.group(1)) if shown else None
    total = int(count.group(1).replace(",", "")) if count else 0 if not products else None
    return PageResult(listings=listings, has_next=total is not None and page * PAGE_SIZE < total, total=total)


def _listing(p: dict) -> Listing:
    names = p.get("names") or {}
    price = _piastres(p.get("prices"))
    old = _piastres(p.get("oldPrices"))
    claim = old if price and old and old > price else None
    return Listing(
        site=AgheztyEG.name,
        sku=str(p["id"]),
        title=html.unescape(names.get("en") or names.get("ar") or "").strip(),
        url=p["url"],
        price=price,
        list_price=claim,
        currency="EGP",
        in_stock=bool(p.get("in_stock")) and price is not None,
        list_price_kind="list" if claim else None,
        brand=(p.get("brand") or "").strip() or None,
        rating=float(p["rating"]) if p.get("reviews") else None,
        reviews=p.get("reviews") or 0,
    )


def _piastres(prices: dict | None) -> int | None:
    number = (prices or {}).get("num")
    return round(float(number) * 100) if number else None
