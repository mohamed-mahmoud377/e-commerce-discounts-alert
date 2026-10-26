"""amazon.eg listing pages.

amazon.eg sits behind an AWS WAF JavaScript challenge, so plain HTTP is refused
until we hold valid session cookies. We solve the challenge once in a headless
browser, save the cookies, and do all crawling over plain HTTP (much lighter).
When the cookies stop working we bootstrap again.
"""

import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from curl_cffi import requests
from selectolax.lexbor import LexborHTMLParser

from deals.models import LIMITED_DEAL, Blocked, Category, Listing, PageResult, Slice
from deals.sites.base import parse_count, parse_money

log = logging.getLogger(__name__)

BASE = "https://www.amazon.eg"
BLOCK_MARKERS = ("challenge.js", "automated access", "validateCaptcha")
# "1–24 of 775 results" / "25 results" are exact; "1–24 of over 1,000 results" is not.
RESULT_COUNT = re.compile(r"(?:of\s+)?(over\s+)?([\d,]+)\s+results?")
STOCK_LEFT = re.compile(r"Only (\d+) left in stock")
RECENT_SALES = re.compile(r"([\d.,]+K?)\+ bought in past month")
# "4.2 out of 5 stars | | (16.6K)"
RATING = re.compile(r"([\d.]+) out of 5 stars(?:[ |]*\(([\d.,]+K?)\))?")


class AmazonEG:
    name = "amazon_eg"
    # Amazon only shows an exact count up to 1,000 and pages through that fully.
    max_slice_total = 1000

    def __init__(self, state_dir: Path):
        self._cookie_file = state_dir / "amazon_eg_cookies.json"
        self._session = requests.Session(impersonate="chrome")
        if self._cookie_file.exists():
            self._load_cookies(json.loads(self._cookie_file.read_text()))

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        params = {}
        refinements = []
        if slice.kind == "search":
            params["k"] = slice.value
        else:
            refinements.append(f"n:{slice.value}")
        # A bare category page is a "landing" layout with no product grid; any
        # price filter (even 0-unbounded) switches it to normal search results.
        if slice.has_price or slice.kind == "category":
            # p_36 is in piastres; either end may be left open.
            low = (slice.price_min or 0) * 100
            high = "" if slice.price_max is None else slice.price_max * 100
            refinements.append(f"p_36:{low}-{high}")
        if refinements:
            params["rh"] = ",".join(refinements)
        if page > 1:
            params["page"] = page
        # The /-/en/ prefix forces English titles.
        url = f"{BASE}/-/en/s?{urlencode(params)}"

        html = self._get(url)
        if html is None:
            self._bootstrap_cookies()
            html = self._get(url)
            if html is None:
                raise Blocked(f"{self.name}: still challenged after refreshing cookies")
        current_node = slice.value if slice.kind == "category" else None
        result = parse_search_page(html, current_node)
        if result.decoy:
            # A soft block: the page is a random listing with no result count. Counting it
            # would make the planner split forever and crawling it would record junk, so
            # treat it like a challenge: the coordinator pauses this IP on amazon for a while.
            raise Blocked(f"{self.name}: decoy page (no result bar) for {slice.key()}")
        return result

    def _get(self, url: str) -> str | None:
        r = self._session.get(url, timeout=30)
        if r.status_code in (202, 503) or any(m in r.text for m in BLOCK_MARKERS):
            return None
        r.raise_for_status()
        return r.text

    def _bootstrap_cookies(self) -> None:
        from playwright.sync_api import sync_playwright

        log.info("%s: solving WAF challenge in headless browser", self.name)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(locale="en-US")
                page = ctx.new_page()
                page.goto(f"{BASE}/-/en/", wait_until="domcontentloaded")
                # The challenge page reloads itself once the token is issued.
                deadline = time.monotonic() + 30
                while any(m in page.content() for m in BLOCK_MARKERS):
                    if time.monotonic() > deadline:
                        raise Blocked(f"{self.name}: browser could not pass the challenge")
                    page.wait_for_timeout(1000)
                cookies = ctx.cookies()
            finally:
                browser.close()

        cookies = [c for c in cookies if "amazon" in c["domain"]]
        self._cookie_file.parent.mkdir(parents=True, exist_ok=True)
        self._cookie_file.write_text(json.dumps(cookies))
        self._session = requests.Session(impersonate="chrome")
        self._load_cookies(cookies)

    def _load_cookies(self, cookies: list[dict]) -> None:
        for c in cookies:
            self._session.cookies.set(c["name"], c["value"], domain=c["domain"])


def parse_search_page(html: str, current_node: str | None = None) -> PageResult:
    tree = LexborHTMLParser(html)
    # "No results for your search query" lives in a MESSAGING widget (the result-info bar
    # is empty on that page) and the grid may still show unrelated recommendations.
    message = tree.css_first('[cel_widget_id*="MESSAGING"]')
    if message is not None and "no results" in message.text(strip=True).lower():
        return PageResult(listings=[], has_next=False, total=0)  # the slice is empty now
    listings: dict[str, Listing] = {}  # sponsored cards repeat organic ones
    for card in tree.css('div[data-component-type="s-search-result"]'):
        asin = card.attributes.get("data-asin")
        title_node = card.css_first("h2")
        if not asin or asin in listings or title_node is None:
            continue
        listings[asin] = _parse_card(card, asin, title_node.text(strip=True))

    has_next = tree.css_first("a.s-pagination-next") is not None
    return PageResult(
        listings=list(listings.values()),
        has_next=has_next,
        total=_parse_total(tree),
        subcategories=_child_nodes(tree, current_node) if current_node else [],
        decoy=bool(listings) and tree.css_first('[data-component-type="s-result-info-bar"]') is None,
    )


def _parse_card(card, asin: str, title: str) -> Listing:
    price = list_price = None
    for node in card.css("span.a-price"):
        amount = parse_money(_text(node.css_first("span.a-offscreen")))
        if node.attributes.get("data-a-strike") == "true":
            list_price = list_price or amount
        else:
            price = price or amount

    text = card.text(separator=" | ", strip=True)
    # "Was:" is Amazon's own recent selling price; "List:" is the brand's RRP.
    list_kind = None
    if list_price is not None:
        list_kind = "was" if "Was:" in text else "list"

    stock = STOCK_LEFT.search(text)
    sales = RECENT_SALES.search(text)
    rating = RATING.search(text)
    badges = (LIMITED_DEAL,) if "Limited time deal" in text else ()

    return Listing(
        site=AmazonEG.name,
        sku=asin,
        title=title,
        url=f"{BASE}/-/en/dp/{asin}",
        price=price,
        list_price=list_price,
        currency="EGP",
        in_stock=price is not None,
        list_price_kind=list_kind,
        stock_left=int(stock.group(1)) if stock else None,
        recent_sales=parse_count(sales.group(1)) if sales else None,
        rating=float(rating.group(1)) if rating else None,
        reviews=parse_count(rating.group(2)) if rating and rating.group(2) else None,
        badges=badges,
    )


def _parse_total(tree: LexborHTMLParser) -> int | None:
    bar = tree.css_first('[data-component-type="s-result-info-bar"]')
    heading = bar.css_first("h2") if bar else None
    m = RESULT_COUNT.search((heading or bar).text(strip=True)) if bar else None
    if m is None or m.group(1):  # missing, or only "over N"
        return None
    return int(m.group(2).replace(",", ""))


def _child_nodes(tree: LexborHTMLParser, current: str) -> list[Category]:
    """Child categories from the 'Department' sidebar.

    Department links carry the whole path, e.g. rh=n:<root>,n:<current>,n:<child>.
    The parent ("back") link stops before the current node, so only links where
    a node follows the current one are children.
    """
    children: dict[str, Category] = {}
    for a in tree.css("#s-refinements a"):
        query = parse_qs(urlparse(a.attributes.get("href") or "").query)
        if not query.get("ref", [""])[0].startswith("sr_nr_n_"):
            continue
        path = [part[2:] for part in query.get("rh", [""])[0].split(",") if part.startswith("n:")]
        if current in path and path[-1] != current and path[-1] not in children:
            children[path[-1]] = Category(path[-1], a.text(strip=True))
    return list(children.values())


def _text(node) -> str | None:
    return node.text(strip=True) if node is not None else None
