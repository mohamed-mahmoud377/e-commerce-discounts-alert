# GC Shop (gcshopeg.com): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/gcshop_eg.py` on `sites/shopify.py` (site name `gcshop_eg`); tests are `test_shopify_*` in `tests/test_parsers.py` against `tests/fixtures/gcshop_products.json.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | جي سي شوب |
| What it sells | Watches: Casio, Citizen, Naviforce, Guess, Fossil, Q&Q, Swatch. 7,729 of 8,500 products; the rest are Zippo lighters, jewellery, backpacks, hair clippers. Median 6,280 EGP |
| Platform | **Shopify**, English only (`/ar/` is a 404) |
| What we fetch | `/products.json?limit=250&page=N` for `category:all`, the whole shop in a stable order |
| Price filter | None |
| Who can crawl it | **Anyone**: the Mac and the droplet |
| Keyed by | Shopify's product id |
| Catalogue | **8,500 products** (34 pages; the sitemap agrees), 3,447 of 8,554 variants in stock |

## Access and robots.txt

Cloudflare in front, 200 from both. `robots.txt` is Shopify's standard: `products.json` isn't blocked.

## The listing, and what is odd

- **`products_count` is no count:** `all` says 3,630 while its listing and the sitemap hold 8,500. Exactly 8,500 looked like a cap, so it was checked against the sitemap (5 Nov 2026). It isn't one.
- Sold-out products stay listed. An empty 35th page ends the walk.
- Variants: `Size` on 39 products and band colours on 2, none priced differently, so all fold into one listing.

## Prices

Crossed-out price on 3,107 of 3,447 in stock (90%), median 10%, **none as much as 30%**. It looks like a permanent markdown. Kind `list`, no rule: no new-listing claim reaches the 30% needed, so these can't alert, and our own history decides. Sells its own stock. Titles carry the model code (`GUESS CRYSTAL CLEAR GW0470L1`), which matching reads; `slug_codes` stays off.

## How we crawl it

The whole shop in **Watches** (minimum 1,000 EGP, every 15 minutes, cap 30, since 5 Nov 2026), including its 657 non-watches above 1,000 EGP: a Zippo lighter's alert says "ساعات". Reading only watches would need one root per brand, overlapping. 34 pages, about 2 minutes a pass at 2 to 4 s: **about 70 pages an hour**, shared by the droplet and the Mac. No block history.

## Running and checking it

```bash
.venv/bin/deals crawl gcshop_eg "category:all" --pages 2 --show 5
.venv/bin/python .claude/skills/add-shop/measure_shop.py gcshop_eg "category:all" --pages 5
```

## Troubleshooting

`HTTP 430` is Shopify's own block. `gc-stores-hotdeals` (53) is a small deals collection, not read separately: the whole shop passes at least every 30 minutes.

## Alert screenshots

English only. Price `.price` (the first is the product's, related products have their own), context `h1` and `.carousel-cell.is-selected img.product__image`. About 3.5 s. Saved page: `tests/fixtures/gcshop_product_rendered.html.gz`.
