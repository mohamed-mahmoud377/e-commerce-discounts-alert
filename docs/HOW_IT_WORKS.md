# How it works

## 1. The pieces

| Piece | Where it runs | Job |
|---|---|---|
| **Coordinator** (`deals serve`) | the droplet | Owns the SQLite database. Decides what to crawl next, hands jobs to workers, stores results, detects deals, sends Telegram alerts, watches for trouble, and backs up daily. |
| **Worker** (`deals worker`) | any machine: droplet, your Mac, friends' PCs | Asks the coordinator for a job, fetches the pages from the shop, and sends back what it found. It holds no database. |
| **Sites** (`sites/amazon_eg.py`, `sites/noon_eg.py`) | inside each worker | Know how to fetch and parse one listing page of a shop. |

Workers only make **outgoing** HTTP requests to the coordinator. Nothing needs to be opened
on your Mac or your friends' routers.

## 2. The life of a price, step by step

1. **Config → plan jobs.** On start, the coordinator reads `[[watch]]` groups (e.g. "TVs ≥ 3,000 EGP on
   amazon.eg node 21832982031 and noon `.../televisions`"). Each (site, root) becomes a **plan job**.
2. **Planning.** A worker leases the plan job and runs the planner (§4). It splits the root into
   **slices**, pieces small enough that the shop lets us page through all of them. The products on
   every probed page are reported too, so planning already fills the database.
3. **Crawl jobs.** Each slice becomes a **crawl job** with a revisit interval (starting at the
   watch's `interval_minutes`).
4. **Leasing.** Workers repeatedly ask for work (`POST /api/lease` with the sites they can reach).
   The coordinator gives the most overdue job they're allowed to take (§5) and a **lease token**
   valid for 5 minutes.
5. **Crawling.** The worker fetches page 1, 2, 3… of the slice, waiting 1–4 s (per site) between
   requests, and posts each page's products to the coordinator. Each post extends the lease.
6. **Recording.** For every product the coordinator updates its latest state (price, crossed-out price,
   stock left, deal badges, rating…). If price, crossed-out price or availability changed, it appends
   one row to `price_history`. Unchanged products cost no history rows, so the database stays small
   even with frequent re-crawls.
7. **Deal check.** Products that are new, got cheaper, or came back in stock are evaluated (§7) against
   their own history. A deal is written to the `alerts` table (the outbox).
8. **Delivery.** A background loop hands each alert to its recipients (deal alerts: every approved
   subscriber; system alerts: only the admin) and sends them, retrying **per person** with backoff.
   Nothing is lost on restarts. The bot reads /start, /stop and the admin's approve/reject buttons by
   long polling, so no public port is needed.
9. **Rescheduling.** When the last page is in, the slice's next visit is scheduled: sooner if prices
   moved, later if nothing changed (§5).
10. **Replanning.** Every 7 days each root is planned again, to pick up new sub-categories and size
    changes. Slices that survive keep their schedule.

## 3. Fetching the shops

**amazon.eg** sits behind an AWS WAF JavaScript challenge. Plain HTTP requests get a "202/503 automated
access" page. The worker opens headless Chromium **once**, lets it pass the challenge, saves the
cookies (`data/amazon_eg_cookies.json`), and then fetches every page with plain HTTP (`curl_cffi`
impersonating Chrome's TLS fingerprint): about 1 s per page and 24 products per category page. When the
cookies stop working, it re-solves once; if still challenged, it reports **blocked**.
Category pages are `/s?rh=n:<node>,p_36:<min>-<max>` (price in piastres). A bare category URL shows a
"landing" layout with no product grid, so a price filter is always sent, even `0-`.

**noon.com/egypt-en**: the HTML pages have a bot challenge, but the catalog JSON API the site itself
uses (`/_svc/catalog/api/v3/u/egypt-en/<category path>/?page=N&f[price][min]=…`) answers plain HTTP with
50 structured products per request. An unknown category path silently returns a *different* category,
so the worker checks the breadcrumb and fails loudly on typos.

**noon sellers.** One noon product can be sold by several sellers at very different prices
(the same earbuds at EGP 7,550, 1,000 and 495), and which offer a listing shows depends on the
query. noon items are therefore tracked **per seller offer** (`SKU@seller`), so each seller has
its own clean price history and a seller switch is never mistaken for a price drop. Amazon's listing
card always shows the featured offer, so Amazon items are tracked per product (ASIN).

**Which machine can reach what** (tested 2026-10-25):

| | home IP (Mac) | droplet (DigitalOcean Frankfurt) |
|---|---|---|
| amazon.eg | ✅ | ⚠️ keyword search OK, category pages mostly blocked |
| noon.com | ✅ | ❌ Akamai 403 on everything |

So the Mac does the real crawling and the droplet coordinates. The droplet's worker keeps
retrying Amazon with growing pauses and takes over whenever it gets through.

**Signals parsed from listing cards** (no extra requests):

| Signal | amazon.eg | noon |
|---|---|---|
| price, crossed-out price | ✅ "Was:" (Amazon's recent price) vs "List:" (RRP) | ✅ (seller-set, treated like RRP) |
| stock left | "Only 3 left in stock" | "Only 3 left" |
| deal badges | "Limited time deal" | "Deal", "Mega Deal" |
| site's own history | – | "Lowest price in 30 days / in a year" |
| demand | "300+ bought in past month" | "200+ sold recently", "Selling out fast", bestseller |
| rating, reviews | ✅ | ✅ |

## 4. The planner: covering a whole category

Shops stop paging long before the end of a big category:

| | Page size | Hard limit |
|---|---|---|
| amazon.eg | 24 (category) | shows an exact count only up to 1,000 ("over 1,000" above) |
| noon | 50 | 50 pages = 2,500 products per listing |

The planner probes page 1 of a slice and reads the count:

1. **Fits** (count known and ≤ limit) → it's a slice.
2. **Too big, and it's a category** → split into its **sub-categories** (Amazon: the Department sidebar;
   noon: the category tree in the API response). They inherit the watch's price floor.
3. **Too big, no sub-categories** → split by **price**: first into bands
   (0–50–100–200–500–1k–2k–5k–10k–20k–50k–100k+ EGP, starting at the floor), then halve any band
   that's still too big, down to 1-EGP-wide ranges.
4. Neighbouring price ranges **share their boundary** (1000–2000, 2000–5000) so fractional prices are
   never lost. The few duplicates are harmless.

Tested on real data: noon *Wearables* (24,787 products) → 27 slices in 44 requests, covering 26,179
(slightly more than the total because of shared boundaries and items listed in two
sub-categories). A test with a synthetic shop checks that **every** product is covered.

## 5. Scheduling, leases and politeness

- **Most overdue first.** Plan jobs come before crawl jobs, then the crawl job with the oldest `next_due_at`.
- **Adaptive revisits.** After a crawl: if any price changed (or new products appeared), the slice's
  interval **halves** (down to `min_interval_minutes`, default 10). If nothing changed, it grows **×1.25**
  (up to `interval_minutes × 3`). Busy slices (sales, new launches) get watched closely, and quiet
  ones cost almost nothing. ±10% jitter keeps slices from moving in lockstep.
- **Leases.** A job belongs to one worker until its lease expires (5 min, extended by every page). A
  crashed or asleep worker's job is automatically re-offered. Late results from an expired lease are
  rejected (HTTP 409).
- **One job per (site, IP).** Two workers behind the same router never crawl the same shop at the same
  time, so adding a second laptop at home doesn't double the load on Amazon from one IP.
- **One loop per site inside a worker.** The Mac crawls noon and Amazon in parallel; a slow Amazon
  crawl never delays noon.
- **Politeness.** Random 2–4 s (Amazon) / 1–2 s (noon) between requests, per worker, between pages and jobs.

**Capacity** for the configured groups (≈5,000 Amazon products and ≈30,000 noon products above the price floors):
about 210 Amazon pages and 600 noon pages per full pass, so **10–15 minutes per site** for a complete
refresh from one home IP. Every product is re-checked roughly every 30–90 minutes, and hot slices every 10–20 minutes.

## 6. Price history and statistics

`price_history` is a **step function**: each row's price holds from its `observed_at` until the next
row. From it, `pricing.compute_stats` derives:

| Stat | Meaning |
|---|---|
| `typical_90d` / `typical_30d` | **time-weighted median** of in-stock prices *before* the current price |
| `low` / `high` | all-time lowest/highest earlier in-stock price |
| `low_90d` / `high_90d` | same, last 90 days |
| `history_days` | how many in-stock days of data we have (last 90 days) |
| `changes_90d` | number of price/stock changes in 90 days |
| `previous` | the price just before the current one |

Why time-weighted: a price that held for 60 days should count 60× more than one that lasted a day. This
is what defeats the most common **fake discount**, where a seller raises the price for a few days and then
"cuts" it back:

```
price  2000 |              ███
       1000 |██████████████   ██████   <- "50% off!" vs the previous price
             day 0        60  63
typical_90d = 1000 (60 days at 1000 outweigh 3 days at 2000)  ->  drop 0%  ->  no alert
```

Out-of-stock periods are excluded from all statistics, and the current price is never part of
its own reference.

## 7. Deal rules (`pricing.evaluate`)

A product is only evaluated when it is **in stock**, at or above the group's `min_price`, and it is
new, got cheaper, or came back in stock.

**With our own history** (≥ `min_history_days`, default 3 days in stock):

| Kind | Rule (defaults) | Message title (Arabic) |
|---|---|---|
| `price_error` | ≥ **60%** below typical_90d | 🚨 خطأ محتمل في السعر |
| `price_drop` | ≥ **30%** below typical_90d (per-group `min_drop` overrides) | 🔥 انخفاض حقيقي في السعر |
| `all_time_low` | lower than every earlier price **and** ≥ 20% below typical, with ≥ 14 days of history | 📉 أقل سعر على الإطلاق |

**Without enough history** (a new product, or a product we only just started tracking): the shop's
claimed discount is the only reference, so it needs backing evidence:

- the crossed-out price must be ≥ 30% higher, and not an absurd "80% off RRP";
- plus at least **one** supporting signal: only ≤ 3 left, a limited-time / mega deal badge, noon's
  "lowest price in 30 days / a year", or Amazon's "Was:" price (its real recent price). "Selling out
  fast" and "N sold recently" are marketing and don't count; "only N left" above 3 is too common
  (about half of amazon.eg's listings) to mean anything;
- during a slice's **first ever crawl**, where every product looks "new", it needs **two** signals.
  This prevents an alert flood on day one.
- these alerts say *"new to us … not yet verified by our history"* and have confidence **low**.

**Messages are in Arabic** (titles, reasons, signals, group names from each watch's `label`); product titles stay as the shop shows them.

**Context in every alert**: typical price, all-time range, number of changes, how long we've tracked it,
the price just before, and stock and deal signals. If the shop's crossed-out price is higher than
anything we've ever seen, the alert says so ("ignore it").

**Score** (0–100, for ranking): drop % + 10 for an all-time low + 5 each for ≤ 5 left, a deal badge, or a
site-history badge, × confidence (high 1.0 / medium 0.85 / low 0.7).

**No spam**: the same product isn't alerted again within 7 days unless the price falls a further 5%.

## 8. Failure handling

| What goes wrong | What happens |
|---|---|
| Worker crashes, Mac sleeps, Wi-Fi drops | Lease expires after 5 min, and the job goes to the next worker that asks. launchd/systemd restart crashed workers. |
| Coordinator restarts or is unreachable | Workers retry with backoff (2 s → 60 s) and resume. Alerts in the outbox are sent after restart. |
| A shop challenges an IP (captcha / 403 / 503) | Worker reports **blocked** → that (site, IP) pauses 15 min, then 30 min, 1 h … up to 24 h. Other IPs continue. Its first successful page clears the strikes. After 3 strikes in a row you get a ⚙️ Telegram alert. |
| Network error / timeout on a page | Retried after 5 s, 20 s, 60 s; then the job is reported **failed** and retried later with backoff (5 min → 6 h). 3 failures → ⚙️ alert. |
| Shop changes its HTML (parser finds 0 products in a slice that had some) | Counted as a failure, not as "everything disappeared". After 3 → ⚙️ alert "site layout changed?". |
| Typo in a category in the config | noon: breadcrumb check fails the plan job with a clear message. |
| A slice can't be split small enough | Kept and crawled as far as the shop allows, with a ⚙️ alert. |
| A site has had no worker for a while | Watchdog: work > 45 min overdue and nothing finished for an hour → ⚙️ "noon_eg is stalled", listing blocked IPs. |
| A worker goes silent | ⚙️ "worker mac has been silent for 40 min" (at most every 12 h). |
| Telegram down / rate limited | Alert stays in the outbox; retried with backoff (30 s → 1 h), up to 10 times. |
| A subscriber blocks the bot | Telegram answers 403; they're marked `blocked` and skipped. `/start` brings them back. |
| Disk / database loss | Daily consistent backup (`/var/lib/deals/backups`, 7 kept). `deals backup` makes one on demand. |
| Server reboot | systemd starts both services. The Mac worker is a launchd agent with KeepAlive. |

## 9. Keeping track: health checks and the daily report

`deals health` (and `make health`, which adds server/log/code/Mac-worker checks) answers "is
everything working?" with ✅/⚠️/❌ lines: database integrity and schema, Telegram delivery, each worker's
last contact, each site's last finished slice and backlog, failing or oversized slices, blocked IPs,
data sanity (bad prices, A→B→A flip-flops), disk space and backup age.

Every day at **09:00 Cairo time** the admin gets an Arabic report on Telegram: anything that needs
attention, products tracked per site, price changes and deals in the last 24 h, subscribers, workers,
and the 5 best deals. The watchdog also does daily maintenance: a compressed backup (7 kept) and
`PRAGMA optimize` for the query planner.

## 10. Scaling

Measured on 2026-10-25 with 38,695 products: the database is 21 MB, products ≈ 400 bytes each and a
price change ≈ 50 bytes (+ index). History only grows when prices change, so expect roughly
0.5–1 MB/day (≈ 200–350 MB/year); backups are gzip-compressed (~4×). Every hot query uses an index
(product lookup, history per product, due jobs, recent changes). The coordinator handles a page in a
few milliseconds, far above what workers send (≈ 1 page / 2 s each).

The real limit is **crawling politely from each IP**, not the server: capacity grows by adding
workers on other connections (friends, a Raspberry Pi), which needs no code change. Single-file
SQLite on this droplet is comfortable into the millions of products; moving to Postgres would only
make sense beyond that, or with several coordinators.

## 11. Data model (SQLite, `db.py`)

| Table | Holds |
|---|---|
| `products` | latest state per (site, sku), where noon skus are `SKU@seller`: title, url, price, crossed-out price and kind, stock left, badges, rating, seller, group, first/last seen, last change |
| `price_history` | one row per price/stock change (the step function) |
| `jobs` | plan and crawl jobs: target slice, group, revisit interval, next due, lease, failures, last results |
| `workers` | last seen, IP, sites, pages, jobs done |
| `blocks` | (site, IP) cooldowns |
| `alerts` | outbox of deal and system alerts, with delivery state |
| `subscribers` | Telegram users: admin, approved, pending, stopped, blocked |
| `deliveries` | one row per (alert, person) with its own retries |

Prices are integers in piastres (1 EGP = 100), so there are no float rounding errors. Migrations are
append-only and applied automatically on start.
