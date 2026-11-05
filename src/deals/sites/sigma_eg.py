"""sigma-computer.com (Sigma Computer) catalog, from its own search pages.

A Next.js shop: `/en/search?filters={"category_id":["<uuid>"]}&page=N` is rendered on the server with the page's 16 products
in the RSC payload, and with how many there are (`"is_stock":{"false":393,"true":194}`; its `totalPages` is that same total,
not a number of pages). The category holds its children. Sold-out products stay listed (`is_stock`), a page past the end has no
products. No usable price filter, so a slice is always a whole category and the coordinator applies the group's minimum.

**`sortBy=createdAt` is not optional.** Its default order repeats products between pages: 498 different ones in 587 reads, 15%
missed, on the laptops of 5 Nov 2026. By creation date it read 587 of 587.

**A listing ends at 1,000 products,** whatever its count says: Hardware Components counts 1,876, and page 63 holds the last 8
of 1,000. Nothing else shows it, so a root over the cap logs a warning: watch its sub-categories instead.

Any category id gives an empty 200, so ids are checked against the category list, once. The products endpoint on the API host
answers 403; the category list is open, and that host's robots.txt allows everything. docs/SIGMA.md.
"""

import json
import logging
import re
from urllib.parse import quote

from curl_cffi import requests

from deals.models import Blocked, Category, Listing, PageResult, Slice, UnknownTarget

log = logging.getLogger(__name__)

BASE = "https://www.sigma-computer.com"
CATEGORIES = "https://api.sigma-computer.com/api/stores/v1/categories"
PER_PAGE = 16
MAX_RESULTS = 1000  # the most a listing shows, whatever it counts
IMPERSONATE = "chrome"
_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)
_STOCK = re.compile(r'"is_stock":\{([^}]*)\}')


class SigmaEG:
    name = "sigma_eg"
    max_slice_total = 100_000  # no price filter: the planner never splits

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)
        self._categories: list[dict] | None = None

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind != "category":
            raise UnknownTarget(f"{self.name}: use category:<uuid>")
        category = self._category(slice.value)
        r = self._session.get(listing_url(slice.value, page), timeout=30)
        if r.status_code in (403, 429, 503):
            raise Blocked(f"{self.name}: HTTP {r.status_code} from {r.url}")
        r.raise_for_status()
        result = parse_listing(r.text, page)
        if page == 1 and (result.total or 0) > MAX_RESULTS:
            log.warning("%s: category %s holds %d products and a listing shows %d: watch its sub-categories",
                        self.name, slice.value, result.total, MAX_RESULTS)
        if page == 1:
            result.subcategories.extend(Category(c["id"], c.get("name") or "") for c in category.get("subcategories") or [])
        return result

    def _category(self, value: str) -> dict:
        if self._categories is None:
            r = self._session.get(CATEGORIES, headers={"accept-language": "en"}, timeout=30)
            if r.status_code in (403, 429, 503) or "json" not in r.headers.get("content-type", ""):
                raise Blocked(f"{self.name}: HTTP {r.status_code} from {CATEGORIES}")
            r.raise_for_status()
            self._categories = [c for top in r.json()["data"] for c in [top, *(top.get("subcategories") or [])]]
        found = next((c for c in self._categories if c.get("id") == value), None)
        if found is None:
            raise UnknownTarget(f"{self.name}: no category {value!r} ({CATEGORIES})")
        return found


def listing_url(category: str, page: int) -> str:
    filters = quote(json.dumps({"category_id": [category]}, separators=(",", ":")))
    return f"{BASE}/en/search?filters={filters}&sortBy=createdAt" + (f"&page={page}" if page > 1 else "")


def parse_listing(page_html: str, page: int = 1) -> PageResult:
    payload = _rsc(page_html)
    counts = _STOCK.search(payload)
    if "self.__next_f" not in page_html:
        raise ValueError(f"{SigmaEG.name}: no product data in the page (layout changed?)")
    total = sum(int(n) for n in re.findall(r":(\d+)", counts[1])) if counts else 0
    products = _products(payload)
    listings = {p["id"]: _parse_item(p) for p in products if p.get("id")}
    return PageResult(listings=list(listings.values()), has_next=len(products) >= PER_PAGE and page * PER_PAGE < total,
                      total=total if page == 1 else None)


def _rsc(page_html: str) -> str:
    return "".join(json.loads(f'"{part}"') for part in _PUSH.findall(page_html))


def _products(payload: str) -> list[dict]:
    """The listing's own array: the first `products` after the filters (the page's text has others)."""
    start = payload.find('"products":[{', max(payload.find('"initialFilters"'), 0))
    if start < 0:
        return []
    products, _ = json.JSONDecoder().raw_decode(payload, start + len('"products":'))
    return products


def _parse_item(p: dict) -> Listing:
    money = p.get("price") or {}
    price = _minor(money.get("current"))
    base = _minor(money.get("base"))
    list_price = base if base and price and base > price else None
    return Listing(
        site=SigmaEG.name,
        sku=p["id"],
        title=(p.get("name") or "").strip(),
        url=f"{BASE}/en/item?id={p.get('slug', '')}",
        price=price,
        list_price=list_price,
        currency=money.get("currency") or "EGP",
        in_stock=bool(p.get("is_stock")) and price is not None,
        list_price_kind="list" if list_price else None,
        brand=((p.get("brand") or {}).get("name") or "").strip() or None,
    )


def _minor(value) -> int | None:
    return round(float(value) * 100) if value not in (None, "", 0) else None
