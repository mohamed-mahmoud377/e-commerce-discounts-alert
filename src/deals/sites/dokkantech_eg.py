"""dokkantech.com (Dokkan Tech) catalog, through Shopify's public collection JSON.

The same Shopify endpoints as Dream 2000 (`deals/sites/dream2000_eg.py`, whose parser this reuses):
`/collections/<handle>/products.json?limit=250&page=N` and `/collections/<handle>.json` for the count.
No price filter and no search; collections are small, so a slice is always a whole collection and the
coordinator applies the group's minimum price.

English is the default locale here (Arabic under `/ar/`). Sold-out products stay listed. Its `Box` option
("Asia Box", "MENA Box") stays a separate listing: the two cost different prices on 41 of 66 products, so folding
them would show a price drop whenever one sells out. Answers from anywhere, the droplet too. docs/DOKKANTECH.md.
"""

from curl_cffi import requests

from deals.models import Blocked, PageResult, Slice, UnknownTarget
from deals.sites import dream2000_eg

BASE = "https://www.dokkantech.com"
PER_PAGE = dream2000_eg.PER_PAGE
IMPERSONATE = "chrome"


class DokkanTechEG:
    name = "dokkantech_eg"
    max_slice_total = 10_000  # bigger than any collection: the planner never splits (no price filter)

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind != "category":
            raise UnknownTarget(f"{self.name}: Shopify has no product search; use category:<collection handle>")
        handle = slice.value.strip("/")
        total = None
        if page == 1:
            meta = self._get_json(f"{BASE}/collections/{handle}.json")
            if meta is None:
                raise UnknownTarget(f"{self.name}: no collection with handle {handle!r} (HTTP 404)")
            total = (meta.get("collection") or {}).get("products_count")
        data = self._get_json(f"{BASE}/collections/{handle}/products.json?limit={PER_PAGE}&page={page}")
        if data is None:
            raise UnknownTarget(f"{self.name}: no collection with handle {handle!r} (HTTP 404)")
        return parse_products(data, total)

    def _get_json(self, url: str) -> dict | None:
        """The JSON answer, None for a 404; Blocked when Shopify refuses us."""
        r = self._session.get(url, timeout=30)
        if r.status_code == 404:
            return None
        if r.status_code in (403, 429, 430, 503) or "json" not in r.headers.get("content-type", ""):
            raise Blocked(f"{self.name}: HTTP {r.status_code} from {url}")
        r.raise_for_status()
        return r.json()


def parse_products(data: dict, total: int | None = None) -> PageResult:
    return dream2000_eg.parse_products(data, total, site=DokkanTechEG.name, base=BASE)
