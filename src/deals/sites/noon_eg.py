"""noon.com Egypt listing pages.

Noon's HTML pages sit behind a bot challenge, but the catalog JSON API the
site itself uses answers plain HTTP with 50 structured products per request.
Listings are capped at 50 pages (~2,500 products) however large the category,
so big categories must be split into sub-categories / price ranges.
"""

import re
from pathlib import Path
from urllib.parse import urlencode

from curl_cffi import requests

from deals.models import (BESTSELLER, LIMITED_DEAL, LOWEST_30D, LOWEST_365D, MEGA_DEAL, SELLING_FAST,
                          Blocked, Category, Listing, PageResult, Slice, UnknownTarget)

API = "https://www.noon.com/_svc/catalog/api/v3/u/egypt-en"
PRODUCT_URL = "https://www.noon.com/egypt-en/{slug}/{sku}/p/"
STOCK_LEFT = re.compile(r"Only (\d+) left")
RECENT_SALES = re.compile(r"(\d[\d,]*)\+? sold recently")
LOWEST_DAYS = re.compile(r"Lowest price in (\d+) days")


class NoonEG:
    name = "noon_eg"
    max_slice_total = 2500  # 50 pages x 50 products

    def __init__(self, state_dir: Path):
        self._session = requests.Session(impersonate="chrome")

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        params: dict[str, str | int] = {"page": page}
        if slice.kind == "search":
            path = "search"
            params["q"] = slice.value
        else:  # category path, e.g. electronics-and-mobiles/mobiles-and-accessories
            path = slice.value.strip("/")
        if slice.price_min is not None:
            params["f[price][min]"] = slice.price_min
        if slice.price_max is not None:
            params["f[price][max]"] = slice.price_max

        r = self._session.get(f"{API}/{path}/?{urlencode(params)}", timeout=30,
                              headers={"x-locale": "en-eg", "x-mp": "noon"})
        if r.status_code in (403, 429) or "json" not in r.headers.get("content-type", ""):
            raise Blocked(f"{self.name}: HTTP {r.status_code}, {r.headers.get('content-type')}")
        r.raise_for_status()
        data = r.json()
        current = None
        if slice.kind == "category":
            # An unknown path silently returns some other category; catch that.
            crumbs = data.get("breadcrumbs") or []
            if not crumbs or crumbs[-1].get("code") != path:
                found = crumbs[-1].get("code") if crumbs else None
                raise UnknownTarget(f"{self.name}: unknown category {path!r} (site returned {found!r})")
            current = path
        return parse_catalog(data, page, current)


def parse_catalog(data: dict, page: int, current_category: str | None = None) -> PageResult:
    listings = [_parse_hit(hit) for hit in data.get("hits", []) if hit.get("sku")]
    return PageResult(
        listings=listings,
        has_next=page < (data.get("nbPages") or 0),
        total=data.get("nbHits"),
        subcategories=_child_categories(data, current_category) if current_category else [],
    )


def _parse_hit(hit: dict) -> Listing:
    regular = _minor(hit.get("price"))
    sale = _minor(hit.get("sale_price"))
    nudges = [n.get("text", "") for n in hit.get("nudges") or []]

    badges = []
    deal_text = ((hit.get("deal_tag") or {}).get("text") or "").lower()
    if "mega deal" in deal_text:
        badges.append(MEGA_DEAL)
    elif "deal" in deal_text:
        badges.append(LIMITED_DEAL)
    stock_left = recent_sales = None
    for text in nudges:
        if m := STOCK_LEFT.search(text):
            stock_left = int(m.group(1))
        elif m := RECENT_SALES.search(text):
            recent_sales = int(m.group(1).replace(",", ""))
        elif "Selling out fast" in text:
            badges.append(SELLING_FAST)
        elif "Lowest price in a year" in text:
            badges.append(LOWEST_365D)
        elif (m := LOWEST_DAYS.search(text)) and int(m.group(1)) >= 30:
            badges.append(LOWEST_30D)
    if hit.get("is_bestseller"):
        badges.append(BESTSELLER)

    rating = hit.get("product_rating") or {}
    # A noon product can have several sellers at wildly different prices, and
    # which offer a listing shows depends on the query (e.g. the price filter).
    # Tracking each seller's offer separately keeps every price history clean.
    seller = hit.get("store_name") or None
    seller_key = re.sub(r"[^a-z0-9]+", "-", (seller or hit.get("offer_code") or "").lower()).strip("-")
    return Listing(
        site=NoonEG.name,
        sku=f"{hit['sku']}@{seller_key}" if seller_key else hit["sku"],
        title=hit.get("name", ""),
        url=PRODUCT_URL.format(slug=hit.get("url", "p"), sku=hit["sku"]),
        price=sale or regular,
        # Seller-set crossed-out price; not proof of an earlier selling price, so it
        # is treated like an RRP. Noon's "lowest price in N days" badges are the
        # trustworthy history signal.
        list_price=regular if sale else None,
        currency="EGP",
        in_stock=bool(hit.get("is_buyable", True)),
        list_price_kind="list" if sale else None,
        brand=hit.get("brand"),
        stock_left=stock_left,
        recent_sales=recent_sales,
        rating=rating.get("value"),
        reviews=rating.get("count"),
        badges=tuple(badges),
        seller=seller,
    )


def _child_categories(data: dict, current: str) -> list[Category]:
    """Children of the current category in the 'category' facet tree."""
    facet = next((f for f in data.get("facets", []) if f.get("code") == "category"), None)
    stack = list(facet["data"]) if facet else []
    while stack:
        node = stack.pop()
        if node.get("code") == current:
            return [Category(child["code"], child.get("name", "")) for child in node.get("children", [])]
        stack.extend(node.get("children", []))
    return []


def _minor(value) -> int | None:
    return round(float(value) * 100) if value not in (None, "") else None
