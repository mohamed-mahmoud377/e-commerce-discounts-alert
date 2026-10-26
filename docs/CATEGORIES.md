# Category catalogue and findings

What we learned about the two shops' category trees while building the watch list, so nobody has to
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
| Video Games department | 18022560031 | Platform nodes: PlayStation 5 21868828031, Xbox Series X&S 21868824031, Nintendo Switch 21868826031, Switch 2 206233785031, Handheld systems 92555823031, PlayStation 4 21868821031, Xbox One 21868820031, PC 21868814031. **Don't watch a platform node**: it mixes consoles, games and accessories, and because the planner splits big nodes by sub-category it recurses into the Games / Downloadable Content children (tried 26 Oct: 470 controller covers landed in the consoles group). Only Nintendo has console-only children: Switch > Consoles 21868896031 (222 items), Switch 2 > Consoles 206233788031. PlayStation and Xbox have no Consoles child in the sidebar, so those use `search:playstation 5 console` etc. with `min_price` 8,000. Even then the searches and the Nintendo Consoles node return racing wheels, headsets and Pro controllers above 8,000 EGP (160 of the first 160 recorded items were mostly accessories), hence the `require_words` / `exclude_words` title filters on that group. Sponsored cards ignore `p_36`, so the group minimum is also enforced when recording |
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
- **Decoy pages.** A session Amazon distrusts (seen on 26 Oct after a burst of test fetches from the Mac,
  which shares the worker's IP) gets a plausible-looking listing of random groceries (milk, tissues, rice;
  48 cards, no result-count bar, "over N results" missing) instead of the requested page. Counting it
  made the planner split a node forever ("too many to count" down to 256,000 EGP+ bands) and crawling it
  would record junk. The parser now flags a page with cards but no result bar (`PageResult.decoy`) and
  the site raises `Blocked`, which pauses that IP on Amazon like a challenge. Lesson: don't run ad-hoc
  Amazon fetches from a machine that runs a worker.

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

## Operational findings worth remembering

- A push to `main` deploys through GitHub Actions about a minute later; running `make deploy` by hand at
  the same time makes the two overlap and the older commit can land last. Use one or the other.
- A deploy restart used to hang up to 90 s when a browser tab held a connection (uvicorn waits for
  connections to close, then systemd kills it); graceful shutdown is now capped at 10 s.
- Reboots are fine: workers log "coordinator unreachable" at INFO, reconnect by themselves, and leases
  expire so no job is lost (measured: 2 minutes of downtime, all three devices back within 10 s).
- The 1 GB droplet runs the coordinator at about 65 MB and its worker at 50 MB; the UI's heaviest call is
  the Overview (all health checks, ~0.35 s). Viewer endpoints run on their own read-only connections.
- `make health` can show transient problems for a minute right after a restart; run it again before
  acting.
