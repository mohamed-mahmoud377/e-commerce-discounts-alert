"""btech.com (B.TECH) catalog, through the shop's own product search API.

B.TECH's site is a Next.js app that only server-renders the first 20 products of a
category; the rest arrive from a JSON API (`POST .../discovery/api/v1/products/search`)
that takes a category slug, a price range (inclusive at both ends), a sort order and
100 products a page, and answers with an exact `total_items`. Calling it needs the
guest token the site drops in its `btech-auth-session` cookie on any page load (valid
a day) and an `Accept-Language: en` header. The API host refuses datacenter IPs
(connection timeout from the droplet), so B.TECH is a home-worker shop.

The API knows a category by its last slug segment only (`televisions`, not
`tvs-projectors/televisions`) and answers an unknown slug with 0 products instead of
an error, so slugs are checked against the category tree embedded in any category
page (the "Category" filter, every level with product counts), cached for a day.
B.TECH is a marketplace: about half the offers come from third-party sellers.
"""

import json
import re
import time
from dataclasses import replace
from urllib.parse import unquote

from curl_cffi import requests

from deals.models import BESTSELLER, Blocked, Category, Listing, PageResult, Slice, UnknownTarget

SITE = "https://btech.com"
TREE_PAGE = f"{SITE}/en/c/tvs-projectors"  # any category page carries the whole tree
API = "https://retail-online-prod.btech.com/api/v1/green/discovery/api/v1/products/search"
PRODUCT_URL = f"{SITE}/en/p/{{slug}}?offering_id={{offer}}"
PAGE_SIZE = 100  # the API's maximum (422 above it)
IMPERSONATE = "chrome"
FIRST_PARTY = "B.TECH"
TREE_TTL = 24 * 3600
SESSION_COOKIE = "btech-auth-session"
_RSC_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)


class BtechEG:
    name = "btech_eg"
    max_slice_total = 2000  # no page cap on the API; keeps a slice to 20 requests

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)
        self._token: str | None = None
        self._tree: dict[str, dict] | None = None
        self._tree_at = 0.0

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        node = self._category(slice.value) if slice.kind == "category" else None
        body = build_body(slice, page)
        status, data = self._post(body)
        if status in (401, 403):
            # The guest token expired (a day) or was refused: one fresh session, then give up.
            self._session = requests.Session(impersonate=IMPERSONATE)
            self._token = None
            status, data = self._post(body)
            if status in (401, 403):
                raise Blocked(f"{self.name}: the API refused our guest token twice (HTTP {status})")
        if status == 500 and page > 1:
            return PageResult(listings=[], has_next=False)  # a page past the end (the slice shrank)
        if status != 200 or not isinstance(data, dict) or "items" not in data:
            raise ValueError(f"{self.name}: API answered HTTP {status}: {json.dumps(data)[:300]} (API changed?)")
        result = parse_products(data, page)
        if node is not None and page == 1:
            result = replace(result, subcategories=[Category(c["id"], f"{c['value'].strip()} ({c.get('count', 0)})")
                                                    for c in node.get("options") or []])
        return result

    def _post(self, body: dict) -> tuple[int, dict | str]:
        headers = {"x-platform": "web", "accept": "application/json", "content-type": "application/json",
                   "accept-language": "en", "authorization": f"Bearer {self._guest_token()}"}
        r = self._session.post(API, json=body, headers=headers, timeout=30)
        if "json" in r.headers.get("content-type", ""):
            return r.status_code, r.json()
        return r.status_code, r.text[:300]

    def _guest_token(self) -> str:
        if self._token is None:
            self._fetch_page_html(f"{SITE}/en")
            self._token = guest_token(self._session.cookies.get(SESSION_COOKIE))
        return self._token

    def _fetch_page_html(self, url: str) -> str:
        """A site page (which also sets the session cookie); Blocked on a challenge."""
        r = self._session.get(url, timeout=30)
        if r.status_code in (403, 429, 503):
            raise Blocked(f"{self.name}: HTTP {r.status_code} from {url}")
        r.raise_for_status()
        if not self._session.cookies.get(SESSION_COOKIE):
            raise Blocked(f"{self.name}: no {SESSION_COOKIE} cookie after loading {url} (challenge or site change?)")
        return r.text

    def _category(self, value: str) -> dict:
        """The tree node of a config slug (`tvs-projectors/televisions` or `televisions`)."""
        slug = category_slug(value)
        tree = self._category_tree()
        if slug not in tree and time.monotonic() - self._tree_at > 60:
            tree = self._category_tree(force=True)  # maybe a brand-new category
        if slug not in tree:
            raise UnknownTarget(f"{self.name}: no category with slug {slug!r} (deals categories btech_eg lists them)")
        return tree[slug]

    def _category_tree(self, force: bool = False) -> dict[str, dict]:
        if force or self._tree is None or time.monotonic() - self._tree_at > TREE_TTL:
            self._tree = parse_category_tree(self._fetch_page_html(TREE_PAGE))
            self._tree_at = time.monotonic()
            self._token = None  # the page refreshed the session cookie too
        return self._tree


def category_slug(value: str) -> str:
    """The API wants the last path segment: 'tvs-projectors/televisions' -> 'televisions'."""
    return value.strip("/").rsplit("/", 1)[-1]


def build_body(slice: Slice, page: int) -> dict:
    """The API request for one page, sorted by price so that paging is stable."""
    filters: dict = {"in_stock": True}  # what the site sends; the shop lists in-stock offers only anyway
    if slice.kind == "category":
        filters["categories"] = category_slug(slice.value)
    if slice.has_price:
        price_range = {}
        if slice.price_min is not None:
            price_range["min"] = slice.price_min
        if slice.price_max is not None:
            price_range["max"] = slice.price_max
        filters["price_range"] = price_range
    body = {"page": page, "page_size": PAGE_SIZE, "sort_by": "price_asc", "filters": filters}
    if slice.kind == "search":
        body["search_query"] = slice.value
    return body


def guest_token(cookie: str | None) -> str:
    """The JWT inside the `btech-auth-session` cookie (URL-encoded JSON)."""
    try:
        return json.loads(unquote(cookie or ""))["JWT"]
    except (ValueError, KeyError, TypeError) as exc:
        raise Blocked(f"btech_eg: cannot read the guest token from {SESSION_COOKIE} ({exc})") from exc


def parse_products(data: dict, page: int) -> PageResult:
    listings: dict[str, Listing] = {}
    for item in data.get("items") or []:
        if item.get("sku") and item["sku"] not in listings:
            listings[item["sku"]] = _parse_item(item)
    return PageResult(
        listings=list(listings.values()),
        has_next=page < (data.get("total_pages") or 0),
        total=data.get("total_items"),
    )


def _parse_item(p: dict) -> Listing:
    prices = p.get("price") or {}
    final = _minor(prices.get("final_price"))
    base = _minor(prices.get("base_price"))
    list_price = base if base and final and base > final else None
    seller = (p.get("seller_name") or "").strip()
    first_party = not seller or seller == FIRST_PARTY
    return Listing(
        site=BtechEG.name,
        sku=p["sku"],
        title=(p.get("name") or "").strip(),
        url=PRODUCT_URL.format(slug=p.get("slug") or p.get("variant_id", ""), offer=p.get("offer_id", "")),
        price=final,
        # B.TECH's own "base price" is its regular shelf price and only about a fifth of
        # its offers carry a promotion, so it is treated like a recent ("was") price.
        # A third-party seller's base price is just a claim, like an RRP.
        list_price=list_price,
        currency="EGP",
        in_stock=bool(p.get("is_in_stock", True)) and final is not None,
        list_price_kind=None if list_price is None else ("was" if first_party else "list"),
        brand=(p.get("brand") or "").strip() or None,
        badges=(BESTSELLER,) if p.get("is_best_seller") else (),
        seller=None if first_party else seller,
    )


def _minor(value) -> int | None:
    return round(float(value) * 100) if value not in (None, "") else None


def parse_category_tree(html: str) -> dict[str, dict]:
    """slug -> node of the "Category" filter embedded in a category page (all levels).

    Each node is {"id": slug, "value": name, "count": products, "options": children}.
    """
    blob = _rsc_payload(html)
    start = blob.find('{"key":"categories"')
    if start < 0:
        raise ValueError("btech_eg: no category filter in the page (site layout changed?)")
    tree = json.loads(_balanced(blob, start))
    index: dict[str, dict] = {}

    def walk(nodes):
        for node in nodes or []:
            index.setdefault(node["id"], node)
            walk(node.get("options"))

    walk(tree.get("options"))
    return index


def _rsc_payload(html: str) -> str:
    """The React Server Components stream a Next.js page embeds, as one decoded string."""
    parts = []
    for chunk in _RSC_CHUNK.findall(html):
        try:
            parts.append(json.loads(f'"{chunk}"'))
        except ValueError:
            parts.append(chunk.encode().decode("unicode_escape", errors="ignore"))
    return "".join(parts)


def _balanced(text: str, start: int) -> str:
    """The JSON object starting at text[start] (brace matching, string-aware)."""
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ValueError("btech_eg: unbalanced category filter JSON")
