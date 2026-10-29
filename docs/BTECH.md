# B.TECH (btech.com): site analysis and how we crawl it

Everything we learned about btech.com (B.TECH, Egypt's biggest electronics and appliance chain, also a
marketplace) while adding it as the sixth shop (27 October 2026). The adapter is `src/deals/sites/btech_eg.py`
(site name `btech_eg`); its tests are in `tests/test_parsers.py` (`test_btech_*`) against the saved API answer
`tests/fixtures/btech_products.json.gz` (TVs, first 100 by price) and the saved category page
`tests/fixtures/btech_category.html.gz` (TVs & Projectors, which carries the whole category tree). Sizes are
from October 2026 and drift.

## Summary

| Question | Answer |
| --- | --- |
| Site name in the code and config | `btech_eg` (Arabic name in alerts: بي تك (B.TECH)) |
| Platform | A custom Next.js storefront ("blue-beetle-web") on top of B.TECH's own retail APIs (`retail-online-prod.btech.com`) |
| What we fetch | The shop's **product search API** (`POST .../green/discovery/api/v1/products/search`): structured JSON, no HTML parsing, plus one category page a day for the category tree |
| Products per request | 100 (`page_size`; the API rejects more with HTTP 422) |
| Deepest page | No cap (`max_slice_total` 2,000, so the planner splits anything bigger) |
| Exact count | Yes, `total_items` and `total_pages` on every answer |
| Bot protection | Cloudflare in front of the pages (plain `curl` passes from home), F5 BIG-IP in front of the API. The **API host refuses datacenter IPs** (connection timeout from the droplet, pages still load there) |
| Who can crawl it | Home/office connections only, like noon, Jumia, Cairo Sales and 2B |
| Authentication | A guest JWT the site puts in its `btech-auth-session` cookie on any page load; valid one day; `Accept-Language: en` is mandatory (422 otherwise) |
| How products are keyed | The offer's `sku` (e.g. `1TSKTVFS3222A000L007`); every offer has its own sku, even third-party ones |
| How categories are keyed | The URL slug: `category:tvs-projectors/televisions` or just `category:televisions` (the API only knows the last segment) |
| How big categories are split | By price only, like every shop (`price_range` is inclusive at both ends) |
| Politeness | 1 to 2 s between requests per worker (`[sites.btech_eg]`); one request covers 100 products |
| Tracked | about 4,000 products across all groups at the group minimums (about 45 roots), all in stock |

## Access

- `https://btech.com/en/...` pages load with plain `curl` and with Chrome's fingerprint, from home and from the
  droplet. The only thing a page is needed for is the session cookie and, once a day, the category tree.
- The API `https://retail-online-prod.btech.com/api/v1/green/discovery/api/v1/products/search` answers from
  home in 0.3 to 1 s. From the droplet (DigitalOcean) the TCP connection to that host times out, with any
  fingerprint: the API is firewalled from cloud ranges, so B.TECH is a home-worker shop.
- Without `Authorization: Bearer <guest JWT>` the API answers 401 ("Unauthorized"); with a malformed token
  403 ("explicit deny in an identity-based policy"). On 401/403 the adapter loads a page again for a fresh
  cookie and retries once, then raises `Blocked`, and the coordinator pauses B.TECH for that IP.
- The JWT (issuer `cypher-identity-service`, audience `guest`) is URL-encoded JSON in the cookie:
  `{"accessToken": "ory_st_...", "JWT": "eyJ...", "jwtExpiresAt": "<tomorrow>", "userId": ..., "isGuest": true}`.

## The API

One POST per page, JSON body:

```json
{"page": 1, "page_size": 100, "sort_by": "price_asc",
 "filters": {"categories": "televisions", "in_stock": true, "price_range": {"min": 3000, "max": 10000}}}
```

Headers: `x-platform: web`, `accept: application/json`, `content-type: application/json`,
`accept-language: en` (or `ar` for Arabic names), `authorization: Bearer <JWT>`.

- **Search**: `"search_query": "playstation 5"` in the body (no `categories`); it accepts the same filters and
  sort. Other key names (`query`, `q`, `keyword`) are silently ignored and return the whole catalogue
  (10,000, the API's cap).
- **`sort_by`**: `default`, `price_asc` or `price_desc` only. We sort by price so that paging is stable.
- **`price_range`**: `{"min": N, "max": M}`, inclusive at both ends (`min 4249, max 4249` returns the one TV at
  4,249); either end may be left out. Prices are whole pounds (floats like `4249.0`). Any other spelling
  (`"4000-5000"`, a list, `price_min`) is rejected (422) or ignored.
- **`categories`**: the leaf slug. A parent slug (`tvs-projectors`) returns the whole department (502),
  two slugs separated by a comma return nothing, an unknown slug returns `total_items: 0` **without an
  error**, hence the tree check below.
- **`in_stock`**: no visible effect; the catalogue only lists offers that are in stock (320 TVs with or
  without it, all `is_in_stock: true`). A product that sells out disappears from its category until it is
  back, like on Jumia and 2B.
- **Paging**: `page_size` up to 100; a page past the end answers **HTTP 500** ("Error executing search with
  filters request"), which the adapter treats as an empty page when it is not page 1.
- The response: `{"items": [...], "total_items": 320, "page": 1, "page_size": 100, "total_pages": 4, "filters": null}`.

### The category tree

The site's categories are three levels deep (`tvs-projectors` > `televisions` > `smart-tvs`); 12 departments,
330 slugs, all unique. The API's own `GET .../discovery/api/v1/categories` only returns the 12 top-level
entries (`subcategories: null`), so the adapter reads the full tree from the "Category" filter embedded in any
category page (the React Server Components payload, `self.__next_f.push(...)` chunks, object
`{"key":"categories",...}` with `id`, `value`, `count` and nested `options`). It is fetched once a day per
worker and used to (a) refuse an unknown slug in the config with `UnknownTarget` and (b) list children in
`deals categories btech_eg <slug>`. The counts are products per category (any level).

Category pages themselves (`/en/c/<l1>/<l2>`) server-render only the first 20 products and ignore `?page=`;
the rest is infinite scroll from the API, which is why we call the API directly. `?sort=price_asc` is the only
URL parameter the page honours.

## What an offer contains

| Field | Example | Used as |
| --- | --- | --- |
| **`sku`** | `1TSKTVFS3222A000L007` | `Listing.sku` |
| **`name`** | "Skyline 32 Inch TV , HD LED - 3222A" | title |
| **`slug`** + **`offer_id`** | `06b30035-...` (usually the variant uuid, sometimes readable), `4e0e2f2d-...` | `https://btech.com/en/p/<slug>?offering_id=<offer_id>` |
| **`price.final_price`** | 4249.0 | price (what you pay, coupons included) |
| **`price.base_price`** | 4499.0 | crossed-out price, kind `was` for B.TECH's own offers, `list` for third-party sellers |
| `price.discounts` | `[{"name": "promotion", "value": {"type": "amount", "value": 250}}]` or `item_coupon` | not used (the kind of discount) |
| `price.final_without_coupon` | differs from `final_price` on 7% of TVs | not used |
| **`seller_name`** / `store_name` | "B.TECH" or a marketplace seller ("Mohamed Mohy", "storeleek") | `Listing.seller` when not B.TECH (shown in alerts) |
| **`is_in_stock`** | true | in stock |
| **`is_best_seller`** | true on about half the TVs | badge `bestseller` |
| `is_new_arrival` | false | not used |
| `price_drop_amount` | 946.0 on 11% of TVs (the shop's own "price dropped by" label) | not used yet; could become a signal once we know what window it covers |
| **`brand`** | "Skyline" | brand |
| `categories.l1/l2/l3` | tvs-projectors / televisions / standard-tvs | not used |
| `variant_id`, `product_id` | uuids | not used (one offer per card) |
| `fulfillment` | `["delivery", "pickup"]` | not used |

There are no ratings, review counts or "only N left" signals in the listing API.

**Marketplace.** 189 of the 320 TVs are third-party offers (20 sellers on the first 100 alone), each with its
own sku, so one TV model can appear several times at different prices under different skus; we do not group
them (unlike noon, where one SKU has several sellers). The seller is kept on the listing and alerts say
"البائع: ...". B.TECH's own offers carry a promotion on about a fifth of the items (28 of 131 TVs), which is
why their `base_price` is treated as a recent price (`was`) rather than an inflated RRP; a third party's
`base_price` is a claim (`list`).

## Category map (watch groups)

Products counted on 27 October 2026 (in stock, before the group's minimum price).

| Group | B.TECH slugs | Products |
| --- | --- | --- |
| TVs | tvs-projectors/televisions | 320 |
| Refrigerators | large-home-appliances/refrigerators, deep-freezers | 321 + 172 |
| Air conditioners | large-home-appliances/air-conditioners | 310 |
| Gaming consoles | gaming-area/consoles (63, with accessories), searches "playstation 5" (41), "xbox series", "nintendo switch"; used consoles are excluded by title ("Pre-Owned"). Console titles name the bundle's controllers ("Sony Playstation 5 Slim 1 TB ,1 Controllers ,White"): until 29 Oct the "controller" exclude word dropped all of these, so only the 5 PS5s with "Console" in the title were tracked (about 35 new PS5 listings, Slim/Disc/Digital/Pro/30th Anniversary, were missed). The filter now ignores a controller count. "Joy-Con Nintendo Switch , 64 GB" (a Switch, 24,999) is still dropped by "joy-con" | |
| Phones | mobiles-tablets/mobile-phones | 819 |
| Tablets | mobiles-tablets/tablets | 63 |
| Laptops | laptop-pc/laptops (pre-owned-laptops 376 not tracked) | 533 |
| Desktops | laptop-pc/pcs | 12 |
| Monitors | laptop-pc/monitors | 101 |
| PC components | laptop-pc/pc-components | 209 |
| Headphones | audio-recording/headphones (on-ear 30, over-ear 116) | 146 |
| Kitchen machines | small-home-appliances/fryers 225, kettles-coffee-makers 458, blenders-mixers 605, choppers 143, food-processors-grinders 104, kitchen-machines 16, microwaves-ovens 174 | about 1,700 |
| Kitchen appliances | juicers-cirtus-juicers 59, grills 100, toasters-sandwich-makers 92, irons 278, hot-plates-electric-stoves 55, specialty-appliances-and-tools 100, water-dispensers 128, water-coolers-and-filters 184 | about 1,000 |
| Cooking & dishwashers | large-home-appliances/cookers 177, dishwashers 80, built-in-ovens 163, built-in-hobs 223 | 643 |

Departments (`deals categories btech_eg <slug>`): tvs-projectors 502, large-home-appliances 1,987,
small-home-appliances 4,009, mobiles-tablets 2,593 (1,711 accessories), laptop-pc 2,242, audio-recording 428,
gaming-area 306, electronics 1,093, wearables 339, personal-care 1,057, non-electronics 5, tech-care 4.
Not tracked: washing machines & dryers 320, water heaters 203, vacuum cleaners 302, fans 341, smartwatches 334,
portable speakers 234, printers 222, cameras.

## Performance and capacity

An API answer with 100 products is about 150 KB and takes under a second from the Mac. About 45 roots plan
into about 60 slices; a full pass is roughly 80 requests, a few minutes of one worker.

## Running and checking it

```bash
.venv/bin/deals categories btech_eg                              # departments
.venv/bin/deals categories btech_eg large-home-appliances        # children with counts
.venv/bin/deals crawl btech_eg "category:tvs-projectors/televisions|price:3000-" --show 5
.venv/bin/deals crawl btech_eg "search:playstation 5|price:8000-"
```

On the server, `make sql`:

```sql
SELECT grp, count(*) products, sum(in_stock) in_stock, sum(seller IS NOT NULL) third_party FROM products WHERE site = 'btech_eg' GROUP BY grp;
SELECT kind, count(*), sum(failures > 0) failing, min(next_due_at) FROM jobs WHERE site = 'btech_eg' GROUP BY kind;
```

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `blocked: btech_eg: the API refused our guest token twice` | The API answers 401/403 even with a fresh cookie | From home: `deals crawl btech_eg "category:televisions"` should print 100 products. If it fails, load btech.com in a browser and check the request headers of `products/search` (a new header or token scheme) |
| `blocked: btech_eg: no btech-auth-session cookie` | The page loaded but set no session cookie (challenge page or a site change) | Open the page in a browser; check `SESSION_COOKIE` in `btech_eg.py` |
| Connection timeouts on `retail-online-prod.btech.com` | A cloud IP | Expected; remove `btech_eg` from that worker |
| `UnknownTarget: no category with slug 'x'` | Wrong slug | `deals categories btech_eg <department>` and copy the slug |
| `ValueError: btech_eg: API answered HTTP 422 ...` | The API rejected a field (renamed or new validation) | Read the `detail`; adjust `build_body`; save a fresh answer into `tests/fixtures/btech_products.json.gz` |
| `ValueError: no category filter in the page` | The category page no longer embeds the filter tree | Save a fresh page into `tests/fixtures/btech_category.html.gz`; adjust `parse_category_tree` (or switch validation to the API's `categories` endpoint if it starts returning children) |
