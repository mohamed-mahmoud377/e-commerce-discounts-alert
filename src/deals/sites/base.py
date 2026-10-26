import re
from typing import Protocol

from deals.models import PageResult, Slice

_NUMBER = re.compile(r"[\d,]+(?:\.\d+)?")


def parse_money(text: str | None) -> int | None:
    """'EGP\xa01,999.50' -> 199950 (minor units)."""
    if not text:
        return None
    m = _NUMBER.search(text)
    if not m:
        return None
    return round(float(m.group().replace(",", "")) * 100)


class Site(Protocol):
    name: str
    # Largest slice the site lets us page through completely; the planner
    # splits anything bigger.
    max_slice_total: int
    # Optional, default True: may the planner split by sub-category? Only if every
    # product of a category sits in one of its children (not so on Jumia).
    split_by_subcategory: bool

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        """Fetch one listing page. Raises Blocked if the site challenges us."""
        ...


def parse_count(text: str) -> int:
    """'16.6K' -> 16600, '1,234' -> 1234."""
    text = text.replace(",", "").strip()
    if text.upper().endswith("K"):
        return round(float(text[:-1]) * 1000)
    return int(float(text))
