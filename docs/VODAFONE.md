# Vodafone Egypt e-shop (eshop.vodafone.com.eg): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/vodafone_eg.py` (site name `vodafone_eg`); tests are `test_vodafone_*` in `tests/test_parsers.py` against
`tests/fixtures/vodafone_phones.json.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | فودافون |
| What it sells | Phones, TVs, laptops, gaming, accessories and small appliances, plus lines (not tracked) |
| Platform | Angular on Broadleaf Commerce |
| What we fetch | `GET /ecommerce/api/catalog-browse/search/catalog`, no login, with two context headers |
| Products per request | 200 (asked and honoured; `totalPages` follows it) |
| Exact count | `totalElements` |
| Who can crawl it | **Anyone**: the Mac and the droplet |
| Keyed by | The product's `id` (`01K05CP63M0QP100468WQJ0YWS`), because **a quarter of the phones have no `sku`** (35 of 136) |
| Catalogue | 921 products in stock, 4,085 with sold-out ones |

## Access and blocking (tested 5 Nov 2026)

**The API resets every second HTTP/2 request on one connection** (curl error 92, `INTERNAL_ERROR`): reliably 200, reset, 200, reset. It looks like a block, and isn't. The session speaks
HTTP/1.1 (`http_version="v1"`): 4 of 4 clean, then the whole measuring run. The two headers it needs:

```
x-price-context: {"locale":"en_US"}
x-context-request: {"applicationId":"01H5FECVAV4YWT0NGQKXEN1T51", "tenantId":"5DF1363059675161A85F576D"}
```

## robots.txt

Allows the site and `?categoryUrls=`. Disallows cart, checkout, account and variant URLs (`?Color=`). The API is not mentioned. Disallows `CCBot`, `Bytespider` and `Meta-ExternalAgent`
(AI training); we are none of them, and we read prices only.

## The listing

```
config=eCommerceTypeAhead & filters[0].name=hideOutOfStock & filters[0].values[0]=true
& filters[1].name=categoryUrls & filters[1].values[0]=/smart-phones & page=0 & size=200 & sort=sku,asc
```

- `page` is 0-based. `last` says when to stop.
- `sort=sku,asc` works, as do `bestPrice` and `discount` (from the answer's `sorts`). The default is unsorted.
- **Empty and unknown look the same** (no results, HTTP 200): the adapter checks `vfe-categories/details?categoryUrl=` once per category per session. Sub-category names come from `main-menu`.
- There is a `bestPrice` facet but the filter's form wasn't found (`12000-18000` is refused), and no need: the whole store is 5 pages. `max_slice_total` is above the store.

## Prices and stock

- `price.amount` is the price. `priceInfo.priceTypeDetails.basePrice.bestPrice.amount` is the crossed-out price when higher: kind `list`. On 69% of phones (median 6% off) and 82% of gaming (median 8%):
  modest, no rule yet; **watch the 1-day and 3-day alerts.**
- **Sold-out products have no flag.** With `hideOutOfStock=true`: 52 TVs, 136 phones. Without: 395 and 429, at old prices. So only `true` is read; a product that sells out disappears, as on 2B, and when it comes back its current price is what is seen. Rule `vodafone-only-in-stock-read`.
- `hideWhenOutOfStock: false` on some products means they stay in the `true` list whatever the stock; none could be shown to be sold out.
- Some "phones" are bundles (`HONOR X9C Smart + Earbuds X6 - White`), `primaryCategoryName: Special Bundles`, with their own price. Matching may not pair them; they are real prices.
- RED customers' prices live in `*_personalizedRed` categories, which we don't read.

## Catalogue and categories

`/ecommerce/api/catalog-browse/main-menu` is the tree, docs/CATEGORIES.md has it. `/deals` (182 in stock) spans every group, so it isn't a useful fast lane; every root is one page anyway.

## How we crawl it

Groups in `config.example.toml`: 18 roots (tv 52, air-conditioners 14, consoles 2, smart-phones 136, tablets 23, laptops 67, monitors 50, headphones 7, true-wireless, headsets 21, in-ear, home-appliances 77, water-dispenser, gaming-accessories 101, controllers 5, wearables 33, chargers 42, powerbanks 19).

**One page a root, 18 pages a pass, about a minute at 2 to 4 s.** About 45 pages an hour at the base intervals, on the droplet and home workers. No block history.

## Running and checking it

```bash
.venv/bin/deals crawl vodafone_eg "category:tv" --show 5
```

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `curl: (92) HTTP/2 stream reset` | The session lost `http_version="v1"` | Restore it |
| `vodafone_eg: no category '/x'` | A menu url changed | `main-menu` |
| All products read as sold out or an empty answer | The context headers' ids changed | Read them from a browser request (`x-context-request`) |

## Alert screenshots

`/ar/prod/` for `/en/prod/`. Price: `vf-product-main-info .price-wrapper`. Context: `h1.product-name`, `img.main-image`. `hide=#product-header`, a sticky copy of the price.
**The page sets `zoom: 0.82` on `<html>`**: bounding boxes come in screen pixels, but CSS `left` is multiplied by the zoom, so a frame drawn at those numbers lands 18% too far up and left. `_DRAW` in `shots.py` now divides by the zoom. About 3 s. Saved page: `tests/fixtures/vodafone_product_rendered.html.gz`.

## Checked against the pages (5 Nov 2026)

5 random products: 5 of 5 prices match. One crossed-out price didn't: Logitech G335 at 2,799, the API says `basePrice` 2,999 (and `discount` 6.67, its own sum), the page crosses out 2,899. The price itself is right, and a crossed-out price is only ever the shop's claim here, so nothing depends on it.
