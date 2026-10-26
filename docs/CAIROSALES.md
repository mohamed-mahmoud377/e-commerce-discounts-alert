# Cairo Sales (cairosales.com): site analysis and how we crawl it

Everything we learned about cairosales.com (Cairo Sales Stores, an appliance and electronics chain) while
adding it as the fourth shop (27 October 2026). The adapter is `src/deals/sites/cairosales_eg.py`; its
tests are in `tests/test_parsers.py` (`test_cairosales_*`) against the saved pages
`tests/fixtures/cairosales_category.html.gz` (TVs above 3,000 EGP) and `cairosales_mobiles.html.gz`
(mobiles, mostly out of stock). Sizes are from October 2026 and drift.

## Summary

| Question | Answer |
| --- | --- |
| Site name in the code and config | `cairosales_eg` (Arabic name in alerts: كايرو سيلز) |
| Platform | PrestaShop 1.6 with the "Amazzing filter" module; English pages under `/en/` |
| What we fetch | Category / search HTML pages, 60 product cards each (`?n=60`), sorted by price |
| Deepest page | No cap (14 pages of 60 for the 820 TVs) |
| Exact count | Yes, "Showing 1 - 60 of 820 items" and "There are 820 products." on every page |
| Bot protection | Cloudflare. Plain `curl` gets "Attention Required!"; Chrome's TLS fingerprint (curl_cffi) passes from a home IP |
| Who can crawl it | Home/office connections only. The droplet gets HTTP 403 with every fingerprint |
| How products are keyed | PrestaShop's `id_product` (`data-id-product` on the card, e.g. `49324`) |
| How big categories are split | By price only, like every shop (`max_slice_total` 3,000) |
| Politeness | 1.5 to 3 s between requests per worker (`[sites.cairosales_eg]`) |
| Tracked | about 7,000 products across all groups at the group minimums (38 roots); a large share out of stock |

## Access

Every page is behind Cloudflare. `curl` with a browser User-Agent gets "Attention Required! | Cloudflare";
`curl_cffi` impersonating Chrome gets the page from the Mac. From the droplet (DigitalOcean) both the Chrome
and Safari fingerprints get 403, so Cairo Sales is a home-worker shop like noon, Jumia and 2B. On a
challenge the adapter retries once with a fresh session and then raises `Blocked`; the coordinator pauses
the shop for that IP (15 minutes doubling up to a day).

`robots.txt` asks search engines not to index sorted or paged URLs (`?n=`, `?orderby=`), which is what
the crawler uses; it is a personal tracker with 1.5 to 3 s between requests, far below a browsing user.

## URLs

| What | URL |
| --- | --- |
| Category | `https://cairosales.com/en/<slug>/`, e.g. `/en/tvs-screens/`, `/en/small-appliances/fryers/` |
| With price range, 60 per page, sorted, page 2 | `/en/tvs-screens/?n=60&orderby=price&orderway=asc&price=3000-10000000&p=2` |
| Search | `/en/search?controller=search&search_query=playstation+5&n=60&...` (same filters) |
| Product | `https://cairosales.com/en/<category>/<name>-<reference>.html` (from the card) |
| Sitemap (every category) | `/en/sitemap` (the mega menu; `deals categories cairosales_eg <slug>` lists children) |

The slug in the config is the path under `/en/` without slashes at the ends: `tvs-screens`,
`mobiles-tablets/mobiles`, `built-in-kitchen-products/built-in-ovens`.

**The price filter** (`price=<min>-<max>`, EGP, from the Amazzing filter module):

- Prices have hidden decimals (a TV shown as "EGP 20,499" is 20,498.80 inside), so a product at the very
  edge of a range can fall on either side of it. The planner's ranges share their edges and cover
  everything, so nothing is lost; a product can at most appear in two neighbouring slices, which is harmless.
- Both ends are needed; an open-ended slice is sent as `<min>-10000000`. Decimals are accepted.
- An empty range answers "No products" with no cards: an empty page, no redirect (unlike Jumia).

**Sort order.** The default order is "best sales" with a daily random seed (`af_random_seed`), so the same
query lists products in a different order every day, and a crawl crossing midnight would see products move
between pages. Pages are therefore fetched sorted by price ascending.

## The traps

### 1. Out-of-stock products stay listed, without a price

A product that is out of stock keeps its listing card but shows no price and a "View" button instead of
"Add to cart" (the product page says `productShowPrice=false`, `quantityAvailable=0`). The dead
back-catalogue is huge: 55 of the first 60 mobiles, 51 of 52 tablets, all 21 game consoles, 261 of 262
laptops. The adapter records such a card as price None, out of stock, which the coordinator does not record
as a new product (and marks a known product as out of stock).

Because the shop sorts out-of-stock products behind every in-stock one (`oos_behaviour=1` in its filter
settings), a page with no price at all means every later page is dead too, and the adapter stops paging
there (`has_next` false). A known product that goes out of stock and sits past that point is simply not
seen again until it is back, the same as on Jumia and 2B where sold-out products vanish.

### 2. An unknown slug answers with the homepage

`/en/no-such-thing/` redirects (HTTP 200) to `/en/`. The adapter compares the served path with the one it
asked for and raises `UnknownTarget` ("redirected to /en/"), so a typo in the config fails loudly in
`deals status` and the failure alerts. A category page always keeps its own path, including with a price
filter or a page past the end (which answers "No products").

### 3. Big "catch-all" categories

`tvs-screens` has 830 products, `refrigerators` 1,020, `built-in-hobs` 814: Cairo Sales lists many sizes,
colours and discontinued models as separate products. With the group minimum prices and the out-of-stock
early stop, the live crawl is far smaller than the counts suggest.

## What a listing card contains

`ul.product_list > li.ajax_block_product` (fields we use in bold):

| Field | Example | Used as |
| --- | --- | --- |
| **`data-id-product`** | `49324` | `Listing.sku` |
| **`a.product-name`** (text, href) | "SAMSUNG 43-inch Crystal UHD 4K Smart TV 43U8000H", product URL | title, url |
| **`.content_price .price.product-price`** | "EGP 15,929" | price (absent when out of stock) |
| **`.content_price .old-price`**, `.price-percent-reduction` | "EGP 18,101", "-12%" | crossed-out price, kind `list` |
| **`.availability`** | "In Stock." | in stock (together with the price being shown) |
| **`.m-logo img[title]`** | "SAMSUNG", "SAMSUNG Mobile" | brand |
| `.sale-label` | "Free Delivery" | not used |
| `data-id-product-attribute` | `7884` | the default variant; not used (one card per product) |

There are no ratings, "only N left" or "sold recently" signals on cards; the only signal is the crossed-out
price, which the shop sets itself (every in-stock TV card shows one, median claim about 10%), so it is treated
like an RRP (`list`). New-listing alerts therefore essentially never fire on Cairo Sales; its alerts come
from our own price history, which is the trustworthy path anyway.

Around the grid: the count (`Showing 1 - 60 of 820 items`), the next link (`a[rel="next"]`), the
"Subcategories" block (`#subcategories a.subcategory-name`, children only) and the Amazzing filter form
(`#af_form`, with `id_category`, brands and features; not used).

## Category map (watch groups)

Products counted at the group's minimum price on 27 October 2026; "in stock" is the number with a price on
the first page (sorted by price, so a number under 60 is the whole in-stock count).

| Group | Cairo Sales slugs | Products (in stock) |
| --- | --- | --- |
| TVs | tvs-screens (children: sizes, OLED, QLED, Neo QLED, QNED, Mini LED...) | 820 (60+) |
| Refrigerators | refrigerators, freezers | 1,020 + 321 |
| Air conditioners | air-conditioners (wall mount, free standing, desert coolers, air curtains, concealed) | 527 |
| Gaming consoles | gaming-accessories/game-consoles, searches "playstation 5", "xbox series", "nintendo switch" (fuzzy: the group's title filters apply) | 21 (0) + 11 (0) + 162 (28) + 3 (1) |
| Phones | mobiles-tablets/mobiles | 259 (5) |
| Tablets | mobiles-tablets/tablets | 52 (0) |
| Laptops | computer-accessories/laptops-desktop-computers | 262 (1) |
| Desktops | computer-accessories/desktop-computers | 7 (0) |
| Monitors | computer-accessories/computer-monitors, gaming-accessories/gaming-monitors | 115 (7) + 42 (0) |
| PC components | none (no such category) | |
| Headphones | home-theaters-speakers/headphones, gaming-accessories/gaming-headphones | 231 (21) + 21 (3) |
| Kitchen machines | small-appliances/fryers, coffee-maker, blenders, hand-stand-mixers, food-processors, kitchen-machines, chopper, ovens-toasters, cookers; microwaves | about 1,370 |
| Kitchen appliances | small-appliances/kettle, juicer, grills, meat-grinders, sandwich-maker, iron; water-dispenser/water-dispenser, water-dispenser/filters | about 530 |
| Cooking & dishwashers | cookers-ovens, dishwashers, built-in-kitchen-products/built-in-ovens, built-in-kitchen-products/built-in-hobs | 516 + 206 + 755 + 814 |

Notes on the tree (from `/en/sitemap`; `deals categories cairosales_eg <slug>` lists children):

- Cairo Sales is first an appliance shop: fridges, cookers, built-in kitchens, air conditioners and
  washing machines are large and in stock; phones, tablets, laptops and consoles are mostly dead listings.
- Children are mostly sizes or types (TVs by inch, fridges by feet, washers by kg); the parent is always
  used, as on every shop.
- Not tracked: washers-dryers (`washers-dryers`), kitchen hoods, water heaters, vacuum cleaners, fans and
  heaters (`small-appliances/fans`, `small-appliances/heaters`), smart watches, home theaters and speakers.

## Performance and capacity

About 1.2 s per page of 60 products from the Mac (560 KB of HTML), plus the 1.5 to 3 s pause. The 38
roots plan into about 40 slices; with the out-of-stock early stop a full pass is roughly 120 pages,
10 minutes of one worker. The Mac crawls Cairo Sales in its own thread, in parallel with the other shops.

## Running and checking it

```bash
.venv/bin/deals categories cairosales_eg                       # some roots
.venv/bin/deals categories cairosales_eg small-appliances      # children
.venv/bin/deals crawl cairosales_eg "category:tvs-screens|price:3000-" --pages 2
.venv/bin/deals plan cairosales_eg "category:refrigerators|price:5000-" --min-delay 1.5 --max-delay 3
```

On the server, `make sql`:

```sql
SELECT grp, count(*) products, sum(in_stock) in_stock FROM products WHERE site = 'cairosales_eg' GROUP BY grp;
SELECT kind, count(*), sum(failures > 0) failing, min(next_due_at) FROM jobs WHERE site = 'cairosales_eg' GROUP BY kind;
```

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `blocked: cairosales_eg: Cloudflare challenge` | Cloudflare refuses this connection or fingerprint | From home: `deals crawl cairosales_eg "category:tvs-screens\|price:3000-"` should print 60 products; if every page is challenged try another `IMPERSONATE` target in `cairosales_eg.py`. From a cloud IP: expected, remove `cairosales_eg` from that worker |
| `UnknownTarget: /en/x/ redirected to /en/` | Wrong slug (the shop answers with its homepage) | Copy the slug from `deals categories cairosales_eg <parent>` or the sitemap |
| A slice suddenly parses 0 products | Layout change (no `ul.product_list`, renamed classes) | Save a fresh page into `tests/fixtures/cairosales_category.html.gz` and fix the parser |
| Every product of a slice is out of stock | Normal for phones, tablets, laptops and consoles on this shop | Nothing to do; the crawl stops after the first all-unpriced page |
