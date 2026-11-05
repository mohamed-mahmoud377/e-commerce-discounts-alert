# Faces (faces.eg): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/faces_eg.py` (site name `faces_eg`); tests are `test_faces_*` in `tests/test_parsers.py` against `tests/fixtures/faces_men.html.gz` and `faces_body.html.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | فيسز |
| What it sells | Perfume, makeup, skin care, hair and body. Dior, Armani, YSL and so on. Faces (Wojooh), a chain also in the UAE and Saudi Arabia. Sells its own stock: no seller box |
| Platform | Salesforce Commerce Cloud (Demandware, SFRA), Cloudflare in front, Constructor.io for search |
| What we fetch | `/on/demandware.store/Sites-Faces_EG-Site/en_EG/Search-UpdateGrid?cgid=<id>&start=<n>&sz=48`, with `X-Requested-With: XMLHttpRequest` |
| Products per request | 48 at most (`sz=200` still gives 48), about 850 KB, 0.4 to 1.2 s |
| Exact count | `.product-count .total`, "560 items" |
| Price filter | Unusable: see below |
| Who can crawl it | **Anyone** so far: the Mac and the droplet, curl and `curl_cffi` as Chrome |
| Keyed by | The tile's `data-pid`: a master (`PM_DIOR_Sauvage_EDP`) or a plain code (`637418042967`) |
| Catalogue | perfume 1,260, makeup 1,371, skincare 488, hair 451, body 267, gifts 189, home-fragrance 40, men-products 31, sale 554 (with overlaps) |

## robots.txt: the user's decision

It allows the bare category pages and product pages, but disallows every way to read past a category's first 48 products:

- `/on/demandware.store/*` and `*/Search-UpdateGrid` (what the Load More button calls)
- `/*&sz=*`, `/*pmin=*`, `/*pmax=*`, `/*prefn*`, `/*prefv*`, `/*?cgid=*`, `/*search?q=*`
- `*/Product-*`, so the variation endpoint too

Load More's address is also written in a Caesar cipher in the page (`data-refinement-url-cipher`, shift 3; `/rq/ghpdqgzduh.vwruh/...` is `/on/demandware.store/...`). It looks like a way to keep crawlers off these links.

**The user was told and chose to crawl it anyway on 5 Nov 2026**, the same call as Dubai Phone, LC Waikiki and Mazaya, on these terms: slow (Pace 2x, so 4 to 8 s) and alerts on "Kept, not sent" until they look right. Both are in the web UI's Shop settings. No block yet. **If it blocks us it means this rule is being enforced: switch it off rather than fight it.**

## The listing

| Asked | Answered |
| --- | --- |
| The grid without `X-Requested-With` | **HTTP 500**, with a 50 KB error page, whatever the query |
| The grid with no cookies | Works: the first answer sets 11 of them |
| `/en/perfume-for-men?start=48`, `?start=99999`, `?page=2`, `?start=48&sz=48` | HTTP 200 and **page 1 again** |
| Just past the end (`start=576` of 560) | **39 repeated products under a different count, "615 items"**: stop on the count, never ask past it |
| Far past the end (`start=9999`) | No tiles, footer with `.category-id` |
| An unknown category | HTTP 200, no tiles, footer **without** `.category-id`: that tells it from an empty one. Its page `/en/<slug>` is a 302 to `/en/404` |
| `pmin` alone | **Ignored:** 560 either way |
| `pmin` with `pmax` | Narrows, but keeps a product if any of its sizes is in range: "5000 to 6000" gives 64 products priced from 950. So the planner must never split (`max_slice_total` 20,000) |
| `srule=price-ascending` | Works. Not needed: the default order is stable |
| `/` with no cookie jar | Redirects to itself until the client gives up. Not used |

Whole walks, 5 Nov 2026: perfume-for-men 560 of 560 in the default order and by price, the same 560; perfume 1,260 of 1,260; makeup 1,369 of 1,371, no repeats. Counts drift by one or two between requests.

**The sitemap is no catalogue.** `/en/sitemap_index.xml` has 1,569 English product URLs. 34 of 48 products on the men's perfume page are in it, and 0 of 48 on the sale page.

## Prices and stock

- **Sizes share one tile, which says "from".** 108 of 560 men's perfumes; never with a crossed-out price. Sauvage EDP shows "from 6760 EGP", 200ml selected, "+2 Sizes". We keep **one listing at that price**, with "(cheapest size)" in the title. Rule `faces-from-price-is-the-cheapest-size`.
- **What that costs.** (1) A sale on a bigger size alone goes unnoticed: Chopard Love's 100ml is 8,372.50, down from 9,850, while its tile shows 6,035 for the 50ml. (2) The page opens on the biggest size, so **the alert's screenshot can show another size's price** (Sauvage: alert 6,760, screenshot 14,230); the alert's own number is the right one, and the admins see both in the review group. (3) If the cheapest size sold out and came back, its return could look like a drop. Ten sampled pages had every size available, so how often is unmeasured.
- **Reading each size** is possible without a disallowed address: a product page lists every size's price (`.select-attribute-price`) and `?dwvar_<id>_size=100ml` selects one. It costs a 500 KB page per product, roughly 500 pages a pass: about an hour at Pace 2x, against 10 minutes for the rest of the shop. Not done.
- **The analytics JSON isn't comparable on those tiles.** Its `price` is another size's list price and its `discount` lands on the cheapest size: 14,230 less 7,470 reads as a false 52% off. On single-price tiles it agrees exactly: 608 tiles compared, no disagreement in price or stock. Prices come only from the price block.
- **An empty crossed-out block:** a product with no discount can carry `.strike-through .value` with `content="null"`, ahead of its real price (Prada L'Homme Intense, 9,200). Read `.js-price-color .value`.
- Sold out: `.out-of-stock-message` and no price, 5 of 560 men's perfumes.
- Crossed-out prices (measured before the empty-block fix, so the in-stock counts run up to 2% low) on 379 of 1,238 perfumes in stock (31%, median 20%, none at 60%) and 491 of 1,360 makeup products (36%, median 30%, 15 at 60% or more): kind `list`, and a `no_claim_alerts` rule. No placeholder prices: the most repeated are ordinary (1,380, 8,000, 6,900; Chopard Oud Malaki's 10,000 is real). No "only N left".
- `data-ean` holds a barcode on about a quarter of tiles (139 of 560): read into `gtins`. Model codes in links are the shop's internal ids, so no `slug_codes`.

## Catalogue and categories

The site's menu links are on any page; `input.category-id` on a category page gives its id, which here equals the slug. `/en/hair-care` and `/en/men` are 404: the ids are `hair` and `men-products`. docs/CATEGORIES.md has the roots.

## How we crawl it

Group **Beauty** (from 300 EGP, every 120 minutes, at most 180), shared with Mazaya:

| Root | Products | Pages |
| --- | --- | --- |
| perfume | 1,260 | 27 |
| makeup | 1,371 | 29 |
| skincare | 488 | 11 |
| hair | 451 | 10 |
| body | 267 | 6 |
| gifts | 189 | 4 |
| home-fragrance | 40 | 1 |
| men-products | 31 | 1 |

**89 pages a pass.** The adapter reads every page and the coordinator applies the 300 EGP minimum to new products, since a filter can't. At Pace 2x, 4 to 8 s plus about 1 s a page, a pass takes **about 11 minutes**, well inside the cap. Fast lane: `deals:extra-sale`, the sale's first 48, at most every 10 minutes. Together **about 50 pages an hour, about 43 MB**, from the Mac and the droplet, with no block history. Whatever overlaps is in one group, so no label flips.

Whether these roots cover the whole shop isn't proved yet: compare the sitemap's product ids with what the first pass read.

## Running and checking it

```bash
.venv/bin/deals crawl faces_eg "category:body-care" --pages 2 --show 8
```

## Troubleshooting

HTTP 500 from every grid request: the `X-Requested-With` header was lost, or the shop changed the grid. `HTTP 403` or a Cloudflare challenge: see robots.txt above.
