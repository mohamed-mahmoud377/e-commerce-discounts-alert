# deals: notes for working on this project

Personal price tracker for amazon.eg and noon.com (Egypt): crawls whole categories, keeps each
product's price history in SQLite, and sends Arabic Telegram alerts for real discounts.
Read [README.md](README.md) (running it) and [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) (design,
algorithms, failure handling) before changing behaviour.

## Where things run

- **Droplet** `root@207.154.206.127` (458 MB RAM + 1 GB swap): systemd `deals-coordinator`
  (FastAPI :8787 + SQLite `/var/lib/deals/deals.db` + Telegram bot + watchdog) and `deals-worker`
  (amazon only; datacenter IPs are often refused by Amazon and always by noon).
  Config: `/etc/deals/config.toml` (worker tokens, Telegram token, watches). Code: `/opt/deals`.
- **The user's Mac**: launchd agent `com.deals.worker` (noon + amazon), env in `./worker.env`,
  log `~/Library/Logs/deals-worker.log`. It runs code from this checkout; after changing worker
  code, restart it: `launchctl kickstart -k gui/$(id -u)/com.deals.worker`.
- Telegram bot @Jerrys_deals_bot; the admin is `[telegram] chat_id`. Friends subscribe with /start
  and the admin approves.

## Everyday commands

| Task | Command |
|---|---|
| Is everything working? | `make health` (server, logs, deployed code, Mac worker, `deals health`) |
| Tests | `make test` |
| Ship a change | `make deploy` (tests → rsync → install → restart → health check) |
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
- Alert and bot texts are Arabic (`alerts.py`, `bot.py`, `health.py`); CLI output is English.
- Parser tests use saved real pages in `tests/fixtures/*.gz`. When a site changes its layout,
  save a new page there and fix the parser against it.
- Never commit `config.toml`, `worker.env` or `data/` (gitignored: they hold tokens and data).
- Ask before committing; deploying and live checks on the droplet are fine without asking.
