"""Split a root category (or search) into slices small enough to crawl completely.

Sites stop paging long before the end of a big category (amazon.eg: exact
counts only up to 1,000; noon: 50 pages; Jumia: 50 pages). The planner probes
page 1 of a slice and, if it is too big, splits it into price ranges, halving
ranges until every piece fits.

It never splits into sub-categories: on every shop a category holds products
that are in none of its children (measured 26 Oct 2026: amazon.eg Headphones
29 of 688 above 5,000 EGP, noon PC components 1,660 of 4,356, Jumia air
conditioners 135 of 1,274), and a sub-category split silently drops them.
Price ranges cover everything.
"""

import random
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from deals.models import Listing, Slice
from deals.sites.base import Site

# First price split, in EGP. Bands are narrow where products are dense; anything
# still too big gets halved further.
PRICE_BANDS = [0, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000]


@dataclass(frozen=True)
class PlannedSlice:
    slice: Slice
    total: int | None  # None: still over the site limit but can't be split further
    oversized: bool = False


def plan(
    site: Site,
    root: Slice,
    *,
    on_listings: Callable[[list[Listing]], None] | None = None,
    delay: tuple[float, float] = (2.0, 4.0),
    report: Callable[[str], None] = print,
) -> list[PlannedSlice]:
    leaves: list[PlannedSlice] = []
    queue = deque([root])
    probes = 0

    while queue:
        current = queue.popleft()
        if probes:
            time.sleep(random.uniform(*delay))
        result = site.fetch_page(current, 1)
        probes += 1
        if on_listings:
            on_listings(result.listings)  # the probe page is real data; keep it

        total = result.total
        if total is None and not result.has_next:
            total = len(result.listings)  # single page (or empty) without a count banner

        if total is not None and total <= site.max_slice_total:
            if total or (current.kind == "deals" and current == root):
                # An empty deal feed is kept and polled: deals start any minute, and a plan
                # runs only weekly (empty category ranges are dropped as before).
                leaves.append(PlannedSlice(current, total))
            report(f"  ok      {current}  ({total} products)")
        elif children := split_price(current):
            report(f"  split   {current}  ({_size(total)}) into {len(children)} price ranges")
            queue.extend(children)
        else:
            report(f"  TOO BIG {current}  ({_size(total)}) and can't be split further")
            leaves.append(PlannedSlice(current, total, oversized=True))

    report(f"{len(leaves)} slices, {probes} requests")
    return leaves


def split_price(s: Slice) -> list[Slice]:
    # Neighbouring ranges share their boundary so fractional prices
    # (e.g. 1999.50) are never lost; a few duplicates are harmless.
    low = s.price_min or 0
    if s.price_max is None:
        cuts = [low] + [b for b in PRICE_BANDS if b > low]
        if len(cuts) > 1:  # first split: the standard bands above the floor
            return [s.with_price(a, b) for a, b in zip(cuts, cuts[1:])] + [s.with_price(cuts[-1], None)]
        mid = max(low * 2, PRICE_BANDS[1])
        return [s.with_price(low, mid), s.with_price(mid, None)]
    if s.price_max - low <= 1:
        return []
    mid = (low + s.price_max) // 2
    return [s.with_price(low, mid), s.with_price(mid, s.price_max)]


def _size(total: int | None) -> str:
    return "too many to count" if total is None else f"{total} products"
