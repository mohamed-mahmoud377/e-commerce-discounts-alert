# Sigma Computer (sigma-computer.com): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/sigma_eg.py` (site name `sigma_eg`); tests are `test_sigma_*` in `tests/test_parsers.py` against `tests/fixtures/sigma_laptops.html.gz` and `sigma_past_end.html.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | سيجما |
| What it sells | PC parts, laptops, monitors, peripherals. Its own stock |
| Platform | **Next.js**, products in the RSC payload of a server-rendered page, its own API behind it |
| What we fetch | `/en/search?filters={"category_id":["<uuid>"]}&sortBy=createdAt&page=N` |
| Products per page | 16 (page about 470 KB, 0.5 s) |
| Exact count | The `is_stock` counts in the payload (393 sold out + 194 in stock = 587). Its `totalPages` is that same 587, not pages |
| Price filter | None usable |
| Who can crawl it | **Anyone**: the Mac and the droplet |
| Keyed by | Product `id` (a UUID) |
| Catalogue | About 4,300 products. Hardware 1,876, Accessories 1,209, Laptops 587, Storage 251, Monitors 238 |

## Access and robots.txt

Cloudflare in front, 200 from both. `www.sigma-computer.com/robots.txt` allows everything but `/api/`, `/admin/`, parts of checkout and profile, and `*.json`: the search pages are allowed. `api.sigma-computer.com/robots.txt` allows everything. On that host `/api/stores/v1/categories` answers anyone, while **`/api/stores/v1/products` answers 403**, so we stopped there and read the pages.

## Traps

- **`sortBy=createdAt` is not optional.** The default order repeats products between pages: 498 different ones in 587 reads, **15% missed with no error anywhere**. `sortBy=price` works too (highest first), but `createdAt` only shifts when a product is added: 587 of 587.
- **Any category id answers 200 with no products**, so ids are checked against the category list, once a session. A page past the end looks the same.
- The payload holds other `products` arrays' text; the listing's is the first after `initialFilters`.
- **Price `base` against `current`:** crossed-out on 9 of 172 in stock (5%), median 6%, one at 60%+. Kind `list`, no rule. Brand on 485 of 487.

## How we crawl it

| Group | Categories | Products | Pages |
| --- | --- | --- | --- |
| Laptops | Laptops | 587 | 37 |
| Monitors | Monitor | 238 | 15 |
| PC components | Hardware Components, Storage | 2,127 | 133 |
| Desktops | Desktop, Bundles | 84 | 6 |
| Headphones | Headphone | see the config | |
| Gaming peripherals | Mouse, Keyboard, Mouse pad, Game pad | see the config | |
| Chargers & power banks | Mobile Accessories | 29 | 2 |
| Home & more | Speaker, Microphone, Webcam, Home Office Furniture, Content Creation Gear | see the config | |

Left out: Network (10), Other (12), Sigma Merch (4), Batteries (3), Combos (83), and small accessories (cables, bags, stands). Gaming Console is empty today.

The longest pass is Hardware Components, 117 pages, about 7 minutes at 2 to 4 s, well inside its group's cap; Laptops, about 2 minutes against a 30-minute cap. **About 290 pages an hour at most**, shared by the droplet and the Mac. No block history; this is the busiest of this batch because of its 16 a page, so slow it first (Pace) if it complains.

## Running and checking it

```bash
.venv/bin/deals crawl sigma_eg "category:9f5039de-5c80-46f3-9fe4-6e8f94189b8c" --pages 2 --show 5
.venv/bin/python .claude/skills/add-shop/measure_shop.py sigma_eg "category:9f5039de-5c80-46f3-9fe4-6e8f94189b8c" --pages 5
```

## Troubleshooting

`no category` means an id went: `https://api.sigma-computer.com/api/stores/v1/categories`. `no product data in the page` means the Next.js payload changed: save a new page to `tests/fixtures/`.

## Alert screenshots

`/ar/` for `/en/`. Price `article section div.flex.items-center:has(span.text-3xl)` (the sidebar repeats it), context `article h1` and `article img.object-contain`. Tailwind classes, so this is the first to break. About 2 s. Saved page: `tests/fixtures/sigma_product_rendered.html.gz`.
