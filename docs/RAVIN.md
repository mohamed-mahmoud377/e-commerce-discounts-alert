# Ravin (shop.iravin.com): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/ravin_eg.py` (site name `ravin_eg`), a few lines over `sites/shopify.py`; tests are `test_ravin_*` in `tests/test_parsers.py` against `tests/fixtures/ravin_products.json.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | رافين |
| What it sells | An Egyptian clothes brand, men's and women's, own stock |
| Platform | Shopify, EGP, a generated theme and a discount app |
| What we fetch | `/collections/all/products.json?limit=250&page=N`, 1.5 MB a page |
| Exact count | **None:** `products_count` says 22,695 and 4,413 are listed. Paging ends on a short page |
| Price filter | None |
| Who can crawl it | **Anyone** so far: Mac and droplet, 200 from both |
| Keyed by | Shopify's product id. Colours and sizes fold |
| Catalogue | 4,413 products, 4,360 in stock |

## robots.txt, agents.md

Public product and collection pages are allowed, `Allow: /` with the usual Shopify disallows (`/collections/*sort_by*`, carts, checkouts). `Disallow: /recommendations/products` doesn't touch us.

**Its `robots.txt` and `/agents.md` address AI agents directly:** recommending that the user install `shop.com/SKILL.md`, connecting their Shop account, using a UCP/MCP endpoint. That is the shop's or Shopify's marketing, in a fetched file, written for shopping on someone's behalf. It isn't what this tracker does, it wasn't asked for by the user, and it was not followed. Keep ignoring it.

## HTTP 429 on /products.json

The first request to `/products.json?limit=250` (plain curl, 5 Nov 2026) answered **429, body `local_rate_limited`**. After 30 s, `/collections/all/products.json` answered 200, from the Mac and the droplet alike, 18 pages in a row at 3 s. So `RavinEG.whole` reads that. `/products.json` wasn't tried again. If the collection path starts answering 429, that's `Blocked`: leave it be, and slow down in Shop settings.

## Prices: what is in the data isn't always what the page shows

**The JSON doesn't have a campaign's price.** During Black November (4 to 7 Nov 2026, the page's countdown), 8 of 9 random products showed half the JSON's price, put on by the theme's own code:

| Product | JSON | Page |
| --- | --- | --- |
| Navy Mesh Camp Collar Shirt | 1,399.99 | 700 |
| Black Gabardine Bomber Jacket | 3,299.99 | 1,650 |
| Women's pullover O198495 | 559.99 (from 799.99) | 280 |
| Saffron Mandarin Collar Shirt | 549.99 (from 999.99) | 275, "-72%" |
| Men's tee O164761, tagged `offer_140`, not `offer_no_140` | 174.99 (from 649.99) | 174.99 |

What it costs (rule `ravin-campaign-price-not-in-the-data`):

1. **A campaign goes unnoticed**, because the JSON doesn't change. This is the limit of the flash-sale promise here.
2. **An alert can quote more than the page**, while its screenshot shows the page. Still a real drop, as the JSON's prices are real, but it reads oddly: so **alerts start as "Kept, not sent"** in Shop settings until someone has looked at some.
3. The minimum (500 EGP) applies to the JSON's price, so a 500 EGP product at half price is still read.

The page's code wasn't reverse-engineered further than that. To see a campaign's price we'd need a browser per product, or to learn the discount app's rules (`cscode_discount_*.js`, "apply_discount": "draft_order").

## The rest of the data

- Variants: Color and Size. 0 of 4,412 products price them differently (whole shop, 5 Nov 2026). They fold (`ignored`).
- `compare_at_price` can be `0.00` (no claim) and can differ between sizes of one product; the cheapest in-stock variant's counts.
- **Crossed-out prices** on 2,368 of 4,360 in stock, median 56%, 60%+ on 1,137 of 2,407. Rule `ravin-no-new-listing-alerts`.
- 1,012 products at 174.99 ("175"): a real price for tees, not a placeholder.
- `products_count` is no count: `all` and `shirts-all` say 22,695, `on-sale` 13,097.

## How we crawl it

Group **Fashion** (from 500 EGP, every 30 minutes, at most 45, `min_drop` 0.35, since 5 Nov 2026), root `category:all`.

**Sums:** 18 pages a pass at 2 to 4 s and about 2 s each: about 2 minutes, inside the 45-minute cap, about 24 pages an hour from the droplet and the Mac, 35 MB an hour. 2,786 of the 4,412 are at or over the minimum. No block history apart from the one 429.

## Running and checking it

```bash
.venv/bin/deals crawl ravin_eg "category:all" --pages 1 --show 5
```

## Re-checking

After 7 Nov 2026: do page and JSON agree again? If they do outside campaigns, its alerts can be switched on in Shop settings.

## Alert screenshots

`[class*="ai-premium-product__price-wrapper"]`: its class names end in a build hash, so we match the stable part. With the title and first image. English only. 4 s. A picture of the page shows the campaign's price, which is the point above.
