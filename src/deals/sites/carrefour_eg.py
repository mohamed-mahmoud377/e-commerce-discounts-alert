"""carrefouregypt.com (Carrefour Egypt, run by Majid Al Futtaim) listing pages.

A Next.js storefront behind Akamai Bot Manager: a browser TLS fingerprint (curl_cffi)
passes from home and from the droplet alike, plain `curl` is cut off at the HTTP/2
layer. The JSON listing API the browser calls (`/mafrp/api/v1/search/listing/...`) is
not reachable from outside, but category pages (`/mafegy/en/c/<id>`) server-render the
same answer into the React Server Components payload: 60 product cards, an exact
`totalResults`, the category's children with counts and the breadcrumbs.

The page takes `sortBy=price_asc`, `currentPage` (0-based), and `minPrice`/`maxPrice`
(inclusive at both ends); the page size is fixed at 60. Sorted by relevance a slice
repeats some products and misses others across pages, sorted by price it pages
cleanly. Pages past `currentPage=20` repeat page 20, so at most 1,260 products of a
query can be read. An unknown category id and an empty price range both answer HTTP
200 with an empty listing, so an empty category slice is checked against the bare
category once. Out-of-stock products are not listed. Carrefour is also a marketplace:
a third of the tracked products are third-party offers (one card per product, the buy-box
offer).
"""

import json
import re
from urllib.parse import urlencode

from curl_cffi import requests

from deals.models import Blocked, Category, LIMITED_DEAL, Listing, PageResult, Slice, UnknownTarget
from deals.sites.btech_eg import _balanced, _rsc_payload

BASE = "https://www.carrefouregypt.com"
STORE = f"{BASE}/mafegy/en"
PER_PAGE = 60  # fixed by the shop (pageSize is ignored)
MAX_PAGES = 21  # currentPage 0..20; later pages repeat page 20
IMPERSONATE = "chrome"
FIRST_PARTY = "Carrefour"
_INVISIBLE = "‎‏﻿"  # direction marks some titles start with


class CarrefourEG:
    name = "carrefour_eg"
    max_slice_total = 1200  # 20 pages of 60, under the 1,260 the shop pages through

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)
        self._known: set[str] = set()  # category ids seen with products

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if page > MAX_PAGES:
            return PageResult(listings=[], has_next=False)
        result = parse_listing_page(self._get(page_url(slice, page)), page)
        if slice.kind != "category":
            return result
        category = slice.value.strip("/")
        if result.listings:
            self._known.add(category)
        elif page == 1 and category not in self._known:
            # An unknown id looks exactly like an empty price range: ask for the whole category.
            bare = result if not slice.has_price else parse_listing_page(
                self._get(page_url(Slice("category", category), 1)), 1)
            if not bare.listings:
                raise UnknownTarget(f"{self.name}: no products in category {category!r} "
                                    f"(unknown id? ids are case-sensitive, e.g. NFEGY4080000)")
            self._known.add(category)
        return result

    def _get(self, url: str) -> str:
        r = self._session.get(url, timeout=30)
        if r.status_code in (403, 429, 503):
            # Akamai's "Access Denied" page; one fresh session (new cookies), then give up.
            self._session = requests.Session(impersonate=IMPERSONATE)
            r = self._session.get(url, timeout=30)
            if r.status_code in (403, 429, 503):
                raise Blocked(f"{self.name}: HTTP {r.status_code} from {url}")
        r.raise_for_status()
        return r.text


def page_url(slice: Slice, page: int) -> str:
    """One listing page, sorted by price so that paging is stable."""
    params: dict[str, str | int] = {"sortBy": "price_asc"}
    if slice.kind == "search":
        path = "/search"
        params = {"keyword": slice.value, **params}
    else:  # category id, e.g. NFEGY4080000
        path = f"/c/{slice.value.strip('/')}"
    if page > 1:
        params["currentPage"] = page - 1
    if slice.price_min is not None:
        params["minPrice"] = slice.price_min
    if slice.price_max is not None:
        params["maxPrice"] = slice.price_max
    return f"{STORE}{path}?{urlencode(params)}"


def listing_data(html: str) -> dict:
    """The listing object the page renders from (cards, pagination, category tree)."""
    blob = _rsc_payload(html)
    start = blob.find('{"topStickySections"')
    if start < 0:
        raise ValueError("carrefour_eg: no listing data in the page (site layout changed?)")
    return json.loads(_balanced(blob, start))


def parse_listing_page(html: str, page: int = 1) -> PageResult:
    data = listing_data(html)
    pagination = data.get("pagination")
    if not pagination:
        # An empty result (or a page past the end) has no pagination and no cards.
        return PageResult(listings=[], has_next=False, total=0 if page == 1 else None)
    listings: dict[str, Listing] = {}
    for section in data.get("sections") or []:
        if section.get("uid") == "master-product-card":
            listing = _parse_card(section.get("componentDTO") or {})
            if listing and listing.sku not in listings:
                listings[listing.sku] = listing
    pages = min(pagination.get("totalPages") or 0, MAX_PAGES)
    return PageResult(
        listings=list(listings.values()),
        has_next=page < pages,
        total=pagination.get("totalResults"),
        subcategories=_subcategories(data),
    )


def _parse_card(card: dict) -> Listing | None:
    attrs = card.get("additionalAttributes") or {}
    sku = attrs.get("productId")
    if not sku or attrs.get("isSponsored"):
        return None  # sponsored cards can come from other categories
    price = _minor(attrs.get("sellingPrice"))
    marked = _minor(attrs.get("markedPrice"))
    list_price = marked if marked and price and marked > price else None
    seller = (attrs.get("shopName") or "").strip()
    first_party = not seller or seller == FIRST_PARTY
    stock = attrs.get("stock") or {}
    status = stock.get("stockLevelStatus") or "inStock"
    badges = [b.get("type") for c in card.get("productCardComponents") or []
              for b in (c.get("componentDTO") or {}).get("badges") or []]
    rating = next((c["componentDTO"].get("rating") for c in card.get("productCardComponents") or []
                   if c.get("uid") == "product-rating" and c.get("componentDTO")), None) or {}
    reviews = re.sub(r"\D", "", str(rating.get("count") or ""))
    analytics = card.get("analytics") or {}
    return Listing(
        site=CarrefourEG.name,
        sku=str(sku),
        title=(attrs.get("productName") or "").strip().strip(_INVISIBLE).strip(),
        url=BASE + (attrs.get("productUrl") or f"/mafegy/en/p/{sku}"),
        price=price,
        # Carrefour's own crossed-out price is its regular shelf price (most cards have none,
        # a timed promotion sets one), so it counts as a recent ("was") price; a marketplace
        # seller's is only a claim.
        list_price=list_price,
        currency=attrs.get("currency") or "EGP",
        in_stock=status in ("inStock", "lowStock") and price is not None,
        list_price_kind=None if list_price is None else ("was" if first_party else "list"),
        brand=_brand(analytics.get("brandName")),
        # "Only N left" on a card is min(stock, order limit); the real stock count is only
        # given for low stock (on about 40% of cards, mostly 1 or 2).
        stock_left=stock.get("value") if status == "lowStock" else None,
        rating=float(rating["value"]) if rating.get("value") and reviews else None,
        reviews=int(reviews) if reviews else None,
        badges=(LIMITED_DEAL,) if "TimedDiscount" in badges else (),  # "Ends in 13 hrs"
        seller=None if first_party else seller,
    )


def _brand(code: str | None) -> str | None:
    """'nonfood_samsung' -> 'Samsung', 'nonfood_lg' -> 'LG'."""
    name = re.sub(r"^(non)?food_", "", code or "").replace("_", " ").strip()
    if not name:
        return None
    return name.upper() if len(name) <= 3 else name.title()


def _subcategories(data: dict) -> list[Category]:
    """The category's children with product counts (the tree leaves some children out)."""
    tree = next((s for s in data.get("topStickySections") or [] if s.get("uid") == "category-tree"), {})
    return [Category(n["id"], f"{n.get('name', '').strip()} ({n.get('count', 0)})")
            for n in (tree.get("componentDTO") or {}).get("queryCategorizationTree") or [] if n.get("id")]


def _minor(value) -> int | None:
    return round(float(value) * 100) if value not in (None, "") else None
