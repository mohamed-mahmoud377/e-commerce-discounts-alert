"""defacto.com.eg (DeFacto Egypt) catalog, from the JSON on each product card of its category pages.

ASP.NET pages behind Akamai (plain curl gets HTTP 403, Chrome's fingerprint passes, from the droplet too).
A category is `/en-eg/woman-dress`, or a collection like `/en-eg/woman-discount`; each product card has a
`data-documents='{...}'` attribute and the page a hidden `totalCount`. **24 to 36 cards a page, whatever the count**,
so paging ends on a page with no cards (an extra request per slice).

`?filter=price:<low>-<high>` filters the current price, inclusive at both ends, and needs both: `price:500-` finds
nothing. `&page=N` from page 2 only: `filter=...&page=1` also finds nothing. An unknown category is an HTTP 404, and
an empty range a page with `totalCount` 0.

Each colour is a product (`ProductVariantIndex`). The price is `CampaignDiscountedPrice` ("November - Net %20
Discount") when there is one, else `DiscountedPrice`; `Price` is the crossed-out price. A few cards are not products
("kargobedava", free shipping, no price): skipped. Sold-out colours show `Stock` 0 where they are listed.
docs/DEFACTO.md.
"""

import html
import json
import re

from curl_cffi import requests

from deals.models import Blocked, Listing, PageResult, Slice, UnknownTarget

BASE = "https://www.defacto.com.eg"
IMPERSONATE = "chrome"
HIGHEST = 99_999  # the filter has no open end
_CARD = re.compile(r"data-documents='([^']*)'")
_TOTAL = re.compile(r'id=["\']?totalCount["\']?[^>]*?value=["\']?(\d+)')
_TOTAL_ALT = re.compile(r'totalCount type=hidden value=(\d+)')


class DeFactoEG:
    name = "defacto_eg"
    max_slice_total = 1000  # about 40 pages; the price filter is exact, so bigger slices are split

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind != "category":
            raise UnknownTarget(f"{self.name}: only category:<page name>, e.g. category:woman-dress")
        r = self._session.get(page_url(slice, page), timeout=60)
        if r.status_code == 404:
            raise UnknownTarget(f"{self.name}: no category {slice.value!r} (HTTP 404)")
        if r.status_code in (403, 429, 503):
            raise Blocked(f"{self.name}: HTTP {r.status_code} (Akamai)")
        r.raise_for_status()
        return parse_listing(r.text)


def page_url(slice: Slice, page: int) -> str:
    parts = []
    if slice.has_price:
        parts.append(f"filter=price:{slice.price_min or 0}-{slice.price_max or HIGHEST}")
    if page > 1:  # `page=1` next to a filter finds nothing
        parts.append(f"page={page}")
    return f"{BASE}/en-eg/{slice.value.strip('/')}" + (f"?{'&'.join(parts)}" if parts else "")


def parse_listing(text: str) -> PageResult:
    found = _TOTAL.search(text) or _TOTAL_ALT.search(text)
    total = int(found.group(1)) if found else None
    cards = _CARD.findall(text)
    if total is None and not cards and "product-card" not in text and "catalog" not in text.lower():
        raise ValueError("defacto_eg: no product cards and no count in the page (layout changed?)")
    listings: dict[str, Listing] = {}
    for card in cards:
        listing = _parse_card(json.loads(html.unescape(card)))
        if listing:
            listings.setdefault(listing.sku, listing)
    return PageResult(listings=list(listings.values()), has_next=bool(cards) and total != 0, total=total)


def _parse_card(p: dict) -> Listing | None:
    regular = _minor(p.get("Price"))
    if regular is None or not p.get("ProductVariantIndex") or not p.get("SeoName"):  # a promotional tile
        return None
    price = _minor(p.get("CampaignDiscountedPrice")) or _minor(p.get("DiscountedPrice")) or regular
    list_price = regular if regular > price else None
    stock = p.get("Stock")
    return Listing(
        site=DeFactoEG.name,
        sku=str(p["ProductVariantIndex"]),
        title=(p.get("Name") or "").strip(),
        url=f"{BASE}/en-eg/{p['SeoName']}-{p['ProductVariantIndex']}",
        price=price,
        list_price=list_price,
        currency="EGP",
        in_stock=bool(stock),
        list_price_kind="list" if list_price else None,
        brand="DeFacto",
        stock_left=stock if stock is not None and stock <= 5 else None,
    )


def _minor(value) -> int | None:
    return round(float(value) * 100) if value not in (None, "") else None
