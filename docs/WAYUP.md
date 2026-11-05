# WayUp Sports (wayupsports.com): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/wayup_eg.py` on `sites/shopify.py` (site name `wayup_eg`); tests are `test_shopify_*` in `tests/test_parsers.py` against `tests/fixtures/wayup_sale.json.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | واي أب سبورتس |
| What it sells | Sports shoes, clothes, equipment, supplements: adidas, Nike, Skechers, Under Armour, Puma. Median 3,909 EGP |
| Platform | **Shopify**, English by default, Arabic under `/ar/` |
| What we fetch | `/collections/<handle>/products.json?limit=250&page=N` |
| Price filter | None; a slice is a whole collection |
| Who can crawl it | **Anyone**: the Mac and the droplet, with `curl_cffi` as Chrome |
| Keyed by | Shopify's product id. Sizes fold into one listing |
| Catalogue | About 35,000 products (its sitemap has 69,512 links, two locales each). `all` counts 45,430 |

## Access and robots.txt

Cloudflare in front; 200 from both, no refusal in about 250 requests at one every 2 s (5 Nov 2026). `robots.txt` is Shopify's standard: it blocks carts, checkouts, `sort_by` and filtered collections, not `products.json`.

## The listing

- 250 products a page. **A listing ends at page 100: page 101 answers HTTP 400**, so `/products.json` shows 25,000 of the shop and no root may be larger. `MAX_PAGES` in `sites/shopify.py` stops there.
- **`products_count` is no count** (see docs/GCSHOP.md); paging ends on a short page.
- Sold-out products stay listed with `available: false`: 13,356 of the first 25,000 products have something in stock.
- Empty: an unknown collection answers 404 at `/collections/x.json`, which the adapter checks.

## Prices, stock, identity

- **Sizes are not prices:** of 18,568 products with a size option, 12 have any variants at different prices. So `size` is folded, at the cheapest in stock. The other options (`Width`, `Weight`) stay separate. Listing each size would make about five times the rows.
- **Crossed-out price on 40,896 of 47,337 in-stock variants, median 25%, 60%+ on 110.** So a `no_claim_alerts` rule: its products alert only from our own history.
- **1 EGP is a placeholder:** 338 variants, none in stock, HOKA's among them. A `placeholder` rule under 10 EGP, which also catches 7 sold-out variants at 6.20.
- Sells its own stock, no seller. No low-stock numbers in the JSON. Titles are English; `slug_codes` is off: links are the shop's own `r-hj2855-370` codes, not model numbers.

## How we crawl it

| Group | Handles | Products | Pages a pass |
| --- | --- | --- | --- |
| Fashion (minimum 500 EGP, `min_drop` 35%) | `all-sale` | 12,434 | 50 |
| Fashion, **once memory allows** (below) | `all-new-in` | 15,614 | 63 |

`all-sale` alone is one pass of about 3 minutes at 2 to 4 s (page 0.5 s + 3 s), against the group's 180-minute cap: **about 25 pages an hour**, shared by the droplet and the Mac; about 55 with `all-new-in`. No block history. No fast lane: Shopify has no feed by date, and the sale collection is too big to read every 10 minutes.

**Why these two and not everything.** The whole shop can't be read from one root (the 25,000 cap), and 35,000 products is a quarter of the current database on a coordinator that already peaked at 244 of its 300 MB (5 Nov 2026). Same choice as DeFacto: the sale section, and new arrivals so that a product has a history before it goes on sale. **Without `all-new-in` a product is first seen already on sale**, so it alerts only when it falls further. Add `all-new-in` after a matching pass with `all-sale` shows the memory: `systemctl show deals-coordinator -p MemoryPeak`.

## Running and checking it

```bash
.venv/bin/deals crawl wayup_eg "category:all-sale" --pages 2 --show 5
.venv/bin/python .claude/skills/add-shop/measure_shop.py wayup_eg "category:all-sale" --pages 5
```

## Troubleshooting

`HTTP 430` is Shopify's own block: slow it down from the web UI's Shop settings (Pace). `no collection with handle` means a handle went: `https://wayupsports.com/collections.json?limit=250`. Names like `adidas-sale-bf26` are seasonal; `all-sale` has lasted.

## Alert screenshots

`wayupsports.com/ar/` for `wayupsports.com/`. Price `.product-info__price`, context `h1.product-info__title` and `.product-gallery img`. About 3 s. Saved page: `tests/fixtures/wayup_product_rendered.html.gz`.
