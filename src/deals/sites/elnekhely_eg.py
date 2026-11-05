"""elnekhelytechnology.com (Elnekhely Technology) catalog: PC parts, from OpenCart's category pages.

`/<category>?limit=100&page=N`: 100 cards a page, "Showing 1 to 100 of 118 (2 Pages)" with the exact count. Sold-out products
stay listed, with `out-of-stock` on the card. An unknown category answers 404, a page past the end 200 with no cards and no
count. No price filter, so a slice is always a whole category; the catalogue is small (838 products on 5 Nov 2026).

Cloudflare in front: a home connection passes with Chrome's fingerprint, the droplet gets the challenge (HTTP 403,
`cf-mitigated: challenge`). A page is about 1.7 MB, nearly all of it inline CSS. docs/ELNEKHELY.md.
"""

import re

from curl_cffi import requests
from selectolax.lexbor import LexborHTMLParser

from deals.models import Blocked, Listing, PageResult, Slice, UnknownTarget
from deals.sites.base import parse_money

BASE = "https://www.elnekhelytechnology.com"
PER_PAGE = 100
IMPERSONATE = "chrome"
_SHOWING = re.compile(r"Showing \d+ to \d+ of (\d+) \((\d+) Pages?\)")


class ElnekhelyEG:
    name = "elnekhely_eg"
    max_slice_total = 100_000  # no price filter: the planner never splits

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind != "category":
            raise UnknownTarget(f"{self.name}: use category:<slug>")
        url = f"{BASE}/{slice.value.strip('/')}?limit={PER_PAGE}" + (f"&page={page}" if page > 1 else "")
        r = self._session.get(url, timeout=30)
        if r.status_code == 404:
            raise UnknownTarget(f"{self.name}: no category {slice.value!r} (HTTP 404; {BASE}/sitemap-category.xml)")
        if r.status_code in (403, 429, 503):
            raise Blocked(f"{self.name}: HTTP {r.status_code} from {url}")
        r.raise_for_status()
        return parse_listing(r.text, page)


def parse_listing(page_html: str, page: int = 1) -> PageResult:
    if "catalog/view" not in page_html:
        raise ValueError(f"{ElnekhelyEG.name}: no product data in the page (layout changed?)")
    found = _SHOWING.search(page_html)
    total, pages = (int(found[1]), int(found[2])) if found else (0, 0)
    tree = LexborHTMLParser(page_html)
    listings = {x.sku: x for x in filter(None, map(_parse_card, tree.css("div.product-layout")))}
    return PageResult(listings=list(listings.values()), has_next=page < pages, total=total if page == 1 else None)


def _parse_card(card) -> Listing | None:
    sku, name = card.attributes.get("data-product-id"), card.css_first(".name a")
    if not sku or name is None:
        return None
    new, old, normal = (card.css_first(f".price {x}") for x in (".price-new", ".price-old", ".price-normal"))
    price = _money(new or normal)
    was = _money(old)
    list_price = was if was and price and was > price else None
    brand = next((a.text(strip=True) for a in card.css(".stats a")), None)
    return Listing(
        site=ElnekhelyEG.name,
        sku=sku,
        title=name.text(strip=True),
        url=(name.attributes.get("href") or "").split("?")[0],
        price=price,
        list_price=list_price,
        currency="EGP",
        in_stock="out-of-stock" not in (card.attributes.get("class") or "") and price is not None,
        list_price_kind="list" if list_price else None,
        brand=brand or None,
    )


def _money(node) -> int | None:
    if node is None:
        return None
    digits = parse_money(node.text(strip=True).replace("L.E", "").strip())
    return digits or None
