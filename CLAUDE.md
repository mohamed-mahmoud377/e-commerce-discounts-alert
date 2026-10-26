# deals: notes for working on this project

Personal price tracker for amazon.eg, noon.com (Egypt), jumia.com.eg, cairosales.com and 2b.com.eg: crawls whole categories, keeps each
product's price history in SQLite, and sends Arabic Telegram alerts for real discounts.
Read [README.md](README.md), [docs/GUIDE.md](docs/GUIDE.md) (design and algorithms) and
[docs/INTERNALS.md](docs/INTERNALS.md) (code map, schema, API) before changing behaviour;
[docs/MONITORING.md](docs/MONITORING.md) is the runbook and [docs/COMMANDS.md](docs/COMMANDS.md) lists every command.
[docs/CATEGORIES.md](docs/CATEGORIES.md) holds category ids and shop quirks; [docs/JUMIA.md](docs/JUMIA.md), [docs/CAIROSALES.md](docs/CAIROSALES.md) and [docs/2B.md](docs/2B.md) are the per-shop analyses. Keep these in step with the code, and
write down anything new you learn about the shops or the operations there (the user wants findings kept).

## Where things run

- **Droplet** `root@207.154.206.127` (458 MB RAM + 1 GB swap): systemd `deals-coordinator`
  (FastAPI :8787 + SQLite `/var/lib/deals/deals.db` + Telegram bot + watchdog) and `deals-worker`
  (amazon only; datacenter IPs are often refused by Amazon and always by noon, Jumia, Cairo Sales and 2B).
  Config: `/etc/deals/config.toml` (worker tokens, Telegram token, watches). Code: `/opt/deals`.
- **The user's Mac**: launchd agent `com.deals.worker` (all five shops), env in `./worker.env`,
  log `~/Library/Logs/deals-worker.log`. `make keep-awake` (launchd `com.deals.awake`,
  `caffeinate -s`) stops it sleeping on power. It runs code from this checkout; after changing worker
  code, restart it: `launchctl kickstart -k gui/$(id -u)/com.deals.worker`. After changing `worker.env`
  (e.g. `DEALS_SITES`) run `make install-mac-worker` instead: the agent copies the env at install time.
- Telegram bot @Jerrys_deals_bot; the admin is `[telegram] chat_id`. Friends subscribe with /start
  and the admin approves.

## Everyday commands

| Task | Command |
|---|---|
| Web UI (everything in one page) | `make ui` (`/ui`, login with `[server] admin_token`; read-only) |
| Is everything working? | `make health` (server, logs, deployed code, Mac worker, `deals health`) |
| Tests | `make test` |
| Ship a change | `make deploy`, or push to `main` (GitHub Actions `deploy` workflow: tests, then the same deploy); then `make restart-mac-worker` if worker code changed |
| Devices and their fetch state | `make devices` (terminal), `make dashboard` (browser), `GET /api/devices` |
| Logs from every device + server | `make save-logs` (download all for analysis), `make events` (live, `SOURCE=mac`), `make logs` (server journal), `make worker-log` (Mac) |
| Live numbers | `make status`, `make alerts`, `make movers`, `make logs` |
| Database | `make sql` (live, read-only), `make dbweb` (browser), `make db` (snapshot for IntelliJ) |
| Daily report now | `make report` |

## Rules of thumb

- Verify on the real system after deploying: `make health`, and query the DB (`make sql`) for the
  thing you changed. The user expects end-to-end checks, not just green tests.
- Migrations in `db.py` are append-only (`PRAGMA user_version`); rehearse risky ones on a copy
  (`make db` then run `db.connect` on a copy) and take a backup first
  (`deals backup --config /etc/deals/config.toml`).
- Prices are integers in piastres. Times are UTC ISO strings. The daily report uses Cairo time.
- noon products are keyed `SKU@seller` (one product has several sellers at very different prices).
- The planner splits by price only, never by sub-category: on every shop a category holds products that are
  in none of its children (GUIDE.md, Algorithm 2). Empty filtered Jumia pages redirect to the parent
  category (see docs/JUMIA.md).
- Alert and bot texts are Arabic (`alerts.py`, `bot.py`, `health.py`); CLI output is English.
- Parser tests use saved real pages in `tests/fixtures/*.gz`. When a site changes its layout,
  save a new page there and fix the parser against it.
- Never commit `config.toml`, `worker.env` or `data/` (gitignored: they hold tokens and data).
- Ask before committing; deploying and live checks on the droplet are fine without asking.
- After pushing to `main` don't also run `make deploy`: GitHub Actions deploys the same commit a minute
  later and the two overlap (an older commit can land last). Use one or the other.
