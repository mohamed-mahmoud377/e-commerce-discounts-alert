# deals: notes for working on this project

Personal price tracker for amazon.eg, noon.com (Egypt), jumia.com.eg, cairosales.com, 2b.com.eg, btech.com, dream2000.com, carrefouregypt.com,
dubaiphone.net, hardwaremarket.net, rayashop.com, eshop.vodafone.com.eg, dokkantech.com, etisalstore.com, eshop.orange.eg, lcwaikiki.eg, defacto.com.eg and Amazon Now (amazon.eg's 20-minute grocery store, its own shop): crawls whole categories, keeps each
product's price history in SQLite, and sends Arabic Telegram alerts for real discounts.
Read [README.md](README.md), [docs/GUIDE.md](docs/GUIDE.md) (design and algorithms) and
[docs/INTERNALS.md](docs/INTERNALS.md) (code map, schema, API) before changing behaviour;
[docs/MATCHING.md](docs/MATCHING.md) is how the same product is found on different shops (model codes from titles);
[docs/PRICING_RESEARCH.md](docs/PRICING_RESEARCH.md) is the research on how Amazon and noon price and on detection science
(raw notes in `docs/research/`); [docs/MONITORING.md](docs/MONITORING.md) is the runbook and [docs/COMMANDS.md](docs/COMMANDS.md) lists every command.
[docs/SHOP_RULES.md](docs/SHOP_RULES.md) lists what each shop gets wrong in its data (placeholder prices, noisy signals) and what we
do about it (`src/deals/shop_rules.py`, shown on the web UI's Rules page); [docs/PUBLISHING.md](docs/PUBLISHING.md) is the way to the public channel (the admins' review group, the Arabic posts); [docs/CATEGORIES.md](docs/CATEGORIES.md) holds category ids and shop quirks; [docs/JUMIA.md](docs/JUMIA.md), [docs/CAIROSALES.md](docs/CAIROSALES.md), [docs/2B.md](docs/2B.md), [docs/BTECH.md](docs/BTECH.md), [docs/DREAM2000.md](docs/DREAM2000.md), [docs/CARREFOUR.md](docs/CARREFOUR.md), [docs/DUBAIPHONE.md](docs/DUBAIPHONE.md), [docs/HARDWAREMARKET.md](docs/HARDWAREMARKET.md), [docs/AMAZON_NOW.md](docs/AMAZON_NOW.md), [docs/RAYA.md](docs/RAYA.md), [docs/VODAFONE.md](docs/VODAFONE.md), [docs/DOKKANTECH.md](docs/DOKKANTECH.md), [docs/ETISAL.md](docs/ETISAL.md), [docs/ORANGE.md](docs/ORANGE.md), [docs/LCWAIKIKI.md](docs/LCWAIKIKI.md) and [docs/DEFACTO.md](docs/DEFACTO.md) are the per-shop analyses. Keep these in step with the code, and
write down anything new you learn about the shops or the operations there (the user wants findings kept).

## Where things run

- **Droplet** `root@207.154.206.127` (1 GB RAM + 1 GB swap): systemd `deals-coordinator`
  (FastAPI :8787 + SQLite `/var/lib/deals/deals.db` + Telegram bot + watchdog) and `deals-worker`
  (amazon, dream2000, carrefour, raya, vodafone, dokkantech, etisal, orange and defacto only; datacenter IPs are often refused by Amazon and always by noon, Jumia, Cairo Sales, 2B,
  B.TECH's API, Dubai Phone (Vercel), Hardware Market and LC Waikiki (Cloudflare); Dream 2000 (Shopify), Carrefour (Akamai, browser fingerprint), Raya, Vodafone, Dokkan Tech, Etisal, Orange and DeFacto (Akamai, browser fingerprint) are open to everyone; Amazon Now lists nothing
  at the droplet's delivery location).
  Config: `/etc/deals/config.toml` (worker tokens, Telegram token, watches). Code: `/opt/deals`.
- **The user's Mac**: launchd agent `com.deals.worker` (all eighteen shops), env in `./worker.env`,
  log `~/Library/Logs/deals-worker.log`. `make keep-awake` (launchd `com.deals.awake`,
  `caffeinate -s`) stops it sleeping on power. It runs code from this checkout; after changing worker
  code, restart it: `launchctl kickstart -k gui/$(id -u)/com.deals.worker`. After changing `worker.env`
  (e.g. `DEALS_SITES`) run `make install-mac-worker` instead: the agent copies the env at install time.
- Telegram bot @Jerrys_deals_bot; the admin is `[telegram] chat_id`. Friends subscribe with /start
  and the admin approves, until `[telegram] review_chat_id` and `public_chat_id` are set: then deals go to the
  admins' review group and approved ones to the public channel, and /start gives the channel's link (docs/PUBLISHING.md).

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
| Pause crawling for a while | `make pause DEVICE=mac SITE=jumia_eg HOURS=3` (ends by itself), `make resume`; web UI → Devices |
| Same product on other shops | `make models` (`FIND=<code>`, `SAMPLE=10`); web UI product page, Queries → Matching |
| Database | `make sql` (live, read-only), `make dbweb` (browser), `make db` (snapshot for IntelliJ) |
| Daily report now | `make report` |

## Rules of thumb

- For any debugging, fix, feature or investigation, follow the `work-on-deals` skill
  (`.claude/skills/work-on-deals/SKILL.md`): orient, read the right doc, debug from live data, check that
  nothing broke, verify live, update the docs.

- To add a shop, follow the `add-shop` skill (`.claude/skills/add-shop/SKILL.md`): it has every stage, what to measure, the traps
  and the shipping order (code before config, or the coordinator won't start).

- The user's first principle (27 Oct 2026): flash sales can be gone in minutes, so *notice drops fast* and
  *alert only on real discounts*. Any change to scheduling, crawling or alert rules must keep the worst-case
  time-to-notice short (group `max_interval_minutes`, the deal-feed fast lanes) and the reference honest
  (own time-weighted history). The principle and its measurements are at the top of docs/GUIDE.md.
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
