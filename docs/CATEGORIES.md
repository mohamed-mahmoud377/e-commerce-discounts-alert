# Category catalogue and findings

What we learned about the shops' category trees while building the watch list, so nobody has to
rediscover it. Ids come from `deals categories <site> <id>`; sizes are from October 2026 and drift.

## amazon.eg browse nodes

Roots known to the `categories` command: Electronics `18018102031`, Home `18021933031`, Appliances
`21863792031`, Video Games `18022560031`. Other departments (Video Games was one) are **not** children of
these roots. To find one, fetch a search page restricted to the department (`/-/en/s?k=<word>&i=<alias>`,
e.g. `i=videogames`) and read the `rh=n%3A<id>` links in its left sidebar; `deals categories amazon_eg <id>`
then lists its children. Department aliases follow amazon.com (`videogames`, `computers`, `electronics`).

| Group | Node | Notes |
| --- | --- | --- |
| TVs | 21832982031 | |
| Refrigerators | 21863822031 | |
| Air conditioners | 21863915031 | |
| Mobile Phones | 21832883031 | under Mobile Phones & Communication 21832868031, which also holds Smartwatches 21832958031 and Accessories 21832886031 |
| Tablets | 21832915031 | under Computers 21832872031 (293 products) |
| Laptops | 21832907031 | Computers; Desktops 21832914031, Monitors 21832909031, Components 21832913031, Data Storage 21832918031 |
| Headphones | 21832887031 | |
| Wearables | 21832878031 | not tracked yet (smartwatches, fitness bands) |
| Video Games department | 18022560031 | Platform nodes: PlayStation 5 21868828031, Xbox Series X&S 21868824031, Nintendo Switch 21868826031, Switch 2 206233785031, Handheld systems 92555823031, PlayStation 4 21868821031, Xbox One 21868820031, PC 21868814031. **Don't watch a platform node**: it mixes consoles, games and accessories, and a split recursed into the Games / Downloadable Content children (tried 26 Oct, when the planner still split by sub-category: 470 controller covers landed in the consoles group). Only Nintendo has console-only children: Switch > Consoles 21868896031 (222 items), Switch 2 > Consoles 206233788031. PlayStation and Xbox have no Consoles child in the sidebar, so those use `search:playstation 5 console` etc. with `min_price` 8,000. Even then the searches and the Nintendo Consoles node return racing wheels, headsets and Pro controllers above 8,000 EGP (160 of the first 160 recorded items were mostly accessories), hence the `require_words` / `exclude_words` title filters on that group. Sponsored cards ignore `p_36`, so the group minimum is also enforced when recording |
| Small kitchen appliances | 21863827031 | children: Fryers 21863949031, Coffee Machines 21864088031, Blenders/Mixers/Food Processors 21863955031, Pressure/Slow Cookers 21863956031, Bread Makers 21863959031, Hot Plates 21863960031, Electric Utensils 21863962031, Juicers 21863952031, Kettles 21863961031, Mills & Grinders 21863957031, Sandwich Makers 21863958031, Specialty 21863950031, Toasters 21863951031, Vacuum Sealers 21863954031, Waffle Makers 21863948031, Water Purifiers 51375552031 |
| Microwaves & countertop ovens | 21863820031 | |
| Ranges / ovens / cooktops | 21863824031 | |
| Dishwashers | 21863823031 | |

Facts that shaped the crawler:

- A bare node page (`rh=n:<node>` without `p_36`) renders a landing layout with 0 product cards; always
  send a price range.
- The exact count caps at 1,000 (24 per page); bigger nodes must be split by price (`planner.py`).
- Prices on FX-linked listings creep by a piastre per visit (Vivo Y05: 9,646 → 9,682 over 34 rows);
  moves under 1% are therefore not recorded.
- The featured offer switches seller between visits on about 70 products an hour, moving the price 2 to
  8% back and forth (A → B → A). Harmless under the 30% rule plus the rarity rule.
- "Only N left" is shown on 45% of listings (49% say 10 or fewer): not evidence of anything.
- 8% of listings show a crossed-out price; "Was:" (a past price) is rarer than "List:" (an RRP).
- Datacenter IPs are challenged often; the droplet manages Amazon with pauses, never noon.
- **"No results" pages.** A tiny node whose last products disappeared (PC components child 21833039031 had
  2) answers with "No results for your search query" and sometimes a grid of 48 unrelated recommendations.
  The parser returns an empty page for it (not a decoy, not a failure), and a crawl that finds 0 products
  only counts as a failure when the slice was expected to hold 10 or more.
- **Decoy pages.** A session Amazon distrusts (seen on 26 Oct after a burst of test fetches from the Mac,
  which shares the worker's IP) gets a plausible-looking listing of random groceries (milk, tissues, rice;
  48 cards, no result-count bar, "over N results" missing) instead of the requested page. Counting it
  made the planner split a node forever ("too many to count" down to 256,000 EGP+ bands) and crawling it
  would record junk. The parser now flags a page with cards but no result bar (`PageResult.decoy`) and
  the site raises `Blocked`, which pauses that IP on Amazon like a challenge. Lesson: don't run ad-hoc
  Amazon fetches from a machine that runs a worker.
- **Deals refinement (fast lane, 27 Oct 2026).** The search sidebar's "Deals & Discounts" facet is
  `p_n_deal_type` (facet id 26901034031): `26901036031` = All discounts, `26901035031` = Today's / limited-time
  deals (tiny: 1 phone, 3 overall when checked). Any extra refinement can be written straight into the
  category id, `category:21832883031,p_n_deal_type:26901036031`, because the adapter sends the id as the
  `n:` refinement and the price filter after it. Sizes above each group's minimum price: TVs 42, fridges 89,
  ACs 105, phones 37, tablets 6, laptops 7, monitors 17, headphones 131, so a sweep of all eight is about 20
  pages. The deals page itself (`/-/en/deals`) is rendered by JavaScript (0 product cards in the HTML).
- **The Today's Deals page and its JSON API (studied 27 Oct 2026).** `/-/en/deals` is the "discounts
  widget": the HTML embeds the first 30 deals as JSON (`productSearchResponse` inside `mountWidget(...)`)
  and the widget pages with a plain GET that works with the worker's WAF cookies and no CSRF token:
  `GET /d2b/api/v1/products/search?pageSize=200&startIndex=0&calculateRefinements=false&rankingContext={"pageTypeId":"deals","rankGroup":"DEFAULT"}&filters={...}`
  (`pageSize` up to 200; any query stops at 500 results, `nextIndex` becomes null). `filters` keys:
  `includedDepartments` (any browse node works, e.g. TVs 21832982031 gave 15, phones 21832883031 gave 3,
  the PS5 platform node 91; department ids differ from the catalogue roots: Electronics 18018103031,
  Mobiles 21832868031, Computers 21832872031, Appliances 21863792031 + 21863887031, Home & Kitchen
  18021934031, Video Games 18022561031), `includedTags` (`PIMA_PRICE_MIN_GV_30D` / `_60D` / `_90D` =
  "lowest price in N days", `PIMA_TRENDING`), `timeRangeFilters` (`STARTS_TODAY`, `ENDS_TODAY`),
  `promotionTypes` (`LIGHTNING_DEAL`: 23 site-wide). Ranges go in a separate `rangeRefinementFilters`
  parameter, a JSON *list*: `[{"id":"percentOff","min":54,"max":80}]` or `{"id":"price",...}` (the
  object form in the page URL is rejected). Each product carries `price.priceToPay`, `price.basisPrice`
  with label `Was:` or `List:`, `dealBadge` ("26% off" / "Limited time deal"), `dealDetails.type`
  (`BEST_DEAL`, `LIGHTNING_DEAL`), `productCategory.productType` and `meta.departmentIds`.
  Measured 27 Oct: about 2,300 distinct deals across the six departments above (17 requests); the
  whole-site default list is 500, two fifths of it fashion; 94 deals had started that day; the 54 to 80%
  band was almost all shoes and shirts. Only 185 of the 2,300 were products we track and **every one was
  already recorded at the deal price**, so this feed found nothing our sweeps and `p_n_deal_type` lanes
  had not. It is a *different* and smaller set than the search facet (TVs: 15 here vs 47 on the "All
  discounts" lane): only products enrolled in Amazon's deals programme appear (the same ones the search
  cards mark "Limited time deal": 170 of the 185 carried that badge). Where it does help is discovery: it
  lists products the node/search sweeps never returned (PS5 Slim Disc B0CKYM15RJ at 43,879, Switch 2 +
  Mario Kart B0F3HFQ5SY at 32,199, Samsung 85" QN70H B0GWJX7BV6 at 78,999), and whole kinds we do not
  watch (Dyson/Tineco vacuums, washers, Kindle). Since 27 Oct the adapter crawls it as the `deals:<node>`
  slice kind (one request per group per visit), added next to each priority group's roots. The "Flash deals" bubble is a landing page
  (`/l/52295425031`, JavaScript, nothing in the HTML); "Last Chance deals" is a node (`206767151031`)
  that renders as a normal search listing, so it could be a `category:` root.
- **Node sweeps miss listings (27 Oct 2026, to verify).** Page 1 of the TVs node at 70,000 EGP and up
  (`p_36:7000000-`, "96 results") held 24 TVs; 8 of them (B0F8BHR91D, B0GWJX7BV6, B0H4YXGC5K,
  B0GWK4STMH, B0D4727X69, B0FR3FLZ8V, B0FRG2L16Z, B0GWJVS585) had never been recorded, although the
  group's `price:3000-` sweep (372 listings, 16 pages) had finished minutes earlier with the same
  cookies and IP. A crawl of the same node in five price bands (3,000 to 10,000, ..., 100,000+) found
  374 listings against the sweep's 372, but the two sets differ: 26 band listings were not in the database
  and 7 database products were not in the bands. So every Amazon search query omits a different few
  percent of a node, consistently for that query; narrower bands alone do not fix it (and 3 of the 8,
  including the Samsung QN70H, were in neither). The `deals:<node>` lane is the cheap complement: it
  surfaces the missed products when they are on a deal, which is when they matter.

## noon.com (Egypt) category paths

Root for the `categories` command: `electronics-and-mobiles`, `home-and-kitchen`.

| Group | Path |
| --- | --- |
| TVs | electronics-and-mobiles/television-and-video/televisions |
| Refrigerators | home-and-kitchen/home-appliances-31235/large-appliances/refrigerators-and-freezers |
| Air conditioners | home-and-kitchen/home-appliances-31235/large-appliances/heating-cooling-and-air-quality/air-conditioners |
| Phones | electronics-and-mobiles/mobiles-and-accessories/mobiles-20905/smartphones |
| Tablets | electronics-and-mobiles/tablets-and-accessories/tablets (216 products; accessories are a sibling) |
| Gaming consoles | electronics-and-mobiles/video-games-10181/gaming-console (821 products); siblings: gaming-accessories, games-34004, gaming-controllers |
| Laptops / Desktops | electronics-and-mobiles/computers-and-accessories/computers-new/laptops, .../desktops |
| Monitors | electronics-and-mobiles/computers-and-accessories/computer-accessories/monitor-accessories/monitors-17248 |
| PC components | electronics-and-mobiles/computers-and-accessories/computer-accessories/computer-components-15997 |
| Headphones | electronics-and-mobiles/portable-audio-and-video/headphones-24056 |
| Small appliances | home-and-kitchen/home-appliances-31235/small-appliances (16,349 products; 28 children, e.g. fryers, coffee-makers, blenders-appliance, mixers-18509, food-processors, ovens-and-toasters, electric-cookers, kettles, irons-and-steamers...) |
| Ranges / cooktops / dishwashers | home-and-kitchen/home-appliances-31235/large-appliances/ranges, .../cooktops, .../dishwashers |

Facts that shaped the crawler:

- The catalogue JSON API returns 50 per page, at most 50 pages (2,500 products per query).
- The same item is listed once per seller at very different prices (1,613 items with several sellers,
  26% with a 30%+ spread), so products are keyed `SKU@seller` and a drop is only a deal if it is also the
  cheapest offer.
- The "lowest price in 30 days" badge sits on 19% of the catalogue; "lowest in 365 days" on 8%. Only the
  latter counts as evidence.
- Every crossed-out price is reported as kind `list` (noon does not say whether it is a past price or an
  RRP), so claims of 60%+ are rejected for new listings.
- Cloud IPs get HTTP 403 from Akamai; only home/office connections can crawl noon.
- **Price-drop feed (fast lane, 27 Oct 2026).** The catalogue API's facets include `min_offer_price`
  ("Price drop": `365_days`, `30_days`, `7_days` = lowest price in that window) and `deal_tag`
  (`deals-generic`, `mega-deal`; both had 0 products in phones when checked). Written into the category id
  after a `?`, `category:<path>?f[min_offer_price]=7_days`, the adapter passes it through as a query
  parameter: phones gave 200 of 1,071 products, 4 pages instead of 22. Unknown parameters are silently
  ignored (`f[is_deal]`, `f[has_discount]` returned the whole category), so check `nbHits` when trying a new one.

## jumia.com.eg category slugs

Full analysis (access, traps, signals, sizes): [JUMIA.md](JUMIA.md). Ids are the URL slugs; roots for the
`categories` command: `electronics`, `phones-tablets`, `computing`, `home-office`, `video-games`.

| Group | Slugs |
| --- | --- |
| TVs | televisions |
| Refrigerators | appliances-fridges-freezers, freezers (under Tools & Home Improvement › Appliances `home-improvement-appliances`, not under "Appliances") |
| Air conditioners | air-conditioning |
| Gaming consoles | playstation-5-consoles, playstation4-consoles, nintendo-switch-consoles + searches "playstation 5", "xbox series", "nintendo switch" (no Xbox Series node) |
| Phones / Tablets | smartphones / tablets |
| Laptops / Desktops / Monitors / PC components | laptops / desktop-computers / monitors / computer-components |
| Headphones | electronics-headphone, mobile-phone-bluetooth-headsets (earbuds such as AirPods are filed there) |
| Kitchen machines | air-fryers, deep-fryers, coffee-makers, espresso-machines, espresso-machine-coffeemaker-combos, blenders, mixers, food-processors, electric-pressure-cookers, slow-cooker, rice-cooker, small-appliances-microwave, ovens-toasters |
| Kitchen appliances | the other children of kitchen-dinning-small-appliances, coffee-tea-espresso-appliances (kettles), coffee-machine-grinder-accessories, small-appliances-ironing-laundry, water-coolers-filters |
| Cooking & dishwashers | cooking-appliances, ranges, wall-ovens, dishwashers |

Facts that shaped the crawler:

- Cloudflare refuses Chrome/Firefox TLS fingerprints and every datacenter IP; Safari's fingerprint passes
  from home. Home workers only.
- 40 products per page, at most 50 pages: 2,000 per query.
- An empty price range or a page past the end redirects to the parent category (HTTP 200 with other
  products); the adapter detects it from the served URL.
- Categories hold products that are in none of their children (air conditioners: 135 of 1,274), so Jumia
  is split by price only. The same turned out true on Amazon and noon (below), so every shop is now.
- 88% of cards show a seller-set crossed-out price and 45% say "3 or fewer left": neither is evidence.
- `category:flash-sales` crawls the flash-sale page (409 products, 86 above 1,500 EGP, all categories); the
  price filter works on it and its listings get the `limited_deal` badge. It is its own watch group.
- **Category pages alternate between a flash price and the regular price** on consecutive loads from the
  same machine (Alaska 377 L freezer: 27,500, 17,999, 27,500, 17,999 at 10-minute crawls, 30 products in
  a day), while a search for the product shows the flash price every time. Details in [JUMIA.md](JUMIA.md).

## cairosales.com category slugs

Full analysis: [CAIROSALES.md](CAIROSALES.md). Ids are the URL paths under `/en/`; roots for the
`categories` command: `tvs-screens`, `air-conditioners`, `refrigerators`, `mobiles-tablets`, `small-appliances`,
`computer-accessories`, `gaming-accessories`, `home-theaters-speakers` (the full tree is at `/en/sitemap`).

| Group | Slugs |
| --- | --- |
| TVs | tvs-screens |
| Refrigerators | refrigerators, freezers |
| Air conditioners | air-conditioners |
| Gaming consoles | gaming-accessories/game-consoles + searches "playstation 5", "xbox series", "nintendo switch" |
| Phones / Tablets | mobiles-tablets/mobiles / mobiles-tablets/tablets |
| Laptops / Desktops | computer-accessories/laptops-desktop-computers / computer-accessories/desktop-computers |
| Monitors | computer-accessories/computer-monitors, gaming-accessories/gaming-monitors |
| Headphones | home-theaters-speakers/headphones, gaming-accessories/gaming-headphones |
| Kitchen machines | small-appliances/fryers, coffee-maker, blenders, hand-stand-mixers, food-processors, kitchen-machines, chopper, ovens-toasters, cookers; microwaves |
| Kitchen appliances | small-appliances/kettle, juicer, grills, meat-grinders, sandwich-maker, iron; water-dispenser/water-dispenser, water-dispenser/filters |
| Cooking & dishwashers | cookers-ovens, dishwashers, built-in-kitchen-products/built-in-ovens, built-in-kitchen-products/built-in-hobs |

Facts that shaped the crawler:

- PrestaShop behind Cloudflare: Chrome's fingerprint passes from home, every datacenter IP is refused.
- 60 products per page (`?n=60`), no page cap, exact counts; pages are fetched sorted by price because the
  default order is shuffled with a daily random seed.
- Out-of-stock products stay listed without a price and are sorted last; most phones, tablets, laptops and
  consoles on the shop are such dead listings. A page with no price at all ends the crawl.
- An unknown slug redirects to the homepage (HTTP 200); the adapter checks the served path.
- Every in-stock card shows a crossed-out price set by the shop (treated like an RRP); no other signals.

## 2b.com.eg category ids

Full analysis: [2B.md](2B.md). Ids are Magento's numeric category ids (readable paths in the API differ
from the site's menu); roots for the `categories` command: Computer `118`, Mobile & Tablet `266`, Home
Appliances `2370`, Televisions `1932`, Audio `2382`, Gaming `120`, Electronics `610`.

| Group | Ids |
| --- | --- |
| TVs | 131 |
| Refrigerators | 260 (refrigerators), 1905 (deep freezers) |
| Air conditioners | 262 |
| Gaming consoles | 162 (PlayStation 5) + search "playstation 5" |
| Phones / Tablets | 119 / 122 |
| Laptops / Desktops / Monitors / PC components | 273 / 159 / 2381 / 1826 |
| Headphones | 592 |
| Kitchen machines | 1973 air fryers, 1972 coffee makers, 1967 blenders, 2395 hand blenders, 1969 mixers, 1970 food processors, 313 microwaves & ovens |
| Kitchen appliances | 1971 kettles, 1964 sandwich makers & grills, 326 irons, 324 water dispensers, 284 water filters |
| Cooking & dishwashers | 2416 cookers, 314 dishwashers, 611 built-in |

Facts that shaped the crawler:

- The Magento GraphQL API (`/graphql`, `Store: en`) gives exact counts, an inclusive price filter on the
  final price, and 100 products per request; Cloudflare lets Chrome's fingerprint through from home only.
- Only in-stock products are returned; sold-out products vanish until they are back.
- "Only N left" sits on 57% of products, so it is not evidence (`LOW_STOCK_EVERYWHERE`); every product
  shows a regular price above its final price (claims of 25 to 30% are the norm), treated like an RRP.
- An unknown category id returns 0 products with no error, so the adapter looks the category up separately.

## btech.com category slugs

The URL path under `/en/c/` (`tvs-projectors/televisions`); the API only uses the last segment and every slug
is unique across the 330-slug tree, so `category:televisions` works too. `deals categories btech_eg <slug>`
lists children with counts; the 12 departments are tvs-projectors 502, large-home-appliances 1,987,
small-home-appliances 4,009, mobiles-tablets 2,593, laptop-pc 2,242, audio-recording 428, gaming-area 306,
electronics 1,093, wearables 339, personal-care 1,057, non-electronics 5, tech-care 4. The watch-group table
with every slug used is in [BTECH.md](BTECH.md). Quirks: only in-stock offers are listed; half the offers are
third-party sellers with their own skus; pre-owned laptops and consoles sit next to new ones (the consoles
group excludes "pre-owned" titles; `pre-owned-laptops` is a separate category that is not tracked); an
unknown slug answers 0 products, which the adapter catches by checking the tree.

## dream2000.com collection handles

Shopify collection handles (`mobiles`, `refrigerator-and-deep-freezer`): flat, no tree. All 386 with counts:
`https://dream2000.com/en/collections.json?limit=250&page=1` and `page=2`. The general collections used per
group are in [DREAM2000.md](DREAM2000.md); next to them the shop keeps one collection per brand
("samsung-tvs", "lg-tvs") and marketing ones ("48-hours-offers", "last-piece", "back-to-school"), all
subsets. Quirks: no price filter and no search (the coordinator applies the minimum price); about half the
catalogue is out of stock but keeps its price; several handles are misspelt (`kattle`, `bluethooth-headphones`,
`nividia-rtx50`) and two overlap (`air-conditioner` 71 and `air-conditioners` 61); products are keyed by Shopify
id plus the non-colour variant values (`8255092588683:8gb-256gb`).

## Parent categories hold products their children don't (all shops)

Measured 26 October 2026 by comparing a category's count with the sum of its children's counts (same price
floor): amazon.eg Headphones misses 29 of 688 above 5,000 EGP in its children; noon PC components misses
1,660 of 4,356 (monitor arms, stands, UPS units, plus about 43 RAM sticks, graphics cards and motherboards);
Jumia misses 4 to 11%. The planner therefore never splits by sub-category. noon's PC components parent
brings in the accessories, so that group has `exclude_words` (arms, stands, mounts, UPS, DVD drives...),
checked against every tracked title so no real part is excluded ("bracket" was dropped from the list
because it matched an RTX 4080 and PC cases).

## Operational findings worth remembering

- A push to `main` deploys through GitHub Actions about a minute later; running `make deploy` by hand at
  the same time makes the two overlap and the older commit can land last. Use one or the other.
- A deploy restart used to hang up to 90 s when a browser tab held a connection (uvicorn waits for
  connections to close, then systemd kills it); graceful shutdown is now capped at 10 s.
- Changing `worker.env` on the Mac (e.g. adding a shop to `DEALS_SITES`) needs `make install-mac-worker`:
  the launchd agent copies the environment at install time, so a plain restart keeps the old sites.
- Reboots are fine: workers log "coordinator unreachable" at INFO, reconnect by themselves, and leases
  expire so no job is lost (measured: 2 minutes of downtime, all three devices back within 10 s).
- The 1 GB droplet runs the coordinator at about 65 MB and its worker at 50 MB; the UI's heaviest call is
  the Overview (all health checks, ~0.35 s). Viewer endpoints run on their own read-only connections.
- `make health` can show transient problems for a minute right after a restart; run it again before
  acting.
- Amazon capacity (26 Oct, 3 devices): about 650 pages per full pass of 15,600 products, roughly 5 s per
  page per device including politeness delays, so a full pass is about 20 minutes of three devices. With
  four 10-minute groups plus the rest, the Amazon queue regularly has about 20 slices due; the lease order
  was changed to relative lateness so the 10-minute groups are not starved by big slow groups.
