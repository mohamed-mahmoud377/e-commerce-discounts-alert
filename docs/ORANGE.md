# Orange Egypt e-shop (eshop.orange.eg): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/orange_eg.py` (site name `orange_eg`); tests are `test_orange_*` in `tests/test_parsers.py` against
`tests/fixtures/orange_tablets.json.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | أورنج |
| What it sells | Phones (98), accessories (328), appliances (139, Bosch and Fresh), TVs (28), laptops (12), tablets (17). **Phones, tablets, laptops and consoles aren't watched: see "Prices and stock"** |
| Platform | Angular SSR on nopCommerce |
| What we fetch | `GET /api/CatalogAPI/Products/GetFilterProducts`, with a guest token |
| Products per request | 100 (`PageSize`), about 1 MB: each carries every image's URLs |
| Exact count | `TotalCount`, `TotalPages` |
| Price filter | **None found**: `MinPrice`, `PriceFrom`... answer HTTP 400 |
| Who can crawl it | **Anyone**: the Mac and the droplet |
| Keyed by | `Id` |

## Access: the trap (tested 5 Nov 2026)

**`/api/...` closes the connection on `curl_cffi`, every fingerprint, every HTTP version, from the Mac and from the droplet** (curl error 56, "Connection closed abruptly"), while plain curl, Python's urllib and a browser all get 200, and `curl_cffi` itself reads `/robots.txt`. It looks like a block and is not: the droplet proves it isn't the IP. So this adapter uses **urllib**, unlike every other shop. Test the shop's API path, not just its home page, and try a second client before calling a shop refused.

`https://eshop.orange.eg/` also redirects to **`http://`** (`Location: http://eshop.orange.eg/en`), where it hangs. Always use `https://eshop.orange.eg/en...` directly.

`robots.txt` allows everything.

## The API

- `POST /api/CatalogAPI/Auth/token` with `{}` gives a guest token for 5 hours, and the token names a new `CustomerId`, so **each creates a guest customer at the shop**. The adapter renews after 4 hours: about 6 a day per worker.
- `Categories/GetCategoryDetails?seName=smart-phones` gives the category's `Id`, HTTP 400 for an unknown one: **this tells unknown from empty**. Slices use the readable name: `category:smart-phones`.
- `Products/GetFilterProducts?CategoryId=1033&ClearFilters=true&PageNumber=1&PageSize=100`.
- **A page past the end, an unknown id and an empty category all answer HTTP 400 "Not Found => Homepage Feature products"**, so a 400 here means no products.
- Sorting isn't honoured by `SortBy=10`; pages were complete anyway (328 of 328 unique, no repeats).

## Prices and stock

- `CashPrice.Price.Amount`, `CashPrice.OldPrice.Amount` (kind `list`). **Modest: a crossed-out price on 13% to 17%**, median 6% to 20%; "Black Friday Offers" shows in `NameOfSeller` on 34 of the first 198 products, so that field is not a seller. No rule.
- **`IsAvailableForAddToCart` false means sold out**, confirmed on the page: its h1 reads "ايفون 17 غير متوفر". The price stays in the listing. `StockText` is always empty. 14% of appliances, 4% of accessories, and many iPhones.
- `LabelName: "Pre-order"` on 3 phones, at what looks like the full price; watch them.
- **One product, several sizes, one price in the listing: the reason phones aren't watched.** The Redmi Note 15 5G is listed at 17,999 (its 8/256 GB) and its page opens at 23,999 (12/512 GB). `Products/GetProductDetails?seName=` shows the truth: `Combinations`, each with `AttributeValues`, `Price`, `OldPrice` and `IsAvailable`. Measured 5 Nov 2026, sizes at different prices: **53 of 98 phones, 9 of 17 tablets, 1 of 12 laptops, the PS5**; 0 of 28 TVs and 0 of 15 headphones. The listing shows the lowest combination on 62 of 63. So one key would hold several prices, a screenshot could contradict its alert, and a cheap size selling out could look like a price change. Smaller differences in other groups (sampled): wearables 3 of 20, by strap (Apple Watch SE 12,999 and 14,999), small appliances 2 of 20 (Fresh kettle 700 and 750), chargers 1 of 15. Those stay: their gaps are 5% to 15%, under the 20% `min_drop`, so a size or colour selling out can't alone make an alert. **Watch their first alerts.**
  **To watch phones:** read each product's details, one listing per size, about 730 requests an hour (phones 588, tablets 102, laptops 36) against about 15 now. The user's decision.
- Titles are short with no storage size ("iPhone 17") and there is no brand field, so **matching would rarely pair its phones** even if they were watched. `PriceAdjustment` is 0 on all 433 colours and proves nothing: prices differ in `Combinations`.

## Catalogue and categories

From `/en/sitemap.xml`; ids in docs/CATEGORIES.md. **`special-offers` is really "Air Conditioners"**; `large-appliances` mixes fridges, dishwashers, ovens and washers.

A product's link is built from its `Breadcrumb`: `/en/accessories/s/headphones-earbuds/p/<name>`. `/en/p/<name>` works too.

## How we crawl it

| Group | Roots |
| --- | --- |
| TVs | smart-tvs (28) |
| Air conditioners | special-offers (6) |
| Monitors | monitors (4) |
| Headphones | headphones-earbuds (107) |
| Kitchen machines | small-appliances (82) |
| Cooking & dishwashers | large-appliances (67), a mixed bag, labelled so for its minimum |
| Gaming peripherals | gaming-accessories (4), mouse (5) |
| Wearables | wearables (70) |
| Chargers & power banks | chargers-cables (109), power-banks (50) |

11 roots, 13 pages and one category lookup each per session; about 15 pages an hour at the base intervals, about 15 MB. Pass about 1 minute at 2 to 4 s. On the droplet and home workers. No block history.

## Running and checking it

```bash
.venv/bin/deals crawl orange_eg "category:smart-phones" --show 5
```

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `Connection closed abruptly` | Someone moved this adapter to `curl_cffi` | urllib |
| `no category named 'x'` | A category was renamed | `/en/sitemap.xml` |
| Slices show 0 products | Category emptied, or the 400 changed meaning | `GetFilterProducts` in a browser |

## Alert screenshots

`/ar/` for `/en/`. Price: `div:has(> div > span.product-price-current)`; context `h1`, `.gallery-slide img`. About 4 s. Saved page: `tests/fixtures/orange_product_rendered.html.gz`.
