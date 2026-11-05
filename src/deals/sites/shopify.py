"""What every Shopify shop has in common, for the shops added from 5 Nov 2026: WayUp, GC Shop, Azzam.

`/collections/<handle>/products.json?limit=250&page=N` lists a collection, and `/products.json` the whole shop
(`category:all`, in a stable order). The parser is Dream 2000's. No price filter and no search: a slice is always a
whole collection, the coordinator applies the group's minimum, and `max_slice_total` keeps the planner from splitting.

Three traps measured on 5 Nov 2026:
- **A listing ends at page 100.** Page 101 answers HTTP 400 (WayUp), so no root may hold over 25,000 products.
- **`products_count` is no count.** Azzam's collection says 21,227 and lists 7,287, GC Shop's `all` says 3,630 and lists
  8,500. Paging ends on a short page. It only tells the planner the root isn't empty.
- **None with `has_next` means "too big" to the planner**, which would split down to 1 EGP ranges with no filter to narrow
  them. So page 1 always carries a number.
"""

from dataclasses import replace

from curl_cffi import requests

from deals.models import Blocked, PageResult, Slice, UnknownTarget
from deals.sites import dream2000_eg

PER_PAGE = dream2000_eg.PER_PAGE
MAX_PAGES = 100  # Shopify's own limit: page 101 is an HTTP 400
IMPERSONATE = "chrome"


class ShopifyShop:
    name = ""
    base = ""  # no trailing slash
    ignored = dream2000_eg.IGNORED_OPTIONS  # options folded into one listing: count what each does to the price first
    max_slice_total = 100_000  # bigger than any root: the planner never splits (no price filter)
    whole = "/products.json"  # what `category:all` reads

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind != "category":
            raise UnknownTarget(f"{self.name}: Shopify has no product search; use category:<collection handle>")
        handle = slice.value.strip("/")
        if page > MAX_PAGES:
            return PageResult(listings=[], has_next=False)
        count = None
        if page == 1:
            meta = self._get_json(f"{self.base}/collections/{handle}.json")
            if meta is None:
                raise UnknownTarget(f"{self.name}: no collection with handle {handle!r} (HTTP 404)")
            count = (meta.get("collection") or {}).get("products_count") or 0
        path = self.whole if handle == "all" else f"/collections/{handle}/products.json"
        data = self._get_json(f"{self.base}{path}?limit={PER_PAGE}&page={page}")
        if data is None:
            raise UnknownTarget(f"{self.name}: no collection with handle {handle!r} (HTTP 404)")
        return self.parse_products(data, count, page)

    @classmethod
    def parse_products(cls, data: dict, count: int | None = None, page: int = 1) -> PageResult:
        products = data.get("products") or []
        total = None if count is None else max(count, len(products))
        result = dream2000_eg.parse_products(data, total, site=cls.name, base=cls.base, ignored=cls.ignored)
        # An empty page after the first only ends the walk: total 0 would tell the coordinator the root emptied.
        return replace(result, has_next=result.has_next and page < MAX_PAGES, total=result.total if page == 1 else None)

    def _get_json(self, url: str) -> dict | None:
        """The JSON answer, None for a 404; Blocked when Shopify refuses us."""
        r = self._session.get(url, timeout=30)
        if r.status_code == 404:
            return None
        if r.status_code in (403, 429, 430, 503) or "json" not in r.headers.get("content-type", ""):
            raise Blocked(f"{self.name}: HTTP {r.status_code} from {url}")  # 430 is Shopify's own "request blocked"
        r.raise_for_status()
        return r.json()
