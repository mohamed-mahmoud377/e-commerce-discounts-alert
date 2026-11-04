---
name: work-on-deals
description: How to understand, debug, change and ship anything in the deals price tracker (crawlers, scheduler, deal rules, alerts, bot, matching, web UI, health) without breaking the live system, and which docs to update afterwards. Use for every bug report, "why did/didn't X happen", new feature, new shop, rule or schedule change, refactor or investigation in this repo, before reading or editing code.
---

# Working on deals

A personal price tracker that is **live**: real people get Arabic Telegram alerts from it every day, and
home IP addresses that shops can block at any time do the crawling. Every change has to keep three
promises. Check each one before you start and again before you ship:

1. **Notice drops fast.** Flash sales can be gone in minutes. The worst-case time-to-notice is the group's
   `max_interval_minutes` cap and the deal-feed fast lanes, not the base interval. Never make it longer
   without saying so.
2. **Alert only on real discounts.** The reference price is the product's own time-weighted history, never
   the shop's crossed-out price. A false alert costs more than a late one.
3. **Don't get blocked.** The home IPs are the only way into noon, Jumia, Cairo Sales, 2B, B.TECH, Dubai
   Phone and Hardware Market. Any change that adds requests has to state the extra pages per hour for each
   shop and the current block history (`blocks` table, `make devices`). Prefer moving requests around over
   adding new ones.

The user also rejected the 30-day prior-price rule (28 Oct 2026). Don't bring it back without asking.

## 1. Orient (every session, about 2 minutes)

- Run `make health` first. It shows what is live, what is failing and whether the server runs this checkout.
- Run `git status` and `git log -5`. Other people (abdelrahman) and other sessions commit here too. Uncommitted
  files you didn't write are someone's work in progress: leave them alone, and remember that **`make deploy`
  rsyncs the working tree, uncommitted files included**.
- Check the live commit with `cat /opt/deals/REVISION` (over ssh), or read it in `make health`.

## 2. Understand before changing: where to read

Read the doc section for the area **before** opening the code. The docs are kept in step with the code and
explain the reasons behind decisions that the code alone does not show.

| Area | Doc to read first | Code | Tests |
|---|---|---|---|
| Overall design, the principles, the 6 algorithms | `docs/GUIDE.md` (top + the algorithm) | | |
| Code map, one page end to end, schema, API, config | `docs/INTERNALS.md` | | |
| Why an alert did or didn't go out | `docs/PRODUCT_FLOW.md` | `pricing.py`, `alerts.py` | `test_pricing.py`, `test_deal_rules.py` |
| A shop's data is wrong (placeholder prices, a signal that means nothing) | `docs/SHOP_RULES.md` | `shop_rules.py` | `test_shop_rules.py` |
| Fetching/parsing a shop | `docs/<SHOP>.md`, `docs/CATEGORIES.md` | `sites/<shop>.py` | `test_parsers.py` + `tests/fixtures/*.gz` |
| Adding a shop; a shop's names, kind, roots | INTERNALS "Add a shop" (`make new-shop`) | `shops.py` (the one list of shops) | `test_shops.py` (every shop has everything) |
| Splitting categories into slices | GUIDE Algorithm 2 | `planner.py` | `test_planner.py` |
| Leases, intervals, blocks, speed | GUIDE Algorithm 3 + "How fast you hear" | `scheduler.py`, `coordinator.py` | `test_coordinator.py`, `test_worker_e2e.py` |
| Same product on other shops | `docs/MATCHING.md` | `matching.py`, `details.py`, `detailq.py` | `test_matching.py`, `fixtures/matching_gold.tsv` |
| Screenshots | GUIDE "Alert screenshots" | `shots.py`, `shotq.py` | `test_shots.py` |
| Bot, subscribers | GUIDE "Alerts and the Telegram bot" | `bot.py` | `test_bot.py` |
| Review group, public channel, the Arabic posts | `docs/PUBLISHING.md` | `publish.py` | `test_publish.py` |
| Health, devices, logs, errors, uptime | `docs/MONITORING.md` | `health.py`, `monitor.py`, `audit.py`, `uptime.py` | `test_health.py`, `test_monitor.py`, `test_uptime.py` |
| Web UI | INTERNALS code map | `webui.py` | `test_webui.py` |
| How Amazon/noon price, detection science | `docs/PRICING_RESEARCH.md` | | |
| Every command | `docs/COMMANDS.md` | `cli.py`, `Makefile` | |

Two boundaries hold everywhere. **Workers never touch the database**: they only speak HTTP to the
coordinator. **`pricing.py` has no I/O**, so deal rules are tested with plain numbers.

## 3. Debugging: live data first, then reproduce locally

Don't guess from the code. Find out what actually happened, then explain it.

| Symptom | First look | Then |
|---|---|---|
| Anything looks off | `make health` → `make devices` → `make errors` (DAYS=1, SITE=) | `make events LEVEL=warning`, `make uptime` |
| A shop stopped / stalled | `make devices` (state + reason per device) | `make save-logs`, read `logs/`; `make worker-log` for the Mac |
| Blocked | `make devices`; `SELECT * FROM blocks` in `make sql` | runbook in MONITORING.md; slow the shop down, don't retry harder |
| Parser broke / "no products parsed" | `.venv/bin/deals crawl <site> "<slice>" --pages 1` on the Mac | save the new page into `tests/fixtures/`, fix against it |
| Wrong/missing/false alert | `.venv/bin/deals product <site> <sku>` on the server (history + stats) | walk it through PRODUCT_FLOW.md step by step; rebuild the history in a `test_pricing.py` test. A round reference (100,000, 50,000, 1 EGP) is likely a shop placeholder: SHOP_RULES.md |
| Wrong match across shops | `make models FIND=<code>`; web UI product page (evidence) | MATCHING.md; add the titles to the gold set |
| Screenshot missing | `SELECT ... FROM shots ORDER BY alert_id DESC` (see runbook) | `.venv/bin/deals shot <site> <url>` on the Mac |
| Slow to notice a drop | `jobs` table: `interval_minutes`, `next_due_at`, late slices | GUIDE "How fast you hear"; check how busy each shop's loop is before speeding it up |

Ready-made SQL is in `docs/queries.sql`. Prices are integers in **piastres** (divide by 100), times are
**UTC** ISO strings, and noon products are keyed `SKU@seller`. Shop pages that come from the droplet
are not what the Mac sees: home-only shops have to be probed from the Mac.

State the root cause with evidence (a log line, a DB row, a reproduced test) before fixing anything. If
you learn something new about a shop, it goes into that shop's doc (step 6).

## 4. Making the change

Follow the recipes in `docs/INTERNALS.md` → "How to improve it" (add a shop, deal rule, message, signal,
health check, watch group). The loop is always: **a test that fails first → change → `make test` → ship
→ verify live**.

Invariants that are easy to break:

- **Migrations** in `db.MIGRATIONS` are append-only (`PRAGMA user_version`). Never edit an old one. For risky
  ones, take a backup first (`deals backup --config /etc/deals/config.toml`) and rehearse on a copy (`make db`,
  then `db.connect` on a copy of `data/server.db`). Add the new version to the INTERNALS migration table.
- **Version skew.** Other machines (abdelrahman-mac, friends' PCs) run older worker code against the new
  coordinator. New request fields have to be optional with defaults, and nothing old workers send can be
  rejected. Config: deploy the code before adding new config keys, because old code rejects unknown keys.
- **The planner splits by price only**, never by sub-category. Price filters must have inclusive ends, and an
  empty slice must come back empty, not as a redirect to the parent (Jumia).
- **Texts:** alerts, bot replies and health/report lines are Arabic; CLI output is English.
- **Thresholds** that might need tuning go in `AlertRules`/config, not in code constants.
- **Matching rule changes:** add the failing titles to `tests/fixtures/matching_gold.tsv` first (precision
  stays 100%), bump `matching.VERSION`, and compare a live snapshot before and after.
- **Memory:** the coordinator has `MemoryMax=300M` on a 1 GB droplet. Stream big queries; don't load whole tables.
- **Parser tests** use real saved pages (`tests/fixtures/*.gz`), never hand-written HTML.
- Match the surrounding code: its comment density, naming and its plain-English docstrings.

## 5. Make sure nothing broke

Before shipping, all of these must hold:

1. `make test` is green (it runs `.venv/bin/pytest -q`, the same as CI and `make deploy`). Also run
   `.venv/bin/python -m pytest -q`. If the two disagree, a test imports `from tests...`, which breaks `make test`.
2. A new test covers the bug or the feature, and it failed before the fix.
3. Run the impact questions, and answer each one in your report:
   - Time-to-notice: does any group, feed or shop get slower in the worst case?
   - Alert honesty: can this create alerts from the shop's anchor price, or alert on a price the product sits at regularly?
   - Block risk: how many extra requests per hour, on which shops, from which IPs?
   - Old workers and old config: still accepted?
   - Database: does the migration run on the real database size, and do queries still use indexes?
4. For alert or matching rule changes, replay them on real data. Before/after counts on a `make db` snapshot
   beat reasoning.

## 6. Ship, verify live, write it down

**Ship** (deploying and live checks are fine without asking; **ask before every commit or push**):
- `make deploy`, **or** push to `main` (GitHub Actions deploys a minute later). **Never both**: they overlap and
  an older commit can land last. Docs-only pushes don't deploy.
- If worker or `sites/` code changed: `make restart-mac-worker`. If `worker.env` changed: `make install-mac-worker`.
  For a new shop, check that every home worker's `DEALS_SITES` has it.

**Verify on the real system.** Green tests are not the end:
- `make health` passes and shows the server running this checkout.
- Query the thing you changed with `make sql` (new rows, new column filled, the job interval you
  changed, the alert that should or shouldn't exist).
- `make devices` and the `blocks` table show no new blocks after a crawl change. Look again after a few cycles.
- For alert changes, `make alerts`; for UI changes, `make ui` and look at it.

**Update the docs in the same change.** The user wants findings kept:

| You changed / learned | Update |
|---|---|
| Any behaviour | the matching section of `docs/GUIDE.md` (algorithm text, tables, numbers) |
| A module, function, schema, API endpoint, config key | `docs/INTERNALS.md` (code map line counts, migration table, API, config reference) |
| Deal rule or alert flow | `docs/PRODUCT_FLOW.md` (+ GUIDE Algorithm 5) |
| Something about a shop (access, quirk, cap, layout change) | `docs/<SHOP>.md`, `docs/CATEGORIES.md`; a data quirk the tracker handles also goes in `shop_rules.RULES` and `docs/SHOP_RULES.md` |
| A command or make target | `docs/COMMANDS.md`, `README.md` table, the `CLAUDE.md` everyday table if it's everyday |
| A health check, warning, runbook step | `docs/MONITORING.md` |
| Matching | `docs/MATCHING.md` |
| Something now done or newly found to do | `docs/ROADMAP.md` |
| State outside the repo (server config edits, who runs which shops) | memory (`deployment-state` etc.), not the repo |

Write dates as absolute dates (e.g. 4 Nov 2026) and record measured numbers ("62 products, 3 s to Telegram")
rather than adjectives.

## 7. Report back

Tell the user what the root cause was (with the evidence), what changed, which tests were added, how
it was verified live (the actual query and output), the answers to the impact questions, and which
docs were updated. If something was skipped or is still unverified, say so plainly. Then ask before
committing.
