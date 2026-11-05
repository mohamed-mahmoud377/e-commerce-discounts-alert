"""lcwaikiki.eg (LC Waikiki Egypt) catalog, from the JSON embedded in its category pages.

An ASP.NET and React shop behind Cloudflare. A category page (`/en/women-clothing-t-10`) holds
`var catalogModel = {...}`: 100 products a page (a few pinned ones repeat on every page), the exact
`ItemCount` and `PageCount`, `?PageIndex=N`. Past the last page it answers page 1 again, so paging stops on
`PageCount`. Its AJAX endpoint ignores the category and the page: don't use it.

A renamed category answers a 301 to its new name and **the redirect drops the query**, so `PageIndex` and `MinPrice`
are lost and every page is page 1 of the whole category. The menu still hands out old names (`femei-incaltaminte-t-33`
is `women-shoes-t-33`), so a redirect is asked again under the new name, once per worker run.

`MinPrice` narrows the count but **`MaxPrice` doesn't hold** (asking for 300 to 500 returns products at 1,299),
so the group's minimum is passed and the planner never splits (`max_slice_total` above any category). An empty
result isn't empty: `ProductFound` is false and the page fills up with 52 unrelated products, which must not
be read. An unknown category is an HTTP 404.

Each colour is a product (`OptionId`) with one price. A sale price is not in `DiscountedPriceValue` (always 0): it
is `CampaignBadges[0].DiscountedPrice` when `ShowDiscountedPrice` is set, on half of the catalogue. `PriceValue`
is then the crossed-out price. Sold-out colours show `AvailableStock` 0.

The droplet gets HTTP 403 whatever it sends: home connections only. docs/LCWAIKIKI.md.
"""

import json
from dataclasses import replace
from urllib.parse import urlencode, urlsplit

from curl_cffi import requests

from deals.models import Blocked, Listing, PageResult, Slice, UnknownTarget

BASE = "https://www.lcwaikiki.eg"
IMPERSONATE = "chrome"
LOW_STOCK = 5  # the shop's own "last items" threshold (OptionLastXItems is 5 on every product)
_MODEL = "var catalogModel = "
_DECODER = json.JSONDecoder()


class LcWaikikiEG:
    name = "lcwaikiki_eg"
    max_slice_total = 20_000  # bigger than any category: `MaxPrice` doesn't hold, so the planner must never split

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)
        self._renamed: dict[str, str] = {}  # category name in the config -> the name it redirects to

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind != "category":
            raise UnknownTarget(f"{self.name}: only category:<page name>, e.g. category:women-clothing-t-10")
        r = self._get(slice, page)
        if r.status_code == 200 and urlsplit(str(r.url)).path != _path(self._current(slice)):
            self._renamed[slice.value] = urlsplit(str(r.url)).path.removeprefix("/en/")
            r = self._get(slice, page)
        if r.status_code == 404:
            raise UnknownTarget(f"{self.name}: no category {slice.value!r} (HTTP 404)")
        if r.status_code in (403, 429, 503):
            raise Blocked(f"{self.name}: HTTP {r.status_code} (Cloudflare)")
        r.raise_for_status()
        return parse_listing(r.text, page)

    def _get(self, slice: Slice, page: int):
        return self._session.get(page_url(self._current(slice), page), timeout=60)

    def _current(self, slice: Slice) -> Slice:
        return replace(slice, value=self._renamed.get(slice.value, slice.value))


def _path(slice: Slice) -> str:
    return f"/en/{slice.value.strip('/')}"


def page_url(slice: Slice, page: int) -> str:
    params = []
    if slice.price_min is not None:
        params.append(("MinPrice", slice.price_min))
    if page > 1:
        params.append(("PageIndex", page))
    return BASE + _path(slice) + (f"?{urlencode(params)}" if params else "")


def parse_listing(text: str, page: int = 1) -> PageResult:
    start = text.find(_MODEL)
    if start < 0:
        raise ValueError("lcwaikiki_eg: no catalogModel in the page (layout changed?)")
    model, _ = _DECODER.raw_decode(text, start + len(_MODEL))
    if not model.get("ProductFound"):  # the 52 products it shows then belong to nothing
        return PageResult(listings=[], has_next=False, total=0)
    catalog = model.get("CatalogList") or {}
    listings: dict[str, Listing] = {}
    for item in catalog.get("Items") or []:
        listing = _parse_item(item)
        if listing:
            listings.setdefault(listing.sku, listing)
    return PageResult(listings=list(listings.values()), total=catalog.get("ItemCount"),
                      has_next=page < (catalog.get("PageCount") or 0))  # the page asked for: an answer can say 1 forever


def _parse_item(p: dict) -> Listing | None:
    if not p.get("OptionId") or not p.get("ModelUrl"):
        return None
    regular = _minor(p.get("PriceValue"))
    sale = next((_minor(b.get("DiscountedPrice")) for b in p.get("CampaignBadges") or []
                 if b.get("ShowDiscountedPrice") and b.get("DiscountedPrice")), None)
    price = sale if sale and regular and sale < regular else regular
    list_price = regular if regular and price and regular > price else None
    stock = p.get("AvailableStock")
    return Listing(
        site=LcWaikikiEG.name,
        sku=str(p["OptionId"]),
        title=(p.get("ProductDescription") or "").strip(),
        url=BASE + p["ModelUrl"],
        price=price,
        list_price=list_price,
        currency="EGP",
        in_stock=bool(stock) and price is not None,
        list_price_kind="list" if list_price else None,
        brand=(p.get("Brand") or "").strip() or None,
        stock_left=stock if stock is not None and stock <= LOW_STOCK else None,
        rating=p.get("AvgScore") or None,
        reviews=p.get("ReviewCount") or None,
    )


def _minor(value) -> int | None:
    return round(float(value) * 100) if value not in (None, "") else None
