#!/usr/bin/env python3
"""Start a new shop: the adapter and doc skeletons, and the list of what else it needs.

    make new-shop NAME=elaraby LABEL="El Araby" URL=https://www.elarabygroup.com

Writes src/deals/sites/<name>_eg.py and docs/<NAME>.md (never over an existing file) and prints the
deals/shops.py entry to paste. tests/test_shops.py then fails until the shop has everything else
(docs/INTERNALS.md, "Add a shop"); `make test` lists what is missing.
"""
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ADAPTER = '''"""{host} ({label}) catalog.

TODO, from the analysis in docs/{doc}: what serves the listing (HTML, JSON API, GraphQL), page size, the
exact count, the price filter (inclusive ends?), what an empty or unknown category answers, and what a
block looks like.
"""

from curl_cffi import requests

from deals.models import Blocked, Listing, PageResult, Slice

BASE = "{url}"
IMPERSONATE = "chrome"


class {cls}:
    name = "{name}"
    max_slice_total = 1000  # TODO: the most products the shop lets you page through in one listing

    def __init__(self, state_dir=None):
        self._session = requests.Session(impersonate=IMPERSONATE)

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        """One listing page. The planner splits by price, so the price filter must be exact; an empty
        slice must come back as an empty page, not as a listing the shop redirects to."""
        r = self._session.get(page_url(slice, page), timeout=60)
        if r.status_code in (403, 429, 503):
            raise Blocked(f"{{self.name}}: HTTP {{r.status_code}}")
        r.raise_for_status()
        return parse_page(r.text, page)


def page_url(slice: Slice, page: int) -> str:
    raise NotImplementedError("TODO: category / search / price filter / page -> URL")


def parse_page(body: str, page: int) -> PageResult:
    """Listings with prices in piastres (sites.base.parse_money), `total` when the shop shows an exact count."""
    listings: list[Listing] = []
    raise NotImplementedError("TODO: parse the saved page in tests/fixtures/{short}_category.html.gz")
    return PageResult(listings=listings, has_next=False, total=None)
'''

DOC = '''# {label} ({host}): site analysis and how we crawl it

Started {today}. Fill every section before the shop goes live; keep it in step with
`src/deals/sites/{name}.py`.

## Summary

What it sells, its platform, how many products, and whether a datacenter IP (the droplet) is let in.

## Access and blocking (tested {today})

From the Mac and from the droplet: status codes, challenges, what a block looks like, safe delays.

## The listing

URL or API, page size, the exact count, sorting, the price filter (inclusive ends?), the page cap,
what an empty slice and an unknown category answer.

## Prices and stock

Where the price and the crossed-out price are, sold-out products, placeholder prices, sellers.
Anything the shop gets wrong in its data goes to `shop_rules.RULES` and docs/SHOP_RULES.md too.

## Catalogue and categories

The category tree, the roots we watch (also in `deals/shops.py`), products that sit in no child.

## How we crawl it

Watch groups, slices, the deal-feed fast lane if the shop has one, pages per hour.

## Running and checking it

`deals categories {name}`, `deals plan ...`, `deals crawl {name} "<slice>" --pages 1`.

## Troubleshooting

## Re-checking

## Alert screenshots

The `shots.SPECS` boxes (price, seller) and the Arabic URL.
'''


def main() -> None:
    args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    short = re.sub(r"_eg$", "", args.get("NAME", "").strip().lower())
    if not re.fullmatch(r"[a-z0-9]+", short):
        sys.exit('usage: make new-shop NAME=elaraby LABEL="El Araby" URL=https://www.elarabygroup.com')
    name, label = f"{short}_eg", args.get("LABEL") or short.title()
    url = args.get("URL") or f"https://www.{short}.com"
    host = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
    cls = re.sub(r"(^|_)(\w)", lambda m: m.group(2).upper(), short) + "EG"
    if cls[0].isdigit():
        cls = "Shop" + cls
    doc = f"{short.upper()}.md"
    values = dict(name=name, short=short, label=label, url=url, host=host, cls=cls, doc=doc, today=f"{date.today():%-d %b %Y}")
    for path, text in ((ROOT / "src/deals/sites" / f"{name}.py", ADAPTER), (ROOT / "docs" / doc, DOC)):
        if path.exists():
            print(f"exists, left alone: {path.relative_to(ROOT)}")
        else:
            path.write_text(text.format(**values))
            print(f"wrote {path.relative_to(ROOT)}")
    print(f"""
Paste into deals/shops.py (SHOPS), and fill in the Arabic name, badge, kind and roots:

    Shop("{name}", "{label}", "TODO اسم المتجر", "{short[:2].upper()}", "electronics", "{url}", True, "{doc}",
         (("TODO category", "TODO id"),)),

Then (tests/test_shops.py checks each one):
  1. src/deals/sites/__init__.py: import {cls} and add it to SITES, in the same place as in SHOPS
  2. tests/fixtures/{short}_category.html.gz: one real listing page (from the Mac for a home-only shop),
     a parser test in tests/test_parsers.py, and an entry in tests/test_shops.py LISTING_PAGES
  3. shots.SPECS["{name}"]: the price and seller boxes on its product page
  4. config.example.toml: a [sites.{name}] block with its delays; /etc/deals/config.toml on the server
     after the code is deployed (old code rejects unknown keys); its roots in the [[watch]] groups
  5. DEALS_SITES: scripts/mac-worker-env.sh, README.md, docs/RUN_A_WORKER.md, docs/GETTING_STARTED.md,
     docs/GUIDE.md, and by hand in every home worker's worker.env (make install-mac-worker)
  6. link docs/{doc} from CLAUDE.md; docs/CATEGORIES.md, docs/SHOP_RULES.md, docs/INTERNALS.md "Add a shop"
""")


if __name__ == "__main__":
    main()
