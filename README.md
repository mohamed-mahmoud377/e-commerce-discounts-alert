# deals: e-commerce price tracker and real-discount alerts

`deals` crawls whole categories on **amazon.eg**, **noon.com (Egypt)**, **jumia.com.eg**, **cairosales.com**, **btech.com**, **dream2000.com**,
**carrefouregypt.com** and **2b.com.eg**, keeps the
price history of every product, and sends you a **Telegram** message when a price
is genuinely low, judged against the product's own history rather than the
"was" price a shop shows.

It runs as one **coordinator** (database, scheduler, alerts) and any number of
**workers** (the crawlers) on your own machines: the server, your Mac, a friend's PC.
Each worker only takes jobs for the sites its internet connection can reach.

- **New here? Start with [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)**: get alerts, run a worker, try it on one computer, or run your own server
- **Running a worker on your computer for someone's server: [docs/RUN_A_WORKER.md](docs/RUN_A_WORKER.md)** (exact steps, expected outputs, what to do when something fails)
- Category ids for every shop and what we learned about them: [docs/CATEGORIES.md](docs/CATEGORIES.md); Jumia in depth: [docs/JUMIA.md](docs/JUMIA.md)
- **Every useful command in one place: [docs/COMMANDS.md](docs/COMMANDS.md)**
- The complete guide (server, database, running it, every algorithm): [docs/GUIDE.md](docs/GUIDE.md)
- One product from first sighting to Telegram alert, with worked examples (how the discount is calculated, why an alert did or didn't go out): [docs/PRODUCT_FLOW.md](docs/PRODUCT_FLOW.md)
- Monitoring, health checks, devices, logs and the runbook: [docs/MONITORING.md](docs/MONITORING.md)
- Code-level detail for improving it (modules, one page end to end, schema, API, config): [docs/INTERNALS.md](docs/INTERNALS.md)
- First review of the live system and ranked improvements: [docs/REVIEW-2026-10-25.md](docs/REVIEW-2026-10-25.md)
- What to improve next: [docs/ROADMAP.md](docs/ROADMAP.md)

```
            ┌──────────── droplet (207.154.206.127) ─────────────┐
            │  deals serve  ── SQLite ── Telegram alerts         │
            │      ▲  :8787 (token auth)                         │
            │      │                                             │
            │  deals worker (amazon_eg, fallback)                │
            └──────┼─────────────────────────────────────────────┘
                   │  lease job / report pages / blocked / failed
      ┌────────────┼─────────────────────┐
  your Mac (launchd)            friend's PC (optional)
  deals worker                  deals worker
  noon, amazon, jumia,          any shop it can reach
  cairosales, 2b, btech, dream2000,  (home connections: all eight;
  carrefour                     cloud: amazon, dream2000, carrefour)
```

## What it tracks

The groups live in `config.toml` (see [config.example.toml](config.example.toml)):
TVs, refrigerators, air conditioners, gaming consoles, phones, tablets, laptops, desktops, monitors, PC components,
headphones, kitchen machines (fryers, coffee machines, blenders, microwaves), other kitchen
appliances, and cooking & dishwashers, each on all three sites, with a
minimum price so cables and cases don't flood the crawl.

## Quick start (everything on your Mac)

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m playwright install chromium   # solves amazon.eg's bot challenge once per session

cp config.example.toml config.toml                 # edit watches / alert rules if you like
.venv/bin/deals local                              # coordinator + worker in one process
```

In another terminal:

```bash
.venv/bin/deals status          # workers, blocks, backlog, freshness
.venv/bin/deals alerts          # deal alerts from the last 24h
.venv/bin/deals movers          # biggest price drops/rises in the last 24h
.venv/bin/deals product amazon_eg B0BS1PRC4L   # one product: stats + full history
```

## Production setup (droplet + your Mac)

Everything is driven by `make` from your Mac:

| Command | What it does |
|---|---|
| `make ui` | **the web UI**, already logged in: status, devices, live logs, alerts, jobs, product search (`make ui-token` for your phone) |
| `make health` | **is everything working?** server services and logs, deployed code, this Mac's worker, database, crawling per site, Telegram delivery, disk, backups (✅/⚠️/❌) |
| `make devices` | every connected device and its fetch state per shop (🟢 ok, 🔵 crawling, ⚪ idle, 🟠 blocked, 🔴 failing/offline) |
| `make dashboard` | the same in your browser, refreshing every 15 s, with everyone's logs (filter by device) |
| `make events` | live log lines from all devices and the server; `SOURCE=mac` or `LEVEL=warning` to filter |
| `make worker-log` | this Mac's worker log, live |
| `make report` | send the daily report to your Telegram now (it also arrives every day at 09:00 Cairo time) |
| `make test` | run the test suite |
| `make deploy` | tests → rsync code → install/upgrade on the server → restart services → health check (pushing to `main` does the same through GitHub Actions) |
| `make telegram TOKEN=...` | connect Telegram alerts (see below) |
| `make status` | `deals status` on the server |
| `make alerts` / `make movers` | recent alerts / price moves, from the server |
| `make logs` | follow the server logs |
| `make dbweb` / `make sql` / `make db` | look at the database (see below) |
| `make mac-worker-env` | fetch this Mac's worker token from the server into `worker.env` |
| `make install-mac-worker` | run the worker in the background on this Mac (starts at login, restarts on crash) |
| `make uninstall-mac-worker` | remove it |
| `make mac-worker` | run the worker in the foreground instead (Ctrl+C to stop) |

First deploy on a fresh Ubuntu server: `make deploy`. `scripts/remote-install.sh` creates a `deals`
system user, a virtualenv in `/opt/deals`, Chromium in `/opt/ms-playwright`,
`/etc/deals/config.toml` with fresh worker tokens, two systemd services
(`deals-coordinator`, `deals-worker`), and opens port 8787 in the firewall. Data lives in
`/var/lib/deals` (database, cookies, daily compressed backups in `backups/`, last 7 kept).

To change what is tracked or the alert rules, edit `/etc/deals/config.toml` on the server
and restart the coordinator: `ssh root@207.154.206.127 systemctl restart deals-coordinator`.
New watches are planned within a minute; removed ones are dropped.

### Telegram alerts (2 minutes, one time)

1. In Telegram, open **@BotFather**, send `/newbot`, choose a display name and a username ending in
   `bot`, and copy the **token** it gives you (looks like `123456789:AAH...`).
2. Open your new bot (BotFather links it) and press **Start**.
3. On your Mac, in this folder: `make telegram TOKEN=123456789:AAH...`

That saves the token on the server, finds your chat id, sends a "✅ connected" test message and
restarts the coordinator. If it says the bot has no messages yet, press Start in the bot and run it
again. Until Telegram is connected, alerts are still stored and visible with `make alerts`.

### Sharing alerts with friends

Send friends the bot link (t.me/Jerrys_deals_bot). When they press **Start**, you get a message with
**✅ موافقة / ❌ رفض** buttons; once approved they receive every deal alert (system warnings stay
yours). They can send `/stop` to pause and `/start` to come back without a new approval. Send
`/subscribers` to the bot to see who is subscribed, with buttons to **remove** someone (asks to confirm;
they can request again later) or make them a **helper admin** (⭐), who then also gets the system warnings and the daily report
(join requests stay yours). People who block the bot are dropped
automatically.

### Devices, fetch status and logs (API)

Every worker reports how each shop is going and ships its log lines to the coordinator, so one
place shows everything. Read-only endpoints (open from the server itself; from elsewhere add
`?token=<any worker token>` or an `Authorization: Bearer` header):

| Endpoint | Returns |
|---|---|
| `GET /dashboard` | the page `make dashboard` opens |
| `GET /api/devices` | JSON: each device (online, IP, version, pages) with per-shop `state` (`ok`, `crawling`, `idle`, `blocked`, `failing`, `offline`) and detail, per-shop totals, and any failing health checks |
| `GET /api/events?source=mac&level=warning&after_id=0` | JSON: log lines (kept 7 days) |

### Looking at the database

The only real database is on the server (`/var/lib/deals/deals.db`). Three ways in:

| Command | What you get |
|---|---|
| `make dbweb` | **live** database in your browser at http://localhost:8001: tables, filters, a SQL editor and the queries from `docs/queries.sql` as clickable pages. Read-only, through an SSH tunnel (nothing exposed to the internet). Ctrl+C to stop. |
| `make sql` | **live** read-only SQL shell on the server (`.tables`, `.schema products`, any SELECT; `.quit` to leave) |
| `make db` | downloads a snapshot to `data/server.db` to open in IntelliJ's Database tool (SQLite data source); run again to refresh |

Prices are stored in piastres (divide by 100 for EGP); times are UTC.

### Adding a worker on another machine (a friend's PC, a laptop)

1. On the server, add a token under `[workers]` (`deals token` generates one), e.g. `ahmed = "..."`,
   and restart the coordinator.
2. On the other machine: clone the repo, create the venv as in Quick start, then:
   ```bash
   DEALS_COORDINATOR=http://207.154.206.127:8787 DEALS_TOKEN=... DEALS_SITES=noon_eg,amazon_eg,jumia_eg,cairosales_eg,2b_eg,btech_eg,dream2000_eg,carrefour_eg \
     .venv/bin/deals worker
   ```
That's all. The coordinator never gives two workers on the same internet connection the
same site at once, so a friend on a different ISP adds real capacity, but two laptops at
home don't.

## Finding category ids

```bash
.venv/bin/deals categories amazon_eg                    # known roots
.venv/bin/deals categories amazon_eg 18018102031        # children of Electronics
.venv/bin/deals categories noon_eg electronics-and-mobiles
.venv/bin/deals categories jumia_eg electronic-television-video
.venv/bin/deals categories cairosales_eg tvs-screens
.venv/bin/deals categories 2b_eg 2370                    # 2B uses numeric ids
.venv/bin/deals categories btech_eg large-home-appliances # B.TECH: URL slugs
.venv/bin/deals categories dream2000_eg mobiles          # Dream 2000: Shopify collection handles
.venv/bin/deals plan noon_eg "category:electronics-and-mobiles/television-and-video/televisions|price:3000-"
.venv/bin/deals crawl amazon_eg "category:21832982031|price:3000-" --pages 2   # check the parser
```

## Project layout

```
src/deals/
  sites/amazon_eg.py, noon_eg.py, jumia_eg.py,  fetch + parse one listing page per site
  cairosales_eg.py, twob_eg.py, btech_eg.py, dream2000_eg.py, carrefour_eg.py
  planner.py      split a category into slices each site lets us page through completely
  scheduler.py    job queue: leases, adaptive revisit intervals, per-IP block cooldowns
  coordinator.py  HTTP API for workers + watchdog (stalls, offline workers, backups)
  worker.py       lease → crawl → report loop, one thread per site, failure handling
  pricing.py      time-weighted price statistics and deal rules
  alerts.py       alert outbox, message formatting, Telegram delivery with retries
  db.py           SQLite schema and migrations
  cli.py          the `deals` command
deploy/           systemd units
scripts/          deploy.sh, remote-install.sh, Mac worker helpers
tests/            parsers (real saved pages), pricing, planner, coordinator, worker end-to-end
```
