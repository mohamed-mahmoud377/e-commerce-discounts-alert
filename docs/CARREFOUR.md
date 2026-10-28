# Carrefour Egypt (carrefouregypt.com): site analysis and how we crawl it

Everything we learned about carrefouregypt.com (Carrefour Egypt, run by Majid Al Futtaim, "MAF") while adding it
as the eighth shop (28 October 2026). The adapter is `src/deals/sites/carrefour_eg.py` (site name
`carrefour_eg`); its tests are in `tests/test_parsers.py` (`test_carrefour_*`) against the saved category page
`tests/fixtures/carrefour_category.html.gz` (Electronics & Appliances, first page by relevance). Sizes are from
October 2026 and drift.

## Summary

| Question | Answer |
| --- | --- |
| Site name in the code and config | `carrefour_eg` (Arabic name in alerts: كارفور مصر) |
| Platform | MAF's Next.js storefront ("mafrp-web", served by `istio-envoy`) behind **Akamai Bot Manager** (`_abck`, `bm_sz` cookies); the same platform runs Carrefour in the UAE, Saudi Arabia and nine other countries (`/mafuae/`, `/mafsau/`...) |
| What we fetch | The category page itself (`/mafegy/en/c/<id>`): the server renders the whole listing answer into the page's React Server Components payload. No HTML parsing: the JSON object is cut out of the payload |
| Products per request | 60 (fixed: `pageSize` is ignored) |
| Deepest page | `currentPage` 20 (0-based): later pages repeat page 20, so 1,260 products per query at most. `max_slice_total` 1,200 |
| Exact count | Yes, `pagination.totalResults` |
| Price filter | `minPrice` / `maxPrice`, inclusive at both ends, either may be left out |
| Sort | `sortBy=price_asc` (also `price_desc`, `relevance`). **Relevance order is not stable across pages** (a 234-product slice gave 232 distinct products in 234 cards); price order gives every product exactly once |
| Search | `/mafegy/en/search?keyword=<words>` takes the same parameters; fuzzy (a search for "air conditioner" returns soap) |
| Bot protection | Akamai. Plain `curl` is cut off at the HTTP/2 layer; Chrome's, Safari's and Firefox's fingerprints through `curl_cffi` pass **from home and from the droplet**. Too fast a pace gets **HTTP 429** (seen on the Mac after ~40 pages at 1 s spacing, following an hour of heavy probing) |
| Who can crawl it | Anyone: the Mac and the droplet both have it in `DEALS_SITES` |
| How products are keyed | Carrefour's `productId` (e.g. `658120`; some marketplace items have long ids like `2251857001360`). One card per product, showing the buy-box offer |
| How categories are keyed | The id after `/c/`, case-sensitive: `NFEGY4080000` (`nfegy4080000` lists nothing). Grocery ids start with `FEGY`, promotions are `promo-<name>` |
| Politeness | 2 to 4 s between requests per worker (`[sites.carrefour_eg]`); one page is about 1.1 MB of HTML and takes about 1 s |
| Tracked | about 1,270 products in 12 groups at the group minimums (no desktops, no PC components: Carrefour sells almost none) |

## Access

| From | Result (28 October 2026) |
| --- | --- |
| Mac (home), `curl_cffi` chrome / safari / firefox | 200, every page |
| Mac, plain `curl` | `HTTP/2 stream 1 was not closed cleanly: INTERNAL_ERROR` |
| Droplet, `curl_cffi` chrome / safari / firefox | 200; 40 category pages in a row at 1.5 to 3 s, all fine |
| Droplet, plain `curl` | same HTTP/2 error |
| Either, the old JSON API (`/api/v8/categories/<id>`, `/api/v7/...`) | 403 "Access Denied" (Akamai) |
| Either, the new listing API (`/mafrp/api/v1/search/listing/category/<id>`) | 503 "DNS failure": it is internal to the Next.js server |

The adapter keeps one session (Akamai's cookies come with the first page). On HTTP 403, 429 or 503 it opens a
fresh session and retries once, then raises `Blocked`, and the coordinator pauses Carrefour for that IP (15
minutes, doubling on repeats). A 429 cleared within minutes in testing.

## The page

`https://www.carrefouregypt.com/mafegy/en/c/NFEGY4080000?sortBy=price_asc&currentPage=1&minPrice=3000&maxPrice=5000`

The listing lives in the `self.__next_f.push([1,"..."])` chunks of the page. Decoded and joined, the payload
contains one object starting `{"topStickySections"` (the adapter cuts it out with the same helpers as B.TECH,
`_rsc_payload` and `_balanced`):

| Key | Holds |
| --- | --- |
| `pagination` | `{"pageSize": 60, "totalPages": 34, "totalResults": 2010, "currentPage": 0, "sort": "price_asc"}`; **missing** when nothing matches |
| `sections` | 60 `master-product-card` objects |
| `topStickySections` | `refinement-bar` (filters: Fulfilled by Carrefour, Deals, Brand, Size, Price; sorts; breadcrumbs) and `category-tree` (`queryCategorizationTree`: the children with `id`, `name`, `count`, `parent`) |
| `additionalAttributes` | `productCount`, `title` (the category name), `constructorPageType` |
| `adTechComponents` | the sponsored carousel (not used) |

**Empty and unknown.** An unknown id, a lowercase id, an empty price range and a page past the end all answer
HTTP 200 with the same empty object: no `pagination`, no cards, no breadcrumbs. So when page 1 of a category
slice is empty, the adapter fetches the bare category once; if that has products, the slice is just empty
(the coordinator records `no_results`), otherwise the id is unknown (`UnknownTarget`). Ids that had products
are remembered for the life of the worker.

**The category tree leaves children out.** Large Appliances (363 products) lists six children that add up to
284; Built-In Hob (`NFEGY4100130`, 20), Built-In Oven (`NFEGY4100800`, 19), Built-In Hood (`NFEGY4100900`, 37)
and Other Built-In (`NFEGY4100150`, 2) are missing from it. Mobile and Smartwatches lists Smartphones and
Mobile phones but not Smart Watches & Wearables (`NFEGY1220300`, 56). Coolers, Heaters & Air Treatment shows
no children at all, but has Fan (`NFEGY4040101`), Electric Heater (`NFEGY4040103`), Moveable Air Conditioner
(`NFEGY4040104`, which are air coolers), Ventilator (`NFEGY4040110`), **Air Conditioners (`NFEGY4040112`, 79)**
and Water Dispenser (`NFEGY4040113`). These were found from the full category paths that search results carry
on each card (`productCategory`, e.g. `nfegy4000000, nfegy4040000, nfegy4040100, nfegy4040112`; on a category
page the field only repeats the category asked for). `deals categories carrefour_eg <id>` shows what the tree
lists; the table below is the complete map.

**The deals filter.** `filter=deal:true` narrows a listing to "Today's Deals" (TVs: 64 of 168, of which 30 have
a crossed-out price and a timed discount; the rest are coupon or instalment offers). `deal=true` is ignored. It
is not used as a fast lane: every Carrefour root is a few pages, so the whole root is swept as cheaply as the
filtered one would be (see GUIDE, Algorithm 3).

## What a card contains

| Field (in the card's `additionalAttributes` unless noted) | Example | Used as |
| --- | --- | --- |
| **`productId`** | `658120` | `Listing.sku` |
| **`productName`** | "Samsung 65-inch 4K Crystal UHD Smart TV - 65U8000F" (some start with an invisible `‎`, stripped) | title |
| **`productUrl`** | `/mafegy/en/uhd-tv/samsung-led-tv-ua-65u8000-f-sm-4k/p/658120` | URL |
| **`sellingPrice`** | 26499 (a number; fractions exist, e.g. 3000.48) | price |
| **`markedPrice`** | 27999 (equal to the price when there is no discount) | crossed-out price: kind `was` when Carrefour sells it, `list` for a marketplace seller |
| **`shopName`**, `offerId`, `shopId` | "Carrefour", `offer_carrefour_`, `0000`; or "Electro Zone", `offer_1069018304` | `Listing.seller` when not Carrefour |
| **`stock.stockLevelStatus`**, `stock.value` | `inStock`; `lowStock` with `value` 1 to 7 (mostly 1 or 2) | in stock; `stock_left` (low stock only) |
| `maxToOrder` | 2 | not used (the per-order limit) |
| `isSponsored` | false | sponsored cards are skipped (none seen in the grid) |
| `productCategory` | `["nfegy4000000"]` | not used |
| `currency` | "EGP" | currency |
| badges in `productCardComponents[].componentDTO.badges` | `TimedDiscount` "Ends in 13 hrs"; `DiscountCoupon` "Extra 500EGP Off"; `LABEL` "Only 2 left" and the delivery day; `IMAGE_BADGE` (valU instalments); `FulfilledByRetailPlatform` | `TimedDiscount` becomes the `limited_deal` badge |
| overlay badges | `discountPercentage` "5% OFF", `MaxReached` | not used |
| the `product-rating` component | `{"value": "4.9", "count": "##(23)##"}` (on 15 of 168 TVs) | rating, reviews |
| `analytics.brandName` | `nonfood_samsung` (None on about a tenth) | brand ("Samsung"; three letters or fewer upper-cased: "LG") |
| `analytics.promotionTag` | `discount_percentage|timed_discount` | not used |

**Prices and claims.** On the TVs (28 October), 32 of 168 had a crossed-out price, 30 of them with a timed
discount ("Ends in N hrs"). Carrefour's own `markedPrice` is therefore its everyday shelf price, which is why it
is treated as a recent ("was") price, like B.TECH's own offers; a marketplace seller's is a claim (`list`).
A coupon ("Extra 500EGP Off") is not in the price: what we record is the shelf price before coupons.

**Stock.** Out-of-stock products are not listed (none on any page), so a product that sells out disappears
from its category until it is back, like Jumia, 2B and B.TECH. "Only N left" on a card is min(stock, order
limit): every in-stock card says it (equal to `maxToOrder`), and 42% of TVs (68% of all tracked products) are
`lowStock` with a count, so Carrefour is in `pricing.LOW_STOCK_EVERYWHERE` (low stock is shown in alerts but is not evidence).

**Marketplace.** A third of the tracked products (421 of 1,267 on 28 October) are third-party sellers ("Electro
Zone", "Radio Talaat", "D Tech Store", "Artcool", "Power Tech", "MG TopGas"...): 16 of 142 TVs and 12 of 91
refrigerators, but 58 of 79 air conditioners, 64 of 77 headphones and 173 of 513 kitchen machines.
The card shows one offer per product (the buy box); the seller is kept and alerts say "البائع: ...".

## Category map (watch groups)

Products counted on 28 October 2026 (in stock; before the group's minimum price unless marked "≥").

| Group | Carrefour ids | Products |
| --- | --- | --- |
| TVs | NFEGY4080000 TVs & Projectors (Televisions NFEGY4080400 139, TV Accessories 25, receivers) | 168, 142 ≥ 3,000 |
| Refrigerators | NFEGY4100500 Fridges, NFEGY4100400 Freezers | 63 + 28 |
| Air conditioners | NFEGY4040112 Air Conditioners (hidden child of Coolers, Heaters & Air Treatment) | 79 |
| Gaming consoles | NFEGY4090000 "PlayStation 5" department (Consoles 7, Games 2, Accessories & VR 3); the group's title filter keeps only consoles. No Xbox or Nintendo (searches find none) | 12, 2 consoles ≥ 8,000 |
| Phones | NFEGY1220200 Smartphones (not NFEGY1220300 Smart Watches & Wearables; NFEGY1220100 Mobile phones holds one basic phone under the minimum) | 64 |
| Tablets | NFEGY1230000 Tablets & E-Readers | 13 |
| Laptops | NFEGY4070500 Laptops | 20 |
| Monitors | NFEGY4070600 Monitors | 6 |
| Headphones | NFEGY1210300 Headphones & Earphone | 94 |
| Kitchen machines | NFEGY4040200 Food Preparation (blenders 152, air fryers 74...), NFEGY4040400 Kettles & Coffee Machines, NFEGY4040600 Microwave Ovens & Portable Hobs | 420 + 150 + 82 |
| Kitchen appliances | NFEGY4040300 Irons & Steamers, NFEGY4040700 Toasters & Grills, NFEGY4040113 Water Dispenser | 76 + 40 + 40 |
| Cooking & dishwashers | NFEGY4100200 Cookers, NFEGY4100300 Dish Washer, NFEGY4100100 Built-In Dish Washer, NFEGY4100130 Built-In Hob, NFEGY4100800 Built-In Oven, NFEGY4100150 Other Built-In | 83 + 29 + 2 + 20 + 19 + 2 |
| Desktops, PC components | none: Computers, Office & Accessories (219) has no desktops and only 7 data-storage items | |

Departments (`deals categories carrefour_eg <id>`): Electronics & Appliances NFEGY4000000 (2,010: Small
Appliances NFEGY4040000 1,084, Large Appliances NFEGY4100000 363, Computers, Office & Accessories NFEGY4070000
219, TVs & Projectors NFEGY4080000 168, Batteries & Power 112, Water Heater 41, PlayStation 5 12, Home Cinema &
Audio 8), Smartphones, Tablets & Wearables NFEGY1200000 (453). The rest of the shop is groceries and household
(`FEGY...`, `NFEGY2000000` personal care, `NFEGY3000000` cleaning, `NFEGY8000000` home & garden...).
Not tracked: washers & dryers 79, built-in hoods 37, vacuum cleaners 73, fans and heaters, water heaters 41,
smartwatches 56, chargers and cables.

## Performance and capacity

A page is about 1.1 MB of HTML (60 cards) and takes about 1 s; parsing it takes about 25 ms. The 22 tracked
roots plan into 22 slices (none needs a price split at the group minimums; the biggest, Food Preparation, is 340
products) and a full pass is about 50 pages: about 3 minutes of one worker at 2 to 4 s a page.

**Pace.** 60 pages in a row at 2 to 3 s from the Mac: no refusal. After about an hour of heavy probing, 40 pages
at 1 s brought an HTTP 429 that a fresh session cleared within minutes. Hence 2 to 4 s.

**First crawl.** Of the 1,267 products, 14 claimed 30% or more off: 4 TVs on Carrefour's own timed promotion
("was" price plus the limited-deal badge: enough evidence for a new-listing alert even on the first crawl) and
10 marketplace offers (an RRP-like claim with only low stock as evidence, which does not count on Carrefour).

**Live, 28 October 2026 (first crawl).** Both workers took Carrefour jobs at once (the droplet 31 pages, the
Mac 27, no blocks); 22 slices, 0 failures, 1,267 products: TVs 142, refrigerators 91, air conditioners 79,
consoles 2, phones 64, tablets 13, laptops 19, monitors 6, headphones 77, kitchen machines 513, kitchen
appliances 106, cooking & dishwashers 155. 75 carried a timed discount. The first crawl sent the 4 new-listing
alerts predicted above (Samsung 55" and 77" OLED, Samsung 65" Neo QLED, Haier 65", 32 to 35% off).

## Running and checking it

```bash
.venv/bin/deals categories carrefour_eg                         # departments
.venv/bin/deals categories carrefour_eg NFEGY4100000            # children with counts (the tree omits some, see above)
.venv/bin/deals crawl carrefour_eg "category:NFEGY4080000|price:3000-" --show 5
.venv/bin/deals plan carrefour_eg "category:NFEGY4000000"        # 2,010 products: split into price ranges
```

On the server, `make sql`:

```sql
SELECT grp, count(*) products, sum(in_stock) in_stock, sum(seller IS NOT NULL) marketplace FROM products WHERE site = 'carrefour_eg' GROUP BY grp;
SELECT kind, count(*), sum(failures > 0) failing, min(next_due_at) FROM jobs WHERE site = 'carrefour_eg' GROUP BY kind;
```

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `blocked: carrefour_eg: HTTP 429` | Akamai's rate limit for this IP | Pauses by itself (15 min, doubling); if it repeats, raise `[sites.carrefour_eg]` delays |
| `blocked: carrefour_eg: HTTP 403` | Akamai refused the client (fingerprint or IP reputation) | Check from a browser on the same connection; try `IMPERSONATE = "safari"` in `carrefour_eg.py` |
| `UnknownTarget: no products in category 'x'` | Unknown or lowercase id, or an emptied category | Open `https://www.carrefouregypt.com/mafegy/en/c/<id>`; find ids with `deals categories carrefour_eg NFEGY4000000` or from search results' `productCategory` |
| `ValueError: no listing data in the page` | The page no longer embeds the listing object | Save a fresh page into `tests/fixtures/carrefour_category.html.gz`; look for `"pagination"` in the decoded `self.__next_f` chunks and adjust `listing_data` |
| A slice suddenly parses 0 products | The card layout changed (`master-product-card`, `additionalAttributes`) | Same as above; fix `_parse_card` |
| Prices differ from what a browser shows | The browser has a delivery area set (`storeInfo`, area cookies); the worker always sees the default `mafegy` store | Expected; prices are consistent between crawls |
