# Miami Center (miamicenters.com): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/miami_eg.py` (site name `miami_eg`); tests are `test_miami_*` in `tests/test_parsers.py` against `tests/fixtures/miami_tablets.json.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | ميامي سنتر |
| What it sells | Phones, Apple, wearables, audio, home appliances, covers |
| Platform | **WooCommerce** on WordPress, English by default, Arabic under `/ar/` |
| What we fetch | The public Store API, `/wp-json/wc/store/v1/products`, as Hardware Market |
| Products per page | 100, with `_fields` about 113 KB; **about 5 s to build each** |
| Exact count | `X-WP-Total` |
| Price filter | `min_price`/`max_price` in **whole pounds**, inclusive at both ends (10,265 to 10,265 finds that product, 10,266 doesn't) |
| Who can crawl it | **Anyone**: the Mac and the droplet |
| Keyed by | Product id. All 2,991 are `simple`: one key, one price |
| Catalogue | 2,991 products, 1,930 in stock |

## Access and robots.txt

Cloudflare in front, 200 from both. `robots.txt` blocks only WooCommerce's internal paths and `add-to-cart`.

## Traps

- **Prices are whole pounds, not piastres:** `currency_minor_unit` is 0, while Hardware Market's same API gives piastres. Copying that adapter makes every price 100 times too small. The adapter scales by the unit each answer states.
- **`on_sale` means nothing:** 602 flagged, at a median 0.2% off (9,392 "was" 9,999 is the larger kind). Only a real gap between `regular_price` and `price` makes a crossed-out price. Kind `list`.
- **Pre-orders:** 18 products named `Pre Order - ...`, whose page says "Estimated preorder price". A change in an estimate is no drop, so they are skipped.
- **Price 0:** `Miami Product Protection` (an insurance add-on) and one gift listing: no price.
- **An unknown category answers an empty 200**, so ids are checked against the category list, once a session.
- **Parents hold products their children don't:** the Xiaomi Redmi Note 17 Pro sits directly in `mobiles-tablets`. So the roots are parents where that happens.
- The page's first `.price` is the mini cart's "0 EGP". The real one is its own widget.

## How we crawl it

| Group | Category ids | Products |
| --- | --- | --- |
| Phones | 52 (with tablets), 110, 91, 9326, 9327 | About 460 |
| Tablets | 95 (iPad) | 47 |
| Laptops | 200 (Mac) | 34 |
| Wearables | 103 (with buds, by brand), 1627, 199 | About 330 |
| Headphones | 166 (AirPods) | 10 |
| Chargers & power banks | 196, 108 | About 230 |
| Gaming consoles | 513 | 10 |
| Home & more | 1639 (appliances), 701 (speakers), 2737 (scooters) | About 620 |

Checked against the whole catalogue: 1 product in two groups (a Honor Pad, whose label flips). **Left out on purpose: about 1,100 phone covers, cables and small accessories**, median 557 EGP. No fast lane: small enough to read whole.

About 25 pages a pass, 4 minutes at 2 to 4 s plus the shop's 5 s. Phones' 15-minute cap holds (about 8 pages, about 1 minute). **About 40 pages an hour**, shared by the droplet and the Mac. No block history. Because each page costs the shop 5 s to build, slow it first (Pace) if it complains.

## Running and checking it

```bash
.venv/bin/deals crawl miami_eg "category:52" --pages 2 --show 5
.venv/bin/python .claude/skills/add-shop/measure_shop.py miami_eg "category:52" --pages 5
```

## Troubleshooting

`no category` means an id went: `https://miamicenters.com/wp-json/wc/store/v1/products/categories?per_page=100`.

## Alert screenshots

`miamicenters.com/ar/` for `miamicenters.com/`. Price `.miamicenters-price-container` ("سعر الكاش", the cash price, which is what the API gives), context `h1.product_title` and `img.wp-post-image`. About 4 s. Saved page: `tests/fixtures/miami_product_rendered.html.gz`.
