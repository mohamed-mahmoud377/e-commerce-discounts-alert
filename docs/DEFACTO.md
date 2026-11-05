# DeFacto Egypt (defacto.com.eg): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/defacto_eg.py` (site name `defacto_eg`); tests are `test_defacto_*` in `tests/test_parsers.py` against `tests/fixtures/defacto_woman_discount.html.gz` and `defacto_man_belts.html.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | ديفاكتو |
| What it sells | Its own clothes and accessories. Each colour is a product |
| Platform | ASP.NET, server-rendered, behind Akamai (`Access Denied` pages from edgesuite.net) |
| What we fetch | The `data-documents='{...}'` JSON on each product card, about 300 KB a page |
| Products per page | **24 to 36**, not fixed |
| Exact count | A hidden `totalCount` input |
| Price filter | `?filter=price:<low>-<high>`, on the current price, inclusive (799 to 799: 29 products at 799) |
| Who can crawl it | **Anyone with a browser fingerprint**: plain curl gets 403 everywhere, Chrome's passes from the Mac and the droplet |
| Keyed by | `ProductVariantIndex`, one colour |

## robots.txt: the user's decision

It disallows `/app/`, `*catalog*`, `*search*`, `/*Product*`, `/*filter=*`, `/*sortorder*`. The user chose to crawl it anyway (5 Nov 2026). Note: the real category pages (`/en-eg/woman-dress`) and collections (`/en-eg/woman-discount`) are **not** under `/app/`, but `filter=` is disallowed and we use it. The sitemap is readable with a Chrome fingerprint: `/sharedxml/en-eg/category.xml` lists 168 categories.

## The listing

| Asked | Answered |
| --- | --- |
| `filter=price:500-` (open end) | **Nothing**: both ends are needed, so the top is 99,999 |
| `filter=price:500-1000&page=1` | **Nothing!** Send `page=` from page 2 only |
| A page past the last | No cards and no count (HTTP 200). Paging ends there: one extra request a slice |
| An unknown category | HTTP 404 |
| An empty range | `totalCount` 0 |
| Sort | None found. 17 pages of dresses read 373 of 373, none repeated |

## Prices and stock

- Price: `CampaignDiscountedPrice` ("November - Net %20 Discount", ending 31 Dec) when set, else `DiscountedPrice`; `Price` is the crossed-out price (kind `list`). Prices have decimals (799.20). Page check: Slim Fit V-Neck Cardigan shows 999 crossed out and 799.
- **Crossed-out on 79% of dresses, 100% of the sale pages, median 20% to 41%.** Rule `defacto-no-new-listing-alerts`.
- `Stock` is a count, 3 or fewer on 28% of dresses (rule `defacto-low-stock`).
- **Promotional cards:** index 9000213, "kargobedava" (free shipping), no price, first on the sale page. Skipped (`defacto-promo-cards`).
- Sizes are in `Sizes` with barcodes (`8684585483148`): not used; matching has nothing to pair clothes with.
- `CampaignBadge` holds the campaign and dates.

## Catalogue and categories

No "all women" page: `/en-eg/woman` is a landing page without products, and the 168 categories are leaves. **Collections cover it:** `woman-discount` 5,328, `woman-new-season` 2,486, `man-discount` 2,679, `man-new-season`, `kids-discount` 2,935. A product on sale is on `*-discount`, a new full-price one on `*-new-season`, and one can move between them with the same key. **Not confirmed that the two collections together are the whole catalogue:** full-price older stock could sit outside them. Kids has no `kids-new-season`, so full-price kids' clothes aren't seen until they go on sale, when they count as new (and don't alert, by the rule).

## How we crawl it

Group **Fashion** (from 500 EGP, 35% `min_drop`, every 30 minutes, at most 45, since 5 Nov 2026): 5 roots, about 9,100 products from 500 EGP (woman-discount 2,850, woman-new-season 2,055, man-discount 1,594, man-new-season 1,383, kids-discount 1,227). `max_slice_total` 1,000, so the planner splits the big ones by price.

**About 410 pages a pass, 24 to 35 minutes of one worker at 2 to 4 s; about 550 pages an hour when every slice is quiet, shared by the droplet and home workers.** On one worker alone a pass would take most of the 45-minute cap, so this shop relies on having two. Akamai can refuse a client without warning: no block history yet.

## Running and checking it

```bash
.venv/bin/deals crawl defacto_eg "category:woman-dress|price:500-99999" --pages 2 --show 5
```

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `blocked: defacto_eg: HTTP 403` | Akamai | Pauses by itself; slow it down if it repeats |
| `no product cards and no count` | Layout changed | Save a new page |
| A slice with 0 products on page 1 | `page=1` got added next to the filter | See above |

## Alert screenshots

No prefix for Arabic: `defacto.com.eg/en-eg/x` becomes `defacto.com.eg/x`. Price: `div.product-detail__price`. Context `h1.product-detail__info-header-title`, `div.product-first-media img`. About 2 s. Saved page: `tests/fixtures/defacto_product_rendered.html.gz`.
