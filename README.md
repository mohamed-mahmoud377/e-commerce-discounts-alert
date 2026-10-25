# deals: e-commerce price tracker and real-discount alerts

`deals` crawls whole categories on **amazon.eg** and **noon.com (Egypt)**, keeps the
price history of every product, and sends you a **Telegram** message when a price
is genuinely low, judged against the product's own history rather than the
"was" price a shop shows.

It runs as one **coordinator** (database, scheduler, alerts) and any number of
**workers** (the crawlers) on your own machines: the server, your Mac, a friend's PC.
Each worker only takes jobs for the sites its internet connection can reach.

- How it works, step by step, and the deal-detection algorithms: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md)
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
  noon_eg + amazon_eg           noon_eg, amazon_eg
```

## What it tracks

The groups live in `config.toml` (see [config.example.toml](config.example.toml)):
TVs, refrigerators, air conditioners, phones, laptops, desktops, monitors, PC components,
headphones, kitchen appliances, and cooking & dishwashers, each on both sites, with a
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
| `make test` | run the test suite |
| `make deploy` | tests → rsync code → install/upgrade on the server → restart services → health check |
| `make telegram TOKEN=...` | connect Telegram alerts (see below) |
| `make status` | `deals status` on the server |
| `make alerts` / `make movers` | recent alerts / price moves, from the server |
| `make logs` | follow the server logs |
| `make mac-worker-env` | fetch this Mac's worker token from the server into `worker.env` |
| `make install-mac-worker` | run the worker in the background on this Mac (starts at login, restarts on crash) |
| `make uninstall-mac-worker` | remove it |
| `make mac-worker` | run the worker in the foreground instead (Ctrl+C to stop) |

First deploy on a fresh Ubuntu server: `make deploy`. `scripts/remote-install.sh` creates a `deals`
system user, a virtualenv in `/opt/deals`, Chromium in `/opt/ms-playwright`,
`/etc/deals/config.toml` with fresh worker tokens, two systemd services
(`deals-coordinator`, `deals-worker`), and opens port 8787 in the firewall. Data lives in
`/var/lib/deals` (database, cookies, daily backups in `backups/`, last 7 kept).

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

### Adding a worker on another machine (a friend's PC, a laptop)

1. On the server, add a token under `[workers]` (`deals token` generates one), e.g. `ahmed = "..."`,
   and restart the coordinator.
2. On the other machine: clone the repo, create the venv as in Quick start, then:
   ```bash
   DEALS_COORDINATOR=http://207.154.206.127:8787 DEALS_TOKEN=... DEALS_SITES=noon_eg,amazon_eg \
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
.venv/bin/deals plan noon_eg "category:electronics-and-mobiles/television-and-video/televisions|price:3000-"
.venv/bin/deals crawl amazon_eg "category:21832982031|price:3000-" --pages 2   # check the parser
```

## Project layout

```
src/deals/
  sites/amazon_eg.py, noon_eg.py   fetch + parse one listing page per site
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
