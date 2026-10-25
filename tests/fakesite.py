"""An in-memory site with a category tree and page caps, for planner/worker tests."""

import random

from deals.models import Category, Listing, PageResult, Slice

TREE = {"root": ["root/a", "root/b"], "root/a": ["root/a/x", "root/a/y"]}


def make_catalog(n=1500, seed=1) -> list[tuple[str, str, int]]:
    """(sku, leaf category, price in EGP); prices skewed like real shops."""
    rng = random.Random(seed)
    leaves = ["root/a/x", "root/a/y", "root/b"]
    return [(f"SKU{i}", rng.choice(leaves), int(rng.lognormvariate(7, 1.2)) + 1) for i in range(n)]


class FakeSite:
    name = "amazon_eg"
    max_slice_total = 100
    page_size = 20

    def __init__(self, catalog, exact_limit=150):
        self.catalog = catalog
        self.exact_limit = exact_limit  # like amazon: above this the count is "over N"
        self.requests = 0

    def matches(self, s: Slice):
        for sku, cat, price in self.catalog:
            if s.kind == "category" and not cat.startswith(s.value):
                continue
            if s.price_min is not None and price < s.price_min:
                continue
            if s.price_max is not None and price > s.price_max:
                continue
            yield sku, cat, price

    def fetch_page(self, s: Slice, page: int) -> PageResult:
        self.requests += 1
        items = sorted(self.matches(s))
        chunk = items[(page - 1) * self.page_size: page * self.page_size]
        listings = [Listing(self.name, sku, f"product {sku}", f"https://x/{sku}", price * 100, None, "EGP", True)
                    for sku, _, price in chunk]
        return PageResult(
            listings=listings,
            has_next=page * self.page_size < len(items),
            total=len(items) if len(items) <= self.exact_limit else None,
            subcategories=[Category(c, c) for c in TREE.get(s.value, [])] if s.kind == "category" else [],
        )
