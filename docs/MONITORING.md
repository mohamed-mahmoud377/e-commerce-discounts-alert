# Monitoring and health

How to know the system works, where to look when it doesn't, and how to fix the usual problems.
Background: [GUIDE.md](GUIDE.md). Code-level detail: [INTERNALS.md](INTERNALS.md).

## Daily routine

Monitoring takes under a minute a day: read the 09:00 Telegram report, and only open a terminal when it
says something needs attention. The system warns you on Telegram by itself when a shop stalls, a device goes
silent, an IP keeps getting blocked, a page layout seems to change, or the disk fills up.

| When | What | Time |
| --- | --- | --- |
| Any time | `make ui`: the web UI shows status, devices, logs, alerts, jobs and products in one place | 1 minute |
| Every morning | Read the daily report (09:00 Cairo). "كل شيء يعمل بشكل طبيعي" means nothing to do | 10 seconds |
| When a warning arrives | Follow its row in the Telegram warnings section below | 1 to 5 minutes |
| When something looks off | `make health`, then `make devices`, then `make events LEVEL=warning` | 2 minutes |
| Once a week | `make health`, `make movers`, glance at `make dashboard`; check that alerts still feel useful | 5 minutes |
| After any code change | `make deploy`, then `make health`, then `make devices` | 3 minutes |

All commands run from the project folder on your Mac (`~/IdeaProjects/e-commerce-discounts-alert`). The ones
that read from the server use the token in `worker.env`, so no login is needed.

## make health, line by line

`make health` answers "is everything working?" in about 10 seconds and ends with "healthy" or the number of
problems; its exit code is 1 when something fails, so scripts can use it. It runs `scripts/health.sh` on
your Mac, which checks the outside of the system, then `deals health` on the server, which checks the inside
(`src/deals/health.py`).

**Outside checks (from your Mac):**

| Line | Passes when | If not |
| --- | --- | --- |
| deals-coordinator running / deals-worker running | systemd says `active`; the restart count is shown | Server: `systemctl status deals-coordinator`, then `make logs` |
| no errors in server logs (24h) | No ERROR or Traceback line in 24 hours | Warning only; read them with `make logs` or `make events LEVEL=error` |
| server runs the same code as this checkout | File hashes match | Warning: run `make deploy` (or you have local edits not yet shipped) |
| git: everything committed | No uncommitted changes | Warning only |
| worker running (launchd) | The Mac's agent is running | `make install-mac-worker`; without it noon and Jumia have no worker |
| no errors in its recent log | Last 2,000 lines clean | `make worker-log` |

**Inside checks (on the server):**

| Check | OK | Warning | Failure | What to do |
| --- | --- | --- | --- | --- |
| database | `quick_check` ok and schema version matches the code | | Integrity problem, or schema older/newer than code | Restore a backup (runbook); or `make deploy` |
| telegram | Configured, nothing stuck | A message waiting more than 15 min | Not configured, or a message failed 10 times | `make logs`; check the bot token |
| worker NAME (one line each) | Seen in the last 15 min | 15 to 60 min | Over 60 min, or no worker at all in 7 days | Is that machine on and awake? Restart its worker |
| crawl SITE | A slice finished within 60 min and the backlog is under 2 h | Last finish 1 to 3 h ago, or backlog over 2 h | Nothing finished for 3 h | `make devices` to see which device should be on it |
| jobs SITE | | Slices failing and retrying | A slice failed 3+ times in a row | `make status` shows the error; often a layout change |
| coverage SITE | | A slice is too big to crawl fully | | Split the category in the config or raise its minimum price |
| blocked SITE | | A shop is pausing an IP | | Usually clears itself; see runbook if it repeats |
| data | No bad prices, few A to B to A flip-flops | Over 50 flip-flops in 24 h (offers mixing) | A zero or negative price (parser bug) | Investigate the parser for that shop |
| disk | 20% or more free | 10 to 20% | Under 10% | Delete old files on the server or resize the droplet |
| backup | Newest backup under 26 h old | Older, or none yet | | Check the watchdog in `make logs` |

The database line also shows its size, product count and price points: the quickest way to see growth over time.

## Devices: who is fetching and how it's going

The quickest view is the web UI (`make ui`, Overview or Devices tab); the commands below show the same.

Every device reports its result for each shop with every page it sends, so the coordinator always knows, per
device and per shop, whether fetching works. Three ways to see it, all showing the same data:

| Way | Command or address |
| --- | --- |
| Terminal | `make devices` |
| Browser, refreshes every 15 s, with logs | `make dashboard` (opens `http://207.154.206.127:8787/dashboard?token=...`) |
| API (JSON) | `GET http://207.154.206.127:8787/api/devices` with `?token=<a worker token>` or header `Authorization: Bearer <token>`; no token needed when called on the server itself |

Each device shows its IP, code version, when it was last seen, pages and jobs done, then one line per shop
with a state:

| State | Meaning | Normal? |
| --- | --- | --- |
| crawling | It holds a job on this shop right now; the detail names the slice and products so far | Yes |
| ok | Its last page for this shop succeeded within 30 min | Yes |
| idle | Nothing is due for this shop at the moment | Yes, between rounds |
| blocked | The shop challenged this device's IP; the detail says until when and why | Fine once in a while; repeated blocks need the runbook |
| failing | Its last attempt on this shop failed within the last hour; the detail has the error | Look into it if it lasts |
| offline | Not heard from in 15 min | Expected when the Mac sleeps; otherwise restart the worker |

Per-shop totals under the devices show products, slices, how many are due now, running, failing, and when
the last slice finished. Failing health checks are listed at the top of the page and of the JSON (`problems`).

The JSON, trimmed:

```json
{"generated_at": "2026-10-25T21:33:15",
 "devices": [{"name": "mac", "ip": "41.36.27.75", "online": true, "last_seen_seconds": 1,
   "sites": [{"site": "noon_eg", "state": "crawling", "detail": "crawl category:...smartphones|price:2500- (300 products so far)",
              "pages": 26, "products": 1203, "blocks": 0, "last_error": null}]}],
 "sites": [{"site": "noon_eg", "products": 26865, "slices": 74, "due": 1, "running": 1, "failing": 0}],
 "problems": []}
```

## Logs

Every device ships its log lines to the coordinator every 10 seconds (up to 1,000 lines wait in memory if
the server is unreachable), and the coordinator stores its own lines in the same `events` table. One command
therefore shows everything. The database keeps 7 days; every night the finished days are also written to
`/var/lib/deals/logs/events-YYYY-MM-DD.jsonl.gz` (one JSON object per line) and kept for 90 days.

| Command | Shows |
| --- | --- |
| `make save-logs` | Downloads everything (all devices, server journal, daily archives, Mac log) into `logs/<UTC time>/` with a `summary.txt`; `logs/` is not committed |
| `make events` | Live lines from all devices and the server, newest at the bottom; Ctrl+C stops |
| `make events SOURCE=mac` | One device (`mac`, `droplet`, a friend's name, or `coordinator`) |
| `make events LEVEL=warning` | Only warnings and errors |
| `make dashboard` | The last 300 lines under the devices table; click a device name to filter |
| `GET /api/events?source=&level=&after_id=&limit=` | The same as JSON; `after_id` returns only newer lines (for polling) |
| `make worker-log` | The Mac worker's own file `~/Library/Logs/deals-worker.log` (kept even when the server is unreachable) |
| `make logs` | The server's systemd journal for both services, including web-server lines and crashes |

What normal looks like (one job):

```text
10-25 21:30:44  droplet  INFO  [amazon_eg] crawl amazon_eg category:21833010031|price:800-
10-25 21:32:58  droplet  INFO  [amazon_eg] done amazon_eg category:21833010031|price:800-: 1012 products, 38 pages, 1 changes, 134s
```

The bracket is the shop loop that wrote the line. "changes" counts prices that moved in that slice; it
drives how soon the slice is visited again.

Lines worth reacting to:

| Line contains | Means | Action |
| --- | --- | --- |
| `coordinator unreachable ... for Ns` | The server is restarting or the network dropped. Logged as INFO for the first minute (deploys restart the server for ~3 s and the droplet worker is stopped first, so deploys produce no warnings), as WARNING after that, then `reachable again after Ns` | Only a WARNING (over a minute) needs a look |
| `blocked: ... still challenged` | A shop refused this device; it pauses that shop for that IP | None once; runbook if it repeats for hours |
| `page N attempt M failed ... retrying` | A network error on one page; retried after 5, 20, 60 s | None unless it keeps happening |
| `job N failed` with a traceback | Something unexpected, often a parser error | Read the traceback; save the page as a test fixture and fix |
| `unknown category` | A typo in a noon category path in the config | Fix the path in `/etc/deals/config.toml` |
| `jumia_eg: no such page ... (HTTP 404)` / `... redirected to ...` | A wrong, aliased or renamed Jumia slug | Put the canonical slug (from the category sidebar, `deals categories jumia_eg <parent>`) in `/etc/deals/config.toml` |
| `blocked: jumia_eg: Cloudflare challenge` | Cloudflare refused this connection (always from cloud IPs) | Pauses by itself; if a home worker is blocked every time, see [JUMIA.md](JUMIA.md) troubleshooting |
| `lease lost` | The device was too slow (over 5 min between pages) and the job went to another device | Fine occasionally |

## Telegram warnings

System messages start with ⚙️ and go to you and your helper admins (⭐ in `/subscribers`). Each kind is sent at most once per cooldown, so a lasting
problem does not flood the chat.

| Message starts with | Sent when | Cooldown | What to do |
| --- | --- | --- | --- |
| "… متوقف: العمل متأخر" (shop stalled) | Work for that shop is 45+ min overdue and nothing finished for an hour | 6 h | Usually the Mac is asleep or off. Wake it; `make devices` |
| "الجهاز … لم يتواصل" (device silent) | A device seen in the last 2 days has been silent 30+ min | 12 h | Same as above, for that device |
| "… يحظر الجهاز" (shop blocking a device) | The same shop blocked the same IP 3 times in a row | 6 h | Runbook: repeated blocks |
| "لم نستخرج أي منتج" (no products parsed) | A slice that had products parsed empty 3 times | 6 h | Runbook: layout changed |
| "مهمة … فشلت" (job failing) | A job failed 3 times in a row; the message has the error | 24 h | Read the error; runbook |
| "… شريحة أكبر من أن نجمعها" (slice too big) | Planning could not split a category below the shop's limit | 7 days | Raise the group's `min_price` or point it at narrower categories |
| "المساحة على الخادم" (disk low) | Under 10% free on the server | 12 h | Runbook: disk |
| "📋 التقرير اليومي" (daily report) | Every day at 09:00 Cairo | 1 day | Read it; act on anything under "يحتاج انتباه" |
| "🔔 طلب اشتراك جديد" (subscription request) | Someone pressed Start | Each time | Tap approve or reject |

## Runbook

**Mac worker stopped or offline.**

1. `make devices`: is `mac` offline?
2. `launchctl print gui/$(id -u)/com.deals.worker | grep state`. Not running: `make install-mac-worker`.
3. Running but offline: `make worker-log` for errors; `launchctl kickstart -k gui/$(id -u)/com.deals.worker` restarts it.
4. To keep it running while the Mac is idle: `make keep-awake` (no sleep while plugged in; the screen still
   turns off; on battery it sleeps normally; closing the lid still sleeps unless an external display is
   attached). Undo with `make allow-sleep`.

**A shop keeps blocking a device.**

1. `make devices` shows `blocked` with the reason and the pause end; the pause doubles each time (15 min up to 24 h).
2. If only the droplet is blocked on Amazon: expected, your Mac covers Amazon.
3. If your Mac is blocked: open amazon.eg or noon in your browser. A captcha there means the IP is flagged;
   it usually clears within hours. For Amazon, delete `data/amazon_eg_cookies.json` and restart the worker
   so it solves the challenge again.
4. Slow it down: raise `min_delay`/`max_delay` for that shop in `/etc/deals/config.toml`, then
   `systemctl restart deals-coordinator` (workers receive delays with each job).

**A shop changed its page layout** ("no products parsed" or parser tracebacks).

1. Reproduce: `.venv/bin/deals crawl amazon_eg "category:21832982031|price:3000-" --pages 1` prints what the parser sees.
2. Save the new page as a test fixture (`tests/fixtures/*.gz`), fix `src/deals/sites/<shop>.py` until `make test` passes.
3. `make deploy`, restart the Mac worker, `make devices`.

**Server service down.**

1. `ssh root@207.154.206.127`, then `systemctl status deals-coordinator`.
2. `journalctl -u deals-coordinator -n 200 --no-pager` for the reason.
3. `systemctl restart deals-coordinator`. If it crashes on start after a deploy, roll back:
   `git checkout <last good commit>` locally, then `make deploy`.

**Disk low on the server.** `ssh` in, `du -sh /var/lib/deals/* /var/log/journal`. Shrink the journal:
`journalctl --vacuum-size=200M`. Manual backups (`manual-*.db.gz`, `before-*.db`) can be deleted once no
longer needed.

**Restore the database from a backup.**

1. On the server: `systemctl stop deals-coordinator deals-worker`.
2. `cd /var/lib/deals && cp deals.db deals.db.broken && gunzip -c backups/deals-YYYY-MM-DD.db.gz > deals.db && rm -f deals.db-wal deals.db-shm && chown deals:deals deals.db`.
3. `systemctl start deals-coordinator deals-worker`, then `make health`.

**Too many or too few alerts.** Change `[alerts]` in `/etc/deals/config.toml` (for example
`min_drop = 0.25`), or a group's own `min_drop` / `min_price` under its `[[watch]]`, then
`systemctl restart deals-coordinator`.
