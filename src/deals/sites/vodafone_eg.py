"""eshop.vodafone.com.eg (Vodafone Egypt e-shop) catalog, through its catalog search API.

An Angular storefront on Broadleaf Commerce. `GET /ecommerce/api/catalog-browse/search/catalog` answers
JSON without a login (it wants the two context headers below): up to 200 products a page, the exact
`totalElements`, `filters[n].name=categoryUrls` for the category, `sort=sku,asc` for a stable order.

`hideOutOfStock=true` is what the site itself asks for. Without it the category holds about four times as
many products, all with old prices and no stock flag in the JSON, so only what is in stock is read: a
product that sells out disappears, and one that comes back is seen with its current price.

There is no price filter we need: the whole store is under 1,000 products in stock, so a slice is always a
whole category (`max_slice_total` above any category; the coordinator applies the group's minimum).

The API resets every second HTTP/2 request on a connection (curl error 92, INTERNAL_ERROR), so the session
speaks HTTP/1.1, and every request of the measuring runs was then clean. Answers from the droplet too. docs/VODAFONE.md.
"""

from urllib.parse import urlencode

from curl_cffi import requests

from deals.models import Blocked, Listing, PageResult, Slice, UnknownTarget

BASE = "https://eshop.vodafone.com.eg"
API = f"{BASE}/ecommerce/api/catalog-browse"
PRODUCT_URL = f"{BASE}/en/prod{{uri}}"
PER_PAGE = 200  # the API's page size is honoured at least this far
IMPERSONATE = "chrome"
HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "x-price-context": '{"locale":"en_US"}',
    "x-context-request": '{"applicationId":"01H5FECVAV4YWT0NGQKXEN1T51", "tenantId":"5DF1363059675161A85F576D"}',
}


class VodafoneEG:
    name = "vodafone_eg"
    max_slice_total = 10_000  # bigger than the whole store: the planner never splits (no price filter)

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE, http_version="v1")
        self._known: set[str] = set()

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind != "category":
            raise UnknownTarget(f"{self.name}: only category:<url name> slices, e.g. category:smart-phones")
        category = "/" + slice.value.strip("/")
        if category not in self._known:  # an unknown category lists nothing with HTTP 200, like an empty one
            if not self._get_json(f"{API}/vfe-categories/details?{urlencode({'categoryUrl': category})}"):
                raise UnknownTarget(f"{self.name}: no category {category!r}")
            self._known.add(category)
        return parse_products(self._get_json(search_url(category, page)))

    def _get_json(self, url: str):
        r = self._session.get(url, headers=HEADERS, timeout=40)
        if r.status_code in (403, 429) or (r.status_code == 200 and "json" not in r.headers.get("content-type", "")):
            raise Blocked(f"{self.name}: HTTP {r.status_code} from {url}")
        if r.status_code in (400, 404):
            return None
        r.raise_for_status()
        return r.json() if r.text.strip() else None


def search_url(category: str, page: int) -> str:
    return f"{API}/search/catalog?" + urlencode([
        ("config", "eCommerceTypeAhead"),
        ("filters[0].name", "hideOutOfStock"), ("filters[0].values[0]", "true"),
        ("filters[1].name", "categoryUrls"), ("filters[1].values[0]", category),
        ("page", page - 1), ("size", PER_PAGE), ("sort", "sku,asc"),
    ])


def parse_products(data: dict) -> PageResult:
    listings: dict[str, Listing] = {}
    for item in data.get("content") or []:
        listing = _parse_item(item)
        if listing:
            listings.setdefault(listing.sku, listing)
    return PageResult(listings=list(listings.values()), has_next=not data.get("last", True),
                      total=data.get("totalElements"))


def _parse_item(p: dict) -> Listing | None:
    if not p.get("id") or not p.get("uri"):
        return None
    price = _minor((p.get("price") or {}).get("amount"))
    base = ((p.get("priceInfo") or {}).get("priceTypeDetails") or {}).get("basePrice") or {}
    regular = _minor((base.get("bestPrice") or {}).get("amount"))
    list_price = regular if regular and price and regular > price else None
    return Listing(
        site=VodafoneEG.name,
        sku=p["id"],  # a quarter of the phones have no `sku`; this id is on every product and never changes
        title=(p.get("name") or "").strip(),
        url=PRODUCT_URL.format(uri=p["uri"]),
        price=price,
        list_price=list_price,
        currency="EGP",
        in_stock=price is not None,  # only products in stock are asked for
        list_price_kind="list" if list_price else None,
        brand=(p.get("brandDisplayValue") or "").strip() or None,
    )


def _minor(value) -> int | None:
    return round(float(value) * 100) if value not in (None, "") else None
