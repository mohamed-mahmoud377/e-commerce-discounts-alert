# How it works in detail

Code-level reference for changing and improving the system. Start with [GUIDE.md](GUIDE.md) for the
concepts and algorithms; this document maps them onto the code.

## Code map

The whole system is about 5,500 lines of Python in `src/deals/`, plus the tests in `tests/`. Each module
owns one job.

| Module | Lines | Owns | Calls |
| --- | --- | --- | --- |
| `models.py` | 103 | The shared data shapes: `Listing` (one product on one page), `Slice` (a category plus optional price range, with its text key), `PageResult`, `Category`, badge names, the `Blocked` and `UnknownTarget` errors | nothing |
| `config.py` | 126 | Reading `config.toml` into `Config`, `AlertRules`, `Schedule`, `SiteSettings`, `Watch` | models |
| `sites/amazon_eg.py` | 198 | Fetching and parsing amazon.eg listing pages; cookies from headless Chromium | models, base |
| `sites/noon_eg.py` | 139 | Fetching and parsing noon's catalog JSON; per-seller keys | models |
| `sites/jumia_eg.py` | 191 | Fetching Jumia category pages with Safari's TLS fingerprint (Cloudflare), reading the embedded `__STORE__` JSON, detecting redirects to the parent category | models, base |
| `sites/cairosales_eg.py` | 150 | Fetching Cairo Sales (PrestaShop) category pages with Chrome's TLS fingerprint, 60 cards a page sorted by price; unpriced cards are out of stock; homepage redirects are unknown slugs | models, base |
| `sites/twob_eg.py` | 140 | 2B's Magento GraphQL API: one POST per 100 products with exact counts, categories by numeric id | models |
| `sites/btech_eg.py` | 230 | B.TECH's product search API (guest token from the session cookie, 100 products a POST, inclusive price filter); the category tree read from a category page once a day to validate slugs | models |
| `sites/dream2000_eg.py` | 115 | Dream 2000's Shopify collection JSON (250 products a GET, exact counts, no price filter); variants folded by everything but colour | models |
| `sites/carrefour_eg.py` | 183 | Carrefour Egypt's category pages (Akamai; the listing JSON cut out of the Next.js RSC payload, 60 cards a page sorted by price, inclusive price filter, 21-page cap); an empty category slice is checked against the bare category to tell an unknown id from an empty range | models, btech_eg (RSC helpers) |
| `sites/base.py` | 35 | The `Site` interface every shop implements; money and count parsing | models |
| `planner.py` | 93 | Splitting a root into slices under the shop's limit, by price ranges only | models |
| `db.py` | 349 | Schema and migrations; `record()` (upsert a product, append history on change); backups | models |
| `scheduler.py` | 241 | The job queue: leases, intervals, blocks, failures, worker bookkeeping, events | db, planner |
| `pricing.py` | 222 | Price statistics and the deal rules; pure functions, no database | config, models |
| `alerts.py` | 279 | Judging a changed product, writing alerts, formatting the Arabic message, the Telegram client and the delivery loop | db, pricing |
| `bot.py` | 292 | The Telegram conversation: /start, /stop, approvals, subscribers; `decide()` is shared with the web UI | alerts, db |
| `health.py` | 207 | Health checks and the daily report | db, alerts |
| `matching.py` | 590 | The same product on different shops: model codes read from titles once (`product_codes`), the hourly grouping streamed through SQL into `models` / `product_models` (in a `nice 19` subprocess), `same_model()` for the views. See [MATCHING.md](MATCHING.md) | db |
| `details.py` | 150 | Product page parsers per shop (barcode, model number, specs) and `fetch_details`, which never retries a challenge | models |
| `detailq.py` | 170 | The coordinator's side of product pages: offer one to an idle worker (paced, capped, paused after a refusal), record what it gave, refill the queue | db, config |
| `monitor.py` | 166 | Device states, events (logs), the dashboard page | db, scheduler |
| `webui.py` | 1182 | The web UI page (one HTML + CSS + JS string, light and dark themes) and its queries | health, monitor, alerts |
| `coordinator.py` | 570 | The FastAPI app (all endpoints) and the watchdog thread (which also starts the hourly matching) | everything above |
| `worker.py` | 271 | The crawling loop on each device, retries, log shipping | sites, planner, HTTP to the coordinator |
| `cli.py` | 445 | The `deals` command: every subcommand | everything |

Two boundaries matter when changing things. Workers never touch the database; they only speak HTTP to the
coordinator. And `pricing.py` has no I/O at all, so deal rules can be tested with plain numbers.

## One page, end to end

This traces a single Amazon TV page from the moment your Mac asks for work to the Telegram message, naming
every function on the way. Reading the code in this order is the fastest way to understand it.

**On the worker (your Mac)**

1. `Worker.run()` starts one thread per shop, each running `Worker._loop([site])`, plus the `LogShipper` thread.
2. `_loop` calls `Coordinator.call("/api/lease", {"sites": ["amazon_eg"], "worker_sites": [...], "version": ...})`.
   `call()` retries connection errors and 5xx answers forever with backoff 2, 4, 8 ... 60 s; a 409 raises
   `LeaseLost`, a 401 stops the worker.

**On the coordinator**

3. `lease()` endpoint: `worker_name()` maps the bearer token to a device name; `scheduler.worker_seen()` records the visit.
4. `scheduler.lease()` runs inside `BEGIN IMMEDIATE` (one lease at a time): it skips shops this IP is
   blocked on (`blocks` table), releases jobs this device held on the requested shops, skips shops another
   device on the same IP is crawling, then picks the due job with plan jobs first and the oldest
   `next_due_at` next. It writes a random `lease_token`, `lease_expires_at = now + 5 min`, and resets
   `run_count`/`run_changes`.
5. The reply carries the job plus `min_delay`/`max_delay` for that shop from the config.

**Back on the worker**

6. `run_job()` sends crawl jobs to `_crawl()`. For each page, `_fetch()` calls the shop adapter and retries
   network errors after 5, 20 and 60 s; `Blocked` and `UnknownTarget` are not retried.
7. `AmazonEG.fetch_page(slice, page)` builds `/-/en/s?rh=n:<node>,p_36:<min>-<max>&page=N`. `_get()` treats
   status 202/503 or the words "challenge.js", "automated access", "validateCaptcha" as a challenge; then
   `_bootstrap_cookies()` opens Chromium, waits up to 30 s for the challenge to clear, saves cookies, and the
   request is retried once.
8. `parse_search_page()` walks every `div[data-component-type="s-search-result"]`; `_parse_card()` reads the
   price (`span.a-price` without strike), the crossed-out price (`data-a-strike="true"`, "Was:" or "List:"),
   "Only N left", "bought in past month", stars and reviews, "Limited time deal". `_parse_total()` reads
   "1-24 of 775 results" (None for "over 1,000"). Sponsored duplicates are dropped by ASIN.
9. The worker posts `/api/jobs/{id}/page` with `lease_token`, `page`, the listings as JSON and `done` (true
   on the last page).

**On the coordinator again**

10. `page()` endpoint: `owned_job()` checks the lease token (409 if lost). `bootstrap` is true for plan jobs
    and slices never finished before.
11. For each listing, `db.record()` upserts the product row and appends a `price_history` row when price,
    crossed-out price or stock changed. It returns `Recorded(status, old_price, old_in_stock)`.
12. If the product is new, got cheaper, or came back in stock, and is in stock, `alerts.check_product()`
    runs: `product_stats()` loads the history and calls `pricing.compute_stats()`; `pricing.evaluate()`
    returns a `Deal` or nothing; `_recently_alerted()` applies the 7-day rule; `format_deal()` writes the
    Arabic text into a new `alerts` row.
13. Still in the same transaction: `scheduler.extend()` pushes the lease 5 min further and adds to
    `run_count`/`run_changes`; `unblock()` clears this IP's block record for the shop; `worker_site_ok()`
    updates the device's per-shop status; `worker_seen()` counts the page.
14. On the last page `_finish_crawl()` treats "slice had products, parsed zero" as a failure; otherwise
    `scheduler.finish_crawl()` halves the interval if anything that could alert happened (`run_changes`
    counts drops of `DROP_SPEEDUP` = 3% or more, restocks and new products, not rises) or multiplies it by
    1.25 if not, clamps it between 10 minutes and the group's cap (`feed_max_interval_minutes` for deal
    feeds, see `is_deal_feed()`), adds plus or minus 10% jitter, sets
    `next_due_at` and clears the lease.

**Delivery**

15. `AlertSender` (a thread in the coordinator, every 3 s): `fan_out()` creates one `deliveries` row per
    recipient; `deliver()` calls `Telegram.send()` for each, one per second, retrying failures with backoff
    and marking people who blocked the bot.

**Worker again**

16. After the last page, `_crawl()` logs `done ... products, pages, changes, seconds`, waits the shop's
    random delay, and `_loop` asks for the next job.

## The database in detail

One SQLite file in WAL mode (readers never block the writer), `synchronous=NORMAL`, foreign keys on, a
30-second busy timeout. Only the coordinator writes; the worker-facing handlers (lease, page, plan...) share one
connection on the event loop, and each background thread (alert sender, watchdog, bot, event writer) opens its
own. The read-only viewer endpoints (web UI, `/api/devices`, `/api/events`, saved queries) are plain `def`
handlers: FastAPI runs them in a thread pool and each opens a private `mode=ro` connection, so a slow report
never delays the workers' uploads (WAL readers don't block the writer).

**Migrations.** `db.MIGRATIONS` is an append-only list of SQL scripts; `PRAGMA user_version` stores how
many have run, and `db.connect()` runs the missing ones in a transaction on start.

| Version | Change |
| --- | --- |
| 1 | `products`, `price_history` |
| 2 | `crawl_slices` (replaced in 3) |
| 3 | Product signal columns; `jobs`, `workers`, `blocks`, `alerts` |
| 4 | `products.seller`; noon data reset for per-seller keys |
| 5 | `subscribers`, `deliveries`, `kv` |
| 6 | Indexes on `price_history(observed_at)`, `alerts(created_at)`, `products(grp, site)` |
| 7 | `worker_sites`, `events` |
| 8 | `alerts.confirm_until` (low-confidence alerts held until the price is seen again) |
| 9 | `subscribers.is_manager` (helper admins) |
| 10 | `models`, `product_models` (cross-shop matching; written only by `matching.rebuild`) |
| 11 | `product_codes`, `product_code_index` (codes read once per title, incremental); `models` / `product_models` recreated with brand-scoped keys and `models.kind` |
| 12 | `product_codes.line`, `.attrs`: the second identity (brand + product line + variant) for titles without a code |
| 13 | `product_codes.ptype`, `.specs`, `.flags`, `.toks` (the spec signature, for the probable tier); `match_overrides` (matches set by hand) |
| 14 | `product_barcodes` (barcodes from a shop's own data: Dream 2000's variant SKUs; source `feed`) |
| 15 | `product_details`, `detail_queue`, `detail_state` (product pages read for barcodes and model numbers) |
| 16 | `crawl_pauses` (crawling paused by hand per device and/or shop, with an end time) |

**Tables and columns** (prices are integer piastres; times are UTC ISO text, which sorts correctly as text):

| Table | Key | Columns and meaning |
| --- | --- | --- |
| `products` | `id`; unique `(site, sku)` | `title`, `url`, `currency`; `price` (None when no price shown), `list_price` (crossed-out), `list_price_kind` (`was` = Amazon's recent price, `list` = RRP or noon seller price), `in_stock`; `brand`, `seller`, `stock_left`, `recent_sales`, `rating`, `reviews`, `badges` (comma list: `limited_deal`, `mega_deal`, `selling_fast`, `lowest_30d`, `lowest_365d`, `bestseller`); `grp` (watch group); `first_seen_at`, `last_seen_at`, `last_change_at` |
| `price_history` | `id`; index `(product_id, observed_at)` and `(observed_at)` | `product_id`, `price`, `list_price`, `in_stock`, `observed_at`. One row per change; the row's values hold until the next row. Moves under 1% (`db.PRICE_NOISE`) are not changes: `products.price` keeps the last recorded value so a slow creep is recorded once it adds up, and it only moves when the price itself moved (a list-price-only change writes a row repeating the stored price), so `products.price` is always the price at `last_change_at` |
| `jobs` | `id`; unique `(kind, site, target)`; index `(site, next_due_at)` | `kind` (`plan`/`crawl`), `target` (slice key), `root`, `grp`, `expected_total`, `oversized`; `base_interval_s` (from the watch), `interval_s` (adaptive), `next_due_at`; lease: `lease_worker`, `lease_ip`, `lease_token`, `lease_expires_at`; current run: `run_count`, `run_changes` (drops, restocks and new products: what speeds the slice up); history: `failures`, `last_error`, `last_done_at`, `last_count`, `last_changes` |
| `workers` | `name` | `last_seen_at`, `last_ip`, `sites`, `version`, `pages`, `jobs_done` |
| `worker_sites` | `(worker, site)` | `last_ok_at`, `last_error_at`, `last_error`, `pages`, `products`, `blocks` |
| `blocks` | `(site, ip)` | `strikes`, `until`, `reason`, `updated_at`; deleted on the IP's next successful page |
| `alerts` | `id`; indexes on product, dedupe key, created | `product_id` (null for system), `kind`, `dedupe_key`, `price`, `reference_price`, `drop_pct`, `score`, `message` (final Arabic HTML), `created_at`, `sent_at` (= handed to recipients), `error` (`cancelled: price not confirmed` when a held alert's price reverted), `confirm_until` (held until then or until the product is seen again) |
| `deliveries` | `id`; unique `(alert_id, chat_id)` | `sent_at`, `attempts`, `next_attempt_at`, `error` |
| `subscribers` | `chat_id` | `name`, `username`, `status` (`pending`, `active`, `rejected`, `removed`, `stopped`, `blocked`), `is_admin` (the owner), `is_manager` (helper admin: also gets system alerts and the daily report), `approved_at` |
| `events` | `id`; indexes on time and source | `at`, `source`, `level`, `message`; pruned after 7 days |
| `kv` | `key` | `telegram_offset` (last Telegram update handled) |
| `product_codes` | `product_id` (cascade delete) | The codes read from one title, kept so a pass re-reads only changed titles: `title_hash`, `version`, `heads`, `shop_brand`, `own`, `mentions`, `before`, `capacity`, `used`, `accessory` ([MATCHING.md](MATCHING.md)) |
| `product_code_index` | `(product_id, code)`; index `(code)` | One row per code of a product (`rank`), for the SQL counts |
| `models` | `model` (`<brand> <code>[ <capacity>]`); index `(status, sites)` | One product across shops: `kind` (`gtin`, `mpn`, `line`, `similar`, `manual`), `code`, `capacity`, `brand`, `status` (`ok`, `probable`, `conflict`, `spec`), `products`, `sites`, `site_list`, `low`/`median`/`high`, `note`, `version`, `updated_at`. Rebuilt hourly |
| `product_models` | `product_id` (cascade delete); index `(model)` | `model`, `status` (`model`, `no_code`, `accessory`, `used`, `bundle`, `ambiguous_brand`, `ambiguous_variant`, `price_outlier`, `split`), `brand`, `brand_from`, `codes`, `note`, `version`, `updated_at` |
| `product_barcodes` | `(product_id, gtin)` (cascade delete); index `(gtin)` | `source` (`feed`; `page` later), `seen_at`. Written by the page upload when a listing carries `gtins`; read only by matching |
| `product_details` | `product_id` (cascade delete) | What the product's own page gave: `status` (ok, gone, failed), `title` (re-read when it changes), `gtins`, `mpns`, `codes` (normalised), `model_name`, `specs` (JSON) |
| `detail_queue` | `(site, product_id)` | The next product pages to read per shop, refilled by every matching pass |
| `detail_state` | `(site, ip)` | Pace and pause of page reading: `last_at`, `day`, `count`, `paused_until`, `reason` |
| `crawl_pauses` | `id` | `worker` and `site` (`*` = all), `pages` (product pages stop too), `until` (always set, at most a week), `reason`, `created_at`. The lease skips paused shops for that device; a shop paused for every device that crawls it raises no "stalled" alarm and shows as "paused by hand" in health |
| `match_overrides` | `(a, b)` (cascade delete) | `verdict` (`same`: b joins a's model; `different`: b leaves it), `note`, `created_at`. Set from the web UI product page or `deals models --same/--different/--forget` |

**Adding a migration:** append a new SQL string to `MIGRATIONS` (never edit an old one), use
`ALTER TABLE ... ADD COLUMN` or new tables, rehearse it on a copy (`make db`, then `db.connect()` on a copy
of `data/server.db`), take a backup on the server, then `make deploy`.

## Coordinator API reference

Base address `http://207.154.206.127:8787` (port 8787 open in the firewall). Worker endpoints need
`Authorization: Bearer <worker token>`; the token decides the device name. Read-only views ("viewer") accept the UI cookie, `?token=` or a Bearer header with the
`admin_token` or any worker token, and need no token when called from the server itself.

| Method and path | Who | Body | Answer |
| --- | --- | --- | --- |
| `GET /health` | anyone | | `{"ok": true, "version"}` |
| `POST /api/lease` | worker | `{"sites": [...], "worker_sites": [...], "version"}` | `{"job": {id, kind, site, target, root, group, lease_token, expected_total, min_delay, max_delay}}` or `{"job": null, "retry_after": 5-60}` |
| `POST /api/jobs/{id}/page` | worker | `{lease_token, page, listings: [Listing], done}` | `{ok, changes, deals}` |
| `POST /api/jobs/{id}/plan` | worker | `{lease_token, slices: [{target, total, oversized}]}` | `{ok, slices}` |
| `POST /api/jobs/{id}/blocked` | worker | `{lease_token, reason}` | `{ok, paused_until}` |
| `POST /api/jobs/{id}/failed` | worker | `{lease_token, reason}` | `{ok}` |
| `POST /api/jobs/{id}/release` | worker | `{lease_token, reason}` | `{ok}` (sent on shutdown) |
| `POST /api/logs` | worker | `{events: [{at, level, message}]}` (max 500) | `{ok}` |
| `GET /api/devices` | viewer | | devices, per-shop states, shop totals, failing checks |
| `GET /api/events?source=&level=&after_id=&limit=` | viewer | | `{events: [{id, at, source, level, message}]}` |
| `GET /dashboard?source=&level=` | viewer | | HTML page |
| `GET /ui`, `GET/POST /ui/login`, `GET /ui/logout` | anyone / viewer | `token` (form or one-click `?token=`) | The web UI; login sets the HttpOnly cookie `deals_ui` (30 days) |
| `GET /api/overview` | viewer | | totals, health checks, latest deals, devices, shops, server load and RAM, live revision, `admin` (is this the admin token) |
| `GET /api/alerts?days=&kind=&limit=` | viewer | | recent alerts with delivery counts and the Telegram text |
| `GET /api/jobs?limit=` | viewer | | jobs, failing first then running |
| `GET /api/products?q=&site=&grp=&sort=&in_stock=&changed_since=&limit=&offset=`, `GET /api/products/{id}` | viewer | | product search (`changed_since`: last change at or after this UTC time); one product with stats and history |
| `GET /api/queries`, `GET /api/queries/{name}?hours=&grp=&site=&q=&sort=&desc=&limit=&offset=` | viewer | | saved reports; `q` = words that must all be in the title |
| `GET /api/subscribers` | viewer | | everyone but removed people, join requests first, messages in 7 days; `admin` |
| `GET /api/rules` | viewer | | alert rules, schedule and watched groups from the config (read-only) |
| `POST /api/subscribers/{chat_id}/{action}` | admin token | | `approve`, `reject`, `remove`, `promote`, `demote` (as the bot's buttons; tells the person on Telegram). 403 for other tokens, 409 if already decided |
| `POST /api/report` | admin token | | queue the daily report to the admins now |

Responses over 2 KB are gzipped (the web UI page is about 80 KB, 23 KB compressed); the page is one static
string, and lists are paged in SQL, so the UI costs the 1 GB droplet next to nothing at rest.

A `Listing` in JSON: `site`, `sku`, `title`, `url`, `price`, `list_price`, `currency`, `in_stock`, and
optional `list_price_kind`, `brand`, `stock_left`, `recent_sales`, `rating`, `reviews`, `badges` (list), `seller`.

| Error | Meaning |
| --- | --- |
| 401 | Unknown token |
| 403 | A change from the web UI made with a worker token (only the admin token may) |
| 409 | Lease lost: expired and given to another worker, or the job was removed by a config change. The worker drops the job |
| 422 | Bad body, a listing for another shop, or a plan sent for a crawl job |

The interactive schema is at `/docs` on the same address (FastAPI's built-in page).

## Worker internals

A worker is one process (`deals worker`) configured by `DEALS_COORDINATOR`, `DEALS_TOKEN`, `DEALS_SITES`
and `DEALS_DATA_DIR` (or the matching flags). It keeps no database; its only local file is the Amazon cookie jar.

**Threads.**

| Thread | Does |
| --- | --- |
| main | Starts the others, waits, handles Ctrl+C / SIGTERM by setting the stop event |
| one per shop (`amazon_eg`, `noon_eg`, `jumia_eg`, `cairosales_eg`, `2b_eg`, `btech_eg`, `dream2000_eg`, `carrefour_eg`) | `_loop`: lease, crawl or plan, pause, repeat. Each holds its own shop adapter and HTTP session |
| `log-shipper` | Every 10 s posts up to 500 buffered log lines to `/api/logs`; keeps up to 1,000 while the server is unreachable |

**Timeouts and retries.**

| What | Value |
| --- | --- |
| Shop request timeout | 30 s |
| Page retries on network errors | after 5, 20, 60 s, then the job is reported failed |
| Coordinator calls | retried forever with backoff 2 s doubling to 60 s; stops only when shutting down. Logged as INFO for the first 60 s of an outage, WARNING after, plus "reachable again" |
| Pause between pages and between jobs | random within the shop's `min_delay`..`max_delay` (Amazon 2-4 s, noon 1-2 s, Jumia 1.5-3 s, Cairo Sales 1.5-3 s, 2B 1-2 s, B.TECH 1-2 s, Dream 2000 1-2 s, Carrefour 2-4 s) |
| Nothing due | waits the `retry_after` the coordinator sends (5-60 s) |
| Lease | 5 min, renewed by each page post |

**Amazon cookies.** `AmazonEG.__init__` loads `amazon_eg_cookies.json` if present. When a response looks
like a challenge, `_bootstrap_cookies()` launches headless Chromium (Playwright), opens the English
homepage, polls every second for up to 30 s until no challenge markers remain, keeps only Amazon cookies,
saves them and builds a fresh `curl_cffi` session that impersonates Chrome's TLS fingerprint. Chromium runs
only for this, a few seconds per day.

**Planning on a worker.** Plan jobs run `planner.plan()` through `_RetryingSite`, so probes get the same
retries; every probed page is posted as a normal page (so its products are recorded), and the slice list
goes to `/api/jobs/{id}/plan`.

**Shutdown.** SIGTERM or Ctrl+C sets the stop event: the current fetch finishes, the job is released with
`/release`, the log shipper flushes once, threads exit. If the process is killed instead, the lease simply
expires after 5 minutes.

**How it runs.** On the Mac: launchd agent `~/Library/LaunchAgents/com.deals.worker.plist` (`RunAtLoad`,
`KeepAlive`, restart throttled to 30 s), environment from `worker.env`. On the droplet: systemd
`deals-worker.service` (`Restart=always`, 700 MB memory cap, read-only system except `/var/lib/deals`).
Deploys stop this worker, restart the coordinator, wait for `/health`, then start the worker, so it never
sees the restart.

## Configuration reference

The coordinator reads `/etc/deals/config.toml` at start; after any edit run `systemctl restart deals-coordinator`.
Workers need no config file: they receive delays with each job. `config.example.toml` in the repo is the
commented template.

| Section and key | Default | Effect |
| --- | --- | --- |
| `[server] host` | `127.0.0.1` | `0.0.0.0` on the server so remote workers can connect |
| `[server] port` | 8787 | API port |
| `[server] data_dir` | `data` | Database, backups (relative to the config file) |
| `[server] admin_token` | empty | Secret for the web UI login (`deals token` makes one) |
| `[workers] NAME = "token"` | | One line per device; the name shows in devices and logs. `deals token` makes one |
| `[telegram] bot_token`, `chat_id` | empty | The bot and the admin chat (`make telegram TOKEN=...` fills both) |
| `[alerts] min_drop` | 0.20 | Real price drop threshold versus typical (3+ days of history) |
| `[alerts] early_min_drop` | 0.30 | Threshold for early drops (1 to 3 days of history) |
| `[alerts] atl_min_drop` | 0.15 | All-time-low threshold (14+ days of history) |
| `[alerts] atl_min_history_days` | 14 | History needed for all-time-low alerts |
| `[alerts] price_error_drop` | 0.60 | Labelled possible price error above this |
| `[alerts] min_history_days` | 3 | Below this, the early-drop or new-listing path is used |
| `[alerts] early_min_history_days` | 1 | From this much history, a drop of `early_min_drop` below everything seen alerts early (low confidence) |
| `[alerts] max_share_at_or_below` | 0.15 | No alert when the product was at or below this price more than 15% of the time |
| `[alerts] new_product_min_drop` | 0.30 | Claimed discount needed for new listings |
| `[alerts] list_max_claimed_drop` | 0.60 | A bigger claim against an RRP ("list") price is rejected |
| `[alerts] confirm_low_confidence` | true | Hold low-confidence alerts until the product is seen again at that price. The live config sets it to `false` since 27 Oct 2026 (0 of 105 held alerts were cancelled; the hold cost 14 min on average) |
| `[alerts] confirm_early_drops` | false | Also hold early drops (by default they are sent at once; only new listings wait) |
| `[alerts] confirm_minutes` | 90 | ...but send anyway after this long if nobody rechecked it (the hold is at least 3x the group's interval + 10 min, so a slow group can actually be rechecked) |
| `[alerts] realert_after_days` | 7 | No repeat alert within this many days... |
| `[alerts] realert_extra_drop` | 0.05 | ...unless the price fell this much more |
| `[alerts] show_other_shops` | true | End each alert with the same product's price on the other shops ([MATCHING.md](MATCHING.md)); text only, appended after the decision |
| `[schedule] min_interval_minutes` | 10 | Fastest revisit of a busy slice |
| `[schedule] max_interval_factor` | 3.0 | Quiet slices slow down to group interval times this |
| `[schedule] feed_max_interval_minutes` | 10 | Cap for shop deal-feed roots (`deals:`, `p_n_deal_type`, noon's `min_offer_price`, Jumia's `flash-sales`) |
| `[schedule] replan_days` | 7 | How often categories are re-split |
| `[schedule] lease_minutes` | 5 | Lease length, renewed per page |
| `[sites.SHOP] min_delay`, `max_delay` | 2.0, 4.0 | Seconds between requests per worker (config sets noon and 2B to 1-2, Jumia and Cairo Sales to 1.5-3) |
| `[[watch]] name` | required | Group name used in the database (keep it stable) |
| `[[watch]] label` | name | Display name in Arabic messages |
| `[[watch]] interval_minutes` | 60 | Starting revisit interval for its slices |
| `[[watch]] max_interval_minutes` | interval x `max_interval_factor` | Cap on how slow a quiet slice of this group may get. The worst-case time to notice a flash sale; 15 min for the top groups, 30 for TVs, fridges, laptops, monitors, 90 for cooking & dishwashers, 120 for kitchen appliances |
| `[[watch]] min_price` | none | EGP. Crawl price bands start here, and a *new* product below it (or without a price) is not recorded; known products keep being updated |
| `[[watch]] require_words`, `exclude_words` | none | Title filters for new products (case-insensitive substrings): the title must contain one of `require_words` and none of `exclude_words`; a title containing "console" is not excluded (bundles) unless it also contains " for " (an accessory for a console). The title is padded with spaces, so a word with a trailing space (`"stand "`) also matches at its end but not inside "Standard"; a controller count ("1 Controllers", B.TECH console titles) is ignored when looking for exclude words. For noisy sources such as searches |
| `[[watch]] min_price` | none | EGP floor for crawling and alerting |
| `[[watch]] min_drop` | none | This group's own price-drop threshold |
| `[[watch]] amazon_eg`, `noon_eg`, `jumia_eg`, `cairosales_eg`, `2b_eg`, `btech_eg`, `dream2000_eg`, `carrefour_eg` | | One root or a list: `category:<id>` or `search:<words>`, optionally `\|price:<min>-<max>` (Jumia and Cairo Sales ids are URL slugs, e.g. `televisions`, `tvs-screens/oled`; 2B ids are Magento's numbers, e.g. `131`; Carrefour ids are case-sensitive codes, e.g. `NFEGY4080000`). Deal-feed roots (fast lanes) use the shop's own filter syntax inside the id: Amazon `category:<node>,p_n_deal_type:26901036031` and `deals:<node>` (the Today's Deals JSON API), noon `category:<path>?f[min_offer_price]=7_days`, Jumia `category:flash-sales` (see GUIDE, Algorithm 3) |

Changing a watch's `min_price` or roots creates new plan jobs and deletes the old ones with their slices;
product history is kept.

## How to improve it

Every change follows the same loop: write a test that shows what you want, change the code, `make test`,
`make deploy`, restart the Mac worker if worker or site code changed, `make health`, then check the real
data with `make sql` or `make devices`.

**Add a shop** (Jumia was added exactly this way on 26 October 2026, Cairo Sales, 2B, B.TECH and Dream 2000 on 27 October, Carrefour on 28 October;
[JUMIA.md](JUMIA.md), [CAIROSALES.md](CAIROSALES.md), [2B.md](2B.md), [BTECH.md](BTECH.md), [DREAM2000.md](DREAM2000.md) and [CARREFOUR.md](CARREFOUR.md) show the analysis to do first: access,
URL scheme or API, page caps, empty-result behaviour, category tree, signals):

1. Create `src/deals/sites/<shop>.py` with a class having `name`, `max_slice_total` (the most products the
   shop lets you page through) and `fetch_page(slice, page) -> PageResult`. Return `total` when the shop
   shows an exact count, `subcategories` for category slices (for `deals categories`), and raise `Blocked` on
   a captcha. The planner splits by price, so the price filter must be exact (check that its ends are
   inclusive). Make sure an empty slice comes back as an empty page and not as some other listing the shop
   redirects to.
2. Register it in `sites/__init__.py` (`SITES`); add its Arabic name to `alerts.SITE_NAME`, its roots to
   `cli.cmd_categories`, and an option to the two shop selects in `webui.py`.
3. Save one real listing page as `tests/fixtures/<shop>_category.html.gz` and add a parser test next to the
   others in `tests/test_parsers.py`.
4. Add its roots to the `[[watch]]` groups and `[sites.<shop>]` delays in the config (deploy the code
   first: the old code rejects unknown keys); add it to a worker's `DEALS_SITES`.
5. `deals categories <shop> <id>` and `deals plan ...` help find and check category ids.

**Change or add a deal rule:** all rules live in `pricing.evaluate()`; statistics in `compute_stats()`. Add
a field to `Deal` for any new fact, render it in `alerts.format_deal()`, and add a test in
`tests/test_pricing.py` with a hand-made history (the `series()` helper builds one in one line). Thresholds
that might need tuning belong in `AlertRules` (`config.py`) so they can be changed without code.

**Change a message:** deal alerts are built in `alerts.format_deal()`, with the word lists `KIND_TITLE`,
`SIGNAL_TEXT`, `SITE_NAME`, `CONFIDENCE` at the top of `alerts.py`. Bot replies are the constants at the top
of `bot.py`; the daily report is `health.daily_report()`.

**Read a new signal from listing cards:** add a field to `Listing` (`models.py`), parse it in the shop
adapter, add it to `ListingIn` (`coordinator.py`) and to `db.record()` plus a migration for its column, then
use it in `pricing.py`.

**Add a health check:** append a `Check` in `health.run_checks()` with English detail and an Arabic line; it
then appears in `make health`, the dashboard and the daily report automatically.

**Add a watch group:** only config, no code: a `[[watch]]` block with `name`, `label`, `interval_minutes`,
`min_price` and roots; restart the coordinator.

**Tests that exist** (run in under a second): parsers against saved real pages; slice keys and price
splitting; the planner covering every product of a fake shop; time-weighted statistics and every deal rule
including the fake-discount case; the coordinator API (leases, one job per IP, blocks, lost leases,
empty-page detection, alert creation and no-repeat); the bot (approval, stop and return, blocked users,
fan-out); health checks and the daily report; device states and logs; and an end-to-end run of a real HTTP
coordinator with a real worker crawling a fake shop.
