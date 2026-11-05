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
- **The two collections we read hold almost only what is in stock** (13,690 of 13,700 products on 5 Nov 2026). A product that sells out leaves them, so its row keeps its last price and `in_stock` until it comes back: a sell-out isn't seen, and `last_seen_at` is what tells. Harmless for alerts: nothing changes, so nothing is judged.
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
| Fashion | `all-new-in` | 15,614 | 63 |

One pass is about 7 minutes at 2 to 4 s (page 0.5 s + 3 s), against the group's 180-minute cap: **about 55 pages an hour**, shared by the droplet and the Mac. No block history. No fast lane: Shopify has no feed by date, and the sale collection is too big to read every 10 minutes.

**Why these two and not everything.** The whole shop can't be read from one root (the 25,000 cap). Same choice as DeFacto: the sale section, and new arrivals so that a product has a history before it goes on sale. Rolled out one at a time for memory: with `all-sale` in, a matching pass peaked at 154 of 300 MB on 190,000 products (5 Nov 2026), so `all-new-in` followed the same evening. `systemctl show deals-coordinator -p MemoryPeak` shows it, though that number counts page cache too.

## Running and checking it

```bash
.venv/bin/deals crawl wayup_eg "category:all-sale" --pages 2 --show 5
.venv/bin/python .claude/skills/add-shop/measure_shop.py wayup_eg "category:all-sale" --pages 5
```

## Troubleshooting

**Idle on every device, no `crawl wayup_eg` line in `make health`:** no `[[watch]]` names it. That was its first 15 minutes: the shop went live on 5 Nov 2026 with `[sites.wayup_eg]` in `/etc/deals/config.toml` but no `wayup_eg = "category:all-sale"` in Fashion, so there were 0 jobs and 0 products, and workers reported "nothing due". `make health` now warns (`watch wayup_eg`).

`HTTP 430` is Shopify's own block: slow it down from the web UI's Shop settings (Pace). `no collection with handle` means a handle went: `https://wayupsports.com/collections.json?limit=250`. Names like `adidas-sale-bf26` are seasonal; `all-sale` has lasted.

## Alert screenshots

`wayupsports.com/ar/` for `wayupsports.com/`. Price `.product-info__price`, context `h1.product-info__title` and `.product-gallery img`. About 3 s. Saved page: `tests/fixtures/wayup_product_rendered.html.gz`.
