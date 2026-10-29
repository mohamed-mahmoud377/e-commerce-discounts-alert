"""dream2000.com (Dream 2000) catalog, through Shopify's public collection JSON.

Dream 2000 is a Shopify store: `/en/collections/<handle>/products.json?limit=250&page=N`
lists a whole collection as JSON (English titles under `/en/`), and
`/en/collections/<handle>.json` gives its exact `products_count` (404 for an unknown
handle). Shopify has no price filter on these endpoints and the biggest collection
has under 1,000 products, so a slice is always the whole collection: the coordinator
applies the group's minimum price itself, and `max_slice_total` is set so that the
planner never splits. There is no usable product search either (`search:` is refused).

Out-of-stock products stay listed with their price (`available: false` on every
variant). A product's variants are colours, storage sizes and the like; variants that
differ only in colour (or in the barcode-style "SKU" option) are folded into one
listing at the cheapest available price, variants that differ in anything else
("8GB / 256GB") become separate listings, so a sold-out small model never shows up as
a price drop of the big one. Reachable from datacenter IPs (the droplet) as well.
"""

import re

from curl_cffi import requests

from deals.models import Blocked, Listing, PageResult, Slice, UnknownTarget, valid_gtin

BASE = "https://dream2000.com/en"
PER_PAGE = 250  # Shopify's maximum
IMPERSONATE = "chrome"
IGNORED_OPTIONS = {"color", "colour", "sku", "title"}  # variants differing only in these are one listing
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


class Dream2000EG:
    name = "dream2000_eg"
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
            # 430 is Shopify's own "request blocked" status; a challenge page is not JSON.
            raise Blocked(f"{self.name}: HTTP {r.status_code} from {url}")
        r.raise_for_status()
        return r.json()


def parse_products(data: dict, total: int | None = None) -> PageResult:
    products = data.get("products") or []
    listings: dict[str, Listing] = {}
    for product in products:
        for listing in product_listings(product):
            listings.setdefault(listing.sku, listing)
    return PageResult(
        listings=list(listings.values()),
        has_next=len(products) >= PER_PAGE,
        total=total if total is not None else (0 if not products else None),
    )


def product_listings(product: dict) -> list[Listing]:
    """One listing per group of variants that differ in something other than colour."""
    options = product.get("options") or []
    kept = [i for i, o in enumerate(options) if (o.get("name") or "").strip().lower() not in IGNORED_OPTIONS]
    groups: dict[tuple[str, ...], list[dict]] = {}
    for variant in product.get("variants") or []:
        values = tuple((variant.get(f"option{i + 1}") or "").strip() for i in kept)
        groups.setdefault(values, []).append(variant)
    out = []
    for values, variants in groups.items():
        available = [v for v in variants if v.get("available")]
        cheapest = min(available or variants, key=lambda v: float(v.get("price") or 0))
        price = _minor(cheapest.get("price"))
        compare = _minor(cheapest.get("compare_at_price"))
        suffix = " / ".join(v for v in values if v)
        out.append(Listing(
            site=Dream2000EG.name,
            sku=str(product["id"]) + (f":{_slug(suffix)}" if suffix else ""),
            title=(product.get("title") or "").strip() + (f" ({suffix})" if suffix else ""),
            url=f"{BASE}/products/{product.get('handle', '')}",
            price=price,
            # Shopify's "compare at" price is the shop's own claim (set on 83% of the catalogue).
            list_price=compare if compare and price and compare > price else None,
            currency="EGP",
            in_stock=bool(available) and price is not None,
            list_price_kind="list" if compare and price and compare > price else None,
            brand=(product.get("vendor") or "").strip() or None,
            # Dream 2000 puts each variant's barcode in its SKU (97% of them): the colours of this listing
            gtins=tuple(sorted({g for v in variants if (g := valid_gtin(v.get("sku")))})),
        ))
    return out


def _slug(text: str) -> str:
    return _NOT_SLUG.sub("-", text.lower()).strip("-")


def _minor(value) -> int | None:
    return round(float(value) * 100) if value not in (None, "") else None
