# Command cheat sheet

Every useful command in one place. `make` commands run from the project folder on your Mac
(`~/IdeaProjects/e-commerce-discounts-alert`); server commands run after `make ssh`.
What the output means: [MONITORING.md](MONITORING.md). How it all works: [GUIDE.md](GUIDE.md).

## Web UI (the easiest way)

Tabs: Overview, Devices, Logs (live), Alerts (with state: sent / held / cancelled), Jobs, Products (search,
group, sort, in-stock filter; click a product for its price chart, typical price, rarity, other sellers and
alerts), Queries (17 saved reports). Click any table header to sort by it.

A small read-only website served by the coordinator itself: nothing to install or run.

| What | How |
| --- | --- |
| Open it, already logged in | `make ui` |
| Open it from a phone or another computer | `make ui-token` (copies the token), then open http://207.154.206.127:8787/ui and paste it |
| Log out | the "log out" link (top right) |

Tabs: **Overview** (status, totals, every health check, devices, shops), **Devices** (each device's state per
shop), **Logs** (live, from every device and the server; filter by device and level), **Alerts** (what was
sent, to how many people), **Jobs** (every slice: failing first, then running; interval, last result, error),
**Products** (search titles; click one for its price history and typical price).

How the login works: the secret is `admin_token` in `/etc/deals/config.toml` (worker tokens also work). After
logging in it is kept in a cookie for 30 days. To change it: `make ssh`, then
`sed -i "s/^admin_token = .*/admin_token = \"$(/opt/deals/.venv/bin/deals token)\"/" /etc/deals/config.toml`
and `make restart-server` (everyone is logged out). The UI only reads data, so using it cannot affect
crawling or alerts. It runs over plain HTTP: fine for a personal tool, but don't log in on untrusted Wi-Fi.

## Is everything OK?

| Command | What it does |
| --- | --- |
| `make health` | Full check: server services and logs, deployed code, git, Mac worker, then database, Telegram, every device, crawling per shop, blocks, data, disk, backups. Ends "healthy" or the number of problems |
| `make devices` | Every connected device and its state per shop (ok, crawling, idle, blocked, failing, offline) |
| `make dashboard` | The same in your browser, refreshing every 15 s, with everyone's logs |
| `make status` | Workers, blocked IPs, backlog per shop, freshness per group |
| `make report` | Sends the daily Arabic report to your Telegram now |

## Logs

| Where | Command |
| --- | --- |
| **Save everything for later analysis** | `make save-logs` → `logs/<UTC time>/`: `events.jsonl` (every device + server, last 7 days), `archive/` (daily files, 90 days), `journal.log` (server, 7 days), `mac-worker.log`, `summary.txt` (counts and every warning) |
| All devices + server, live | `make events` (Ctrl+C to stop) |
| One device | `make events SOURCE=mac` (or `droplet`, `coordinator`, a friend's name) |
| Only problems | `make events LEVEL=warning` (or `LEVEL=error`) |
| This Mac's worker file, live | `make worker-log` (the file is `~/Library/Logs/deals-worker.log`; also opens in the macOS Console app) |
| Server systemd journal, live | `make logs` |
| Last N log lines without following | `.venv/bin/deals logs --limit 100` (after `set -a; . ./worker.env; set +a`) |
| In the browser | `make dashboard`, click a device name to filter |
| As JSON | `curl "http://207.154.206.127:8787/api/events?token=$DEALS_TOKEN&level=warning"` |

On the server (`make ssh` first):

| What | Command |
| --- | --- |
| Both services, live | `journalctl -u deals-coordinator -u deals-worker -f` |
| Last 200 lines of the coordinator | `journalctl -u deals-coordinator -n 200 --no-pager` |
| Since a time | `journalctl -u deals-worker --since "2026-10-26 08:00"` |
| Only errors | `journalctl -u deals-coordinator -u deals-worker -p err --since -1d` |

## Database

| What | Command |
| --- | --- |
| Live, in the browser (tables, filters, SQL, ready-made queries) | `make dbweb`, then open http://localhost:8001 (Ctrl+C stops) |
| Live SQL prompt (read-only) | `make sql`, then `.tables`, `.schema products`, `SELECT ...;`, `.quit` |
| Copy for IntelliJ | `make db` (writes `data/server.db`; run again to refresh) |
| Ready-made queries | open `docs/queries.sql` in IntelliJ, cursor in a query, Cmd+Enter |
| One product's history | `make ssh`, then `runuser -u deals -- /opt/deals/.venv/bin/deals product amazon_eg B0CYZK7PVC --config /etc/deals/config.toml` |
| Biggest price moves | `make movers` |
| Recent alerts | `make alerts` |
| Backup now | `make ssh`, then `runuser -u deals -- /opt/deals/.venv/bin/deals backup --config /etc/deals/config.toml` |

Prices are in piastres (divide by 100 for EGP); times are UTC.

## Deploying and changing things

| What | Command |
| --- | --- |
| Run the tests | `make test` |
| Deploy from your Mac | `make deploy` (tests, copy, install, restart, health check) |
| Deploy from GitHub | push to `main`: the "deploy" workflow tests and deploys automatically (docs-only pushes skip it). Or Actions tab, deploy, Run workflow |
| Restart the server services | `make restart-server` |
| Restart this Mac's worker | `make restart-mac-worker` (needed after `git pull` or changing worker/site code) |
| Change watched categories or alert rules | `make ssh`, edit `/etc/deals/config.toml`, then `make restart-server` |
| Check a group more or less often | `interval_minutes` in its `[[watch]]`, then `make restart-server` ([GUIDE](GUIDE.md#how-fast-you-hear-about-a-price-change)) |
| Log in to the server | `make ssh` |

After a GitHub deploy the Mac worker still runs the code in your local checkout: `git pull`, then
`make restart-mac-worker`.

**GitHub Actions setup (one time).** The deploy workflow logs in with its own key,
`~/.ssh/deals_deploy`, which is already authorised on the server.

1. On your Mac: `pbcopy < ~/.ssh/deals_deploy` (copies the private key).
2. GitHub, the repository, Settings, Secrets and variables, Actions, New repository secret.
3. Name `DEPLOY_SSH_KEY`, paste, Add secret.
4. Actions tab, deploy, Run workflow, to check it once.

To revoke it later: remove the `github-actions-deploy@deals` line from `/root/.ssh/authorized_keys` on the
server and delete the secret.

## The Mac worker

| What | Command |
| --- | --- |
| Is it running? | `launchctl print gui/$(id -u)/com.deals.worker \| grep state` |
| Restart | `make restart-mac-worker` |
| Keep the Mac awake while plugged in (screen may turn off) | `make keep-awake`; undo with `make allow-sleep` |
| Install (start at login, restart on crash) | `make install-mac-worker` (needs `make mac-worker-env` once) |
| Remove | `make uninstall-mac-worker` |
| Run in the foreground instead | `make mac-worker` (Ctrl+C stops) |
| Force Amazon to solve its challenge again | `rm data/amazon_eg_cookies.json`, then `make restart-mac-worker` |

## Server services

After `make ssh`:

| What | Command |
| --- | --- |
| Status | `systemctl status deals-coordinator deals-worker` |
| Restart | `systemctl restart deals-coordinator deals-worker` |
| Stop / start | `systemctl stop deals-worker` / `systemctl start deals-worker` |
| Edit settings | `nano /etc/deals/config.toml`, then restart the coordinator |
| Disk and memory | `df -h /`, `free -m`, `du -sh /var/lib/deals/*` |
| Backups | `ls -lh /var/lib/deals/backups/` |
| Run any deals command | `runuser -u deals -- /opt/deals/.venv/bin/deals <command> --config /etc/deals/config.toml` |

## Telegram

| What | Command |
| --- | --- |
| Connect a bot | `make telegram TOKEN=<token from @BotFather>` |
| See subscribers | send `/subscribers` to the bot |
| Remove someone | `/subscribers`, tap ❌ next to their name, then confirm |
| Make someone a helper admin (also gets system warnings and the daily report) | `/subscribers`, tap ⭐ next to their name (⬇️ undoes it) |
| Approve a friend | tap the approve button on the request message (only you get requests) |
| Test message | `make ssh`, then `runuser -u deals -- /opt/deals/.venv/bin/deals telegram --test --config /etc/deals/config.toml` |

## The deals command

Every subcommand (`.venv/bin/deals <command> --help` for options). Commands marked "server" read the
database, so run them on the server as shown above.

| Command | What it does | Where |
| --- | --- | --- |
| `serve` | Runs the coordinator | server (systemd) |
| `worker` | Runs a worker (`DEALS_COORDINATOR`, `DEALS_TOKEN`, `DEALS_SITES`) | any machine |
| `local` | Coordinator + worker in one process, local database | your Mac, for experiments |
| `health` | Pass/warn/fail checks | server |
| `status` | Workers, blocks, backlog, freshness | server |
| `devices` | Devices and their per-shop state | anywhere (API) |
| `logs [-f] [--source] [--level]` | Log lines from everyone | anywhere (API) |
| `report [--send]` | The daily report | server |
| `alerts [--days] [--system]` | Recent alerts | server |
| `movers [--days] [--group] [--min-price]` | Biggest price moves | server |
| `product SITE SKU` | One product's stats and history | server |
| `categories SITE [ID]` | Child categories, to find ids for the config | anywhere |
| `plan SITE ROOT` | Dry-run the planner for a category | anywhere |
| `crawl SITE TARGET [--pages N]` | Fetch and print pages (check a parser) | anywhere |
| `telegram [--test]` | Find your chat id / send a test | server |
| `backup [DEST]` | Compressed copy of the database | server |
| `token` | A random worker token | anywhere |

## API

Base `http://207.154.206.127:8787`; read-only endpoints need the UI cookie, `?token=<admin or worker token>`
or a Bearer header (no token needed on the server itself). Full reference: [INTERNALS.md](INTERNALS.md).

| Endpoint | Returns |
| --- | --- |
| `GET /health` | `{"ok": true}` (no token) |
| `GET /api/devices` | Devices, per-shop states, shop totals, failing checks |
| `GET /api/events?source=&level=&after_id=` | Log lines |
| `GET /ui` | The web UI (login at `/ui/login`) |
| `GET /api/overview` | Status, totals, every health check, devices, shops |
| `GET /api/alerts?days=1` | Recent alerts with delivery counts |
| `GET /api/jobs` | Every job, soonest due first |
| `GET /api/products?q=samsung+55` / `GET /api/products/{id}` | Product search / one product with stats and history |
| `GET /dashboard` | The older one-page dashboard |
| `GET /docs` | Interactive API schema |
