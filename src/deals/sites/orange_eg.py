"""eshop.orange.eg (Orange Egypt e-shop) catalog, through its catalog API.

An Angular storefront on nopCommerce. `GET /api/CatalogAPI/Products/GetFilterProducts?CategoryId=..` answers
JSON: `PageSize` up to at least 100, the exact `TotalCount` and `TotalPages`. Reading a category needs its
number, from `Categories/GetCategoryDetails?seName=<url name>` (HTTP 400 for an unknown one); slices use the
readable url name (`category:smart-phones`). It has no price filter we could find, and the store has under
1,000 products, so a slice is always a whole category. A page past the end, or an empty category, answers
HTTP 400 "Not Found".

The API wants a guest token from `POST Auth/token` (5 hours). **It closes the connection on `curl_cffi`
whatever it impersonates**, from the Mac and the droplet alike, while the same URL answers plain curl, Python's
urllib and a browser, so this adapter uses urllib. Sold-out products stay listed (`IsAvailableForAddToCart`
false) with their price. A hundred products are about 1 MB.

One product can come in several storage sizes at different prices, and the listing shows only the cheapest, while
the page opens on any of them (53 of 98 phones). Those roots aren't watched: one key would hold several prices.
docs/ORANGE.md.
"""

import json
import time
import urllib.error
import urllib.request
from urllib.parse import quote

from deals.models import Blocked, Listing, PageResult, Slice, UnknownTarget

BASE = "https://eshop.orange.eg"
API = f"{BASE}/api/CatalogAPI"
PER_PAGE = 100
TOKEN_LIFE_S = 4 * 3600  # it lasts 5 hours
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")


class OrangeEG:
    name = "orange_eg"
    max_slice_total = 10_000  # bigger than the whole store: the planner never splits (no price filter)

    def __init__(self, state_dir=None):
        self._token: str | None = None
        self._token_at = 0.0
        self._ids: dict[str, int] = {}

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind != "category":
            raise UnknownTarget(f"{self.name}: only category:<url name> slices, e.g. category:smart-phones")
        category = self._category_id(slice.value)
        status, body = self._call(f"/Products/GetFilterProducts?CategoryId={category}&ClearFilters=true"
                                  f"&PageNumber={page}&PageSize={PER_PAGE}")
        if status == 400:  # an empty category, or a page past the end
            return PageResult(listings=[], has_next=False, total=0 if page == 1 else None)
        if status != 200:
            raise RuntimeError(f"{self.name}: HTTP {status} from the product list")
        return parse_products(body, page)

    def _category_id(self, name: str) -> int:
        if name not in self._ids:
            status, body = self._call(f"/Categories/GetCategoryDetails?seName={quote(name)}")
            data = (body or {}).get("Data") if status == 200 else None
            if not data:
                raise UnknownTarget(f"{self.name}: no category named {name!r}")
            self._ids[name] = data["Id"]
        return self._ids[name]

    def _call(self, path: str) -> tuple[int, dict | None]:
        if self._token is None or time.time() - self._token_at > TOKEN_LIFE_S:
            self._new_token()
        status, body = self._http(path, {"authorization": f"Bearer {self._token}"})
        if status == 401:
            self._new_token()
            status, body = self._http(path, {"authorization": f"Bearer {self._token}"})
        return status, body

    def _new_token(self) -> None:
        status, body = self._http("/Auth/token", {"content-type": "application/json"}, data=b"{}")
        token = ((body or {}).get("Data") or {}).get("AccessToken")
        if status != 200 or not token:
            raise Blocked(f"{self.name}: HTTP {status} asking for a guest token")
        self._token, self._token_at = token, time.time()

    def _http(self, path: str, headers: dict, data: bytes | None = None) -> tuple[int, dict | None]:
        request = urllib.request.Request(API + path, data=data,
                                         headers={"accept": "application/json", "user-agent": USER_AGENT, **headers})
        try:
            with urllib.request.urlopen(request, timeout=60) as answer:
                status, text = answer.status, answer.read().decode()
                kind = answer.headers.get("content-type", "")
        except urllib.error.HTTPError as e:
            status, text, kind = e.code, e.read().decode(errors="replace"), e.headers.get("content-type", "")
        if status in (403, 429) or (status == 200 and "json" not in kind):
            raise Blocked(f"{self.name}: HTTP {status} from {path}")
        return status, json.loads(text) if "json" in kind and text.strip() else None


def parse_products(body: dict, page: int) -> PageResult:
    data = body.get("Data") or {}
    listings: dict[str, Listing] = {}
    for item in data.get("Products") or []:
        listing = _parse_item(item)
        listings.setdefault(listing.sku, listing)
    return PageResult(listings=list(listings.values()), has_next=page < (data.get("TotalPages") or 0),
                      total=data.get("TotalCount"))


def _parse_item(p: dict) -> Listing:
    cash = p.get("CashPrice") or {}
    price = _minor((cash.get("Price") or {}).get("Amount"))
    old = _minor((cash.get("OldPrice") or {}).get("Amount"))
    list_price = old if old and price and old > price else None
    crumbs = [c.get("SeName") for c in p.get("Breadcrumb") or []][:-1]
    path = "/s/".join(c for c in crumbs if c)  # /accessories/s/headphones-earbuds/p/<name>
    return Listing(
        site=OrangeEG.name,
        sku=str(p["Id"]),
        title=(p.get("Name") or "").strip(),
        url=f"{BASE}/en/{path + '/' if path else ''}p/{p.get('SEName', '')}",
        price=price,
        list_price=list_price,
        currency="EGP",
        in_stock=bool(p.get("IsAvailableForAddToCart")) and price is not None,
        list_price_kind="list" if list_price else None,
    )


def _minor(value) -> int | None:
    return round(float(value) * 100) if value not in (None, "") else None
