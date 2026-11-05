"""samsung.com/eg (Samsung Egypt) catalog, through the search API its category pages call.

`searchapi.samsung.com/v6/front/b2c/product/finder/global?type=<8 digits>&siteCode=eg&start=1&num=48`: a family per
record (`totalRecord` counts families), each with its models in `modelList`, one per colour and size. No price filter, and
the catalogue is small, so a slice is always a whole category. An unknown type is an empty 200: it can't be told from an
empty category, so a type that isn't 8 digits is refused here. Past the end: no products.

**Most models have no price** (`learnMore`, no stock word), and many categories have none at all (5 Nov 2026: 96 of 179
phone models, 0 of 32 tablets). They stay listed as sold out, so one is noticed the day it gets a price.

The price is `promotionPrice` when there is one, and then `price` is the crossed-out one (`priceDisplay` always shows what
you pay). Colours fold into one listing at the cheapest in stock: an `SM-` code ends in two letters of colour, one of
version, three of region (SM-A185G ZK K EGY), and the version is a real difference even when the page says the same size
(SM-A185GZKK 14,240 and SM-A185GZKL 16,470 are both "128GB"). docs/SAMSUNG.md.
"""

import html
import re
from collections import Counter

from curl_cffi import requests

from deals.models import Blocked, Listing, PageResult, Slice, UnknownTarget

BASE = "https://www.samsung.com"
FINDER = "https://searchapi.samsung.com/v6/front/b2c/product/finder/global"
IMPERSONATE = "chrome"
PAGE_SIZE = 48
COLOUR_CHIP = "COLOR"
_TYPE = re.compile(r"\d{8}")
_FOLDED_CODE = re.compile(r"(SM-[A-Z0-9]+)[A-Z]{2}([A-Z0-9])[A-Z]{3}")
_BIDI = re.compile("[\u200e\u200f]")


class SamsungEG:
    name = "samsung_eg"
    max_slice_total = 10_000  # bigger than any category: there is no price filter to split with

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind != "category" or not _TYPE.fullmatch(slice.value):
            raise UnknownTarget(f"{self.name}: use category:<8-digit type>, e.g. category:01010000")
        r = self._session.get(FINDER, params=_params(slice.value, page), timeout=60)
        if r.status_code in (403, 429, 503) or "json" not in r.headers.get("content-type", ""):
            raise Blocked(f"{self.name}: HTTP {r.status_code}")
        r.raise_for_status()
        return parse_products(r.json(), page)


def _params(category: str, page: int) -> dict:
    return {"type": category, "siteCode": "eg", "start": (page - 1) * PAGE_SIZE + 1, "num": PAGE_SIZE,
            "sort": "newest", "onlyFilterInfoYN": "N", "keySummaryYN": "N"}


def parse_products(data: dict, page: int = 1) -> PageResult:
    result = ((data.get("response") or {}).get("resultData")) or {}
    families = result.get("productList") or []
    total = int(((result.get("common") or {}).get("totalRecord")) or 0)
    listings = [listing for family in families for listing in _family(family)]
    return PageResult(listings=listings, has_next=bool(families) and page * PAGE_SIZE < total, total=total)


def _family(family: dict) -> list[Listing]:
    """One listing per model, or per version of a phone with its colours folded."""
    groups: dict[str, list[dict]] = {}
    for model in family.get("modelList") or []:
        if model.get("modelCode"):
            groups.setdefault(_key(model["modelCode"]), []).append(model)
    sizes = Counter(tuple(_sizes(models[0])) for models in groups.values())
    # two versions the page calls the same size keep the version letter in the title, or matching would take them for one
    return [_listing(family, key, models, sizes[tuple(_sizes(models[0]))] > 1) for key, models in groups.items()]


def _key(code: str) -> str:
    folded = _FOLDED_CODE.fullmatch(code)
    return f"{folded.group(1)}-{folded.group(2)}" if folded else code


def _sizes(model: dict) -> list[str]:
    return [c["fmyChipName"].strip() for c in model.get("fmyChipList") or []
            if c.get("fmyChipType") != COLOUR_CHIP and c.get("fmyChipName")]


def _listing(family: dict, key: str, models: list[dict], ambiguous: bool) -> Listing:
    priced = [m for m in models if _amount(m) is not None]
    buyable = [m for m in priced if m.get("stockStatusText") == "inStock"]
    chosen = min(buyable or priced or models, key=lambda m: _amount(m) or 0)
    price = _amount(chosen)
    before = _piastres(chosen.get("price")) if chosen.get("promotionPrice") else None
    claim = before if price and before and before > price else None
    name = _BIDI.sub("", html.unescape(chosen.get("displayName") or family.get("fmyEngName") or "")).strip()
    sizes = [s for s in _sizes(chosen) if s.strip('"') not in name]  # a TV's name has its size already
    code = key if ambiguous or not key.startswith("SM-") else key.rsplit("-", 1)[0]
    return Listing(
        site=SamsungEG.name,
        sku=key,
        title=" ".join([*([] if name.startswith("Samsung") else ["Samsung"]), name, *sizes, code]),
        url=BASE + _page(chosen),
        price=price,
        list_price=claim,
        currency="EGP",
        in_stock=bool(buyable),
        list_price_kind="list" if claim else None,
        brand="Samsung",
        rating=float(chosen["ratings"]) if chosen.get("reviewCount") not in (None, "", "0") else None,
        reviews=int(chosen.get("reviewCount") or 0),
    )


def _page(model: dict) -> str:
    """A new phone's own link goes to a marketing page with no price; its /buy/ page has one."""
    if model.get("marketingpdpYN") == "Y" and model.get("configuratorUrl"):
        return model["configuratorUrl"]
    return model.get("originPdpUrl") or model.get("pdpUrl") or ""


def _amount(model: dict) -> int | None:
    return _piastres(model.get("promotionPrice")) or _piastres(model.get("price"))


def _piastres(value) -> int | None:
    return round(float(value) * 100) if value not in (None, "") and float(value) > 0 else None
