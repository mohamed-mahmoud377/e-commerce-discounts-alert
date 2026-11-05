# Azzam Watches (azzamwatches.com): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/azzam_eg.py` on `sites/shopify.py` (site name `azzam_eg`); tests are `test_shopify_*` in `tests/test_parsers.py` against `tests/fixtures/azzam_products.json.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | عزام للساعات |
| What it sells | Watches from Casio (1,095 EGP) to Richard Mille (133,466,700 EGP): 7,167 of 7,287 products. Median 50,400 EGP |
| Platform | **Shopify**, English by default, Arabic under `/ar/` |
| What we fetch | `/products.json?limit=250&page=N` for `category:all` |
| Price filter | None |
| Who can crawl it | **Anyone**: the Mac and the droplet |
| Keyed by | Shopify's product id, plus any option but the SKU |
| Catalogue | **7,287 products** (30 pages), 7,275 of 7,337 variants "available" |

## Access and robots.txt

Cloudflare in front, 200 from both. `robots.txt` is Shopify's standard: `products.json` isn't blocked.

## The listing, and what is odd

- **`products_count` is no count:** `best-selling-products` says 21,227 and lists 7,287, the same products as `/products.json`.
- **Nearly everything is "available"** (99%), with listings like a Patek Philippe at 81,920,940 EGP. These look like orders on request, so a sell-out is rarely seen. Don't read `in_stock` as stock on the shelf.
- Options on 10 products (colour, case, dial, movement), 4 priced differently: nothing is folded, each is its own listing.

## Prices

Crossed-out price on 284 of 7,275 (4%), median 15%, none at 60%: kind `list`, no rule. Prices aren't round, with many repeats across models of one brand, so **they may move together with the dollar. Watch the flip-flop count and the price changes after a few days**: a shop-wide reprice under the 20% drop needed can't alert, but it fills the history. Sells its own stock. Titles carry the model code (`CASIO - MTP-VT05L-7EDF`); `slug_codes` stays off.

## How we crawl it

The whole shop in **Watches** (minimum 1,000 EGP, every 120 minutes, cap 180). 30 pages, under 2 minutes a pass at 2 to 4 s: **about 15 pages an hour**, shared by the droplet and the Mac. No block history.

## Running and checking it

```bash
.venv/bin/deals crawl azzam_eg "category:all" --pages 2 --show 5
.venv/bin/python .claude/skills/add-shop/measure_shop.py azzam_eg "category:all" --pages 5
```

## Troubleshooting

`HTTP 430` is Shopify's own block.

## Alert screenshots

**English, deliberately.** On `/ar/` the gallery's carousel makes the page 11,280 px wide in right-to-left, and the crop comes out blank white (both Arabic shots were the same 9 KB, which gave it away; the English one is 93 KB). Price `.price-list--product`, context `h1.product-title` and `.product-gallery__media.is-initial img`. About 3 s. Saved page: `tests/fixtures/azzam_product_rendered.html.gz`.
