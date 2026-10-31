# Dream 2000 (dream2000.com): site analysis and how we crawl it

Everything we learned about dream2000.com (Dream 2000, a mobile and electronics chain) while adding it as the
seventh shop (27 October 2026). The adapter is `src/deals/sites/dream2000_eg.py` (site name `dream2000_eg`);
its tests are in `tests/test_parsers.py` (`test_dream2000_*`) against the saved collection JSON
`tests/fixtures/dream2000_products.json.gz` (the Mobiles collection in English). Sizes are from October 2026
and drift.

## Summary

| Question | Answer |
| --- | --- |
| Site name in the code and config | `dream2000_eg` (Arabic name in alerts: دريم 2000) |
| Platform | **Shopify** (moved from Magento: collection descriptions still contain old `/en/<path>.html` links). Arabic is the default locale, English under `/en/` |
| What we fetch | Shopify's public collection JSON: `/en/collections/<handle>/products.json?limit=250&page=N`, plus `/en/collections/<handle>.json` for the exact count |
| Products per request | 250 (Shopify's maximum) |
| Deepest page | No cap; the biggest collection (Accessories, 841) is 4 pages |
| Exact count | Yes, `products_count` on the collection JSON |
| Price filter | **None** on these endpoints. A slice is always a whole collection; the coordinator drops products under the group's minimum price itself (`max_slice_total` 10,000 so the planner never splits) |
| Search | None usable (Shopify's suggest endpoint stops at 10 results); `search:` targets are refused with `UnknownTarget` |
| Bot protection | Cloudflare in front, but plain `curl` and Chrome's fingerprint both pass, **from the droplet too**; a burst of 10 requests was fine. Shopify answers HTTP 430 when it does block a client |
| Who can crawl it | Anyone: the Mac and the droplet both have it in `DEALS_SITES` |
| How products are keyed | Shopify's numeric product id, plus the non-colour variant options when they differ: `8255092588683`, `8255092588683:8gb-256gb` |
| Catalogue | 6,015 products in 386 collections (`/en/collections.json?limit=250&page=1..2`); about half of all products are out of stock |
| Politeness | 1 to 2 s between requests per worker (`[sites.dream2000_eg]`); one request covers 250 products |
| Tracked | about 1,000 products across all groups (about 45 collections), roughly half in stock |

## Access and endpoints

| What | URL |
| --- | --- |
| Every collection, with counts (250 a page, 2 pages) | `https://dream2000.com/en/collections.json?limit=250&page=1` |
| One collection's count and title | `https://dream2000.com/en/collections/mobiles.json` (HTTP 404 for an unknown handle) |
| A collection's products | `https://dream2000.com/en/collections/mobiles/products.json?limit=250&page=1` (an unknown handle answers `{"products": []}` with HTTP 200, hence the separate count request) |
| Product page | `https://dream2000.com/en/products/<handle>` |
| Arabic | the same URLs without `/en/` (titles in Arabic; `?locale=en` does nothing) |

`https://dream2000.com/` sometimes refuses the TCP connection to plain `curl` while `www.dream2000.com` and the
`/en` paths answer; Chrome's fingerprint through `curl_cffi` had no trouble. The order of a collection is
stable between requests (no `sort_by` on `products.json`), and the collections are small, so paging is safe.

## What a product contains (Shopify's product JSON)

| Field | Example | Used as |
| --- | --- | --- |
| **`id`** | 8255092588683 | `Listing.sku` (with a variant suffix, see below) |
| **`title`** | "Samsung Galaxy A17" | title (plus the variant group, e.g. "(8GB / 256GB)") |
| **`handle`** | `galaxy-a17` | product URL |
| **`vendor`** | "Samsung" | brand (371 vendors; "Dream 2000" for house brands) |
| **`options`** | `[{"name": "RAM"}, {"name": "Storage"}, {"name": "Color"}]` | which variants to fold together |
| **`variants[].option1..3`**, **`price`**, **`compare_at_price`**, **`available`** | "8GB", "256GB", "Black", "20490.00", "24600.00", true | price, crossed-out price (kind `list`), in stock |
| `tags` | barcodes and `collection:gaming` | not used (a product's collections are in its tags) |
| `product_type` | always empty | not used |
| `published_at`, `updated_at`, `images`, `body_html` | | not used |

**Variants.** 1,002 of the 6,015 products have several variants and 506 of those have variants at different
prices. Option names: Color 4,288, Title (the "Default Title" of single-variant products) 1,537, Storage 491,
RAM 372, Capacity 297, Wattage 264, Size 227, Length 195, SKU 45. A phone lists every RAM/storage/colour
combination as a variant, and the small model is often sold out while the big one is in stock; folding a
product to one price would then show a "price drop" every time the small model comes back. So variants are
grouped by every option except colour (`Color`, `Colour`, `SKU`, `Title` are ignored): each group is one
listing keyed `<id>:<values slug>` with the group's cheapest *available* variant (or the cheapest variant when
none is available), the product title plus "(8GB / 256GB)", and `in_stock` when any variant of the group is
available. A product with only a colour option (or none) is one listing keyed by its id.

**Prices.** 83% of the catalogue carries a `compare_at_price` above the price, so it is the shop's own claim
(kind `list`); new-listing alerts rarely fire, alerts come from our own history. Out-of-stock products keep
their price (unlike Cairo Sales) and stay listed (unlike Jumia, 2B and B.TECH), so going out of stock is seen
directly.

## Category map (watch groups)

Shopify has no category tree: collections are flat, and Dream 2000 has one per brand ("Samsung Tvs" 16,
"LG Tvs" 10...) next to the general ones, plus marketing collections ("48 Hours offers", "Last Piece" 126,
"Back to school"). The general collections are used; a product in several collections has the same id and is
stored once (the first group that sees it keeps it). Counts on 27 October 2026 (products, about half out of
stock).

| Group | Dream 2000 handles | Products |
| --- | --- | --- |
| TVs | tvs (`brands` "All TV" 19 is a subset) | 78 |
| Refrigerators | refrigerator-and-deep-freezer, deep-freezer-1 | 51 + 47 |
| Air conditioners | air-conditioners, air-conditioner (two overlapping collections) | 61 + 71 |
| Gaming consoles | consoles (PS5, Switch 2, a gamepad) | 6 |
| Phones | mobiles | 112 |
| Tablets | tablets | 31 |
| Laptops | laptop (business-laptop 51, gaming-laptop 37 are subsets) | 103 |
| Desktops | computer | 19 |
| Monitors | monitors, gaming-monitors | 40 + 20 |
| PC components | nividia-rtx50 (graphics cards), internal-hard, storage | 28 + 4 + 26 |
| Headphones | airbuds, wired-headphone, bluethooth-headphones, headset, gaming-headset | 123 + 63 + 25 + 24 + 45 |
| Wearables (from 31 Oct) | wearables (smart-watch 104 and smart-band 9 are subsets) | 108 |
| Chargers & power banks (from 31 Oct) | chargers, power-bank, wireless-chargers, car-chargers (`ravpower` 50 is a brand subset) | 164 + 106 + 13 + 40 |
| Kitchen machines | air-fryer, kettles-and-coffee-makers ("Coffee Makers"), blender-and-mixers, hand-blender, mixers, food-processor, microwave, electric-oven | about 250 |
| Kitchen appliances | kattle (kettles), irons, grill, sandwich-maker, water-dispenser-and-filters, water-filters | about 190 |
| Cooking & dishwashers | stoves, flat-stove, oven, built-in, dish-washers | 25 + 15 + 6 + 14 + 6 |

Not tracked: accessories 841, mobile-accessories 551, appliances 697 (everything electrical),
cables 202, vacuum-cleaner 45, fans 64, water-heaters 18, security cameras.

## Performance and capacity

A 250-product answer is about 1.5 MB and takes about a second. About 45 collections are about 50 requests
per pass (plus one count request per collection), two minutes of one worker.

## Running and checking it

```bash
.venv/bin/deals categories dream2000_eg                 # the main handles; the collections.json URL lists all 386
.venv/bin/deals categories dream2000_eg mobiles         # the count (Shopify has no children)
.venv/bin/deals crawl dream2000_eg "category:mobiles" --show 5     # the price filter is ignored by the shop
```

On the server, `make sql`:

```sql
SELECT grp, count(*) products, sum(in_stock) in_stock FROM products WHERE site = 'dream2000_eg' GROUP BY grp;
SELECT kind, count(*), sum(failures > 0) failing, min(next_due_at) FROM jobs WHERE site = 'dream2000_eg' GROUP BY kind;
```

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `UnknownTarget: no collection with handle 'x'` | Wrong handle (the count request got 404) | Find it in `https://dream2000.com/en/collections.json?limit=250` (page 1 and 2) |
| `UnknownTarget: Shopify has no product search` | A `search:` target in the config | Use a collection handle |
| `blocked: dream2000_eg: HTTP 430` | Shopify's bot block for this client | Pauses by itself; lower the request rate if it repeats |
| A slice suddenly parses 0 products | The JSON changed shape (unlikely: Shopify's `products.json` is a stable public format) or the collection was emptied | Open the URL in a browser |
| Many "new product" listings for one phone | Variant grouping changed (a new option name) | Add the option name to `IGNORED_OPTIONS` in `dream2000_eg.py` if it is a colour-like option |

## Barcodes (30 Oct 2026)

Each variant's `sku` is its GTIN-13 barcode for almost every product (481 of 498 variants in the saved
collection page pass the check digit; e.g. Samsung Galaxy A17 `8806097657460`). The parser passes the
barcodes of a listing's variants (its colours) as `Listing.gtins`; matching uses them as its strongest
evidence (docs/MATCHING.md).
