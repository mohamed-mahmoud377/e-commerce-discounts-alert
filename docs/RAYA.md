# Raya Shop (rayashop.com): site analysis and how we crawl it

Added 5 November 2026 as the twelfth shop. The adapter is `src/deals/sites/raya_eg.py` (site name `raya_eg`); tests are
`test_raya_*` in `tests/test_parsers.py` against `tests/fixtures/raya_tvs.json.gz` and `raya_deposits.json.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | راية شوب |
| What it sells | Everything electrical, a marketplace: Raya's own stock next to third-party sellers (SKUs ending in `-Teba HA`, `-Grohy`, `-NewTrade`) |
| Platform | Magento 2 behind a Nuxt 2 storefront |
| What we fetch | Its **GraphQL API**: `POST https://api-rayashop.global.ssl.fastly.net/graphql`, header `Store: en` |
| Products per request | 100 (any `pageSize` works) |
| Exact count | `total_count`, which **stops at 9,999**: only the whole catalogue reaches it. Mobiles & Tablets alone is 5,446 |
| Price filter | `price: {from, to}` on the current price, inclusive at both ends (ask for 1,000 to 1,000: 7 products all at 1,000) |
| Who can crawl it | **Anyone**: the Mac and the droplet both got clean answers (5 Nov 2026) |
| Keyed by | Magento `sku`. Each seller has its own SKU, so one key has one price |
| Tracked | 7,667 products above the group minimums in 36 roots |

## Access and blocking (tested 5 Nov 2026)

`www.rayashop.com` is behind Cloudflare; the API host is on Fastly and answers plain curl from the droplet too. No 403 or 429 in any request at about
2 s. Introspection is off ("GraphQL introspection is not allowed"), so fields were found by trying them: Magento's "Cannot query field" answer is the
schema. **A whole-tree `categoryList` takes 15 s and once timed out at 40 s**; asking for one category's children takes about a second.

## robots.txt

Blocks checkout, account, `/search/` and Magento's `?p=`, `?dir=`, `?limit=` on the HTML pages. The API host is not mentioned, and we send no such URLs. Same position as
2B (docs/2B.md).

## The listing

| What is asked | Answer |
| --- | --- |
| Empty price range, unknown category id in the filter | `total_count: 0`, HTTP 200 |
| Unknown category | `categoryList` answers `[]`: this is how the adapter tells the two apart (`UnknownTarget`) |
| Sort | `sort: {price: ASC}`; 3 pages of 295 TVs read 295 unique products, none repeated. The first rows can be bundles out of price order |
| Sold-out products | **Not returned**: every product read was `IN_STOCK`, and `stock_status` can't be filtered. A sell-out disappears |
| A category's `product_count` vs `total_count` | Slightly different (TVs parent 642 vs 629): use `total_count` |

## Prices and stock

- `final_price` is the price, `regular_price` the crossed-out price, kind `list`. **It is on 97% to 100% of products, median 20% off**, and is often exactly price x 1.25 (37 of 200 TVs; iPhone 18 Pro Max
  129,999 "was" 162,498.75, a price nobody would set). Hence the `raya-no-new-listing-alerts` rule.
- `only_x_left_in_stock` is shown on most: 3 or fewer on 64% of TVs and 29% of phones (`raya-low-stock` rule).
- **Pre-booking deposits**: 10 "Pre Booking - Samsung Galaxy Z Fold8" SKUs at exactly 10,000 EGP. Skipped in the adapter (`raya-prebooking-deposits`). The shop's count still includes them.
- Sellers: `marketplace_seller` is a number and `seller { uid }` an id; the API has no name for it (`seller { name }` is refused). The name is only on the product page: its link
  is `/ar/seller/<uid>?name=Raya%20Shop`. Raya's own sellers are in the page's `raya_seller_uids`. So `Listing.seller` is empty, and the screenshot's seller box shows who sells it.
- Raya is a marketplace but gets no "check the seller" warning: `alerts.py` and `publish.py` hard-code that to noon. **Worth deciding after seeing real alerts.**

## Catalogue and categories

The root (`2`) has about 260 children: 11 real ones (`include_in_menu: 1`) and the rest campaigns (`free-shipping` 16,354, `raya-anniversary` 6,648). See docs/CATEGORIES.md for the ids.
**Screen protectors are 3,599 of the 5,220 mobile accessories**, which is why we watch sub-categories and not `Mobiles & Tablets`.

Offer categories exist and could become a fast lane (Jumia's flash sales precedent): `flash-sale` 1437 (22), `daily-deals-offers` 3722 (106), `mega-deals` 4771 (128), `one-day-offer` 4777 (50), `lucky-hour` 4779 (42). **Not used yet:** it
needs a new watch group, which is the user's call, and the pass is already short.

## How we crawl it

- Groups and roots: `config.example.toml`. About 94 pages a pass over all groups.
- `max_slice_total` 2,000 (20 requests), split by price above that; Built-in (1,051) stays whole.
- Delays 2 to 4 s: a page plus a pause is about 4 s, so **a whole pass is about 6 minutes of one worker**, well inside every group's cap.
- **Pages per hour**: about 225 at the groups' base intervals (ACs 54, kitchen machines 48, fridges 24, others under 20), about one request per 16 s, shared by the droplet and the home workers. No block history.

| Group | Min EGP | Roots (products above the minimum) |
| --- | --- | --- |
| TVs | 3,000 | 534 (295) |
| Refrigerators | 5,000 | 293 (440), 294 freezers (287) |
| Air conditioners | 8,000 | 298 (849) |
| Gaming consoles | 8,000 | 338 (12) |
| Phones | 2,500 | 643 (292) |
| Tablets | 5,000 | 641 (11) |
| Laptops | 8,000 | 270 (270) |
| Desktops | 8,000 | 271 (7) |
| Monitors | 2,500 | 273 (50) |
| PC components | 1,500 | 3124 storage (17), 4677 hardware (17) |
| Headphones | 800 | 285 (384) |
| Kitchen machines | 1,500 | 3463 air fryers (308), 358 coffee (275), 2148 blenders (590), 356 mixers (195), 2200 processors (174), 313 microwaves (215), 335 ovens (60) |
| Kitchen appliances | 2,000 | 334 kettles (147), 372 grills (84), 371 sandwich (96), 305 dispensers (181), 359 filters (51), 302 irons (261) |
| Cooking & dishwashers | 5,000 | 326 cookers (309), 296 dishwashers (160), 1561 built-in (1,051) |
| Gaming peripherals | 1,000 | 329 mice (42), 330 keyboards (43), 346 accessories (23), 757 joysticks (28) |
| Wearables | 1,200 | 363 (141) |
| Chargers & power banks | 500 | 369 (207), 282 (95) |

Not watched: washing machines 455, water heaters 350, fans 643, vacuum cleaners 363, personal care 1,243: no group for them. Tell the user if a group is added.

## Running and checking it

```bash
.venv/bin/deals categories raya_eg 316
.venv/bin/deals crawl raya_eg "category:643|price:2500-" --pages 1 --show 5
```

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `GraphQL error ... (API changed?)` | A field was renamed | Find the new name in a product page's payload, or by trying it |
| Timeouts on the API host | Its Fastly host changed: read `apiURL` in `window.__NUXT__` on the home page | Update `GRAPHQL` |
| `raya_eg: no category with id` | Magento ids don't change often, but campaigns are deleted | `deals categories raya_eg <parent>` |

## Alert screenshots

`/ar/` for `/en/`. Price box: `div.ProductInfoColumn div.justify-between:has(span.currency)`, with the crossed-out price and "توفر". Seller box: `ul:has(a[href*="/seller/"])`, the list under "معلومات البائع".
Context: `h1.text-primary-700`, `a.MagicZoom img.object-contain`. About 5 s. Saved page: `tests/fixtures/raya_product_rendered.html.gz`.
