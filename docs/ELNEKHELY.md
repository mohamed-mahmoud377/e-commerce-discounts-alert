# Elnekhely Technology (elnekhelytechnology.com): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/elnekhely_eg.py` (site name `elnekhely_eg`); tests are `test_elnekhely_*` in `tests/test_parsers.py` against `tests/fixtures/elnekhely_gpu.html.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | النخيلي تكنولوجي |
| What it sells | PC parts, monitors, laptops, accessories: ASUS, Gigabyte, MSI. Median 42,999 EGP for graphics cards |
| Platform | **OpenCart** with the Journal theme, HTML pages. English only |
| What we fetch | `/<category>?limit=100&page=N` |
| Products per page | 100 (page about 1.7 MB, nearly all inline CSS) |
| Exact count | "Showing 1 to 100 of 118 (2 Pages)" |
| Price filter | None |
| Who can crawl it | **Home connections only.** The droplet gets Cloudflare's challenge (HTTP 403, `cf-mitigated: challenge`), with `curl_cffi` too |
| Keyed by | `data-product-id` |
| Catalogue | About 1,300 listings across the watched categories; the sitemap lists 838 |

## Access and robots.txt

`robots.txt` forbids one single product and nothing else. From the Mac, `curl_cffi` as Chrome passes.

## Traps

- **"from" on a price means nothing:** every normal price carries the label; product pages have no options.
- **No price:** a few sold-out cards show none (`NVIDIA RTX PRO 6000`), read as no price.
- **An unknown category is a 404**, a page past the end a 200 with no cards and no "Showing". `desktop` and `powered-by-asus-1` are empty categories in the sitemap: 200 with no cards.
- **The page really says when an offer ends** ("This offer ends in: 03 days"). Not read yet.
- The product links carry `?limit=100` when read with it: the adapter strips it.

## Prices and stock

Crossed-out price on 1 of 67 in stock in graphics cards, 33 of 171 across graphics cards and monitors (mostly under 16% off). Kind `list`, no rule. Sold-out products stay listed with `out-of-stock` on the card (29 of 100 graphics cards). Sells its own stock.

**Product pages show `Model`, `SKU`, `UPC`, `EAN`.** The barcode would make matching exact: a candidate for `details.PARSERS` after a quiet week (docs/MATCHING.md).

## How we crawl it

| Group | Slugs |
| --- | --- |
| PC components | graphics-card 118, processors 69, motherboards 154, ram 57, ssd 26, cases 191, power-supply 56, fans-coolers 167 |
| Laptops | laptop 23 |
| Monitors | monitors 71 |
| Gaming peripherals | accessories 367 |
| Desktops | original-pc 4 |

Checked: 2 products are in both PC components and accessories. Left out: `gaming-chair` 1, `bundeles` 1, `qnap` 3, `5090-dhahab` 1. About 18 pages a pass, about 1 minute at 2 to 4 s. **About 20 pages an hour, about 35 MB an hour, from home IPs only.** No block history. Cloudflare can refuse a home IP too, so slow it (Pace) at the first block.

## Running and checking it

```bash
.venv/bin/deals crawl elnekhely_eg "category:graphics-card" --pages 2 --show 5
.venv/bin/python .claude/skills/add-shop/measure_shop.py elnekhely_eg "category:graphics-card" --pages 5
```

## Troubleshooting

`HTTP 403` from a home IP: Cloudflare; it pauses by itself. `no category` means a slug went: `https://www.elnekhelytechnology.com/sitemap-category.xml`.

## Alert screenshots

English only. Price `div.product-price-group div.price-wrapper`, context `h1.page-title` and `.swiper-slide-visible img`, cookie bar hidden (`.notification-wrapper`). About 3 s. Saved page: `tests/fixtures/elnekhely_product_rendered.html.gz`.
