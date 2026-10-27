# Deals price tracker: complete guide

Also kept as a shared doc with the same content. Companion docs: [MONITORING.md](MONITORING.md)
(health, devices, logs, runbook), [INTERNALS.md](INTERNALS.md) (code-level detail),
[REVIEW-2026-10-25.md](REVIEW-2026-10-25.md) (first review of the live system), [ROADMAP.md](ROADMAP.md),
[JUMIA.md](JUMIA.md) (the Jumia analysis), [PRODUCT_FLOW.md](PRODUCT_FLOW.md) (one product from first sighting to
alert, with worked examples).

## Overview

The system crawls whole categories on amazon.eg, noon.com (Egypt), jumia.com.eg, cairosales.com, 2b.com.eg, btech.com and dream2000.com, stores every product's price
history, and sends an Arabic Telegram alert when a price is genuinely low compared with that product's
own history. It tracks about 50,000 products on Amazon and noon plus about 14,000 on Jumia, in 14 groups: TVs, refrigerators, air conditioners, gaming consoles,
phones, tablets, laptops, desktops, monitors, PC components, headphones, kitchen machines (fryers, coffee
machines, blenders, microwaves), other kitchen appliances, and cooking and dishwashers.

**The principle that drives the design (stated by the user, 27 October 2026).** Flash sales and big drops on
these shops can be gone in minutes, so two things come before everything else: *notice a drop fast* and
*alert only when it is a real discount*. Everything below serves one of the two:

| Goal | What does it |
| --- | --- |
| Notice fast | A drop is judged the moment a worker posts the page (no batch); the alert loop runs every 3 s; the slice queue takes the most-late slice relative to its own interval; busy slices speed up to 10 min; priority groups have a hard cap on how slow a quiet slice may get (`max_interval_minutes`, 15 min for phones, tablets, ACs and consoles); and each shop's own *deals feed* is crawled as a cheap fast lane (Amazon's "all discounts" filter per category, noon's "lowest price in 7 days" feed per category, Jumia's `/flash-sales/` page) |
| Only real discounts | The reference is the product's own time-weighted history, never the shop's crossed-out price; the current price never counts towards its own reference; out-of-stock time is ignored; a price the product sits at regularly is not a deal (rarity rule); noon offers are keyed per seller and a drop must also be the cheapest offer; the shop's claim is only used for listings we have not tracked yet, and then needs evidence |

Measured on the first two days (25 to 27 October 2026): a qualifying drop seen on a page becomes a Telegram
message 3 seconds later (noon Crusher 540 headphones, 18,599 to 12,648, seen 01:20:56 UTC, delivered 01:20:59).
The waiting time is therefore the slice's revisit interval, which the caps and fast lanes keep short.

It has four parts. One coordinator owns the data and decides what to do; any number of workers do the
crawling from their own internet connections.

| Part | Runs on | What it does |
| --- | --- | --- |
| Coordinator (`deals serve`) | Droplet 207.154.206.127 | Owns the SQLite database, hands out crawl jobs, stores results, detects deals, runs the Telegram bot, watches for problems, takes daily backups |
| Worker (`deals worker`) | Your Mac, the droplet, any friend's PC | Asks for a job, downloads the shop pages, sends the products back. Holds no database |
| Site adapters (`sites/amazon_eg.py`, `sites/noon_eg.py`, `sites/jumia_eg.py`, `sites/cairosales_eg.py`, `sites/twob_eg.py`, `sites/btech_eg.py`, `sites/dream2000_eg.py`) | Inside each worker | Know how to fetch and read one listing page of each shop |
| Telegram bot (@Jerrys_deals_bot) | Inside the coordinator | Sends deal alerts to approved subscribers and system warnings to you |

Workers only make outgoing requests to the coordinator, so nothing needs to be opened on your Mac or on a
friend's router. Your Mac does the real crawling because the shops refuse most datacenter traffic (noon,
Jumia, Cairo Sales, 2B and B.TECH's API always); the droplet's worker is a fallback for Amazon and also crawls Dream 2000, which is open to cloud IPs.

```mermaid
flowchart TB
    shops["amazon.eg, noon.com, jumia.com.eg,<br/>cairosales.com, 2b.com.eg, btech.com, dream2000.com<br/>category listing pages and APIs"]
    shops --> mac["Your Mac (launchd)<br/>all five shops<br/>does most of the crawling"]
    shops --> dw["Droplet worker<br/>amazon only<br/>fallback: often refused"]
    shops --> fr["Friend's PC (optional)<br/>any shop it can reach"]
    mac -- "lease a job, post each page" --> api
    dw --> api
    fr --> api
    subgraph coord["Coordinator on the droplet (deals serve)"]
        api["API :8787<br/>hands out jobs, stores pages"] --> dbx["SQLite database<br/>products, history, jobs"] --> rules["Deal rules<br/>current price vs own history"]
        rules --> outbox["Alert outbox<br/>per-person delivery, retries"]
        wd["Watchdog<br/>stalls, backups, report"] --> outbox --> bot["Telegram bot<br/>alerts out, approvals in"]
    end
    bot --> admin["You (admin)<br/>deals, warnings, report"]
    bot --> friends["Approved friends<br/>deal alerts only"]
```

Pages flow down from the shops through the workers; only the coordinator writes the database and talks to
Telegram.

## Connecting to the server

You log in with `ssh root@207.154.206.127` from your Mac, using the key `~/.ssh/id_ed25519` (fingerprint
`a8:65:ea:af:...:7f:62`, named "jerry" in DigitalOcean).

1. Load the key once per Mac login, because it has a passphrase: `ssh-add --apple-use-keychain ~/.ssh/id_ed25519`.
   With `--apple-use-keychain` macOS remembers the passphrase after reboots.
2. Log in: `ssh root@207.154.206.127`. If it says "Permission denied (publickey)", step 1 was skipped.
3. Leave with `exit`.

What lives where on the server:

| Path | What it is |
| --- | --- |
| `/opt/deals` | The code (copied there by `make deploy`) and its Python environment `.venv` |
| `/etc/deals/config.toml` | Settings: watched categories, alert rules, worker tokens, Telegram token. Never commit it |
| `/etc/deals/worker.env` | The droplet worker's token and sites |
| `/var/lib/deals/deals.db` | The database (the only real copy of all data) |
| `/var/lib/deals/backups/` | Daily compressed backups, last 7 kept |
| `/opt/ms-playwright` | Headless Chromium, used to pass Amazon's bot check |

Two systemd services run everything and restart automatically after a crash or reboot:

| Service | Command it runs |
| --- | --- |
| `deals-coordinator` | `deals serve` on port 8787: API, database, Telegram bot, watchdog |
| `deals-worker` | `deals worker` for amazon_eg only |

Useful commands once logged in:

- Status: `systemctl status deals-coordinator deals-worker`
- Live logs: `journalctl -u deals-coordinator -u deals-worker -f` (or `make logs` from your Mac)
- Restart after editing the config: `systemctl restart deals-coordinator`
- Run any `deals` command as the service user:
  `runuser -u deals -- /opt/deals/.venv/bin/deals status --config /etc/deals/config.toml`

The firewall (ufw) only allows SSH and port 8787. Port 8787 accepts workers that present a token from
`[workers]` in the config.

## Connecting to the database

The only real database is the SQLite file `/var/lib/deals/deals.db` on the server; your Mac has no
database of its own. SQLite is a file, not a network server, so IntelliJ cannot connect to it live. Use
one of three ways, all run from the project folder on your Mac:

| Command | What you get | Live? |
| --- | --- | --- |
| `make dbweb` | The database in your browser at http://localhost:8001 (Datasette): tables, filters, a SQL box, and the ready-made queries as clickable pages. Runs through an SSH tunnel, nothing exposed. Ctrl+C stops it | Yes, read-only |
| `make sql` | A SQL prompt on the server: `.tables`, `.schema products`, any `SELECT ...;`, `.quit` to leave | Yes, read-only (writes are refused) |
| `make db` | Downloads a consistent copy to `data/server.db` for IntelliJ | No, a snapshot; run again to refresh |

Setting up IntelliJ (one time, after `make db`):

1. View, Tool Windows, Database.
2. Click +, Data Source, SQLite.
3. Name it "deals (server)" and pick the file `data/server.db` in the project folder. Click "Download
   missing driver files" if offered.
4. Test Connection, then OK.
5. To refresh later: run `make db`, then click Refresh in the Database window (Disconnect first if old
   data still shows).

The tables (full column list in [INTERNALS.md](INTERNALS.md)):

| Table | One row per | Main columns |
| --- | --- | --- |
| `products` | Product (noon: product and seller) | site, sku, title, url, price, list_price, list_price_kind, in_stock, stock_left, badges, rating, reviews, seller, grp, first_seen_at, last_seen_at |
| `price_history` | Price or stock change | product_id, price, list_price, in_stock, observed_at |
| `jobs` | Category root to plan, or slice to crawl | kind, site, target, grp, interval_s, next_due_at, lease_*, failures, last_error, last_done_at |
| `alerts` | Deal alert or system message | product_id, kind, price, reference_price, drop_pct, score, message, created_at |
| `deliveries` | Alert sent to one person | alert_id, chat_id, sent_at, attempts, error |
| `subscribers` | Telegram user | chat_id, name, status, is_admin, is_manager (helper admin), approved_at |
| `workers` | Crawling machine | name, last_seen_at, last_ip, sites, pages, jobs_done |
| `worker_sites` | Machine and shop | last_ok_at, last_error_at, last_error, pages, products, blocks |
| `blocks` | Shop that blocked an IP | site, ip, strikes, until, reason |
| `events` | Log line from a device or the coordinator | at, source, level, message |

Three conventions to remember when reading the data:

- Prices are whole numbers in piastres: divide by 100 for EGP (`1999900` = 19,999 EGP).
- Times are UTC text such as `2026-10-25T21:05:00+00:00`. Cairo is UTC+3 until 29 October 2026, then UTC+2.
- noon skus look like `N70091992V@startup`: the product code plus the seller.

Ready-made queries live in `docs/queries.sql` (open it in IntelliJ, put the cursor in a query, Cmd+Enter):
overview, products per group, what is crawling now, latest price changes, biggest drops, alerts, scarce
products on a deal, one product's full history, find a product by name.

## Running the code

Everything is driven by `make` from the project folder `~/IdeaProjects/e-commerce-discounts-alert` on your
Mac. The production system already runs; these are the commands to check it, change it and ship changes.

| Command | What it does |
| --- | --- |
| `make health` | Checks everything (server services and logs, deployed code, Mac worker, database, crawling per site, Telegram, disk, backups) and prints a pass, warning or failure line for each |
| `make devices` / `make dashboard` | Every device and its fetch state per shop (terminal / browser) |
| `make events` | Live logs from all devices and the server |
| `make status` | Workers, blocked IPs, backlog per site, freshness per group |
| `make alerts` / `make movers` | Deal alerts and biggest price moves of the last 24 hours |
| `make report` | Sends the daily report to your Telegram now (it also arrives at 09:00 Cairo time) |
| `make logs` | Follows the server's systemd logs live |
| `make test` | Runs the automated tests |
| `make deploy` | Tests, copies the code to the server, installs, restarts both services, checks they answer |
| `make telegram TOKEN=...` | Connects a Telegram bot (already done) |

First-time setup on a new Mac:

1. `python3 -m venv .venv` then `.venv/bin/pip install -e ".[dev]"`.
2. `.venv/bin/python -m playwright install chromium` (needed to pass Amazon's bot check).
3. `make mac-worker-env` writes `worker.env` with this Mac's token from the server.
4. `make install-mac-worker` runs the worker in the background: it starts at login and restarts if it
   crashes. Its log is `~/Library/Logs/deals-worker.log`; `make uninstall-mac-worker` removes it.

Shipping a change: edit the code, run `make test`, then `make deploy`, then `make health`. If you changed
worker code, also restart the Mac worker: `launchctl kickstart -k gui/$(id -u)/com.deals.worker`.

Changing what is tracked: edit `[[watch]]` groups in `/etc/deals/config.toml` on the server, then
`systemctl restart deals-coordinator`. New groups are planned within a minute; removed ones are dropped.
A group can use `search:<words>` roots and, for noisy sources, `require_words` / `exclude_words` title
filters (see the gaming consoles group in `config.example.toml`); products already recorded stay, so
delete them by hand if a source turned out to be junk.
Category ids come from `deals categories amazon_eg 18018102031` or `deals categories noon_eg electronics-and-mobiles`.

Adding a friend's computer as a worker (more crawling capacity from another connection):

1. On the server, create a token with `deals token`, add it under `[workers]` as `ahmed = "..."`, restart
   the coordinator.
2. On their computer: clone the repo, do setup steps 1 and 2, then run
   `DEALS_COORDINATOR=http://207.154.206.127:8787 DEALS_TOKEN=... DEALS_SITES=noon_eg,amazon_eg,jumia_eg,cairosales_eg,2b_eg,btech_eg,dream2000_eg .venv/bin/deals worker`.

Running everything on one machine without the server (for experiments): `cp config.example.toml config.toml`,
then `.venv/bin/deals local` starts a coordinator and a worker in one process with a local database.

## Algorithm 1: fetching the shops

Each shop has an adapter with one job: given a slice (a category, optionally with a price range) and a page
number, return the products on that page, the total count, and the sub-categories (used by `deals categories`). No adapter opens
product pages; everything comes from listing pages, which hold 24 to 50 products per request.

**amazon.eg.** Amazon sits behind an AWS WAF JavaScript challenge, so a plain request gets a 202 or 503
"automated access" page.

1. The first time, the worker opens headless Chromium on the Amazon homepage and waits until the challenge
   page disappears (up to 30 seconds).
2. It saves the browser's cookies to `data/amazon_eg_cookies.json`.
3. Every page after that is a plain HTTP request with those cookies, sent by `curl_cffi`, which copies
   Chrome's network fingerprint. That takes about 1 second per page.
4. If a response is the challenge again, it solves the challenge once more. If that also fails, the worker
   reports the job as blocked.

Category pages are fetched as `/-/en/s?rh=n:<category id>,p_36:<min>-<max>`. The `/-/en/` part forces
English titles, and `p_36` is a price range in piastres. A price filter is always sent, even `0-`, because a
bare category page uses a "landing" layout with no product grid.

**noon.com.** noon's web pages have a bot challenge too, but the JSON API its own website uses
(`/_svc/catalog/api/v3/u/egypt-en/<category path>/?page=N&f[price][min]=...`) answers plain requests with
50 structured products each time. An unknown category path silently returns a different category, so the
adapter checks the breadcrumb and fails loudly on a typo.

**jumia.com.eg.** Jumia sits behind a Cloudflare challenge that refuses Chrome's and Firefox's network
fingerprints but lets Safari's through from a home connection, so the adapter sends plain requests that copy
Safari (no browser needed). Category pages (`/<slug>/?price=<min>-<max>&page=N`) embed their 40 products
as JSON, with an exact "N products found" count. Two traps shape the adapter: an empty price range (or a page
past the end) silently redirects to the *parent* category, so every page's served URL is compared with the
one asked for; and a category holds products that are in none of its children, so Jumia is split by price
only (now true of every shop, see Algorithm 2). Details, numbers and the category map: [JUMIA.md](JUMIA.md).

**cairosales.com.** Cairo Sales Stores runs PrestaShop behind Cloudflare; Chrome's fingerprint passes from
home. Category pages (`/en/<slug>/?n=60&p=N&price=<min>-<max>`) carry an exact count ("Showing 1 - 60 of
820 items") and 60 product cards each; they are fetched sorted by price because the default order is
shuffled daily. Out-of-stock products stay listed without a price (recorded as out of stock), and an unknown
slug redirects to the homepage, which the adapter turns into a loud failure. Details: [CAIROSALES.md](CAIROSALES.md).

**2b.com.eg.** 2B runs Magento 2, and its public GraphQL API (`/graphql`) answers with exact counts, an
inclusive price filter and 100 structured products per request, so no HTML is parsed at all. Categories are
keyed by Magento's numeric id (`category:131` = TVs). Only in-stock products are returned, so a sold-out
product disappears from its category until it is back. Details: [2B.md](2B.md).

**btech.com.** B.TECH's Next.js storefront renders 20 products and loads the rest from the shop's own product
search API (`retail-online-prod.btech.com`, 100 products a request, exact counts, an inclusive price filter),
which needs the guest token from the site's session cookie and refuses datacenter IPs. It is a marketplace:
half the offers are third-party sellers, each offer with its own sku. Only in-stock offers are listed.
Details: [BTECH.md](BTECH.md).

**dream2000.com.** Dream 2000 is a Shopify store, and Shopify's public collection JSON (250 products a
request, exact counts, no price filter) is readable from anywhere, the droplet included. Collections are small,
so a slice is a whole collection and the coordinator applies the group minimum. Variants that differ in more
than colour (storage sizes) are separate listings. Details: [DREAM2000.md](DREAM2000.md).

**Which machine can reach which shop** (tested 25 October 2026):

| Shop | Home IP (your Mac) | Droplet (DigitalOcean Frankfurt) |
| --- | --- | --- |
| amazon.eg | Works | Varies: category pages were refused about 5 times in 6 in one test, fine in others |
| noon.com | Works | Always refused (Akamai 403) |
| jumia.com.eg | Works with Safari's fingerprint (tested 26 October) | Always refused (Cloudflare 403, every fingerprint) |
| cairosales.com | Works with Chrome's fingerprint (tested 27 October) | Always refused (Cloudflare 403) |
| btech.com | Works, plain curl too (tested 27 October) | Pages load, the API host times out (firewalled) |
| dream2000.com | Works (tested 27 October) | Works (Shopify) |
| 2b.com.eg | Works with Chrome's fingerprint, HTML and GraphQL (tested 27 October) | Always refused (Cloudflare challenge) |

**What is read from each product card** (no extra requests):

| Signal | amazon.eg | noon | Jumia | Cairo Sales | 2B | B.TECH | Dream 2000 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Price | The featured offer's price | The offer's sale price, else its price | The product's price | The price (hidden when out of stock) | `final_price` | `final_price` (coupons included) | The cheapest available variant of the group |
| Crossed-out price | "Was:" (Amazon's own recent price, trusted) or "List:" (brand RRP, often inflated) | Seller-set, treated like an RRP | Seller-set (on 88% of cards), treated like an RRP | The shop's "regular" price (every card), treated like an RRP | `regular_price` (every product), treated like an RRP | `base_price`: B.TECH's own offers (a promotion on a fifth of them) count as "was", third-party sellers' as an RRP | `compare_at_price` (83% of the catalogue), treated like an RRP |
| Stock left | "Only 3 left in stock" | "Only 3 left" | "1 item left" (45% of cards say 3 or fewer, so not evidence) | none | `only_x_left_in_stock` (57% of products, so not evidence) | none | none (a "Last Piece" collection exists, not used) |
| Deal badge | "Limited time deal" | "Deal", "Mega Deal" | none on category pages (flash sales are only marked on `/flash-sales/`) | none | none | none | none |
| Shop's own price history | none | "Lowest price in 30 days", "in a year" | none | none | none | `price_drop_amount` (not used yet) | none |
| Demand | "300+ bought in past month" | "200+ sold recently", "Selling out fast", bestseller | none | none | none | `is_best_seller` (half the TVs) | none |
| Rating | Stars and review count | Stars and review count | Stars and review count | none | `rating_summary`, `review_count` | none | none |
| Seller | not shown | Store name | not shown (one seller per SKU) | the shop | the shop | `seller_name` (half the offers are third-party, each its own sku) | the shop |

**noon sellers.** One noon product can be sold by several sellers at very different prices. The same
earbuds were seen at 7,550, 1,000 and 495 EGP, and which offer a listing shows depends on the price filter.
If they shared one history, the system would see fake price drops. So noon items are tracked per seller
offer, as `SKU@seller`, each with its own clean history. Amazon cards always show the featured offer, so
Amazon items are tracked per product (ASIN).

## Algorithm 2: the planner (covering a whole category)

Shops stop paging long before the end of a big category, so the planner splits each category into slices
small enough to read completely. A slice is a category, optionally narrowed to a price range, written like
`category:21832982031|price:3000-5000`.

| Shop | Products per page | Limit the planner must stay under |
| --- | --- | --- |
| amazon.eg | 24 | 1,000 per slice (Amazon shows an exact count only up to 1,000, "over 1,000" above) |
| noon | 50 | 2,500 per slice (50 pages of 50) |
| Jumia | 40 | 2,000 per slice (50 pages of 40); split by price only, see below |

It works through a queue, starting with the group's root category and its minimum price (TVs start at
3,000 EGP). For each slice it fetches page 1 and reads the product count:

```mermaid
flowchart TB
    q["Take the next slice<br/>(starts with root + minimum price)"] --> p["Fetch page 1, read the count<br/>limit: Amazon 1,000 · noon 2,500 · Jumia 2,000"]
    p --> d1{"Under the limit?"}
    d1 -- yes --> keep["Keep as a crawl slice"]
    d1 -- no --> d3{"Wider than 1 EGP?"}
    d3 -- yes --> price["Queue its price ranges<br/>(fixed bands first, then halves)"] --> q
    d3 -- no --> over["Mark oversized and warn"]
```

**Why never by sub-category.** Until 26 October 2026 the planner split an oversized category into its
sub-categories first. Measurements that day showed that on every shop a category holds products that sit
in none of its children, and a sub-category split silently never crawls them:

| Shop and category (group minimum price) | Products | In its children | Never crawled |
| --- | --- | --- | --- |
| amazon.eg Headphones, 5,000 EGP and up | 688 | 659 | 29 (4%), e.g. JBL Tour One M3, Bowers & Wilkins Px8, Dyson OnTrac |
| amazon.eg PC components / ranges & ovens / blenders, 20,000 EGP and up | 143 / 300 / 86 | 142 / 298 / 86 | 1 / 2 / 0 |
| noon PC components, 1,500 EGP and up | 4,356 | 2,696 | 1,660 (38%): mostly monitor arms, stands and UPS units, but also about 43 real parts (27 RAM sticks, 6 graphics cards, 4 motherboards) |
| Jumia air conditioners / TVs / smartphones | 1,274 / 454 / 2,041 | 1,139 / 434 / 2,015 | 135 / 20 / 26 |

Price ranges always cover everything, so every shop is now split by price only. Where a parent category also
collects accessories (noon's PC components: monitor arms, UPS units), the group's `exclude_words` keep them
out.

Price splits start with fixed bands (0, 50, 100, 200, 500, 1k, 2k, 5k, 10k, 20k, 50k, 100k EGP and above,
from the group's minimum price); any range still too big is halved, down to 1 EGP wide. Empty slices are
dropped.

Neighbouring ranges share their edge (1,000 to 2,000, then 2,000 to 5,000), so a price like 1,999.50 can
never fall between two slices. The few products sitting exactly on an edge are seen twice, which is harmless.

Every probed page is real data, so planning already fills the database. Each root is planned again every 7
days to pick up changed sizes; slices that survive keep their schedule.

Two checks show nothing is missed. On real data, noon Wearables (24,787 products) became 27 slices in 44
requests covering 26,179 products, slightly more than the total because of shared edges. An automated test
builds a fake shop with a known catalogue and confirms the slices cover every product.

On 26 October the roots produced 50 slices on Amazon, 55 on noon and 57 on Jumia (52 roots).

## Algorithm 3: scheduling, leases and politeness

The coordinator keeps a queue of jobs in the `jobs` table: one **plan** job per root (re-planned weekly)
and one **crawl** job per slice. Workers pull work; the coordinator never pushes.

The loop each worker runs, once per shop and in parallel (so a slow Amazon crawl never delays noon):

1. Ask for a job: `POST /api/lease` with the shops this worker handles and its token.
2. The coordinator picks the most overdue job the worker may take: plan jobs first, then the crawl job with
   the oldest due time. It hands back a lease token valid for 5 minutes.
3. Fetch page 1, 2, 3 and so on, waiting a random 2 to 4 seconds (Amazon) or 1 to 2 seconds (noon) between
   requests, and post each page's products to the coordinator. Each post extends the lease by 5 minutes.
4. On the last page, the coordinator marks the slice done and schedules its next visit.
5. Wait the same 1 to 4 seconds, then ask again. With nothing due, wait 5 to 60 seconds.

What the coordinator enforces when handing out jobs:

- **One job per shop per internet connection.** Two workers behind the same router never crawl the same
  shop at the same time, so a second laptop at home adds no load on Amazon from that IP.
- **Blocked connections cool down.** When a shop challenges a worker, the worker reports it and that
  shop-and-IP pair pauses for 15 minutes, then 30 minutes, 1 hour, 2 hours, up to 24 hours on repeat
  blocks. Other IPs carry on, and the first successful page clears the record.
- **Leases expire.** If a worker crashes, sleeps or loses Wi-Fi, its lease runs out after 5 minutes and the
  job goes to the next worker that asks. Late results from an expired lease are refused.

**Adaptive revisits.** Each slice starts at its group's `interval_minutes` (20 to 180 minutes). After every crawl:

- if any price changed or new products appeared, the interval halves, down to 10 minutes;
- if nothing changed, it grows by a quarter, up to 3 times the group's interval, or up to the group's
  `max_interval_minutes` when it sets one;
- the next visit gets plus or minus 10% random jitter so slices don't move in lockstep.

Busy slices (a sale, a new launch) are therefore watched every 10 to 20 minutes while quiet ones cost
almost nothing. **The catch:** a flash sale always *starts* in a quiet slice, so the time to notice one is the
slow end of the range, not the fast end. Measured on 27 October, every "10-minute" group was in fact sitting
at 30 minutes on every shop (nothing had changed for a while, so the slices had backed off to 3x). Since that
day the priority groups cap the backoff: 15 minutes for air conditioners, gaming consoles, phones and tablets,
30 for TVs, refrigerators, laptops and monitors. Later that day the two slowest groups got caps too: Cooking &
dishwashers 90 minutes (was 3 x 120 = 6 h) and Kitchen appliances 120 minutes (was 3 x 180 = 9 h), both
starting at 60. Kitchen machines, headphones, PC components and desktops keep the 3x rule.

**Fast lanes: the shops' own deal feeds.** A category of 1,000 products costs 42 Amazon pages to sweep, but
the same category filtered to "discounted" costs 1 or 2, and that filtered page is exactly where a flash
sale appears. So each priority group has, next to its category root, a *deal-feed root* that is planned and
crawled like any other slice (same group, same interval and cap, same filters):

| Shop | Root key | What it lists | Size (27 Oct) |
| --- | --- | --- | --- |
| amazon.eg | `category:<node>,p_n_deal_type:26901036031` (Amazon's "All discounts" refinement; `26901035031` is "Today's deals", much smaller) | products in the node with any discount | TVs 42, fridges 89, ACs 105, phones 37, tablets 6, laptops 7, monitors 17, headphones 131 |
| noon | `category:<path>?f[min_offer_price]=7_days` (noon's own "lowest price in 7 days" filter; `30_days` and `365_days` exist too; `deal_tag` = `deals-generic` / `mega-deal` was empty when checked) | products whose price just hit a 7-day low | phones 200 of 1,071 |
| Jumia | `category:flash-sales` (its own watch group, "Jumia flash sales", 10 to 15 min) | the flash-sale page, all categories; the adapter marks these listings `limited_deal`, so a new product with a 30%+ claimed discount can alert the first time it is seen | 409, 86 above 1,500 EGP |
| Cairo Sales, 2B | none found | | |

A product seen on a deal feed is the same product as on its category page (same key), so a drop seen there
is judged against the same history. The feeds are about 20 Amazon pages and 30 noon pages per sweep in
total, so they cost almost nothing.

**Capacity.** The tracked groups hold about 17,000 Amazon and 27,000 noon products: roughly 700 Amazon
pages and 540 noon pages for a full pass. Two workers crawl Amazon (Mac and droplet) and one crawls noon
(the Mac).

## How fast you hear about a price change

The time from a price changing on the shop to the Telegram message is three steps added together:

1. **Waiting for the next visit** to the slice holding that product: on average half the slice's current
   interval, at worst the whole interval.
2. **Crawling the slice:** 1 to 3 minutes (Amazon: 24 products a page, 2 to 4 s between pages; noon: 50
   a page, 1 to 2 s).
3. **Sending:** the alert loop runs every 3 seconds; measured delivery time is 11 s on average, 37 s at
   most (107 messages).

Measured on the live system on 27 October 2026, 06:30 UTC, *before* that day's caps (every priority group had
backed off to its 3x ceiling because nothing had changed for a while), with the range the caps now allow:

| Group | `interval_minutes` | Measured 27 Oct, before the caps | Allowed since the caps | Worst case now |
| --- | --- | --- | --- | --- |
| Air conditioners, Gaming consoles, Phones, Tablets | 10 | 15 to 30 min (Amazon, noon, Jumia, Cairo Sales, 2B alike) | 10 to 15 min | ~15 min + crawl |
| TVs, Refrigerators, Laptops, Monitors | 20 | 10 to 60 min | 10 to 30 min | ~30 min + crawl |
| Jumia flash sales (new) | 10 | | 10 to 15 min | ~15 min |
| Headphones | 60 | 10 to 180 min | unchanged (3x) | ~3 h |
| PC components, Desktops | 60 | 10 to 180 min | unchanged | ~3 h |
| Kitchen machines (fryers, coffee, blenders, microwaves) | 30 | 10 to 90 min | unchanged | ~1.5 h |
| Cooking & dishwashers | 60 (was 120) | 48 to 293 min | 10 to 90 min (was up to 360) | ~1.5 h (was ~6 h) |
| Other kitchen appliances (kettles, irons, toasters...) | 60 (was 180) | 16 to 540 min | 10 to 120 min (was up to 540) | ~2 h (was ~9 h) |

How long do big drops actually last? In the 24 hours before 27 October 06:30 UTC there were 133 drops of 20%
or more (Amazon 32, Jumia 41, noon 60). On noon none had reverted; on Amazon 6 of 32 reverted, 3 of them
within an hour; on Jumia 13 of 41 "reverted" within 14 minutes on average, but those are the shop's own
alternating cache, not real reversals (see [JUMIA.md](JUMIA.md)). So most drops last hours or days, and a
15-to-30-minute revisit catches nearly all of them; the fast lanes are for the rest.

Re-measure any time with `make sql`:

```sql
SELECT grp, count(*) slices, round(min(interval_s)/60.0) min_m, round(avg(interval_s)/60.0) avg_m,
       round(max(interval_s)/60.0) max_m, sum(last_count) products
FROM jobs WHERE kind = 'crawl' GROUP BY grp ORDER BY avg_m;
```

Things that add delay:

- **History first.** A full price-drop alert needs 3 days of history for that product (`min_history_days`);
  an early-drop alert needs 1 day (`early_min_history_days`);
  before that only "new listing" alerts are possible.
- **noon needs the Mac.** While the Mac sleeps or is offline, noon is not crawled at all; changes are picked
  up when it comes back (most overdue slices first). Amazon carries on from the droplet, less reliably.
- **Blocks.** A blocked shop-and-connection pair pauses 15 minutes to 24 hours.
- **Backlog.** If workers can't keep up, every slice runs late. `make health` warns when the most overdue
  slice is far behind; `make status` shows it per shop.

### Priority: which slice goes next

There is no separate priority setting. The coordinator hands out plan jobs first, then the crawl job that is
latest *relative to its own interval*: a 10-minute slice that is 6 minutes late (60%) goes before a 3-hour
slice that is 14 minutes late (8%). A group's priority is therefore its revisit interval, and when the queue
is behind (Amazon often has 20 slices due with 3 workers) the fast groups keep their pace while slow groups
absorb the delay. Each shop is crawled separately (one job per shop per connection), so Amazon and noon
never wait for each other.

### Changing how often a group is checked

On the server (`make ssh`), edit `/etc/deals/config.toml`:

```toml
[[watch]]
name = "TVs"
interval_minutes = 20     # starting revisit interval; slices adapt between the minimum and the cap
max_interval_minutes = 30 # the cap: a quiet slice is never left longer than this (default: 3x interval_minutes)
min_price = 3000          # EGP; cheaper items are neither crawled nor alerted
```

then `systemctl restart deals-coordinator` (or `make restart-server` from your computer). For catching
flash sales the cap is the number that matters: it is the longest a drop can sit unseen. The other knobs,
under `[schedule]`, apply to every group:

| Setting | Default | Effect |
| --- | --- | --- |
| `min_interval_minutes` | 10 | Fastest any slice is revisited, even while its prices keep moving |
| `max_interval_factor` | 3.0 | Quiet slices slow down to at most `interval_minutes` x this, unless the group sets `max_interval_minutes` |

What happens after the restart: the new limits apply at each slice's next crawl, so a slice already scheduled
far ahead keeps that one visit, then follows the new range. To make it take effect at once, mark the group
due now (`ssh root@207.154.206.127`, then `runuser -u deals -- sqlite3 /var/lib/deals/deals.db`):

```sql
UPDATE jobs SET next_due_at = strftime('%Y-%m-%dT%H:%M:%S+00:00','now'), interval_s = base_interval_s
WHERE kind = 'crawl' AND grp = 'TVs';
```

Adding a new group is the same edit with a new `[[watch]]` block (find category ids with
`deals categories amazon_eg <node>` or `deals categories noon_eg <path>`); it is planned within a minute
of the restart.

Trade-offs before making everything faster:

- **Faster is not free.** Every group shares the same workers. Halving one group's interval takes crawl
  time from the others, and more requests mean more blocks from Amazon (noon is more tolerant).
- **Rough budget.** A worker crawls about 1,000 pages an hour on noon and 600 on Amazon. A group's load
  per hour is about (its products / products per page) x (60 / its interval in minutes). Keep the total
  under what your workers can do, or every slice runs late.
- **To get faster for real,** add workers on other home connections (a friend's PC, a Raspberry Pi): each
  new connection adds a full share for both shops.
- **Narrowing helps too.** Raising `min_price` drops cheap items from the crawl, which frees time for the rest.

**Where the capacity actually is (measured 27 October 2026, 02:40 to 08:40 UTC, from the workers' "done"
events; three workers: the Mac, the droplet and a friend's Mac).** Each worker runs one loop per shop, and a
loop's request rate is fixed by the delay between pages, so a shorter interval never makes one connection hit
a shop faster: it only shortens the idle time between jobs. How much of each loop's time was spent crawling:

| Shop | Loops busy | Pages per hour (all workers) | Biggest costs |
| --- | --- | --- | --- |
| amazon.eg | 86 to 91% on all three, 20 slices overdue (worst 45 min) | about 1,900 | Kitchen machines 56 crawl-min/h, Headphones 21, Cooking & dishwashers 16, Kitchen appliances 14 |
| noon | 64 to 69% | about 1,300 | Kitchen machines 20, Headphones 16, Kitchen appliances 15 |
| Jumia | 51 to 63% | about 580 | Phones 17, Kitchen machines 13, Refrigerators 12 |
| Cairo Sales, 2B, B.TECH, Dream 2000 | 0 to 5% | 10 to 80 each | |

No shop had ever blocked any worker (the `blocks` table was empty, every `worker_sites.blocks` 0). The slow
end of the two kitchen groups was on the cheap shops (2B 293 to 352 min, Cairo Sales up to 352, B.TECH and
Dream 2000 150 to 225, Jumia up to 439), where a whole sweep of the group takes 0.1 to 7 minutes. On Amazon
their prices move often enough that the slices were already revisited every 24 to 30 minutes (median), so the
caps cost Amazon little. A full quiet sweep of both groups at the new caps costs about 9 crawl-min/h on Amazon,
3 on noon and 5 on Jumia, at most, against less than 0.5 on each small shop. **Amazon is the limit**: at 90% busy
it cannot take a broad speed-up of the heavy groups (Kitchen machines, Headphones) without every slow slice
running late; there the cheaper way is a deal-feed root or another home connection.

## Algorithm 4: price history and statistics

A product's history is stored as a step function: a new row is written only when its price, crossed-out
price or availability changes, and each price holds until the next row. Re-checking 40,000 products every
half hour therefore costs almost no storage; a change takes about 50 bytes.

When a product is new or its price moves, the coordinator turns its history into statistics
(`pricing.compute_stats`):

| Statistic | Meaning |
| --- | --- |
| typical (90 days) | Time-weighted median of the in-stock prices before the current one, over the last 90 days. The main reference for "is this cheap?" |
| typical (30 days) | The same over 30 days |
| share at or below | Fraction of the in-stock time (90 days, before the current price) the product spent at or below the current price. 0 = never this cheap before; 0.4 = this cheap 40% of the time |
| low / high | Lowest and highest earlier in-stock price ever seen |
| previous | The price just before the current one |
| history days | How many in-stock days of data the 90-day window holds |
| changes (90 days) | Number of price or stock changes in 90 days |

Three rules make these numbers hard to fool:

- **Time-weighted.** Each earlier price counts in proportion to how long it lasted. A price that held 60
  days counts 60 times more than one that lasted a day.
- **Out of stock is ignored.** Periods when the product could not be bought don't count, so a stale price
  shown while unavailable can't drag the typical price around.
- **The current price is never part of its own reference.** Otherwise a long sale would slowly lower its own
  typical price.

The time-weighted median: sort the earlier prices, give each a weight equal to how long it held inside the
window, and take the price where the running weight first reaches half of the total.

```latex
\text{typical} = \min\Big\{\, p_k : \sum_{i \le k} w_i \ \ge\ \tfrac12 \sum_i w_i \Big\},\qquad w_i = \text{seconds price } p_i \text{ held (in stock, last 90 days)}
```

**Worked example: the classic fake discount.** A seller keeps a TV at 1,000 for 60 days, raises it to
2,000 for 3 days, then advertises "50% off" back to 1,000.

| Check | Result |
| --- | --- |
| Naive "versus the previous price" | 2,000 to 1,000 = 50% off, would alert |
| Typical (90 days) | 1,000, because 60 days at 1,000 outweigh 3 days at 2,000 |
| Drop versus typical | 0%, no alert |

An automated test runs exactly this scenario so the protection can't silently break.

## Algorithm 5: deciding what is a real deal

A product is only judged when it just appeared, got cheaper, or came back in stock, and only if it is in
stock and at or above its group's minimum price. The judgement (`pricing.evaluate`) takes one of two paths
depending on how much history we have.

```mermaid
flowchart TB
    t["A product is new or got cheaper<br/>(in stock, at or above the group minimum)"] --> h{"How much history?"}
    h -- "3+ days: Path A, our own history" --> a["Compare with its own typical price<br/>60%+ below: possible price error<br/>20%+ below: real price drop<br/>new low, 15%+ below: all-time low<br/>was this cheap over 15% of the time: no alert"]
    h -- "1 to 3 days: early drop" --> e["30%+ below typical AND below<br/>every price seen: early alert, low confidence"]
    h -- "under 1 day: Path B, new listing" --> b["Check the shop's claimed discount<br/>crossed-out price 30%+ higher<br/>not 60%+ off an RRP<br/>1 real signal (2 on a first crawl)<br/>any check fails: no alert"]
    a --> n["noon: no alert if another seller<br/>of the same item is already cheaper"]
    e --> n
    b --> n
    n --> r["No repeat within 7 days<br/>unless 5% cheaper than the last alert"]
    r --> c{"New listing<br/>(low confidence)?"}
    c -- "yes" --> w["Held until the next crawl sees<br/>the same price (cancelled if it reverted;<br/>sent anyway after 90 min)"]
    c -- "no" --> s["Arabic alert to every subscriber<br/>with confidence and score"]
    w --> s
```

The shop's own discount claim only matters for listings we have not tracked long enough; after 3 days, our
history decides.

**Path A: we have at least 3 days of in-stock history.** The shop's crossed-out price is ignored; the
product is compared with its own typical price.

| Kind | Rule (defaults, in the config) | Arabic title |
| --- | --- | --- |
| Possible price error | 60% or more below typical | خطأ محتمل في السعر |
| Real price drop | 20% or more below typical (a group can set its own threshold) | انخفاض حقيقي في السعر |
| All-time low | Below every earlier price, at least 15% below typical, with at least 14 days of history | أقل سعر على الإطلاق |

**Rarity.** Being below the median is not enough: a product that is at 3,400 forty percent of the time and
5,000 the rest (noon's alternate-week promos, an Amazon seller that keeps winning the listing back) is simply
sometimes 3,400. So Path A also requires that the product spent no more than 15% of its in-stock time
(`max_share_at_or_below`) at or below today's price. A price it has never been near passes with 0%; one it
touched for a day in fifty passes too. The alert prints this share ("was at this price or less 4% of the
time").

Confidence is "high" with 14 or more days of history and a share of 5% or less, "medium" otherwise. The alert
also says if the shop's crossed-out price is more than 15% above anything we ever saw, so you know to
ignore it.

**Early drop: 1 to 3 days of history.** Not enough for a full verdict, but a price that is 30% below the
typical price *and* below every price seen is alerted as an early drop (⏳, confidence low) with a note that
the history is short. Without this, a product that drops on day 1 and stays there would never alert at
all, because history only counts the time before the current price.

**Cheapest offer (noon).** noon lists each item once per seller. A seller's drop to 3,200 is not a deal when
another seller has had it at 3,000 all along, so no alert goes out while a cheaper in-stock offer of the
same item exists. Only offers seen in the last 24 hours count: a seller that stops selling simply
disappears from the pages, so its last price must not block alerts forever.

**Confirmation (off since 27 October 2026).** The code can hold new-listing alerts (they rest on the shop's
own crossed-out price) until the next crawl sees the product again: still at that price, the alert goes out;
back up, it is cancelled (a glitch, a seller switch, a one-crawl mistake). The hold lasts long enough for the
slowest revisit of the group (3x its interval plus 10 minutes, at least `confirm_minutes` = 90) and goes out
anyway after that. In two days of running it, 105 alerts were held, 98 released by a second sighting, 7 by the
timeout and **none cancelled**, at a cost of 14 minutes on average (90 at most) per alert. A flash sale can be
over in that time, so the live config sets `confirm_low_confidence = false` and every alert goes out at once;
the message still says "not yet verified by our history" and asks the reader to check the page. Set it back
to `true` if cancellations ever turn out to be needed (`make alerts` shows them). Early drops were never held
(`confirm_early_drops`).

**Path B: less than a day of history (a new listing).** We have no reference of our own, so the shop's
claim is used but must be backed by evidence:

1. The crossed-out price must be at least 30% above the current price.
2. A claim of 60% or more off a "List" (RRP) price is rejected as almost always fake (a real Amazon
   "Was:" price may fall that far).
3. At least one real signal is required: 3 or fewer left in stock (noon only; amazon.eg shows it on about half its listings), a limited-time or mega deal badge,
   noon's "lowest price in a year", or Amazon's "Was:" price. noon's "lowest price in 30 days" sits on
   19% of its catalogue, so it does not count.
4. "Selling out fast", "N sold recently" and "only 8 left" do not count: they are marketing.
5. During a slice's very first crawl, when every product looks new, two signals are required. This stops a
   flood of alerts on day one.

These alerts are marked confidence "low" and say "not yet verified by our history".

**Score (0 to 100), for ranking:** the drop in percent, plus 10 for an all-time low, minus half the
rarity share in percent (a price seen 10% of the time loses 5), plus 5 each for 5 or fewer left, a deal
badge, or the shop's own "lowest price" badge, then multiplied by 1.0 (high confidence), 0.85 (medium) or
0.7 (low).

**No repeats.** The same product is not alerted again within 7 days unless its price falls another 5%.

**Worked examples:**

| Situation | Outcome |
| --- | --- |
| TV at 32,000 for 50 days, 30,000 for 10 days, now 19,999 | Typical 32,000, 38% below and below every earlier price: real price drop, high confidence |
| Kettle seen for the first time at 1,999, crossed-out 2,999, "selling out fast" only | 33% claimed but no real signal: no alert |
| Earbuds seen for the first time at 999, crossed-out 1,699, 1 left, "lowest price in a year" | 41% claimed with two signals: new listing alert, low confidence |
| Fridge at 10,000 for 60 days, now 7,500 but it was 7,000 last month | 25% below typical and not an all-time low: no alert |
| Blender that alternates 5 days at 5,000 and 2 days at 3,400, now 3,400 again | 32% below typical, but at this price 29% of the time: its regular promo, no alert |
| Same blender now 2,900 | Never this cheap: real price drop |
| noon seller A drops a TV from 5,000 to 3,200 while seller B sells it at 3,000 | Not the cheapest offer: no alert |
| Air fryer tracked for 2 days at 5,220, now 3,500 | 33% below and a new low: early drop (⏳), low confidence, sent at once |
| Same air fryer at 3,977 | Only 24% below: under the 30% early-drop bar, no alert (yet) |

## Alerts and the Telegram bot

**What a deal message contains**, top to bottom: the kind and the discount; the product link, brand and
rating; the numbers (price now, typical price, how much you save, the price just before, the range we
recorded and for how long, the shop's crossed-out price with the discount it claims or a note to ignore
it); a "why is this real?" section that explains the decision in plain words (typical price versus crossed-out
price, lowest ever, how rarely it was this cheap, price-error warning, inflated crossed-out price, what the
confidence level means; for new listings, the evidence the shop gives); a "before you buy" section (stock
left, third-party seller and the cheapest other seller on noon, low rating or few reviews, the shop's
marketing badges we ignore, earlier alerts for the same product, and for low confidence a reminder to
check the page); and a footer with group, shop, confidence and score.

An alert is written to the `alerts` table first and sent afterwards, so a Telegram outage or a server
restart never loses one. A background loop in the coordinator runs every 3 seconds:

1. **Hand out.** Each new alert gets one `deliveries` row per recipient: deal alerts go to every approved
   subscriber, system messages to you (the admin) and your helper admins.
2. **Send.** Each delivery is sent on its own, one message per second. A failure is retried after 30
   seconds, then 1, 2, 4 minutes and so on up to an hour, at most 10 times. Telegram's own "retry after"
   hint is respected.
3. **Drop blockers.** If Telegram says a person blocked the bot (HTTP 403), they are marked "blocked" and
   skipped from then on.

**What a deal alert contains** (in Arabic): the kind and the discount; the product name linked to the shop;
the price now against its typical price, or against the shop's crossed-out price for a new listing; the
range of prices we have seen, the number of changes and how long we have tracked it; the reasons (below
typical, all-time low, the price just before, an inflated crossed-out price to ignore); the signals (only 2
left, limited-time deal, the shop's "lowest price in a year", rating); and finally the group, the shop, the
seller on noon, the confidence and the score. Product names stay as the shop writes them.

**Who receives alerts.** The bot answers messages by long polling Telegram, so the server needs no public
web address for it.

1. A friend opens t.me/Jerrys_deals_bot and presses Start. They are told the request went to you.
2. You get their name with two buttons: approve or reject.
3. Approve: they get a welcome message and every deal alert from then on. Reject: they are told politely.
4. `/stop` pauses their alerts. `/start` later brings them back without a new approval.
5. Send `/subscribers` to the bot to see everyone and their status. Under the list each person has buttons:
   **❌ remove** (asks "are you sure?" first; they are told, stop getting alerts, and a later `/start` is a
   new request) and **⭐ make helper** / **⬇️ unmake helper**.

System messages (a shop is stalled, a worker went silent, an IP is blocked repeatedly, a page layout seems
to have changed, low disk) and the daily report go to you and to your helper admins.

| Who | Deal alerts | Join requests | System warnings and daily report | Manage people |
| --- | --- | --- | --- | --- |
| You (the owner, `[telegram] chat_id`) | ✅ | ✅ approve/reject | ✅ | ✅ remove, make/unmake helpers |
| Helper admin (⭐) | ✅ | ❌ | ✅ | ❌ |
| Subscriber | ✅ | ❌ | ❌ | ❌ |

A second tap on an already answered request just says it was handled. A helper who sends `/stop` gets
nothing until `/start` again. `make report` (sending the report by hand) still goes only to you.

## Keeping it healthy

See [MONITORING.md](MONITORING.md) for the full monitoring guide and runbook. In short: run `make health`
any time, or read the report that arrives on Telegram every day at 09:00 Cairo time.

What happens when something goes wrong:

| Problem | What the system does |
| --- | --- |
| Worker crashes, Mac sleeps, Wi-Fi drops | Its lease expires after 5 minutes and the job goes to the next worker. launchd and systemd restart crashed workers |
| Coordinator restarts or is unreachable | Workers retry every 2 to 60 seconds and continue; queued alerts are sent after the restart |
| A shop blocks an IP | That shop pauses for that IP (15 minutes, doubling up to 24 hours); other IPs continue; after 3 blocks in a row you get a warning |
| Network error on a page | Retried after 5, 20 and 60 seconds, then the job is retried later (5 minutes, doubling up to 6 hours); 3 failures send you a warning |
| A shop changes its page layout | A slice that had products but now parses as empty counts as a failure, not as "everything disappeared"; 3 in a row send a "layout changed?" warning |
| Typo in a noon category | The breadcrumb check fails the plan job with a clear message |
| A shop has no working worker | Work more than 45 minutes overdue with nothing finished for an hour sends "stalled", listing blocked IPs |
| A worker goes silent | Warning after 30 minutes (at most every 12 hours) |
| Telegram down or rate-limited | Alerts wait in the outbox and are retried |
| Disk below 10% free | Warning (at most every 12 hours) |
| Data loss | Daily compressed backup in `/var/lib/deals/backups`, 7 kept; `deals backup` makes one on demand |

The watchdog that sends these warnings runs every minute inside the coordinator. Once a day it also takes
the backup, prunes logs older than 7 days and refreshes the database's query statistics (`PRAGMA optimize`).

## Scaling and what's next

The server is far from its limits; the real limit is how fast each internet connection can politely read
the shops. Measured on 25 October 2026:

| Measure | Value |
| --- | --- |
| Products tracked | 43,463 |
| Database size | 28 MB (about 400 bytes per product, 50 bytes per price change) |
| Expected growth | 0.5 to 1 MB per day, about 200 to 350 MB per year |
| Daily backup | about 7 MB compressed, 7 kept |
| Coordinator memory | 96 MB of the droplet's 458 MB |
| Time to store one page | A few milliseconds; workers send about one page every 2 seconds each |
| Free disk on the droplet | 4.8 GB |

Every frequent database query uses an index: finding a product, its history, the next due job, and recent
changes. SQLite on this droplet is comfortable into the millions of products; Postgres would only be worth
it beyond that, or with several coordinators.

To crawl more or faster, add workers on other connections (a friend's PC, an always-on Raspberry Pi at
home). That needs a token in the config and no code change. Adding another shop (Raya, Carrefour)
needs one new adapter file and a saved-page test; the planner, scheduler, alerts and bot are shared. See
[ROADMAP.md](ROADMAP.md) and the improvements in [REVIEW-2026-10-25.md](REVIEW-2026-10-25.md).
