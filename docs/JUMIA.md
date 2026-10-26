# Jumia Egypt (jumia.com.eg): site analysis and how we crawl it

Everything we learned about jumia.com.eg while adding it as the third shop (26 October 2026), so nobody
has to rediscover it. The adapter is `src/deals/sites/jumia_eg.py`; its tests are in
`tests/test_parsers.py` (`test_jumia_*`) against the saved page `tests/fixtures/jumia_category.html.gz`.
Sizes and percentages are from October 2026 and drift.

## Summary

| Question | Answer |
| --- | --- |
| Site name in the code and config | `jumia_eg` (Arabic name in alerts: جوميا مصر) |
| What we fetch | The normal category / search HTML pages; each embeds its products as JSON (`window.__STORE__`) |
| Products per page | 40 |
| Deepest page | 50, so at most **2,000 products per query** (`max_slice_total = 2000`) |
| Exact count | Yes, "454 products found" on every page |
| Bot protection | Cloudflare managed challenge. Passes with Safari's TLS fingerprint from a home IP |
| Who can crawl it | Home/office connections only (the Mac, friends). The droplet is always refused, like noon |
| How products are keyed | Jumia's product SKU (e.g. `LG178EA3AHODVNAFAMZ`); one seller per SKU, so no `@seller` |
| How big categories are split | **By price only**, never by sub-category (see "Sub-categories lose products") |
| Politeness | 1.5 to 3 s between requests per worker (`[sites.jumia_eg]`) |
| Tracked | about 13,800 products in all 14 groups, 52 roots |

## Access: Cloudflare and TLS fingerprints

Every page (even `robots.txt`) is behind Cloudflare's managed challenge ("Just a moment...",
`window._cf_chl_opt`, `cType: 'interactive'`, HTTP 403). Cloudflare decides by the TLS/HTTP2
fingerprint and the IP, not by cookies:

| `curl_cffi` impersonation | From the Mac (home IP) | From the droplet (DigitalOcean) |
| --- | --- | --- |
| `chrome`, `chrome131`, `chrome124`, `edge101`, `chrome131_android` | 403 challenge | 403 |
| `firefox`, `safari_ios` | 403 challenge | 403 |
| **`safari`**, `safari17_0` | **200, full page** | 403 (`safari18_0` too) |

So the adapter uses `requests.Session(impersonate="safari")`. No browser, no cookies to bootstrap;
Cloudflare sets `__cf_bm` on the first answer and the session keeps it. On a challenge the adapter
retries once with a fresh session, then raises `Blocked`, and the coordinator pauses Jumia for that IP
(15 minutes doubling up to a day) while other IPs carry on.

If Safari's fingerprint ever stops working: try the other `curl_cffi` targets first (`deals crawl jumia_eg
"category:televisions|price:3000-"` shows at once whether a page parses), then a headless browser that
solves the challenge and hands its `cf_clearance` cookie to plain requests (the amazon.eg pattern in
`amazon_eg.py`). The challenge is the interactive (Turnstile) kind, which headless browsers often fail;
a datacenter IP fails it regardless.

## URLs

| What | URL |
| --- | --- |
| Category | `https://www.jumia.com.eg/<slug>/`, e.g. `/televisions/` |
| Category with price range and page | `/televisions/?price=3000-10000000&page=2` |
| Search | `/catalog/?q=playstation+5&price=8000-10000000` |
| Product | `https://www.jumia.com.eg/<name>-<number>.html` (from the product JSON's `url`) |
| Other sort orders (unused) | `&sort=newest`, `lowest-price`, `highest-price`, `rating`; the default is popularity |

English is the default language on `www.jumia.com.eg` (Arabic lives under `/ar/`), so titles come in
English like the other shops.

**The price filter** (`price=<min>-<max>`, whole EGP):

- Both ends are inclusive (a TV at exactly 15,299 matches `15299-15299`, `15000-15299` and
  `15299-15400`), which is what the planner's shared edges assume.
- It needs both ends. `price=3000-` happens to work but echoes a nonsense range, so an open-ended slice is
  sent as `<min>-10000000`. Jumia clamps the upper end to the category's real maximum.
- Decimals are invalid: `price=2999.5-3000.5` redirects to the parent category.

## The traps

### 1. An empty result redirects to the parent category

Jumia never shows "no results" for a category. These all answer **HTTP 200 with the parent category's
products** after a redirect:

| Request | What came back |
| --- | --- |
| `/televisions/?price=999000-1000000` (no TV that expensive) | `/electronic-television-video/`, 590 products |
| `/televisions/?price=3000-1000000&page=13` (one past the last page) | `/electronic-television-video/` |
| `/smartphones/?page=51` | `/mobile-phones/`, 2,222 products |
| `/xbox-one-consoles/?price=8000-...` (none that dear) | `/xbox-one/` |

Taken at face value that would record hundreds of unrelated products into the group. The adapter compares
the path it asked for with the path the page says it is (`__STORE__.url`): if they differ on page 2+, the
listing ended; on page 1, it is an empty slice if the page we landed on lists the requested category in its
sidebar (it is the parent), and otherwise the slug is wrong or was renamed and the job fails with
`UnknownTarget` ("redirected to ..."), which `deals status` and the failure alerts show.

### 2. Unknown slugs: 404, and some aliases

A slug that doesn't exist answers 404 (`UnknownTarget`, the job fails loudly). A few old slugs are aliases
that redirect elsewhere (`/appliances/` goes to `kitchen-dinning-small-appliances`, `/air-conditioners/` to
`air-conditioning`); always put the canonical slug from the sidebar links in the config.

### 3. Sub-categories lose products

A Jumia category holds products that sit on the category itself and in none of its children:

| Category (with the group's minimum price) | Products | Sum of its children | Missing |
| --- | --- | --- | --- |
| Televisions (3,000+) | 454 | 434 (LED & LCD, OLED, Smart, Large screen) | 20 (4%) |
| Air Conditioners (8,000+) | 1,274 | 1,139 (Evaporative, Portable, Split, Wall) | 135 (11%) |
| Smartphones (2,500+) | 2,041 | 2,015 (Android, iOS, Other) | 26 (1%) |

A sub-category split would silently drop these, so the planner splits by price only (price ranges cover
everything, and the slices still keep the group's minimum price). Checking the same on Amazon and noon
showed the gap there too, so since 26 October every shop is planned this way (GUIDE.md, Algorithm 2). Smartphones (2,041 > 2,000) plans
into 6 price slices covering 2,049 products (a few sit on shared edges).

## What a listing page contains

`window.__STORE__ = {...};` in a `<script>`. Keys: `view` ("Catalog", or "NotFound"), `url` (the path
actually served: used for the redirect check), `products` (40 dicts), `appliedFilters`, plus tracking,
cart and ad settings. Sponsored products are not in `products` (they load from a separate
`/fragment/sp/...` URL), but the parser drops any `isSponsored` one anyway.

The HTML around it gives two more facts: the count (`<p>454 products found</p>`) and the pagination links
(`<a class="pg" aria-label="Next Page">`, "Last Page" stops at 50). The left sidebar's "Category" box lists
the ancestors (class `-phxl`), the current category (bold, `-m`) and its children (indented, `-phxxxl`);
for a leaf it lists its siblings at the same indent. Links carry the slug in the href and
`data-eventLabel="2313-televisions"` (Jumia's numeric id + slug).

**One product** (fields we use in bold):

| Field | Example | Used as |
| --- | --- | --- |
| **`sku`** | `LG178EA3AHODVNAFAMZ` | `Listing.sku` |
| **`name`**, **`brand`**, `displayName` | "LED Smart TV 43 inch ...", "LG", "LG LED Smart TV ..." | title = name with the brand in front unless the name already has it (`displayName` would give "Sony Sony PlayStation 5") |
| **`prices.rawPrice`** | `"15299.00"` | price |
| **`prices.oldPrice`**, `discount` | `"EGP 19,999.00"`, `"24%"` | crossed-out price, kind `list` |
| **`isBuyable`** | `true` | in stock (out-of-stock products are mostly not listed at all) |
| **`rating.average`**, **`rating.totalRatings`** | 3.8, 10 | rating (None when 0 ratings), reviews |
| **`simples[].stockText`** | `"1 item left"` | stock left (of the selected variant) |
| `stock` | `{"percent": 3, "text": "3 items left"}` | only on `/flash-sales/`; read as stock left when present |
| **`url`** | `/lg-led-smart-tv-...-134491099.html` | product link |
| `sellerId` | 198631 | not used (no seller name on listings) |
| `badges.main` | `{"identifier": "JMALL", "name": "Official Store"}` | not used yet |
| `badges.campaign` | "Non-Returnable" (NORET), "Made in Egypt" (LOCAL), BSELL, BUNDLE | not used |
| `isShopExpress` | true | "Jumia Express": stocked and shipped by Jumia; not used |
| `tags` | `"B2S_1\|BF\|BLF\|CP_30\|JF\|JMALL"` | internal campaign codes; meanings unknown, not used |
| `mpg` | `{"lowPriceFormatted": "EGP 8,499.00", "otherOffers": 2}` | other sellers' offers on the product page; not used |
| `simples` | one per variant (size/colour) with its own price and stock | electronics almost always have one |
| `rotateBanner` | "Low Stock", "More Variants Available" | not used |
| `tracking.isSecondChance` | false | probably used/refurbished ("second chance"); never true in our sample |

## Signals and how they feed the deal rules

Measured on 280 cards from category pages (phones, fryers, fridges, headphones, blenders, laptops) on
26 October 2026:

| Signal | Share of cards | Treatment |
| --- | --- | --- |
| Crossed-out price (`oldPrice`) | 88% | Seller-set, so kind `list` (an RRP-like claim). Claims of 60%+ are rejected for new listings, same as noon |
| Claimed discount size | median 12% of the cards that show one; 24% of all cards claim 25% or more | |
| "N items left" | 72% show one; 45% say 3 or fewer | Proves nothing, so `jumia_eg` is in `LOW_STOCK_EVERYWHERE` (like amazon.eg) and the alert says so |
| Deal badges ("limited deal", "lowest price in N days") | none exist on listing cards | No strong signal, so **new-listing alerts essentially never fire on Jumia**: its alerts come from our own price history (real drops, all-time lows, early drops), which is the trustworthy path anyway |
| Rating with 0 reviews | common on new items | stored as rating None, reviews 0 |

**Flash sales.** `/flash-sales/` (409 products when checked) shows a stock bar (`stock`: "3 items left",
"28 items in stock"). The same products on their category pages carry the same flash price but no flash
marker at all (checked 12 of 12), so a category crawl cannot tell a flash sale. A future improvement could
crawl `/flash-sales/` as its own source and mark those SKUs `limited_deal`.

**Other sellers.** Unlike noon, each Jumia SKU has one seller; the listing shows that SKU's price. When other
sellers offer the same product, the card carries `mpg.otherOffers` and the cheapest of their prices; we
don't use it yet. A product the buy-box flips between sellers would look like small A→B→A moves, which the
1% noise floor and the rarity rule already absorb (same as Amazon).

## Category map (watch groups)

All roots get the group's `min_price`. Products counted at that minimum on 26 October 2026.

| Group | Jumia roots (slugs) | Products |
| --- | --- | --- |
| TVs | televisions | 454 |
| Refrigerators | appliances-fridges-freezers, freezers | 691 + 457 |
| Air conditioners | air-conditioning (includes portable and evaporative coolers above 8,000) | 1,274 |
| Gaming consoles | playstation-5-consoles, playstation4-consoles, nintendo-switch-consoles, search "playstation 5", "xbox series", "nintendo switch" | 6 + 1 + 2 + 21 + 1 + 6 |
| Phones | smartphones (split into 6 price slices) | 2,041 |
| Tablets | tablets | 83 |
| Laptops | laptops | 287 |
| Desktops | desktop-computers | 8 |
| Monitors | monitors | 87 |
| PC components | computer-components | 168 |
| Headphones | electronics-headphone, mobile-phone-bluetooth-headsets (AirPods-style earbuds live here) | 305 + 972 |
| Kitchen machines | air-fryers, deep-fryers, coffee-makers, espresso-machines, espresso-machine-coffeemaker-combos, blenders, mixers, food-processors, electric-pressure-cookers, slow-cooker, rice-cooker, small-appliances-microwave, ovens-toasters | about 3,900 |
| Kitchen appliances | beverage-refrigerators, bread-machines, contact-grills, countertip-burners, electric-griddles, electric-skillets, electric-woks, icecream-machines, juicers, small-appliance-sets, specialty-appliances, cooking-steamer, waffle-irons, coffee-tea-espresso-appliances (kettles, frothers), coffee-machine-grinder-accessories (grinders), small-appliances-ironing-laundry (irons, steamers), water-coolers-filters | about 1,600 |
| Cooking & dishwashers | cooking-appliances (cookers, cook tops), ranges, wall-ovens, dishwashers | 973 + 42 + 201 + 172 |

Notes on the tree (top level: `electronics`, `phones-tablets`, `computing`, `home-office`, `video-games`;
`deals categories jumia_eg <slug>` lists children):

- Large appliances are not under "Appliances" (`home-office-appliances` only holds cooking appliances) but
  under Home & Office › Tools & Home Improvement › Appliances (`home-improvement-appliances`): dishwashers,
  freezers, range hoods, ranges, refrigerators, wall ovens, washers & dryers.
- Small kitchen appliances are `kitchen-dinning-small-appliances` (Jumia's spelling) under Kitchen & Dining.
  Coffee machines are elsewhere: Kitchen & Dining › Coffee, Tea & Espresso (`coffee-makers`,
  `espresso-machines`), while kettles are in the small-appliances tree (`coffee-tea-espresso-appliances`).
- Gaming: each platform has a Consoles child (`playstation-5-consoles`, `playstation4-consoles`,
  `nintendo-switch-consoles`, `xbox-one-consoles`, empty above 8,000 EGP). There is no Xbox Series node, and
  consoles are also filed under accessories, hence the searches. Jumia's search is fuzzy ("steam deck"
  returns steam mops), so the group's `require_words` / `exclude_words` filters matter here too.
- Computing is small on Jumia: 287 laptops, 8 desktops, 87 monitors.
- Not tracked (not in our groups): washers & dryers (`appliances-washers-dryers`), range hoods
  (`range-hoods`, 435 above 5,000), smart watches.

## Performance and capacity

- About 1.3 s to fetch a page from the Mac (670 KB of HTML, parsed in milliseconds), plus the 1.5 to 3 s
  pause and the upload to the coordinator. Measured on the live system: air conditioners, 1,274 products in
  32 pages, took 153 s (4.8 s per page), so one worker does about 750 Jumia pages an hour.
- The 52 roots plan into 57 slices (phones split into 6 price ranges, every other root is one slice). A full pass
  is about 350 pages (13,800 products / 40), roughly 30 minutes for one worker. The four 10-minute groups
  (air conditioners, consoles, phones, tablets) are about 95 pages; at their fastest that is about 570 pages
  an hour, most of one worker's budget when everything moves. Quiet slices back off to 3 times their
  interval, which is what keeps the rest on time; a second home worker with `jumia_eg` doubles the budget.
- The Mac crawls Jumia in its own thread, in parallel with noon and Amazon (one job per shop per IP).
- More capacity: any friend's worker with `jumia_eg` in `DEALS_SITES`.

## Running and checking it

```bash
.venv/bin/deals categories jumia_eg                        # top-level slugs
.venv/bin/deals categories jumia_eg electronic-television-video
.venv/bin/deals crawl jumia_eg "category:televisions|price:3000-" --pages 2
.venv/bin/deals plan jumia_eg "category:smartphones|price:2500-" --min-delay 1.5 --max-delay 3
```

A worker crawls Jumia when `jumia_eg` is in its `DEALS_SITES` (the Mac's `worker.env` has
`noon_eg,amazon_eg,jumia_eg`; after changing `worker.env` run `make install-mac-worker`, because the launchd
agent copies the environment when it is installed). On the server, `make sql`:

```sql
SELECT grp, count(*) products, sum(in_stock) in_stock FROM products WHERE site = 'jumia_eg' GROUP BY grp;
SELECT kind, count(*), sum(failures > 0) failing, min(next_due_at) FROM jobs WHERE site = 'jumia_eg' GROUP BY kind;
```

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `blocked: jumia_eg: Cloudflare challenge` | Cloudflare refuses this connection or fingerprint | From a home IP: check `deals crawl jumia_eg ...`; if every page is challenged, try another `IMPERSONATE` target in `jumia_eg.py`. From a cloud IP: expected, remove `jumia_eg` from that worker |
| `UnknownTarget: ... (HTTP 404)` | Wrong slug | Fix the slug (copy it from the sidebar link) |
| `UnknownTarget: /x/ redirected to /y/` | The slug is an alias or was renamed | Use `/y/` or the new slug |
| A slice suddenly parses 0 products | Layout change (no `__STORE__`, renamed fields) | Save a fresh page into `tests/fixtures/jumia_category.html.gz` and fix the parser against it |
