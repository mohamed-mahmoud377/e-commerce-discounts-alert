# Aghezty (aghezty.com): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/aghezty_eg.py` (site name `aghezty_eg`); tests are `test_aghezty_*` in `tests/test_parsers.py` against `tests/fixtures/aghezty_fryers.html.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | أجهزتي |
| What it sells | Home appliances: fridges, washing machines, air conditioners, built-in kitchens, small appliances, TVs. Sells its own stock: no seller box |
| Platform | Its own PHP site, Bootstrap, Cloudflare in front (not challenging us) |
| What we fetch | `/en/<category>/?min_price=&max_price=&page=N`, and the `window.AGHEZTY` JSON in the page |
| Products per request | 12, about 290 KB |
| Exact count | `#catalog-results-count`: "Showing 1 - 12 of 116 products available" |
| Price filter | `min_price`, `max_price`, whole pounds, inclusive at both ends |
| Who can crawl it | **Anyone** so far: Mac and droplet, curl and `curl_cffi` as Chrome |
| Keyed by | The site's numeric product id |
| Catalogue | 6,266 products (also 6,266 links in `sitemap-products-en.xml`) |

`robots.txt` disallows only `/sys/` and `/accounting/`.

## The listing

| Asked | Answered |
| --- | --- |
| `/en/Air-Fryer/` | 116 products, newest first |
| `?page=10` of 10 pages | 8 products |
| `?page=11`, `?page=99` | **The last page again**, HTTP 200: paging must stop on the count |
| `?min_price=5299&max_price=5299` | The 1 product at exactly 5,299 |
| `?min_price=1&max_price=2` | HTTP 200, no products, "No results found" |
| `?min_price=5000&max_price=6000` | 19 of 116 |
| `?available=1` | 101 of 116: sold-out products are listed unless asked not to be |
| `?per_page=60` | Ignored |
| `?sort=price-asc`, `?sort=discount` | Work. Not used |
| An unknown category | HTTP 404, 293 bytes |

**Whole walk, 5 Nov 2026:** Large-Appliances 173 pages, 2,074 of 2,074 unique, no repeats, in the default order.

## Prices and stock

- The JSON has `prices.num`, `oldPrices.num`, `names.en`, `names.ar`, `brand`, `sku` (the model), `in_stock`, `discount`. Numbers are whole pounds.
- **Price 0 means "ask us"** (16 De'Longhi products on 5 Nov 2026): stored as no price.
- **Sold out** stays listed with its price: 764 of 2,074 large appliances (37%).
- **Crossed-out prices** on 1,310 of 1,310 large appliances in stock, 215 of 215 air conditioners, 180 of 180 small appliances: median 14%, none at 60%+. So kind `list` and rule `aghezty-no-new-listing-alerts`. Its price often isn't round (17,441 for 14,999), which looks computed.
- "Only N left": not shown on cards.
- Compared with the page in a browser, 6 of 6 prices and crossed-out prices matched (5 Nov 2026).

## Catalogue and categories

`sitemap-categories.xml` lists them all. Each product names one leaf (`category`), and a parent adds up its children, so the roots are leaves, kept apart by group. `Built-In` is the exception: read whole, 1,357 products.

Counts on 5 Nov 2026: Large-Appliances 2,074 (Refrigerator 617, washing-machines 538, Freezers 233, cookers 209, Dish-washer 127, Refrigerator-Mini-Bar 52, Dryers 28, Air-Conditioner 270), Small-Appliances 2,089, Built-In 1,357, TV 561 (TVs 433, Sound-system 92, Projector 20).

`Air-Conditioner` isn't under TV or Small: check a category's parent in the sitemap before moving roots.

## How we crawl it

| Group | Roots | Products at or over its minimum | Pages | Cap (min) |
| --- | --- | --- | --- | --- |
| TVs | `TVs` | 432 | 36 | 30 |
| Refrigerators | `Refrigerator`, `Freezers`, `Refrigerator-Mini-Bar` | 896 | 75 | 30 |
| Air conditioners | `Air-Conditioner` | 246 | 21 | 15 |
| Kitchen machines | `Air-Fryer`, `Coffee-Espresso-Makers`, `Mixing-Blending`, `Grinding-Chopping`, `Pressure-Cooker` | 593 | 50 | 30 base |
| Kitchen appliances | `MICROWAVE`, `Ovens`, `Grill`, `Rice-Cooker`, `Food-Steamer`, `Portable-hot-plate`, `Breakfast-Appliances`, `Specialized-Appliances` | 459 | 39 | 120 |
| Cooking & dishwashers | `cookers`, `Dish-washer`, `Built-In` | 1,655 | 138 | 90 |
| Home & more | `washing-machines`, `Dryers`, `Vacuum-Steam-Cleaners`, `iron`, `Water-Dispenser`, `Water-Heater`, `Heaters`, `Fan`, `Ventilating-Fans`, `Air-purifier`, `Sound-system`, `Bluetooth-Speakers`, `personal-care` | 1,890 | 158 | 180 |

The price filter narrows the roots at the source: `|price:<group's minimum>-` goes in the URL. **Fast lane:** `deals:flash-sale`, the offers' one page of 28, every 10 minutes, under Home & more (those 28 take that group's label when it reads them last).

**Sums:** 514 pages, about 6,200 products. At 2 to 4 s between pages and about 1.5 s each, one pass of everything takes 39 minutes on one IP, 19 minutes on the droplet and the Mac together. On their base schedules the groups ask for about 820 pages an hour, inside what the two can read (about 1,600), and less as quiet slices back off to their caps. So about 400 pages, 120 MB, an hour from each IP. A drop is noticed within the group's cap, or the fast lane's 10 minutes for offers. No block history.

## Running and checking it

```bash
.venv/bin/deals crawl aghezty_eg "category:Air-Fryer" --pages 1 --show 5
.venv/bin/deals crawl aghezty_eg "deals:flash-sale" --pages 1
```

## Troubleshooting

- `no product data in the page (layout changed?)`: `window.AGHEZTY` moved. Save a new page into `tests/fixtures/`.
- Cloudflare is in front: a `Blocked` here means a challenge or HTTP 403 and benches that IP. It hasn't happened in about 300 test pages.

## Re-checking

Is `?min_price` still exact? Does a page past the end still repeat? (`measure_shop.py aghezty_eg "category:Air-Conditioner" --pages 25`.)

## Alert screenshots

`.pd-price-card` holds the price, the crossed-out price and "you save"; with the title and main image. Arabic at `/ar/`. Takes 3 s.
