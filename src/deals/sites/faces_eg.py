"""faces.eg (Faces, also Wojooh) catalog, from the grid its Load More button asks for.

Salesforce Commerce Cloud (SFRA) behind Cloudflare. A category's own page (`/en/perfume`) holds only its first 48
products, about 1.5 MB, and ignores `start` and `page`, so the listing comes from `Search-UpdateGrid`: 48 products a
request at most (`sz=200` still gives 48), about 850 KB. **It answers HTTP 500 without `X-Requested-With`.**
`robots.txt` disallows this address, and the page writes it in a Caesar cipher: the user chose to crawl it anyway, slowly
(docs/FACES.md, "robots.txt"). If it starts refusing us, switch the shop off rather than fight it.

The count is the footer's "560 items". Past the end it answers no products, or, just past it, repeats of other
products under a bigger count (asking start=576 of 560 gave 39 under "615 items"), so paging stops on the count and
never asks past it. An unknown category is an HTTP 200 with no `.category-id` in the footer.

The price filter can't split a category: `pmin` alone is ignored, and `pmin` with `pmax` keeps a product if any of its
sizes is in the range, so the tile's price can be outside it. `max_slice_total` keeps the planner from trying.

**One tile, several sizes.** Where sizes cost differently the tile says "from" with the cheapest size's price and no
crossed-out price, while the analytics JSON gives another size's list price: 14,230 less a "discount" that lands on
6,760 for Sauvage, which is no discount. So prices come only from the price block, and those titles say so.
"""

import re
from dataclasses import replace
from urllib.parse import urlencode

from curl_cffi import requests
from selectolax.lexbor import LexborHTMLParser

from deals.models import BESTSELLER, Blocked, Listing, PageResult, Slice, UnknownTarget, valid_gtin

BASE = "https://www.faces.eg"
GRID = f"{BASE}/on/demandware.store/Sites-Faces_EG-Site/en_EG/Search-UpdateGrid"
IMPERSONATE = "chrome"
PAGE_SIZE = 48
CHEAPEST_SIZE = "(cheapest size)"
_TOTAL = re.compile(r"(\d[\d,]*)\s*items?")


class FacesEG:
    name = "faces_eg"
    max_slice_total = 20_000  # bigger than any category: its price filter can't split one (see above)

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind not in ("category", "deals"):
            raise UnknownTarget(f"{self.name}: only category:<id> or deals:<id>, e.g. category:perfume")
        r = self._session.get(page_url(slice, page), headers={"X-Requested-With": "XMLHttpRequest"}, timeout=60)
        if r.status_code in (403, 429, 503) or "Just a moment" in r.text[:2000]:
            raise Blocked(f"{self.name}: HTTP {r.status_code} (Cloudflare)")
        r.raise_for_status()
        result = parse_grid(r.text, page)
        if slice.kind == "deals":  # a fast lane reads the sale's first page and no further
            result = replace(result, has_next=False, total=len(result.listings))
        if slice.kind == "category" and page == 1 and not result.listings and 'class="category-id"' not in r.text:
            raise UnknownTarget(f"{self.name}: no category {slice.value!r}")
        return result


def page_url(slice: Slice, page: int) -> str:
    return f"{GRID}?{urlencode({'cgid': slice.value, 'start': (page - 1) * PAGE_SIZE, 'sz': PAGE_SIZE})}"


def parse_grid(body: str, page: int = 1) -> PageResult:
    tree = LexborHTMLParser(body)
    tiles = tree.css(".js-product-tile-container")
    if not tiles:
        return PageResult(listings=[], has_next=False, total=0)
    count = tree.css_first(".product-count .total")
    match = _TOTAL.search(count.text()) if count else None
    if not match:
        raise ValueError("faces_eg: no item count in the grid (layout changed?)")
    total = int(match.group(1).replace(",", ""))
    listings: dict[str, Listing] = {}
    for tile in tiles:
        listing = _parse_tile(tile)
        if listing:
            listings.setdefault(listing.sku, listing)
    if not listings:
        raise ValueError("faces_eg: no product data in the grid (layout changed?)")
    return PageResult(listings=list(listings.values()), total=total, has_next=page * PAGE_SIZE < total)


def _parse_tile(tile) -> Listing | None:
    sku = tile.attributes.get("data-pid")
    link = tile.css_first("a.product-tile-name-link")
    if not sku or not link or not link.attributes.get("href"):
        return None
    block = tile.css_first(".product-tile-pricing")
    sold_out = block is None or block.css_first(".out-of-stock-message") is not None
    several = block is not None and block.css_first(".js-price-range") is not None
    price, list_price = (None, None) if sold_out else _prices(block)
    brand = _text(tile.css_first(".product-tile-brand"))
    size = _text(tile.css_first(".selected-size"))
    name = _text(link)
    title = name if name.casefold().startswith(brand.casefold()) else f"{brand} {name}".strip()
    if several:
        title = f"{title} {CHEAPEST_SIZE}"
    elif size and not tile.css_first(".sizes-left"):
        title = f"{title} {size}"
    inner = tile.css_first(".js-product-tile")
    gtin = valid_gtin((inner.attributes.get("data-ean") or "") if inner else "")
    badges = {b.attributes.get("data-badge-value") for b in tile.css("[data-badge-value]")}
    return Listing(
        site=FacesEG.name,
        sku=sku,
        title=title,
        url=BASE + link.attributes["href"],
        price=price,
        list_price=list_price,
        currency="EGP",
        in_stock=not sold_out and price is not None,
        list_price_kind="list" if list_price else None,
        brand=brand or None,
        badges=(BESTSELLER,) if "bestseller" in badges else (),
        gtins=(gtin,) if gtin else (),
    )


def _prices(block) -> tuple[int | None, int | None]:
    """(price, crossed-out price) from the tile's price block. A product with no discount can still carry an empty
    crossed-out block (`content="null"`), ahead of its real price."""
    price = _minor(block.css_first(".js-price-color .value"))
    regular = _minor(block.css_first(".strike-through .value"))
    return price, regular if regular and price and regular > price else None


def _minor(node) -> int | None:
    value = node.attributes.get("content") if node else None
    try:
        return round(float(value) * 100) or None
    except (TypeError, ValueError):
        return None


def _text(node) -> str:
    return " ".join(node.text().split()) if node else ""
