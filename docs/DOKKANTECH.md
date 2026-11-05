# Dokkan Tech (dokkantech.com): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/dokkantech_eg.py` (site name `dokkantech_eg`); tests are `test_dokkantech_*` in `tests/test_parsers.py` against
`tests/fixtures/dokkantech_keyboards.json.gz`. Same endpoints as Dream 2000: read docs/DREAM2000.md first.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | دكان تك |
| What it sells | Tech accessories: gaming peripherals, headsets, chargers, monitors, cameras. Median 1,799 EGP |
| Platform | **Shopify**. English by default, Arabic under `/ar/` |
| What we fetch | `/collections/<handle>/products.json?limit=250&page=N` and `/collections/<handle>.json` |
| Price filter | None; a slice is a whole collection |
| Who can crawl it | **Anyone**: plain curl and the droplet too |
| Keyed by | Shopify's product id, plus the non-colour options: `8634306953365:asia-box-english` |
| Catalogue | 1,612 products in `all`, 1,858 listings, 593 in stock (32%) |

## Access and robots.txt

Cloudflare in front, plain curl gets 200 from both the Mac and the droplet (5 Nov 2026). `robots.txt` is Shopify's standard: it blocks `sort_by` and filtered collections, not `products.json`.

## The listing, and what differs from Dream 2000

- **`products_count` is wrong, in both directions**: `all` counts 590 and lists 1,612 (7 pages); `audio` counts 268 and lists 205; `mouse` counts 124 and lists 124. So paging ends on a short page, never on the count, and `last_count != expected_total` is normal here (rule `dokkantech-wrong-counts`).
- `/collections/x/products.json` of an unknown handle: 200 with `{"products":[]}`; `/collections/x.json`: 404. The adapter checks the second.
- **Handles lie: `wearables-2` ("Wearables", 358) is headphones**; the real wearables are `wearables` (18, "Smart Watch & Band"). Sample a collection's titles before using it.
- Options that fold together are Dream 2000's (colour, SKU, title). **`Box` ("Asia Box", "MENA Box") looks like packaging but is a price:** of 66 products sold in two boxes, 41 price them differently (Anker 332 hub 1,399 Asia, 1,699 MENA; Logitech Superlight 2 7,799 and 8,199). Folded, every sell-out of the cheap box would look like a price change, so each box is its own listing (`<id>:asia-box`). `Region` (2 of 2 differ) and `Band color` (2 of 5) stay separate for the same reason.

## Prices and stock

Crossed-out price on 66% to 77% of what is in stock, median 13% to 15%, 60%+ on 3 of 1,072: kind `list`, no rule. Two thirds sold out and listed with a price: a sell-out is seen directly. No low-stock numbers. Sells its own stock, no seller. Variant `sku` is a shop code (`MOU538B-B2B-CN`), not a barcode, but **the product page shows the barcode** (`الباركود: 5099206030015`): a candidate for `details.PARSERS` later.

## How we crawl it

| Group | Handles |
| --- | --- |
| Monitors | monitors-tv (40) |
| PC components | computer-component 16, cooling 15, data-storage 11 |
| Headphones | wearables-2 (358, despite the name) |
| Gaming peripherals | mouse 124, keyboards 54, gaming-gamepad 4 |
| Wearables | wearables 18 |
| Chargers & power banks | charger 35, power-banks 12, car-charger 8 |

12 roots, about 14 requests plus 12 count requests a pass, about 1.5 minutes at 2 to 4 s. **About 40 requests an hour.** Delays start at the skill's default, though Dream 2000 runs at 1 to 2 s. No block history.

## Running and checking it

```bash
.venv/bin/deals crawl dokkantech_eg "category:mouse" --show 5
```

## Troubleshooting

As Dream 2000 (`HTTP 430` is Shopify's block). `no collection with handle` means a handle went: `https://www.dokkantech.com/collections.json?limit=250`.

## Alert screenshots

`dokkantech.com/ar/` for `dokkantech.com/`. Price: `.product-info__price`; context `h1.product-title`, `img.product-image`. About 3 s. Saved page: `tests/fixtures/dokkantech_product_rendered.html.gz`.
