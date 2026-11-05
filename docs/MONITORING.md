# Monitoring and health

How to know the system works, where to look when it doesn't, and how to fix the usual problems.
Background: [GUIDE.md](GUIDE.md). Code-level detail: [INTERNALS.md](INTERNALS.md).

## Daily routine

Monitoring takes under a minute a day: read the 09:00 Telegram report, and only open a terminal when it
says something needs attention. The system warns you on Telegram by itself when a shop stalls, a device goes
silent, an IP keeps getting blocked, a page layout seems to change, or the disk fills up, and tells you
again (✅) when the shop, the device or the server is back.

Two tables record what happens in the background, so it can be read back later:

- **Uptime** (`status`, `status_changes`; web UI → Uptime, `make uptime`): the state of every shop (up, down,
  paused), device (online, offline) and device on each shop (ok, blocked, failing, offline, paused), checked every
  minute, and every change with its reason and how long the state before it lasted. Kept 180 days.
- **Errors**, the error book (`errors`; web UI → Errors, `make errors`): every warning and error any device or
  the server logs, plus the workers' failed and blocked reports, one row per kind of problem (device, shop,
  kind, message with numbers blanked), counted, with first and last time seen, the likely cause and what to
  do. Mark a row resolved in the web UI; it opens again if the problem comes back. Kept 90 days.

| When | What | Time |
| --- | --- | --- |
| Any time | `make ui`: the web UI shows status, deals, products, devices, jobs, logs and subscribers in one place | 1 minute |
| Every morning | Read the daily report (09:00 Cairo). "كل شيء يعمل بشكل طبيعي" means nothing to do | 10 seconds |
| When a warning arrives | Follow its row in the Telegram warnings section below | 1 to 5 minutes |
| When something looks off | `make health`, then `make uptime`, then `make errors DAYS=1` | 2 minutes |
| Once a week | `make health`, `make errors`, `make movers`; resolve what is fixed in web UI → Errors; check that alerts still feel useful | 5 minutes |
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
| watch SITE | | A worker offers the shop but no `[[watch]]` names it, so it has no jobs and no `crawl` line, and its devices read "idle" | | Add its roots to `/etc/deals/config.toml` as in `config.example.toml`, then restart the coordinator; or drop it from that worker's `DEALS_SITES` |
| jobs SITE | | Slices failing and retrying | A slice failed 3+ times in a row | `make status` shows the error; often a layout change |
| coverage SITE | | A slice is too big to crawl fully | | Split the category in the config or raise its minimum price |
| parser SITE | | | A page came in the last hour with over half of its in-stock listings (10 or more) without a price | The shop changed its layout: `deals crawl SITE "<slice>" --pages 1` on the Mac, save the page into `tests/fixtures/`, fix the parser. The page is still recorded (those products read "no price"); mark the error resolved in web UI → Errors once fixed |
| blocked SITE | | A shop is pausing an IP | | Usually clears itself; see runbook if it repeats |
| data | No bad prices, few A to B to A flip-flops | Over 50 flip-flops in 24 h (offers mixing) | A zero or negative price (parser bug) | Investigate the parser for that shop |
| disk | 20% or more free | 10 to 20% | Under 10% | Delete old files on the server or resize the droplet |
| backup | Newest backup under 26 h old | Older, or none yet | | Check the watchdog in `make logs` |

The database line also shows its size, product count and price points: the quickest way to see growth over time.

## Devices: who is fetching and how it's going

The quickest view is the web UI (`make ui`: Overview, Shops for one shop end to end, or Devices); the commands below show the same.

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
| `blocked: cairosales_eg: ...` / `blocked: 2b_eg: ...` | Same, on Cairo Sales or 2B (both always refuse cloud IPs) | Pauses by itself; see [CAIROSALES.md](CAIROSALES.md) / [2B.md](2B.md) |
| `cairosales_eg: /en/x/ redirected to /en/` | Unknown Cairo Sales slug (the shop answers with its homepage) | Fix the slug (`deals categories cairosales_eg <parent>`) |
| `2b_eg: no category with id N` | Unknown 2B category id | Fix the id (`deals categories 2b_eg <parent id>`) |
| `blocked: btech_eg: the API refused our guest token twice` | B.TECH's API answers 401/403 even with a fresh session cookie (cloud IP, or the token scheme changed) | Pauses by itself; from a cloud IP remove `btech_eg` from that worker; see docs/BTECH.md |
| `btech_eg: no category with slug 'x'` | Unknown B.TECH slug | `deals categories btech_eg <department>` |
| `dream2000_eg: no collection with handle 'x'` | Unknown Dream 2000 collection | Look it up in `https://dream2000.com/en/collections.json?limit=250` |
| `blocked: carrefour_eg: HTTP 429` (or 403) | Akamai rate-limited or refused this connection | Pauses by itself; if it repeats, raise `[sites.carrefour_eg]` delays; see [CARREFOUR.md](CARREFOUR.md) |
| `carrefour_eg: no products in category 'x'` | Unknown (or lowercase) Carrefour id | Ids are case-sensitive, e.g. `NFEGY4080000`; see [CARREFOUR.md](CARREFOUR.md) |
| `blocked: dubaiphone_eg: HTTP 429 ... (Vercel challenge)` | Vercel's bot challenge: expected from any cloud IP, rare from home | Pauses by itself; keep `dubaiphone_eg` off cloud workers; see [DUBAIPHONE.md](DUBAIPHONE.md) |
| `dubaiphone_eg: no category with slug 'x'` | Unknown Dubai Phone slug (the page was Next's 404) | Find it in `https://www.dubaiphone.net/sitemap-categories.xml` |
| `dubaiphone_eg: no shopData in the page` | The shop answered an error page (it does for `per_page` over 100 or an unknown sort) or changed its layout | Open the URL from the log in a browser; retried by itself |
| `blocked: hardwaremarket_eg: HTTP 403` | Cloudflare's challenge: expected from any cloud IP | Pauses by itself; keep `hardwaremarket_eg` off cloud workers; see [HARDWAREMARKET.md](HARDWAREMARKET.md) |
| `hardwaremarket_eg: no category 'x'` | Unknown WooCommerce category id or slug | `deals categories hardwaremarket_eg <id>` or the category list URL in the message |
| `blocked: amazon_now_eg: Amazon Now lists nothing here (delivery location not served)` | That worker's delivery location has no Amazon Now dark store (expected on the droplet and cloud machines) | Pauses by itself; keep `amazon_now_eg` out of that worker's `DEALS_SITES`; see [AMAZON_NOW.md](AMAZON_NOW.md) |
| `raya_eg: GraphQL error` | The API changed a field | [RAYA.md](RAYA.md); `deals crawl raya_eg ...` on the Mac |
| `vodafone_eg: no category '/x'` | A menu url changed | `main-menu`, [VODAFONE.md](VODAFONE.md) |
| `curl: (92) HTTP/2 stream` on `vodafone_eg` | The session stopped using HTTP/1.1 | [VODAFONE.md](VODAFONE.md) |
| `blocked: dokkantech_eg: HTTP 430` | Shopify's block | Pauses by itself; lower the rate if it repeats |
| `etisal_eg: no guest token` | `home/initialize` changed | [ETISAL.md](ETISAL.md) |
| `blocked: orange_eg: HTTP ... asking for a guest token` | `Auth/token` refused | [ORANGE.md](ORANGE.md); check with plain curl from the same machine |
| `Connection closed abruptly` on `orange_eg` | The adapter must use urllib | [ORANGE.md](ORANGE.md) |
| `blocked: lcwaikiki_eg: HTTP 403` | Cloudflare: expected on any cloud IP | Keep it off cloud workers; [LCWAIKIKI.md](LCWAIKIKI.md) |
| `lcwaikiki_eg: no catalogModel in the page` | Layout changed | Save a new page; [LCWAIKIKI.md](LCWAIKIKI.md) |
| `blocked: defacto_eg: HTTP 403` | Akamai | Pauses by itself; slow it down if it repeats; [DEFACTO.md](DEFACTO.md) |
| `defacto_eg: no product cards and no count` | Layout changed | Save a new page; [DEFACTO.md](DEFACTO.md) |
| `elaraby_eg: GraphQL error` | A field changed | [ELARABY.md](ELARABY.md) |
| `blocked: wayup_eg: HTTP 430` (also `gcshop_eg`, `azzam_eg`) | Shopify's own block | Pauses by itself; slow it from the web UI's Shop settings (Pace) if it repeats |
| `blocked: elnekhely_eg: HTTP 403` | Cloudflare: expected from any cloud IP | Pauses by itself; keep `elnekhely_eg` off cloud workers; [ELNEKHELY.md](ELNEKHELY.md) |
| `blocked: miami_eg: HTTP 403` | Cloudflare refused this connection | Pauses by itself; its pages cost it 5 s to build, so slow it if it repeats; [MIAMI.md](MIAMI.md) |
| `blocked: mazaya_eg: HTTP 403` or a non-JSON answer | `robots.txt` disallows the API we read, so this may be the shop enforcing it | Pauses by itself; if it repeats, switch it off in Shop settings rather than fight it; [MAZAYA.md](MAZAYA.md) |
| `blocked: raneen_eg: HTTP 403` | From a home IP, that IP is refused. The droplet is always refused: it should never have Raneen in `DEALS_SITES` | Never lower its delay (`robots.txt` asks for 10 s); pause it; [RANEEN.md](RANEEN.md) |
| `blocked: eand_eg: HTTP 200` | Its API answered something other than JSON: the `Accept` header was lost (XML), or FortiWeb's "Web Page Blocked" page | The body says which; [EAND.md](EAND.md) |
| `blocked: ravin_eg: HTTP 429` | `local_rate_limited`, as its `/products.json` did on the first day | Pauses by itself; if it repeats, set Pace 2x in Shop settings; [RAVIN.md](RAVIN.md) |
| `aghezty_eg: no product data in the page` | `window.AGHEZTY` is gone from the page: a redesign | [AGHEZTY.md](AGHEZTY.md) |
| `raneen_eg: no product cards in the page` | A landing page in place of a category, or a new theme | [RANEEN.md](RANEEN.md) |
| `blocked: faces_eg: HTTP 403` | `robots.txt` disallows the address we read, so this may be the shop enforcing it | Pauses by itself; if it repeats, switch it off in Shop settings rather than fight it; [FACES.md](FACES.md) |
| `faces_eg: no item count in the grid` or HTTP 500 from it | The grid changed, or the request lost its `X-Requested-With` header | [FACES.md](FACES.md) |
| `mazaya_eg: GraphQL error` | A field changed | [MAZAYA.md](MAZAYA.md) |
| `sigma_eg: no product data in the page` | The Next.js payload changed | Save a new page, fix against it; [SIGMA.md](SIGMA.md) |
| `last_count` far from `expected_total` on `gcshop_eg`, `azzam_eg`, `wayup_eg` | Shopify's `products_count` is wrong there | Normal; [GCSHOP.md](GCSHOP.md) |
| Screenshot timeouts on `elaraby_eg` now and then | Its pages answer 404 at random | Nothing: the alert goes out as text; [ELARABY.md](ELARABY.md) |
| `lease lost` | The device was too slow (over 5 min between pages) and the job went to another device | Fine occasionally |

## Telegram warnings

System messages go to you and your helper admins (⭐ in `/subscribers`). They start with ⚙️, or 🔴 when a shop or
device goes down and ✅ when it is back. Each warning is sent at most once per cooldown, so a lasting problem does
not flood the chat. Down and back come from `uptime.py`: a state is announced when it changes, once, and "back"
is sent only for something you were told went down. A shop paused by hand while down is not announced as back.

| Message starts with | Sent when | Cooldown | What to do |
| --- | --- | --- | --- |
| "🔴 … متوقف: العمل متأخر" (shop stalled) | Work for that shop is 45+ min overdue and nothing finished for an hour | once per outage (1 h if it flaps) | Usually the Mac is asleep or off. Wake it; `make uptime`, `make devices` |
| "✅ … عاد للعمل" (shop back) | A slice of that shop finished again; says how long it was down | each time | Nothing |
| "🔴 الجهاز … لم يتواصل" (device silent) | A device has been silent 30+ min | once per outage (1 h if it flaps) | Same as above, for that device |
| "✅ الجهاز … عاد للاتصال" (device back) | The device talked to the server again; says how long it was gone | each time | Nothing |
| "… يحظر الجهاز" (shop blocking a device) | The same shop blocked the same IP 3 times in a row | 6 h | Runbook: repeated blocks |
| "✅ … لم يعد يحظر الجهاز" (block over) | After that warning, a page from that device worked again | each time | Nothing |
| "✅ الخادم عاد للعمل" (server back) | The watchdog had no round for 10+ min: the server was stopped or crashed (nothing was watched or sent meanwhile) | each time | `make logs` around that time; a reboot or out of memory? |
| "لم نستخرج أي منتج" (no products parsed) | A slice that had products parsed empty 3 times | 6 h | Runbook: layout changed |
| "مهمة … فشلت" (job failing) | A job failed 3 times in a row; the message has the error | 24 h | Read the error; runbook |
| "… شريحة أكبر من أن نجمعها" (slice too big) | Planning could not split a category below the shop's limit | 7 days | Raise the group's `min_price` or point it at narrower categories |
| "المساحة على الخادم" (disk low) | Under 10% free on the server | 12 h | Runbook: disk |
| "📋 التقرير اليومي" (daily report) | Every day at 09:00 Cairo | 1 day | Read it; act on anything under "يحتاج انتباه" |
| "🔔 طلب اشتراك جديد" (subscription request) | Someone pressed Start | Each time | Tap approve or reject |

## Runbook

**The review group and the public channel** ([PUBLISHING.md](PUBLISHING.md)). The `review` line in `make health`: cards
waiting for a decision. ❌ means approved deals could not be posted: the card says why and has **Try again**, usually the bot was
removed from the channel or lost its *Post messages* right. ⚠️ means cards took over 30 minutes to reach the group:
a burst, or Telegram refusing them; if the error is "group chat was upgraded to a supergroup", the group's id changed, the bot
told the owner the new one, update `[telegram] review_chat_id` and restart. Look at the queue with
`SELECT status, COUNT(*) FROM reviews GROUP BY 1` and the errors with `SELECT alert_id, error FROM reviews WHERE error IS NOT NULL`.


**What went wrong while nobody was looking?** web UI → Errors (or `make errors`, `make errors DAYS=1 SITE=jumia_eg`):
the newest first, "Times" says how often. Hover a kind for its cause; the table under it says what to do. A row
seen within the hour is still happening. web UI → Uptime → History (or `make uptime`) says when each shop and
device went down, why, and for how long. Device-on-shop blocks are listed with `ALL=1`.

**Alerts come without a screenshot.** Only amazon.eg and Amazon Now alerts have one, and only while a
`DEALS_SCREENSHOTS=1` worker (the Mac) crawls the shop. `make sql`, then
`SELECT alert_id, site, status, worker, attempts, bytes, error, taken_at FROM shots ORDER BY alert_id DESC LIMIT 10`:
no rows (the Mac is offline or its `worker.env` lacks `DEALS_SCREENSHOTS=1`: add it, `make install-mac-worker`),
`none` with "price block not found" (the shop changed its page: `deals shot <site> <url>` on the Mac, fix the
selectors in `shots.py`), `late` (the screenshot took longer than `screenshot_wait_seconds`), `pending` /
`taking` left over (the Mac's reply never came: `make worker-log`, search "screenshot"). `make worker-log`
shows each one as "screenshot for alert N: 137 KB in 6.0s".

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

## Switching a shop off

Web UI → **Shop settings** ([GUIDE.md](GUIDE.md), "Shop settings"): crawl, each device, alerts, screenshots, product pages,
pace, alert floors. They stay until changed; `make health` shows them on a `shop settings` line (OK level), the
daily report on a ⚙️ line. To check by hand: `make sql`, then `SELECT * FROM shop_settings; SELECT * FROM device_shops;`.
A device that is switched off shows the state `off` under Devices and `paused` under Uptime, and its worker still
asks for work every 30 s (`job: null`), which keeps it online. A worker's log line `lease lost for job ...; moving on` right after a switch is expected.

## Pausing crawling by hand

To stop a device or a shop for a while (testing, a shop acting up, the Mac needs its bandwidth): web UI →
Devices → "Paused crawling" (admin token), or `make pause DEVICE=mac SITE=jumia_eg HOURS=3` (`PAGES=1` stops
product-page reading too). A pause always ends by itself (2 hours by default, a week at most); `make resume`
ends it early. A job already running finishes. While every device that crawls a shop is paused, the watchdog
sends no "stalled" alert for it and `make health` shows "paused by hand" (a warning, not a failure).

