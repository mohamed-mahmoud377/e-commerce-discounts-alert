"""eand.com.eg (e& Egypt, formerly Etisalat) eshop, through the JSON API its Angular pages call.

`/eshopapi/v1/category/getCategoryWithSubCategories?categoryId=<id>&count=100&page=<from 0>&sortBy=nameAsc`, the exact count in
`recordsTotal`. **Without `Accept: application/json` it answers XML**, with HTTP 200. The default order moves between pages;
names don't. No usable price filter (the page filters by range on the shop's side only), and the shop is small, so a slice is
a whole category. An unknown category is an HTTP 200 whose `category.id` is 0.

A marketplace: `store` is the seller (2B, Raya, Etisalat), and it sells the same phones as 2B and Raya do themselves. Colours
are separate products. Its categories overlap heavily, most of them being campaigns ("Black Friday Flash Sale", "Valu Offer 18
Month"), so `KEEP` and `SKIP` hold each root to its group by the product's `type` (a product takes the group of the last
root that read it). docs/EAND.md.
"""

from urllib.parse import quote

from curl_cffi import requests

from deals.models import Blocked, Listing, PageResult, Slice, UnknownTarget

BASE = "https://www.eand.com.eg"
API = f"{BASE}/eshopapi/v1/category/getCategoryWithSubCategories"
IMPERSONATE = "chrome"
PAGE_SIZE = 100
LOW_STOCK = 5  # the page says "Only N left in stock" at about this many
# Measured on 5 Nov 2026. Laptops also holds 400 cables, mice and bags, and 104 of them are under Chargers too.
KEEP = {"27": {"laptop"}, "18": {"Wearables", "smartwatch"}}
SKIP = {"2": {"tablet"}, "4": {"smartphone"}, "30": {"Headphones", "headset"}}  # read through Tablets, Devices and Headsets


class EandEG:
    name = "eand_eg"
    max_slice_total = 100_000  # bigger than any category: no price filter to split with

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind != "category":
            raise UnknownTarget(f"{self.name}: use category:<id>, e.g. category:2")
        r = self._session.get(API, params={"categoryId": slice.value, "count": PAGE_SIZE, "page": page - 1, "sortBy": "nameAsc"},
                              headers={"Accept": "application/json"}, timeout=60)
        if r.status_code in (403, 429, 503) or "json" not in r.headers.get("content-type", ""):
            raise Blocked(f"{self.name}: HTTP {r.status_code}")
        r.raise_for_status()
        data = r.json()
        if not ((data.get("response") or {}).get("category") or {}).get("id"):
            raise UnknownTarget(f"{self.name}: no category {slice.value!r}")
        return parse_products(data, page, slice.value)


def parse_products(data: dict, page: int = 1, category: str = "") -> PageResult:
    found = (data.get("response") or {}).get("productsList") or {}
    products = found.get("products") or []
    keep, skip = KEEP.get(category), SKIP.get(category, ())
    listings = [_listing(p) for p in products if (keep is None or _type(p) in keep) and _type(p) not in skip]
    return PageResult(listings=listings, has_next=bool(products) and page < (found.get("totalPages") or 0),
                      total=found.get("recordsTotal") or 0)


def _type(product: dict) -> str | None:
    return (product.get("type") or {}).get("code")


def _listing(p: dict) -> Listing:
    name = ((p.get("description") or {}).get("name") or "").strip()
    price = _piastres(p.get("finalPrice"))
    before = _piastres(p.get("originalPrice"))
    claim = before if price and before and before > price else None
    left = p.get("quantity") or 0
    return Listing(
        site=EandEG.name,
        sku=str(p["id"]),
        title=name,
        url=f"{BASE}/eshop2/#/product/{quote(name.replace(' ', '-'))}?pid={p['id']}",
        price=price,
        list_price=claim,
        currency="EGP",
        in_stock=bool(p.get("canBePurchased")) and left > 0 and price is not None,
        list_price_kind="list" if claim else None,
        brand=(((p.get("manufacturer") or {}).get("description") or {}).get("name") or "").strip() or None,
        stock_left=left if 0 < left <= LOW_STOCK else None,
        seller=((p.get("store") or {}).get("name") or "").strip() or None,
    )


def _piastres(value) -> int | None:
    """Text like '12,999' (the API has no other form of the crossed-out price)."""
    text = str(value if value is not None else "").replace(",", "").strip()
    return round(float(text) * 100) if text and float(text) > 0 else None
