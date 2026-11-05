# LC Waikiki Egypt (lcwaikiki.eg): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/lcwaikiki_eg.py` (site name `lcwaikiki_eg`); tests are `test_lcwaikiki_*` in `tests/test_parsers.py` against `tests/fixtures/lcwaikiki_shoes.html.gz` and `lcwaikiki_empty.html.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | إل سي وايكيكي |
| What it sells | Its own clothes and shoes, with a home line. Each colour is a product |
| Platform | ASP.NET and React, Cloudflare in front, images on `img-lcwaikiki.mncdn.com` |
| What we fetch | The `var catalogModel = {...}` JSON inside each category page, about 2 MB a page |
| Products per page | 100, plus a few pinned ones that repeat on every page |
| Exact count | `CatalogList.ItemCount`, with `PageCount` |
| Who can crawl it | **Home connections only.** The droplet gets HTTP 403 from every client; plain curl gets 403 from the Mac too |
| Keyed by | `OptionId` (one colour of a model) |

## robots.txt: the user's decision

It allows the bare category pages but **disallows `*PageIndex=*`, `*MinPrice=*`, `*MaxPrice=*`, `*sort=*`** and every filter. The user was told and chose to crawl it anyway (5 Nov 2026). We use `PageIndex` and `MinPrice`, with 3 to 5 s between pages.

## The listing

`/en/women-clothing-t-10?MinPrice=500&PageIndex=2`.

| Asked | Answered |
| --- | --- |
| A page past the last | **Page 1 again**, so stop on `PageCount`, counting the page asked for: the answer's own `PageIndex` can say 1 forever |
| A category's old name | **301 to the new name, and the redirect drops the query**: `PageIndex` and `MinPrice` are lost, so every page is page 1 of the whole category. The adapter asks again under the new name |
| An unknown category | HTTP 404 |
| An empty range | **`ProductFound: false` and 52 unrelated products**, from 249 EGP. Check `ProductFound` first |
| `MaxPrice` | **Doesn't hold:** "300 to 500" lists 860 products at 349 to 1,699. So the planner must never split (`max_slice_total` 20,000) |
| `MinPrice` | Works as a floor: 5,366 of women's 6,505 from 500 EGP |
| `sort=` | Ignored; the default order is stable enough: 5 pages of dresses read 486 of 486, with 16 repeats, the pinned rows |
| The AJAX URL in `LoadMoreUrl` | **Don't use it:** it ignores the category and the page, and answers the same 52 products or nothing |

## Prices and stock

- **The sale price isn't where it looks.** `DiscountedPriceValue` is always 0. It is `CampaignBadges[0].DiscountedPrice` when `ShowDiscountedPrice` is true ("Sale Items", `DiscountRate` 15 to 65). `PriceValue` is then the crossed-out price (kind `list`). Checked on the page: Waist Belted Plaid Shirt Dress 1,999 → 1,499 (the page calls it "price in cart").
- On 40% to 44% of products, median 31% to 36%: rule `lcwaikiki-no-new-listing-alerts`.
- `AvailableStock` is a count: 0 is sold out and 3 or fewer on 44% of dresses (rule `lcwaikiki-low-stock`). `stock_left` is set at 5 or under, the shop's own threshold (`OptionLastXItems`).
- `LowestPriceInLastMonth` equals `PriceValue` on all 557 read, sale or not, so it says nothing.
- `SupplierStoreName` is LC Waikiki on all of them: no seller box.

## Catalogue and categories

`GET /en/ajax/Home/GetMenuData?platformId=1` is the menu, 226 category links. Docs/CATEGORIES.md has the roots.

## How we crawl it

Group **Fashion** (from 500 EGP, 35% `min_drop`, every 30 minutes, at most 45, since 5 Nov 2026):

| Root | Products from 500 EGP | Pages |
| --- | --- | --- |
| women-clothing-t-10 | 5,366 | 54 |
| men-clothing-t-9 | 5,315 | 54 |
| girls-clothing-t-100046 | 1,066 | 11 |
| boys-clothing-t-200046 | 786 | 8 |
| femei-incaltaminte-t-33 (women's shoes; redirects to women-shoes-t-33) | 128 | 2 |

**About 130 pages a pass and 11 to 14 minutes of one worker. About 170 pages an hour when every slice is quiet, about 340 MB, from home IPs only.** About 12,700 products. Baby clothes (1,100 from 500 EGP) and men's shoes aren't watched.

## Running and checking it

```bash
.venv/bin/deals crawl lcwaikiki_eg "category:women-dresses-t-149|price:500-" --pages 1 --show 5
```

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `blocked: lcwaikiki_eg: HTTP 403` | Cloudflare. Expected on any cloud IP | Keep it off cloud workers; if on a home IP, slow it down. `make pause SITE=lcwaikiki_eg HOURS=3` |
| `no catalogModel in the page` | The layout changed | Save a new page in `tests/fixtures/` |
| A root reads 0 products for a long time | `ProductFound` false: the category was renamed | `GetMenuData` |
| A slice runs for hours and `run_count` passes `expected_total` | Paging doesn't end. It happened on 5 Nov 2026: `femei-incaltaminte-t-33` redirected, every page answered `PageIndex` 1, and `has_next` read that. About 2 hours at 13 pages a minute, 4 s apart, against a budget of 65 pages an hour; no block, but the shop's other four roots had to wait | Fixed in the adapter: see the listing table. `curl -sI` the category to see a redirect |

## Alert screenshots

No prefix for Arabic: `lcwaikiki.eg/en/x-o-1` redirects to the Arabic slug. Price: `div.product-detail__price`, with the crossed-out price and "-25%". Context `h1.product-detail__title`, `img.main-image`. 3 to 5 s, four of four the same. **A headless user agent gets Cloudflare's wall**: Shooter's normal agent passes. Saved page: `tests/fixtures/lcwaikiki_product_rendered.html.gz`.
