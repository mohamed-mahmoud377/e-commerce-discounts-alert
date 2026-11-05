"""raneen.com (Raneen) catalog, from its category pages.

Magento (Hyvä): 20 products a page, about 1.2 MB. **Refuses the droplet (HTTP 403), and its GraphQL API answers 403 "not
available to this client" from anywhere.** Its `robots.txt` asks for `Crawl-delay: 10` and disallows every query string except
`?p=N`, so there is no price filter, sorting or page size here even though Magento has them: we keep to those rules. That
makes it slow, so only chosen leaf categories are watched (docs/RANEEN.md).

The count is in `.toolbar-amount` ("Showing 1 - 20 of 83 products"). Page 1 can carry a 21st card, repeated on page 2. Past the
end: an HTTP 200 with no cards. An unknown category is an HTTP 404. `-conf` products are configurable: see the doc for what
was counted about their prices.
"""

import re

from curl_cffi import requests
from selectolax.lexbor import LexborHTMLParser

from deals.models import Blocked, Listing, PageResult, Slice, UnknownTarget

BASE = "https://www.raneen.com"
IMPERSONATE = "chrome"
PAGE_SIZE = 20
_OF = re.compile(r"of\s*([\d,]+)\s*product", re.I)
_ONLY = re.compile(r"([\d,]+)\s*product", re.I)


class RaneenEG:
    name = "raneen_eg"
    max_slice_total = 100_000  # bigger than any category: robots.txt rules out its price filter, so nothing can split

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind != "category":
            raise UnknownTarget(f"{self.name}: use category:<path>, e.g. category:appliances/large-appliances/air-conditioners")
        r = self._session.get(page_url(slice, page), timeout=60)
        if r.status_code == 404:
            raise UnknownTarget(f"{self.name}: no category {slice.value!r} (HTTP 404)")
        if r.status_code in (403, 429, 503):
            raise Blocked(f"{self.name}: HTTP {r.status_code}")
        r.raise_for_status()
        return parse_listing(r.text, page)


def page_url(slice: Slice, page: int) -> str:
    """No price in the address: robots.txt allows only ?p=N."""
    return f"{BASE}/en/{slice.value.strip('/')}" + (f"?p={page}" if page > 1 else "")


def parse_listing(body: str, page: int = 1) -> PageResult:
    tree = LexborHTMLParser(body)
    cards = tree.css(".products-grid article.product-item")
    amount = tree.css_first(".toolbar-amount")
    text = amount.text(strip=True) if amount else ""
    found = _OF.search(text) or _ONLY.search(text)
    if not cards:
        if page == 1 and not tree.css_first(".message.empty, .message.info.empty"):
            raise ValueError("raneen_eg: no product cards in the page (layout changed?)")
        return PageResult(listings=[], has_next=False, total=0 if page == 1 else None)
    if not found:
        raise ValueError("raneen_eg: no product count in the page (layout changed?)")
    total = int(found.group(1).replace(",", ""))
    listings: dict[str, Listing] = {}
    for card in cards:
        listing = _listing(card)
        listings.setdefault(listing.sku, listing)
    return PageResult(listings=list(listings.values()), has_next=page * PAGE_SIZE < total, total=total)


def _listing(card) -> Listing:
    link = card.css_first(".product-item-link")
    price = _amount(card, "finalPrice")
    old = _amount(card, "oldPrice")
    claim = old if price and old and old > price else None
    left = card.css_first("[data-rn-stock-left]")
    brand = card.css_first(".rn-card__brand")
    return Listing(
        site=RaneenEG.name,
        sku=card.attributes["data-sku"],
        title=link.text(strip=True),
        url=link.attributes["href"],
        price=price,
        list_price=claim,
        currency="EGP",
        in_stock=price is not None and card.css_first(".stock.unavailable") is None,
        list_price_kind="list" if claim else None,
        brand=brand.text(strip=True) if brand else None,
        stock_left=int(left.attributes["data-rn-stock-left"]) if left else None,
    )


def _amount(card, kind: str) -> int | None:
    node = card.css_first(f'[data-price-type="{kind}"]')
    value = node.attributes.get("data-price-amount") if node else None
    return round(float(value) * 100) if value else None
