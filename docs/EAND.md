# e& Egypt (eand.com.eg, formerly Etisalat): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/eand_eg.py` (site name `eand_eg`); tests are `test_eand_*` in `tests/test_parsers.py` against `tests/fixtures/eand_tablets.json.gz` and `eand_laptops.json.gz`. Not the same shop as Etisal Store (`etisal_eg`, etisalstore.com).

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | اتصالات (إي آند) |
| What it sells | Phones, tablets, laptops, TVs and accessories, from sellers `2B`, `Raya`, `Etisalat`. The page calls itself "etisalat Egypt" |
| Platform | Angular with a hash route (`/eshop2/#/...`), Java API, **FortiWeb firewall** |
| What we fetch | `/eshopapi/v1/category/getCategoryWithSubCategories?categoryId=<id>&count=100&page=<from 0>&sortBy=nameAsc` |
| Products per request | Up to 100 asked for (200 worked too), about 6 KB each |
| Exact count | `recordsTotal`. Also `totalPages` |
| Price filter | None we can use: `/product/filterationCriteriaMulti` lists a range with no values |
| Who can crawl it | **Anyone** so far: Mac and droplet |
| Keyed by | The product id. Colours are separate products |
| Catalogue | 1,760 categories, nearly all of them campaigns over the same products |

`www.etisalat.eg` serves the same shop and API. `robots.txt` only disallows old `/StaticFiles*` paths. Its `sitemap.xml` is years stale (`/eshop/products/Handsets/...`, Lenovo Vibe C2).

## Client traps

- **Without `Accept: application/json` it answers XML** (`application/xhtml+xml`, HTTP 200, `<CommonResponseDto>`), since `curl_cffi` as Chrome sends a browser's `Accept`. Hence the header. That XML reads as a `Blocked` here, so losing the header would look like a block.
- **The firewall blocks a browser whose user agent says `HeadlessChrome`.** On 5 Nov 2026 five page loads of `/eshop2/` in a default Playwright got "Web Page Blocked! ... Attack ID: 20000051" from the Mac, while the droplet's API answered at the same moment. `shots.Shooter` uses a normal Chrome's agent. Don't test its pages with a default Playwright.

## The listing

| Asked | Answered |
| --- | --- |
| `categoryId=2` | Devices, 318 products |
| `category=2`, `cid=2`, `id=2`, `categoryCode=devices` | HTTP 200, 112 bytes, no products: **only `categoryId` works** |
| `sortBy=priceAsc`, `nameAsc` | Work. `sort=` is ignored. We sort by name: a price change moves nothing |
| `manufacturer=79` | Works (Samsung, 134) |
| An unknown category | HTTP 200, `category.id` 0: that is how it is told from an empty one |
| `page=99` | HTTP 200, no products |
| `/category?filter=visible&count=200` | The category list, 9 pages |

**Whole walks, 5 Nov 2026:** 2 (318), 5 (43), 27 (464), 29 (36), 30 (42), 35 (1,111), 6 (415): each count read exactly, no repeats.

## Prices and stock

- `price` and `finalPrice` are what you pay (`finalPrice` as text, "53,333"). `originalPrice` is the crossed-out price, only ever as text. `discounted` says if they differ.
- Sold out: `canBePurchased: false`, `quantity: 0`, still listed. 27 of 318 Devices.
- `stock_left` is `quantity` when 5 or fewer: the page said "Only 2 Left In Stock" at 2.
- **Promo codes aren't in the price:** "Mobile Deals (Use code: MOBILE25)" holds 91 of 318 Devices. We track the price without a code (`eand-promo-codes-not-read`).
- **Crossed-out prices:** 134 of 260 Devices in stock (median 27%), 160 of 333 chargers (median 22%, 3 at 60%+), all 43 tablets, all 58 home appliances. So rule `eand-no-new-listing-alerts`.
- A marketplace, like Raya and Jumia. The alerts' "not the shop itself, check the seller" warning is noon's only, as it is for those.
- Compared with its page, the S26+ matched (53,333, crossed out 67,800). **The rest of that check was cut short by the firewall, above.** Prices come straight from the API the page itself calls.

## Catalogue and categories

**Keep roots to one group.** The tree is 1,760 categories, mostly campaigns ("Valu Offer 18 Month"), so one Anker cable is in 18 of them, and a product can be in the roots of several groups (a product takes the group of the last root that read it). Measured overlaps: Devices has 31 of the 43 tablets; Laptops has 104 chargers and 25 gaming mice. `KEEP` and `SKIP` in the adapter hold a root to its group by `type`:

| Root | Holds | Read |
| --- | --- | --- |
| `2` Devices | 318 | All but `tablet` (287) |
| `5` Tablets | 43 | All |
| `27` Laptops | 464 | Only `laptop` (63); the rest is cables, mice and bags |
| `29` TVs | 36 | All |
| `30` Gaming | 42 | Not `Headphones`, `headset` |
| `4` Headsets & speakers | 347 | Not `smartphone` |
| `6` Power banks & chargers | 415 | All |
| `18` Smart watch | 71 | Only `Wearables`, `smartwatch` |
| `10085` Home appliances | 58 | All |

So `last_count` stays under `expected_total` on 2, 27, 30, 4 and 18: that is this, not a bug. `35` is the parent of 4, 6 and 18 and isn't read. Not watched: `36` Internet (routers), `8` Storage (12), `14652` Tech gear (6), `13` Best Deals (66, all in the roots above).

## How we crawl it

| Group | Root | Pages |
| --- | --- | --- |
| Phones | `category:2` | 4 |
| Tablets | `category:5` | 1 |
| Laptops | `category:27` | 5 |
| TVs | `category:29` | 1 |
| Gaming peripherals | `category:30` | 1 |
| Headphones | `category:4` | 4 |
| Chargers & power banks | `category:6` | 5 |
| Wearables | `category:18` | 1 |
| Home & more | `category:10085` | 1 |

**Sums:** 23 pages a pass at 2 to 4 s and about 2 s each: about 2 minutes, well inside every cap. At most about 60 pages an hour from each of the droplet and the Mac (Phones and Tablets every 10 minutes, the rest less). No fast lane: `13` Best Deals would flip groups, and a pass is short. No block on the API; the firewall block above was on the pages.

## Running and checking it

```bash
.venv/bin/deals crawl eand_eg "category:29" --pages 1 --show 5
```

## Troubleshooting

- `Blocked ... HTTP 200` with XML: the `Accept` header. A real FortiWeb block is an HTML page, also `Blocked` here: try the droplet to tell which IP it has.
- A root at 0: categories are often emptied (`26` Triple Play, `40` Al-Ahly have 0). `deals categories` doesn't help; read `/category/getCategoryWithSubCategories`.

## Re-checking

The other four price comparisons of the 5 Nov check, through `deals shot` a minute apart, not a default Playwright. Whether `quantity <= 5` is still where "Only N left" starts.

## Alert screenshots

`div.product-price div.price` in the bar under the gallery; title `div.product-name h2`, image `.swiper-slide-active img`. Language goes in the hash route: `?lang=ar&pid=`. 6 s, with 2 s to settle.
