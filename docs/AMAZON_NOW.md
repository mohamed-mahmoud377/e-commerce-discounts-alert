# Amazon Now (amazon.eg): site analysis and how we crawl it

Amazon Now is amazon.eg's quick-commerce store: groceries, household, beauty, baby and some electronics, delivered
in about 20 to 30 minutes from dark stores in parts of Cairo, Giza, Alexandria and the North Coast (launched in
Egypt around July 2026; EGP 25 delivery, free for Prime above EGP 150). We added it as the eleventh shop on
3 November 2026. The adapter is `src/deals/sites/amazon_now_eg.py` (site name `amazon_now_eg`, Arabic name in alerts:
أمازون ناو), a small subclass of `sites/amazon_eg.py`. Its tests are in `tests/test_parsers.py` (`test_amazon_now_*`),
run against the saved pages `tests/fixtures/amazon_now_root.html.gz` (the whole store) and `amazon_now_node.html.gz`
(Grocery, 20 to 40 EGP). Sizes are from 3 November 2026 and drift.

## Summary

| Question | Answer |
| --- | --- |
| What it is | The ordinary amazon.eg search under its own browse root, **node `211761638031`** |
| What to fetch | `/-/en/s?rh=n:211761638031[,n:<child>],p_36:<lo>-<hi>[&page=N]`: same HTML, same WAF cookies, same parser as amazon_eg |
| Why a separate shop | **An ASIN on Amazon Now is a separate offer at its own price.** Juhayna 6×1 L (B0CVW9DPCB): EGP 275.95 from Amazon, EGP 289.75 on Amazon Now, at the same moment. One product row per offer keeps each price history honest |
| Products per page | 24 on the root, 33 under a child node |
| Exact count | Up to 1,000, like amazon_eg ("over 1,000" above that); the planner splits by price |
| Children | The Department sidebar, with the root in each link's path (`rh=n:211761638031,n:<child>`), so `_child_nodes` works unchanged |
| Product links | `/-/en/dp/<asin>?almBrandId=sAuWWBROaG&fpw=alm` opens the Amazon Now offer; plain `/dp/<asin>` shows the regular one |
| Catalogue | About 8,000 to 12,000 products (root price bands: 0 to 15 EGP 339, 15 to 25 696, 25 to 350 over 1,000 each, 350 to 700 608, 700+ 469) |
| Crossed-out prices | On about 44% of cards. Almost all are "List:" (RRP claims); "Was:" is rare |
| Deals feed | **None.** The Today's Deals API (`deals:211761638031`) returns 0 products, and the "All Discounts" facet (`p_n_deal_type:26901036031`) returns 1. The adapter rejects `deals:` |
| Who can crawl it | **Depends on the delivery location.** The Mac's anonymous session has a served default location ("FREE delivery within 28 mins"). The droplet's (Frankfurt) is not served: the root says "no results". **Mac only** |

## Traps

- **The root must be in every query.** `rh=n:18020637031` (Grocery) without the root lists the regular Amazon offers
  at the regular prices, and so does `i=amazonyalla&rh=n:<node>,p_36:…`. The adapter always puts `n:211761638031`
  first. `i=amazonyalla` (the "Amazon Now" entry in the search box) and `almBrandId=sAuWWBROaG` are not needed once
  the root is in `rh`.
- **A node without a price filter is a landing page** with no product cards, as on amazon_eg. Every category query
  carries `p_36` (0-unbounded when there is no band).
- **An unserved location gets "no results" for the whole store.** That would look like an empty shop, so a 0 total
  on the unpriced root raises `Blocked` ("delivery location not served") and the coordinator pauses that worker.
  A narrow price band may truly be empty and is recorded as such. Keep `amazon_now_eg` out of `DEALS_SITES` on
  workers outside the served areas (the droplet, cloud machines).
- The storefront pages (`/fmc/storefront`, `/alm/category/…`, `/fmc/deals/yalla`) are built by JavaScript; over
  plain HTTP they have no product cards. The search pages are the only listing to crawl.
- Sold-out-at-this-dark-store products show a card with no price, recorded as out of stock.
- Same IP, same WAF as amazon_eg: the two shops together double the Amazon request rate from a worker, so
  `[sites.amazon_now_eg]` waits 3 to 5 s between pages (amazon_eg 2 to 4 s). The adapter keeps its own cookie file
  (`amazon_now_eg_cookies.json`) and solves the challenge in a headless browser when it has none.

## What we track

One watch group, "Amazon Now" (أمازون ناو), with the whole store as its root (`category:211761638031`):
`min_price = 50` (cheap snacks neither crawled nor alerted), starting interval 30 minutes, never slower than 60.
Every product is tracked, not only those with an offer. Real discounts are judged against each offer's own
history, and an offers-only crawl would have to trust the crossed-out "List:" prices, which are RRP claims.
Same alert rules as every shop.

Same-product matching: an Amazon Now offer and the regular Amazon offer of the same ASIN have the same title,
so an Amazon Now alert lists the regular Amazon price among "other shops" (and the reverse).

## How it was measured (3 November 2026)

Plain HTTP (`curl_cffi`, Chrome fingerprint) with the Mac worker's saved amazon_eg cookies. Root and child queries
with and without `i=amazonyalla` / `almBrandId`, a 10-band size estimate, product pages with and without
`fpw=alm`, and the deals API on the root and on Grocery. From the droplet, the same queries with its worker's
cookies: Grocery without the root gave the regular prices; the root gave "no results"; Grocery under the root gave 9
cards and no count.
