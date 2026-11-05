# Etisal Store (etisalstore.com): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/etisal_eg.py` (site name `etisal_eg`); tests are `test_etisal_*` in `tests/test_parsers.py` against
`tests/fixtures/etisal_wearables.json.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | اتصال ستور |
| What it sells | Phones (138), mobile accessories (145), portable audio (88), wearables (54), a few TVs and Mienta kitchen appliances |
| Platform | Angular SSR on the SuperCommerce platform (`cdn.supercommerce.io`) |
| What we fetch | `GET /api/customer/products/search`, with a guest token |
| Products per request | **10, fixed** (`per_page` is ignored) |
| Exact count | `total_products`; `total` is the page count |
| Price filter | `price_from`, `price_to` on the current price, inclusive (asking for 1,299 to 1,299 gives 4 products at 1,299). `min_price`/`max_price` are silently ignored |
| Who can crawl it | **Anyone**: the Mac and the droplet |
| Keyed by | The product id; each colour and size has its own |
| Catalogue | 478 products, all in stock |

## Access (tested 5 Nov 2026)

Cloudflare in front; Chrome's fingerprint passes from both. Without a token the API answers 401 `Token not provided`. **The token comes from `GET /api/customer/home/initialize?disable_cart=1&disable_category=1`, with these headers:** `lang: 1` (English), `device-type: web`, `device: 3`, `x-admin-preview: 0`, `web-url`.
It lasts about a thousand years, but **each call creates a guest customer at the shop** (the token's `sub`), so a session asks once and keeps it. A worker restart makes one more; no need to save it.

`robots.txt` is empty.

## The listing

`/products/search?page=N&category_id=1081&price_from=2500`. `sub_categories[]=1382` is a sub-category, written `category:s1382` in our slices. **`sub_categories=1382` without brackets answers nothing, and `category=`, `sub_category=`, `from=` are silently ignored**, returning the whole shop: test each parameter against the count.

- Newest first, by id; no sort parameter worked, and none is needed: 48 pages read 478 unique products, none repeated.
- Empty range and unknown id: both 200 with `total_products: 0`. Top categories are checked against `/home/categories`; sub-categories can't be, so a wrong `s<id>` looks empty.
- The page cap: none met.
- `max_price` in the answer can say 999,999 with no product at that price.

## Prices and stock

- `discount_price` when set is the price and `price` the crossed-out price (kind `list`). **On 452 of 478 products (95%), mean 21% off, with `discount_end_date` 2027-12-31 or 2026-12-30:** a permanent anchor, hence rule `etisal-no-new-listing-alerts`.
- `stock` is a count: 3 or fewer on 57% of phones, 37% of accessories (rule `etisal-low-stock`).
- Sold-out products are not returned.
- `sku` is often the barcode (68% to 78% pass the check digit), passed as `gtins`: matching's strongest evidence.
- `brand.name` is the brand; `brand.name_en` is null.
- The page's note "this discount applies only to cash on delivery / credit card / bank instalments" means the price is for the usual payment methods.

## Catalogue and categories

14 top categories (`/api/customer/home/categories`). Sub-categories are mostly by brand (`mobile-accessories-cables-anker`): read a product's `category.sub_categories`. See docs/CATEGORIES.md.

`Mobiles & Tablets` (1081) holds 9 tablets under `s1382`, `s1383`. They are watched in Tablets too, so whichever group sees one first keeps it; a tablet can land in Phones, with Phones' lower minimum. Harmless.

## How we crawl it

| Group | Roots |
| --- | --- |
| TVs | 1160 (6) |
| Air conditioners | 1525 (1) |
| Phones | 1081 (138) |
| Tablets | s1382, s1383 (9) |
| Monitors | s1388 (2) |
| Headphones | 1334 (88) |
| Kitchen machines | 1531 (16), 1570 (14) |
| Wearables | 1372 (54) |
| Chargers & power banks | 1283 (145) |

**10 products a page is the expensive part: phones are 14 pages every 10 minutes**, about 49 s of one worker. About 45 pages a pass, **about 135 pages an hour** at the base intervals, on the droplet and home workers. Fits well inside every cap. `max_slice_total` is 500. No block history.

## Running and checking it

```bash
.venv/bin/deals crawl etisal_eg "category:1081|price:2500-" --pages 2 --show 5
```

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `no guest token in the answer` | The answer's shape changed | Look at `home/initialize` in a browser |
| `no category with id` | Id gone | `/api/customer/home/categories` |
| A `category:s<id>` root goes silent | The sub-category went or the brand moved | Read `category.sub_categories` from a product |

## Alert screenshots

`/ar/` for `/en/`. Price: `div.product-detail-price`; context `h1.product-name`, `.img-zoom-container img`. Sells its own stock, no seller. About 5 to 6 s.

**`settle=3`.** The server's page is redrawn while Angular takes over. A shot in that moment is a squashed mix of the top and the bottom of the page: it happened on 3 of the first 6 shots, and the rectangles measure correctly throughout, so nothing in the numbers shows it. With 3 s more, 6 of 6 were clean. Saved page: `tests/fixtures/etisal_product_rendered.html.gz`.
