# How it works in detail

Code-level reference for changing and improving the system. Start with [GUIDE.md](GUIDE.md) for the
concepts and algorithms; this document maps them onto the code.

## Code map

The whole system is about 3,400 lines of Python in `src/deals/`, plus the tests in `tests/`. Each module
owns one job.

| Module | Lines | Owns | Calls |
| --- | --- | --- | --- |
| `models.py` | 103 | The shared data shapes: `Listing` (one product on one page), `Slice` (a category plus optional price range, with its text key), `PageResult`, `Category`, badge names, the `Blocked` and `UnknownTarget` errors | nothing |
| `config.py` | 126 | Reading `config.toml` into `Config`, `AlertRules`, `Schedule`, `SiteSettings`, `Watch` | models |
| `sites/amazon_eg.py` | 198 | Fetching and parsing amazon.eg listing pages; cookies from headless Chromium | models, base |
| `sites/noon_eg.py` | 139 | Fetching and parsing noon's catalog JSON; per-seller keys | models |
| `sites/base.py` | 35 | The `Site` interface every shop implements; money and count parsing | models |
| `planner.py` | 93 | Splitting a root into slices under the shop's limit | models |
| `db.py` | 349 | Schema and migrations; `record()` (upsert a product, append history on change); backups | models |
| `scheduler.py` | 241 | The job queue: leases, intervals, blocks, failures, worker bookkeeping, events | db, planner |
| `pricing.py` | 222 | Price statistics and the deal rules; pure functions, no database | config, models |
| `alerts.py` | 279 | Judging a changed product, writing alerts, formatting the Arabic message, the Telegram client and the delivery loop | db, pricing |
| `bot.py` | 191 | The Telegram conversation: /start, /stop, approvals, subscribers | alerts, db |
| `health.py` | 200 | Health checks and the daily report | db, alerts |
| `monitor.py` | 166 | Device states, events (logs), the dashboard page | db, scheduler |
| `webui.py` | 243 | The web UI page (HTML + JS) and its read-only queries | health, monitor, alerts |
| `coordinator.py` | 363 | The FastAPI app (all endpoints) and the watchdog thread | everything above |
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
    `scheduler.finish_crawl()` halves the interval if anything changed or multiplies it by 1.25 if not,
    clamps it between 10 minutes and 3 times the group interval, adds plus or minus 10% jitter, sets
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
30-second busy timeout. Only the coordinator writes; its request handlers share one connection on the event
loop, and each background thread (alert sender, watchdog, bot, event writer) opens its own.

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

**Tables and columns** (prices are integer piastres; times are UTC ISO text, which sorts correctly as text):

| Table | Key | Columns and meaning |
| --- | --- | --- |
| `products` | `id`; unique `(site, sku)` | `title`, `url`, `currency`; `price` (None when no price shown), `list_price` (crossed-out), `list_price_kind` (`was` = Amazon's recent price, `list` = RRP or noon seller price), `in_stock`; `brand`, `seller`, `stock_left`, `recent_sales`, `rating`, `reviews`, `badges` (comma list: `limited_deal`, `mega_deal`, `selling_fast`, `lowest_30d`, `lowest_365d`, `bestseller`); `grp` (watch group); `first_seen_at`, `last_seen_at`, `last_change_at` |
| `price_history` | `id`; index `(product_id, observed_at)` and `(observed_at)` | `product_id`, `price`, `list_price`, `in_stock`, `observed_at`. One row per change; the row's values hold until the next row. Moves under 1% (`db.PRICE_NOISE`) are not changes: `products.price` keeps the last recorded value so a slow creep is recorded once it adds up |
| `jobs` | `id`; unique `(kind, site, target)`; index `(site, next_due_at)` | `kind` (`plan`/`crawl`), `target` (slice key), `root`, `grp`, `expected_total`, `oversized`; `base_interval_s` (from the watch), `interval_s` (adaptive), `next_due_at`; lease: `lease_worker`, `lease_ip`, `lease_token`, `lease_expires_at`; current run: `run_count`, `run_changes`; history: `failures`, `last_error`, `last_done_at`, `last_count`, `last_changes` |
| `workers` | `name` | `last_seen_at`, `last_ip`, `sites`, `version`, `pages`, `jobs_done` |
| `worker_sites` | `(worker, site)` | `last_ok_at`, `last_error_at`, `last_error`, `pages`, `products`, `blocks` |
| `blocks` | `(site, ip)` | `strikes`, `until`, `reason`, `updated_at`; deleted on the IP's next successful page |
| `alerts` | `id`; indexes on product, dedupe key, created | `product_id` (null for system), `kind`, `dedupe_key`, `price`, `reference_price`, `drop_pct`, `score`, `message` (final Arabic HTML), `created_at`, `sent_at` (= handed to recipients), `error` (`cancelled: price not confirmed` when a held alert's price reverted), `confirm_until` (held until then or until the product is seen again) |
| `deliveries` | `id`; unique `(alert_id, chat_id)` | `sent_at`, `attempts`, `next_attempt_at`, `error` |
| `subscribers` | `chat_id` | `name`, `username`, `status` (`pending`, `active`, `rejected`, `stopped`, `blocked`), `is_admin`, `approved_at` |
| `events` | `id`; indexes on time and source | `at`, `source`, `level`, `message`; pruned after 7 days |
| `kv` | `key` | `telegram_offset` (last Telegram update handled) |

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
| `GET /api/overview` | viewer | | totals, health checks, devices, shops, live revision |
| `GET /api/alerts?days=&kind=&limit=` | viewer | | recent alerts with delivery counts |
| `GET /api/jobs?limit=` | viewer | | jobs, failing first then running |
| `GET /api/products?q=&site=&limit=`, `GET /api/products/{id}` | viewer | | product search; one product with stats and history |

A `Listing` in JSON: `site`, `sku`, `title`, `url`, `price`, `list_price`, `currency`, `in_stock`, and
optional `list_price_kind`, `brand`, `stock_left`, `recent_sales`, `rating`, `reviews`, `badges` (list), `seller`.

| Error | Meaning |
| --- | --- |
| 401 | Unknown token |
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
| one per shop (`amazon_eg`, `noon_eg`) | `_loop`: lease, crawl or plan, pause, repeat. Each holds its own shop adapter and HTTP session |
| `log-shipper` | Every 10 s posts up to 500 buffered log lines to `/api/logs`; keeps up to 1,000 while the server is unreachable |

**Timeouts and retries.**

| What | Value |
| --- | --- |
| Shop request timeout | 30 s |
| Page retries on network errors | after 5, 20, 60 s, then the job is reported failed |
| Coordinator calls | retried forever with backoff 2 s doubling to 60 s; stops only when shutting down. Logged as INFO for the first 60 s of an outage, WARNING after, plus "reachable again" |
| Pause between pages and between jobs | random within the shop's `min_delay`..`max_delay` (Amazon 2-4 s, noon 1-2 s) |
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
| `[alerts] min_drop` | 0.30 | Real price drop threshold versus typical |
| `[alerts] atl_min_drop` | 0.20 | All-time-low threshold |
| `[alerts] atl_min_history_days` | 14 | History needed for all-time-low alerts |
| `[alerts] price_error_drop` | 0.60 | Labelled possible price error above this |
| `[alerts] min_history_days` | 3 | Below this, the early-drop or new-listing path is used |
| `[alerts] early_min_history_days` | 1 | From this much history, a 30% drop below everything seen alerts early (low confidence) |
| `[alerts] max_share_at_or_below` | 0.15 | No alert when the product was at or below this price more than 15% of the time |
| `[alerts] new_product_min_drop` | 0.30 | Claimed discount needed for new listings |
| `[alerts] list_max_claimed_drop` | 0.60 | A bigger claim against an RRP ("list") price is rejected |
| `[alerts] confirm_low_confidence` | true | Hold low-confidence alerts until the product is seen again at that price |
| `[alerts] confirm_minutes` | 90 | ...but send anyway after this long if nobody rechecked it |
| `[alerts] realert_after_days` | 7 | No repeat alert within this many days... |
| `[alerts] realert_extra_drop` | 0.05 | ...unless the price fell this much more |
| `[schedule] min_interval_minutes` | 10 | Fastest revisit of a busy slice |
| `[schedule] max_interval_factor` | 3.0 | Quiet slices slow down to group interval times this |
| `[schedule] replan_days` | 7 | How often categories are re-split |
| `[schedule] lease_minutes` | 5 | Lease length, renewed per page |
| `[sites.SHOP] min_delay`, `max_delay` | 2.0, 4.0 | Seconds between requests per worker (config sets noon to 1-2) |
| `[[watch]] name` | required | Group name used in the database (keep it stable) |
| `[[watch]] label` | name | Display name in Arabic messages |
| `[[watch]] interval_minutes` | 60 | Starting revisit interval for its slices |
| `[[watch]] min_price` | none | EGP floor for crawling and alerting |
| `[[watch]] min_drop` | none | This group's own price-drop threshold |
| `[[watch]] amazon_eg`, `noon_eg` | | One root or a list: `category:<id>` or `search:<words>`, optionally `\|price:<min>-<max>` |

Changing a watch's `min_price` or roots creates new plan jobs and deletes the old ones with their slices;
product history is kept.

## How to improve it

Every change follows the same loop: write a test that shows what you want, change the code, `make test`,
`make deploy`, restart the Mac worker if worker or site code changed, `make health`, then check the real
data with `make sql` or `make devices`.

**Add a shop** (for example Jumia):

1. Create `src/deals/sites/jumia_eg.py` with a class having `name`, `max_slice_total` (the most products the
   shop lets you page through) and `fetch_page(slice, page) -> PageResult`. Return `total` when the shop
   shows an exact count, `subcategories` for category slices, and raise `Blocked` on a captcha.
2. Register it in `sites/__init__.py` (`SITES`).
3. Save one real listing page as `tests/fixtures/jumia_category.html.gz` and add a parser test next to the
   others in `tests/test_parsers.py`.
4. Add its roots to the `[[watch]]` groups and `[sites.jumia_eg]` delays in the config; add it to a worker's `DEALS_SITES`.
5. `deals categories jumia_eg <id>` and `deals plan ...` help find and check category ids.

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
