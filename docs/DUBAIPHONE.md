# Dubai Phone (dubaiphone.net): site analysis and how we crawl it

Everything we learned about www.dubaiphone.net (Dubai Phone, a phone and electronics chain with shops across
Egypt) while adding it as the ninth shop (30 October 2026). The adapter is `src/deals/sites/dubaiphone_eg.py`
(site name `dubaiphone_eg`, Arabic name in alerts: دبي فون); its tests are in `tests/test_parsers.py`
(`test_dubaiphone_*`) against the saved RSC stream `tests/fixtures/dubaiphone_mobiles.rsc.gz` (Smart Phone,
page 1, with the 1 EGP placeholders and the pre-orders). Sizes are from 30 October 2026 and drift.

## Summary

| Question | Answer |
| --- | --- |
| Platform | **Next.js** (App Router, Turbopack build) on **Vercel**. The data comes from a private backend at `to.dubaiphone.net/api/v1` (images are still under `wp-content/uploads`, so it began as WordPress). Arabic is the default locale, English under `/en/` |
| What to fetch | Category listing pages: `/en/category/<slug>?per_page=100&page=N&sort=newest`. The page carries the listing as JSON (`shopData`) in its React Server Components payload. A request with the header `RSC: 1` returns only that payload (331 KB instead of 832 KB for 100 products) |
| Whole shop in one listing | **Yes**: `/en/category/all` holds every product (1,268), so 13 requests at 100 a page read the whole catalogue |
| Products per request | 1 to 100 (`per_page`); 101 and more answer an error page |
| Exact count | Yes: `shopData.products.total`, `last_page` |
| Price filter | `price_min` / `price_max`, on the selling price, whole pounds, inclusive |
| Fresh prices | Listing pages are cached at Vercel's edge for **10 minutes**. `/api/products/by-ids?ids=…` (up to 100 ids) is never cached and gives the current price and the crossed-out price |
| Bot protection | Vercel's bot challenge (HTTP 429, `x-vercel-mitigated: challenge`) for plain `curl`, even from home. Chrome's TLS fingerprint (`curl_cffi`) passes from the Mac. Static files (`static.dubaiphone.net`) are behind Cloudflare, which answers 403 to plain `curl` |
| Who can crawl it | **Home connections only**: the Mac's `DEALS_SITES` has it, the droplet's doesn't. The droplet gets the 429 challenge even with Chrome's fingerprint |
| How products would be keyed | The numeric product id (`2818`); slugs change (44 end in `-1`) |
| Catalogue | 1,268 products, 977 in stock; 145 categories (20 of them empty); 96 brands |
| robots.txt | Lets named search engines in and **disallows every other crawler** (`User-agent: *` / `Disallow: /`) |

## Access and blocking (tested 30 October 2026)

| Client | Where | Result |
| --- | --- | --- |
| `curl` with a browser User-Agent | Mac | HTTP 429, Vercel challenge page (40 KB) |
| `curl_cffi` with `impersonate="chrome"` | Mac | 200, every page and API route |
| `curl_cffi` Chrome, and plain `curl` | Droplet | 429 challenge on pages and on `/api/*` |
| `curl` | Mac, `static.dubaiphone.net` JS chunks | 403 Cloudflare "Attention Required"; Chrome's fingerprint gets 200 |

Load we put on it from the Mac without a single refusal: about 750 requests in 40 minutes, including
the 145 categories walked at about one request a second, 30 listing pages with no pause, and 40 with 4
in parallel. Nothing slowed down (pages took 0.2 to 2.7 s throughout). The challenge seems to be decided
by the TLS fingerprint and the IP's reputation, not by request rate at these levels.

**robots.txt.** It lets Google, Bing, Apple, DuckDuckGo, Meta and OpenAI's search bots in (without the
`sort=`, `price_min=`, `price_max=` and `per_page=` URL variants) and ends with `User-agent: *` /
`Disallow: /`. The other shops' robots files do not say this. The user decided to crawl it anyway (30
October 2026); we keep the load small (about 20 pages per pass of every group, 1 to 2 s apart).

**A private backend.** The site's JavaScript calls `https://to.dubaiphone.net/api/v1` with an
`X-SSR-Secret` header whose value is in the public bundle. That header is the shop's own server
credential, so we don't use it; the public page routes below are enough.

## The listing JSON (`shopData`)

The category page is server-rendered; its RSC payload (`self.__next_f.push([1,"…"])` strings in the HTML,
or the whole body when the request has `RSC: 1`) contains `"shopData":{…}`:

```json
{"products": {"total": 19, "current_page": 1, "per_page": 20, "last_page": 1,
              "has_prev": false, "has_next": false,
              "data": [{"id": 2818, "slug": "samsung-65-inch-4k-oled-s85h-…-qa65s85h",
                        "name": "Samsung 65 Inch 4K OLED S85H Vision AI Smart TV with Built In Receiver",
                        "price": {"amount": 72999, "formatted": "72,999 EGP"},
                        "selling_price": {"amount": 72999, "formatted": "72,999 EGP"},
                        "has_special_price": false, "is_flash_sale": false, "in_stock": true,
                        "is_new": false, "availability_type": "normal", "preorder": {"enabled": false},
                        "base_image": "…", "fading_ticker": {…}}]},
 "summary": {"items_count": 19, "price_range": {"min": 6399, "max": 72999}},
 "category": {"id": 10, "name": "TVs", "slug": "tvs", …},
 "filters": {"categories": [{"id": 78, "slug": "samsung-tv", "products_count": 12}, …], "brands": […]},
 "brand": null, "brand_slider": […], "seo": {…}}
```

| Field | Meaning |
| --- | --- |
| `id` | Product id; the key. Arabic and English pages share it |
| `name` | Title in the page's language. English titles carry model codes (`QA65S85H`, `G614PR-RV161W`) |
| `selling_price.amount` | **The price to record.** Whole pounds (integer); multiply by 100 for piastres |
| `price.amount` | The crossed-out price when it is above `selling_price` (only 4 products) |
| `has_special_price` | **Unreliable**: true on 331 products, but 327 of them have `price` equal to `selling_price` |
| `is_flash_sale` | False on all 1,268, even the two items in the Flash Sale category |
| `in_stock` | Stock; up to 10 minutes stale (see caching) |
| `availability_type` | `normal` 1,262, `preorder` 5, `coming_soon` 1 |
| `preorder.enabled` | True on the 5 pre-orders |

There is no brand, category or barcode in the listing. A product's categories are only known by which
listing it appears in (most are in 3 or 4).

## URL parameters and edge cases

| Request | Answer |
| --- | --- |
| `/en/category/mobiles-all-2` | 20 a page, `total` 120, `last_page` 6 |
| `?per_page=100` (60 also works) | 100 a page |
| `?per_page=101` (or 150, 200, 1000) | HTTP 200 page **without `shopData`** (the backend refused it; the page says "Something went wrong") |
| `?page=999` | HTTP 200, `total` 120, empty `data` |
| `?price_min=19777&price_max=19777` | 1 product at exactly 19,777: inclusive, on the selling price |
| `?price_min=19777.5&price_max=19800` | 0: decimals accepted |
| `?sort=price_desc` | Same as no sort: **the default order is price high to low**, so a product whose price changes during a pass can move between pages (missed or seen twice) |
| `?sort=price_asc`, `?sort=newest` | Work. `newest` orders by id, newest first; it doesn't move when prices change, so use it for paging |
| `?sort=latest`, `popularity`, `name_asc`, anything else | Page without `shopData`, like a bad `per_page` |
| `?in_stock=1` | Ignored (same 120) |
| `/en/category/does-not-exist` | **HTTP 200**, title "Does Not Exist \| Dubai Phone", the page holds `NEXT_HTTP_ERROR_FALLBACK;404` and no `shopData` |
| `/en/shop/no-such-product` | HTTP 200, "Product Not Found", same 404 marker |
| `/en/shop`, `/search?q=` | No server-side listing (filled by JavaScript) |
| `/brand/<slug>` | Same `shopData` listing, by brand (`/brand/samsung`: 52) |

So an adapter must tell three cases apart on an HTTP 200: `shopData` present (good), the 404 marker
(unknown category: `UnknownTarget`), neither (an error: retry later). A 429 with `x-vercel-mitigated`
is a block.

## Traps for price alerts

- **1 EGP placeholders.** Samsung Galaxy Z Fold 8 and Z Fold 8 Ultra (ids 2848, 2854) are listed at
  **1 EGP**, out of stock, while their pre-order twins sell at 128,999 and 143,000. A price of 1 must never be
  recorded, or the day they come in stock (or the day a price drops back to 1) looks like a 99.99% drop.
- **Pre-order deposits.** "Pre Order - Xiaomi Redmi Note 17" and "Pre Order - Vivo V80" cost **500 EGP**: that
  is the deposit, not the phone. Skip `availability_type` `preorder` and titles starting "Pre Order".
- **Coming soon at a price.** "iPhone Duo" is `coming_soon`, `in_stock` true, 176,000 EGP; it can't be bought.
- **Bundles.** "iPhone 18 Pro Max 256GB (Prime Bundle .. PanzerGlass + AirTag 2 + 20W adapter)" is
  136,888: a bundle, not the phone alone. Matching must not pair it with a bare iPhone.
- **Crossed-out prices are rare** (4 products), so the shop makes almost no claims of its own: alerts would
  come from our own history, as with every shop.
- **Edge cache.** Listing pages are served from Vercel's cache (`x-vercel-cache: HIT`, `age` up to 600 s,
  then one `STALE` answer while it re-renders, then fresh). Measured over 30 minutes on three pages: the age
  climbed to 600 to 640, one STALE, then restarted at 45. A listing can therefore show a price or stock
  state **up to 10 minutes old**. In a sample of 50 product pages, 6 were in stock on the product page but
  out of stock on the cached listing; two of them re-read through a fresh listing URL were in stock. Any URL not seen before
  (another parameter order, an extra `&x=1`) is rendered fresh (`MISS`, `age` 0, 2.4 to 2.7 s for 100
  products), but that costs the shop a full render each time.
- **Slugs change**; 44 products have a `-1` suffix. Key by id.

## Fresh prices: `/api/products/by-ids`

`GET /api/products/by-ids?ids=2116,406,500&locale=en` is the route the site uses for "recently viewed".
It is **never cached** (`x-vercel-cache: MISS`, `age` 0 on repeats) and takes **up to 100 ids** (101 or
more returns `[]`). About 1 KB a product, 0.5 to 1.3 s for 100:

```json
[{"id": "500", "title": "Anker MagGo 3-in-1 Foldable Wireless Charging Station", "href": "/shop/anker-…",
  "price": 5599, "originalPrice": 5899, "comingSoon": false, "preorder": false, "isNew": false, …}]
```

It has no stock flag. It is the right tool for a fast lane: the whole catalogue's current prices are 13
requests, and a watch list of a few hundred ids is 2 to 5.

Other public routes: `/api/categories/searchable?locale=en` (the 61-node menu tree, 41 KB),
`/api/products/store-availability?slug=<slug>&locale=en` (which branch has which colour in stock),
`/api/products/pickup-stores`, `/api/products/payment-methods?productId=`. `/api/shop/search?q=` answered
HTTP 500.

## Product page

`/en/shop/<slug>` holds a `"product":{"id":"1392",…}` object and a JSON-LD `Product`:

| Field | Example | Notes |
| --- | --- | --- |
| `price.current` / `price.original` | 8799 / undefined | `original` was undefined on all 50 sampled pages, even for the 4 products with a crossed-out price in the listing |
| `productType` | `variable` 37, `simple` 13 of 50 | |
| `variants[]` | `{"type": "color", "name": "Black", "available": true, "priceModifier": undefined}` | Colours (88 of 95 sampled variants), sometimes `size_/_ram` (Z Fold 8). No price differences seen: storage sizes are usually separate products |
| `inStock`, `maxQty` | true, 3 | |
| `brand` | "Xiaomi" | Only here, not in the listing |
| JSON-LD `sku` | `DP005414` | The shop's own code, simple products only; **no barcodes** anywhere |

## Catalogue and categories

1,268 products (the product sitemap's `dp:count` and `/category/all` agree), 977 in stock. In-stock prices:
median 1,899 EGP; 680 at 1,000 or more, 372 at 3,000+, 210 at 10,000+, 106 at 30,000+; top 355,999.

The sitemap (`/sitemap-categories.xml`) lists 145 categories; the menu tree
(`/api/categories/searchable`) shows 61 of them. Largest: Mobile accessories 248, Mobile wearable 245,
Laptop Store 146 (laptops plus JBL speakers), Laptop Accessories 146, Smart Phone 120, Covers 114, TWS 96,
Laptops 84, Chargers & Docks 84, Kitchen Appliances 82, Apple Store 76. Empty on 30 October: Desktop PC,
Microwaves, Water Heaters, Air Cooler, Heater, Fans, Motorola, Nothing, Dell Laptop and 11 more.

Categories that fit the existing watch groups (in stock at or above the group's minimum price):

| Group (minimum) | Dubai Phone slugs | Products | In stock ≥ minimum |
| --- | --- | --- | --- |
| TVs (3,000) | `tvs` | 19 | 19 |
| Phones (2,500) | `mobiles-all-2` | 120 | 75 |
| Tablets (5,000) | `tablets` | 30 | 21 |
| Laptops (8,000) | `all-laptops` (`asus-rtx` 16 is ASUS gaming laptops, a subset) | 84 | 50 |
| Monitors (2,500) | `monitors` | 18 | 8 |
| Headphones (800) | `headphones`, `tws`, `gaming-headset`, `earphones` | 141 | 86 |
| Air conditioners (8,000) | `air-conditioner` | 13 | 1 |
| Gaming consoles (8,000) | `ps-consoles` (1 PS5; `games` mixes a PS5 with e-scooters) | 1 | 1 |
| Kitchen machines (1,500) | `air-fryer`, `kettle-coffee-maker`, `blenders-mixers`, `microwaves`, `oven`, `waffle-sandwich-maker` | 82 | 56 |

Nothing for refrigerators, cooking, dishwashers, desktops or PC components. Deal lanes: `flash-sale` (2
earbuds at 499, both out of stock) and `last-piece` (3).

## How we crawl it

- **Requests**: `GET /en/category/<slug>?per_page=100&sort=newest[&page=N][&price_min=..&price_max=..]&t=<minute>`
  with the header `RSC: 1` and Chrome's fingerprint. The slice key is `category:<slug>`; `category:all` is the
  whole shop. `search:` is refused (`UnknownTarget`: the search page is filled by JavaScript).
- **`t=<minutes since 1970>`** makes each minute's URL new to Vercel's cache, so the page is rendered from the
  shop's current data instead of a copy up to 10 minutes old (the first principle: notice drops fast). Each
  such request costs the shop one render (0.3 to 2.7 s); a pass of every group is about 20 pages.
  `/api/products/by-ids` would be lighter but needs the ids, which the adapter doesn't have.
- **Reading**: `shopData` cut out of the stream. Price = `selling_price` in piastres; a `price` above it is the
  crossed-out price (kind `list`). Below 10 EGP the price is a placeholder (recorded as no price, not in
  stock). Pre-orders (`availability_type` `preorder`, `preorder.enabled`, or a title starting "Pre Order") are
  skipped; `coming_soon` is recorded as not in stock. Keyed by the product id.
- **Answers**: `shopData` present = a page (an empty one past the end); the 404 marker = `UnknownTarget`; neither
  = `ValueError` (a shop error, retried later); 403/429/503 twice (a fresh session in between) = `Blocked`.
- **Groups** (config.example.toml): TVs `tvs`, Air conditioners `air-conditioner`, Gaming consoles
  `ps-consoles`, Phones `mobiles-all-2`, Tablets `tablets`, Laptops `all-laptops`, Monitors `monitors`,
  Headphones `headphones`, `tws`, `gaming-headset`, `earphones`, Kitchen machines (fryers, kettles and coffee,
  blenders, microwaves, ovens, waffle makers), Kitchen appliances `irons`, Gaming peripherals `keyboard-mouse`,
  `gaming-mouse-and-keyboard`. About 510 products above the group minimums (360 in stock), one or two pages per root.
- **Politeness**: 1 to 2 s between requests (`[sites.dubaiphone_eg]`).

## Running and checking it

```bash
.venv/bin/deals categories dubaiphone_eg                     # main slugs
.venv/bin/deals categories dubaiphone_eg mobiles-all-2       # count and children (brand sub-categories)
.venv/bin/deals crawl dubaiphone_eg "category:tvs|price:3000-" --show 5
```

On the server, `make sql`:

```sql
SELECT grp, count(*) products, sum(in_stock) in_stock FROM products WHERE site = 'dubaiphone_eg' GROUP BY grp;
SELECT kind, count(*), sum(failures > 0) failing, min(next_due_at) FROM jobs WHERE site = 'dubaiphone_eg' GROUP BY kind;
```

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `blocked: dubaiphone_eg: HTTP 429 ... (Vercel challenge)` | Vercel's bot challenge | Normal from a cloud IP (keep it off the droplet); from home it pauses by itself |
| `UnknownTarget: no category with slug 'x'` | Next's 404 page | Look the slug up in `/sitemap-categories.xml` |
| `no shopData in the page` | The shop rendered an error (a `per_page` above 100 or an unknown sort do this) or changed its layout | Open the URL; if the layout changed, save a new page in `tests/fixtures/` and fix `shop_data` |
| A phone "drops" to a tiny price | A new kind of placeholder or deposit | Check the product page; widen `PLACEHOLDER_PRICE` or the pre-order test |

## Re-checking

```bash
# from the Mac (the droplet is refused)
.venv/bin/python - <<'EOF'
from curl_cffi import requests
import json
s = requests.Session(impersonate="chrome")
r = s.get("https://www.dubaiphone.net/en/category/all?per_page=100&page=1&sort=newest", headers={"RSC": "1"})
i = r.text.find('"shopData":')
sd = json.JSONDecoder().raw_decode(r.text[r.text.find("{", i + 10):])[0]
print(r.status_code, r.headers.get("x-vercel-cache"), r.headers.get("age"), sd["products"]["total"])
print(s.get("https://www.dubaiphone.net/api/products/by-ids?locale=en&ids=500,2818").json())
EOF
```
