#!/usr/bin/env python3
"""Measure a shop through its adapter before it goes live: is paging complete, and what does its data claim?

    .venv/bin/python .claude/skills/add-shop/measure_shop.py <shop> "<slice key>" [--pages 5] [--delay 3]

Reads real pages from this machine's IP, one at a time, and stops at the first refusal. Saves nothing. The
numbers decide the shop's rules (shop_rules.py, docs/SHOP_RULES.md) and go in its doc with the date.
"""
import argparse
import statistics
import time
from collections import Counter
from pathlib import Path

from deals.matching import norm_code, slug_codes
from deals.models import Blocked, Slice
from deals.shop_rules import placeholder
from deals.sites import SITES


def share(part: int, whole: int) -> str:
    return f"{part:,} of {whole:,} ({part / whole:.0%})" if whole else "none"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("shop", choices=sorted(SITES))
    parser.add_argument("target", help="'category:<id>' or 'search:<words>', optional '|price:<min>-<max>'")
    parser.add_argument("--pages", type=int, default=5)
    parser.add_argument("--delay", type=float, default=3.0, help="seconds between pages")
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()

    site, target = SITES[args.shop](Path(args.data_dir)), Slice.from_key(args.target)
    seen, listings, total, has_next, pages = Counter(), [], None, True, 0
    while has_next and pages < args.pages:
        if pages:
            time.sleep(args.delay)
        try:
            result = site.fetch_page(target, pages + 1)
        except Blocked as exc:
            print(f"REFUSED on page {pages + 1}: {exc}\nStopping. Don't retry from this IP for now.")
            break
        pages += 1
        total = result.total if total is None else total
        has_next = result.has_next
        seen.update(x.sku for x in result.listings)
        listings += result.listings
        print(f"page {pages}: {len(result.listings)} listings, total {result.total}, next {result.has_next}")
    if not listings:
        return print("no listings: nothing to measure")

    unique = {x.sku: x for x in listings}
    items = list(unique.values())
    stocked = [x for x in items if x.in_stock]
    priced = [x.price for x in items if x.price is not None]
    crossed = [x for x in items if x.list_price]
    low = [x for x in stocked if x.stock_left is not None and x.stock_left <= 3]
    repeats = sum(n - 1 for n in seen.values())
    finished = not has_next

    print(f"\nPaging ({pages} pages)")
    print(f"  shop's count {total}, unique products read {len(unique):,}, repeated across pages {repeats:,}")
    if finished and total and total != len(unique):
        print("  CHECK: read the whole slice but the count differs: pages overlap, the count is wrong, or one "
              "product makes several listings (Dream 2000's sizes: fine)")
    if repeats:
        print("  CHECK: repeats mean the sort shifts between pages and some products are missed; use a stable sort")
    if not finished:
        print(f"  not the whole slice: raise --pages to walk all of it ({args.pages} read)")
    if total is None and has_next:
        print("  CHECK: no count while there are more pages. The planner reads that as 'too big' and keeps splitting")

    print("\nPrices")
    if priced:
        print(f"  EGP min {min(priced) / 100:,.2f}, median {statistics.median(priced) / 100:,.2f}, "
              f"max {max(priced) / 100:,.2f}; no price on {share(len(items) - len(priced), len(items))}")
    print(f"  in stock {share(len(stocked), len(items))}; in stock without a price "
          f"{share(sum(x.price is None for x in stocked), len(stocked))}")
    common = [(p, n) for p, n in Counter(priced).most_common(8) if n > 1]
    print("  most repeated: " + (", ".join(f"{p / 100:,.0f} x{n}" for p, n in common) or "none"))
    odd = [x for x in items if x.price is not None and not placeholder(x.site, x.price)
           and (x.price < 5000 or (x.price >= 1_000_000 and x.price % 1_000_000 == 0))]
    print(f"  under 50 EGP or a round 10,000s (placeholders?): {len(odd)}")
    for x in odd[:5]:
        print(f"    {x.price / 100:,.2f}  {x.sku}  {x.title[:60]}")

    print("\nThe shop's claims")
    crossed_stocked = [x for x in stocked if x.list_price]  # sold-out products often have none: they would dilute it
    print(f"  crossed-out price on {share(len(crossed_stocked), len(stocked))} of what is in stock; kinds "
          f"{dict(Counter(x.list_price_kind for x in crossed))}")
    if crossed:
        off = [1 - x.price / x.list_price for x in crossed if x.price]
        print(f"  claimed discount: median {statistics.median(off):.0%}, 60%+ on {share(sum(d >= .6 for d in off), len(off))}")
    print(f"  'only 3 or fewer left' on {share(len(low), len(stocked))}; badges "
          f"{dict(Counter(b for x in items for b in x.badges))}")
    if len(crossed_stocked) >= len(stocked) * .8 > 0:
        print("  CHECK: nearly everything is 'on sale': a permanent anchor. Use kind 'list', consider no_claim_alerts")
    if len(low) >= len(stocked) * .4 > 0:
        print("  CHECK: low stock everywhere (existing low_stock_noise rules: 42% to 57%). Consider that rule")

    print("\nIdentity (matching)")
    print(f"  brand on {share(sum(bool(x.brand) for x in items), len(items))}; barcode on "
          f"{share(sum(bool(x.gtins) for x in items), len(items))}; seller on "
          f"{share(sum(bool(x.seller) for x in items), len(items))} ({len({x.seller for x in items if x.seller})} sellers)")
    slugged = [x for x in items if slug_codes(x.url)]
    spelled = [x for x in slugged if any(c in norm_code(x.title) for c in slug_codes(x.url))]
    print(f"  a model code in the link's last words: {share(len(slugged), len(items))}; of those, also in the title: "
          f"{share(len(spelled), len(slugged))} (slug_codes: only if the shop writes them, one link per model)")
    if slugged:
        print(f"  e.g. {slugged[0].url[:90]} -> {slug_codes(slugged[0].url)}")


if __name__ == "__main__":
    main()
