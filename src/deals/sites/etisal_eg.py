"""etisalstore.com (Etisal Store) catalog, through its customer API.

An Angular storefront on the SuperCommerce platform. `GET /api/customer/products/search` answers JSON:
10 products a page (fixed), the exact count (`total_products`, with `total` the page count), newest
first, which doesn't shift when prices change. `category_id=<id>` is a top category,
`sub_categories[]=<id>` a sub-category (keyed `category:s<id>` here), `price_from`/`price_to` filter the
current price, inclusive at both ends. Empty ranges and unknown ids both answer an empty 200.

The API wants a guest token: `/api/customer/home/initialize` returns one that lasts for ever (`exp` is
about the year 3167) and creates a guest customer at the shop, so a session asks for one only once.

Only products in stock are listed: one that sells out disappears. Each colour and size is its own product
with its own SKU, price and link; the SKU is often the barcode. Almost everything shows a crossed-out
price (95%, its "discount" ends 31 Dec 2027), kind `list`. Answers from the droplet too. docs/ETISAL.md.
"""

import re
from urllib.parse import urlencode

from curl_cffi import requests

from deals.models import Blocked, Listing, PageResult, Slice, UnknownTarget, valid_gtin

BASE = "https://etisalstore.com"
API = f"{BASE}/api/customer"
PRODUCT_URL = f"{BASE}/en/{{slug}}"
IMPERSONATE = "chrome"
# lang 1 is English; the rest is what the site sends
HEADERS = {"accept": "application/json, text/plain, */*", "lang": "1", "device-type": "web", "device": "3",
           "x-admin-preview": "0", "web-url": f"{BASE}/en"}
_TOKEN = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")


class EtisalEG:
    name = "etisal_eg"
    max_slice_total = 500  # 50 requests at 10 products a page; the filter is exact, so bigger slices are split

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)
        self._token: str | None = None
        self._roots: set[str] | None = None

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind == "deals":
            raise UnknownTarget(f"{self.name}: no deals feed; use category:<id> or category:s<sub-category id>")
        if slice.kind == "category":
            self._check_category(slice.value)
        return parse_products(self._get_json(f"{API}/products/search?{query(slice, page)}"), page)

    def _check_category(self, value: str) -> None:
        """Unknown ids list nothing with HTTP 200, like an empty range; only top categories can be checked."""
        if not re.fullmatch(r"s?\d+", value):
            raise UnknownTarget(f"{self.name}: category ids are numbers or s<number>, got {value!r}")
        if value.startswith("s"):
            return
        if self._roots is None:
            self._roots = {str(c["id"]) for c in self._get_json(f"{API}/home/categories")["data"]}
        if value not in self._roots:
            raise UnknownTarget(f"{self.name}: no category with id {value}")

    def _get_json(self, url: str) -> dict:
        r = self._session.get(url, headers=self._headers(), timeout=60)
        if r.status_code == 401:  # the token was refused: ask once for a new one
            self._token = None
            r = self._session.get(url, headers=self._headers(), timeout=60)
        if r.status_code in (403, 429) or (r.status_code == 200 and "json" not in r.headers.get("content-type", "")):
            raise Blocked(f"{self.name}: HTTP {r.status_code} from {url}")
        r.raise_for_status()
        body = r.json()
        if "data" not in body:
            raise ValueError(f"{self.name}: no data in the answer {r.text[:200]} (API changed?)")
        return body

    def _headers(self) -> dict:
        if self._token is None:
            r = self._session.get(f"{API}/home/initialize?disable_cart=1&disable_category=1",
                                  headers=HEADERS, timeout=40)
            if r.status_code in (403, 429):
                raise Blocked(f"{self.name}: HTTP {r.status_code} asking for a guest token")
            r.raise_for_status()
            found = _TOKEN.search(r.text)
            if not found:
                raise ValueError(f"{self.name}: no guest token in the answer (API changed?)")
            self._token = found.group()
        return {**HEADERS, "authorization": f"Bearer {self._token}"}


def query(slice: Slice, page: int) -> str:
    params = [("page", page)]
    if slice.kind == "search":
        params.append(("q", slice.value))
    elif slice.value.startswith("s"):
        params.append(("sub_categories[]", slice.value[1:]))
    else:
        params.append(("category_id", slice.value))
    if slice.price_min is not None:
        params.append(("price_from", slice.price_min))
    if slice.price_max is not None:
        params.append(("price_to", slice.price_max))
    return urlencode(params)


def parse_products(body: dict, page: int) -> PageResult:
    data = body["data"]
    listings: dict[str, Listing] = {}
    for item in data.get("products") or []:
        listing = _parse_item(item)
        listings.setdefault(listing.sku, listing)
    return PageResult(listings=list(listings.values()), has_next=page < (data.get("total") or 0),
                      total=data.get("total_products"))


def _parse_item(p: dict) -> Listing:
    regular = _minor(p.get("price"))
    discounted = _minor(p.get("discount_price"))
    price = discounted if discounted else regular
    list_price = regular if discounted and regular and regular > discounted else None
    gtin = valid_gtin(p.get("sku"))
    return Listing(
        site=EtisalEG.name,
        sku=str(p["id"]),
        title=(p.get("name_en") or p.get("name") or "").strip(),
        url=PRODUCT_URL.format(slug=p.get("slug", "")),
        price=price,
        list_price=list_price,
        currency="EGP",
        in_stock=bool(p.get("in_stock")) and price is not None,
        list_price_kind="list" if list_price else None,
        brand=((p.get("brand") or {}).get("name") or "").strip() or None,
        stock_left=p.get("stock"),
        gtins=(gtin,) if gtin else (),
    )


def _minor(value) -> int | None:
    return round(float(value) * 100) if value not in (None, "") else None
