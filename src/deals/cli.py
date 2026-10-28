import argparse
import logging
import os
import secrets
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

from deals import config as config_mod
from deals import db, planner
from deals.alerts import Telegram, product_stats
from deals.db import iso, parse_iso, utcnow
from deals.models import Slice
from deals.sites import SITES

SPARK = "▁▂▃▄▅▆▇█"


def _config(args) -> config_mod.Config:
    path = Path(args.config or os.environ.get("DEALS_CONFIG", "config.toml"))
    if not path.exists():
        sys.exit(f"config not found: {path} (copy config.example.toml, or pass --config)")
    return config_mod.load(path)


def _egp(minor) -> str:
    return "-" if minor is None else f"EGP {minor / 100:,.0f}"


def _ago(ts: str | None) -> str:
    if not ts:
        return "never"
    secs = (utcnow() - parse_iso(ts)).total_seconds()
    future = secs < 0
    secs = abs(secs)
    text = f"{secs:.0f}s" if secs < 120 else f"{secs / 60:.0f}m" if secs < 7200 else (
        f"{secs / 3600:.1f}h" if secs < 172800 else f"{secs / 86400:.0f}d")
    return f"in {text}" if future else f"{text} ago"


# --- running -----------------------------------------------------------------

def cmd_serve(args) -> None:
    from deals.coordinator import serve

    serve(_config(args))


def cmd_worker(args) -> None:
    from deals.worker import run

    url = args.coordinator or os.environ.get("DEALS_COORDINATOR")
    token = args.token or os.environ.get("DEALS_TOKEN")
    sites = (args.sites or os.environ.get("DEALS_SITES") or ",".join(SITES)).split(",")
    if not url or not token:
        sys.exit("need --coordinator and --token (or DEALS_COORDINATOR / DEALS_TOKEN)")
    run(url, token, [s.strip() for s in sites if s.strip()],
        Path(args.data_dir or os.environ.get("DEALS_DATA_DIR", "data")))


def cmd_local(args) -> None:
    """Coordinator and a worker in one process: everything on this machine."""
    import uvicorn

    from deals.coordinator import create_app
    from deals.worker import Worker

    cfg = _config(args)
    if not cfg.workers:
        sys.exit("add at least one token under [workers] in the config")
    server = uvicorn.Server(uvicorn.Config(create_app(cfg), host="127.0.0.1", port=cfg.port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    sites = args.sites.split(",") if args.sites else list(SITES)
    worker = Worker(f"http://127.0.0.1:{cfg.port}", next(iter(cfg.workers.values())), sites, cfg.data_dir)
    worker.install_signal_handlers()
    worker.run(ship_logs=False)  # same process: the coordinator already records these lines
    server.should_exit = True


# --- looking at the data ----------------------------------------------------------

def cmd_status(args) -> None:
    cfg = _config(args)
    conn = db.connect(cfg.db_path)
    now = utcnow()
    q = lambda sql, *a: conn.execute(sql, a).fetchall()  # noqa: E731

    products = q("SELECT COUNT(*) n, SUM(in_stock) s FROM products")[0]
    changes = q("SELECT COUNT(*) n FROM price_history WHERE observed_at >= ?", iso(now - timedelta(days=1)))[0]
    alerts = q("SELECT COUNT(*) n, SUM(sent_at IS NULL AND attempts < 10) pending FROM alerts "
               "WHERE created_at >= ? AND kind != 'system'", iso(now - timedelta(days=1)))[0]
    print(f"database   {cfg.db_path}  ({cfg.db_path.stat().st_size / 1e6:.1f} MB)")
    subs = {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM subscribers GROUP BY status")}
    print(f"telegram   {subs.get('active', 0)} subscribed, {subs.get('pending', 0)} waiting for approval")
    print(f"products   {products['n']:,} tracked, {products['s'] or 0:,} in stock; "
          f"{changes['n']:,} price/stock changes and {alerts['n']} deal alerts in the last 24h "
          f"({alerts['pending'] or 0} waiting to send)")

    print("\nworkers")
    for w in q("SELECT * FROM workers ORDER BY last_seen_at DESC"):
        print(f"  {w['name']:<12} {w['last_ip'] or '':<16} seen {_ago(w['last_seen_at']):<10} "
              f"sites {w['sites'] or '-':<18} pages {w['pages']:,}  jobs {w['jobs_done']:,}  v{w['version']}")
    blocks = q("SELECT * FROM blocks WHERE until > ?", iso(now))
    if blocks:
        print("\nblocked (paused)")
        for b in blocks:
            print(f"  {b['site']:<10} {b['ip']:<16} until {b['until'][11:16]} UTC  strikes {b['strikes']}  {b['reason']}")

    print("\nsites")
    for r in q("""SELECT site, COUNT(*) n, SUM(kind = 'crawl') slices, SUM(next_due_at <= ?) due,
                         SUM(lease_expires_at > ?) leased, SUM(failures > 0) failing,
                         MIN(next_due_at) oldest_due, SUM(last_done_at IS NULL AND kind = 'crawl') never
                  FROM jobs GROUP BY site""", iso(now), iso(now)):
        print(f"  {r['site']:<10} {r['slices'] or 0} slices, {r['never'] or 0} never crawled, {r['due'] or 0} due now "
              f"(oldest due {_ago(r['oldest_due'])}), {r['leased'] or 0} running, {r['failing'] or 0} failing")

    print("\ngroups")
    for r in q("""SELECT grp, site, COUNT(*) slices, SUM(expected_total) total, MIN(last_done_at) oldest,
                         AVG(interval_s) / 60 avg_interval
                  FROM jobs WHERE kind = 'crawl' GROUP BY grp, site ORDER BY grp, site"""):
        print(f"  {r['grp']:<22} {r['site']:<10} {r['slices']:>4} slices ~{r['total'] or 0:>6,} products  "
              f"revisit ~{r['avg_interval']:.0f} min  stalest {_ago(r['oldest'])}")
    failing = q("SELECT site, kind, target, failures, last_error FROM jobs WHERE failures > 0 "
                "ORDER BY failures DESC LIMIT 5")
    if failing:
        print("\nfailing jobs")
        for f in failing:
            print(f"  {f['site']} {f['kind']} {f['target']}  x{f['failures']}: {f['last_error']}")


def cmd_alerts(args) -> None:
    cfg = _config(args)
    conn = db.connect(cfg.db_path)
    rows = conn.execute(
        """SELECT a.*, p.title, p.site, p.url FROM alerts a LEFT JOIN products p ON p.id = a.product_id
           WHERE a.created_at >= ? AND (? OR a.kind != 'system') ORDER BY a.created_at DESC LIMIT ?""",
        (iso(utcnow() - timedelta(days=args.days)), args.system, args.limit),
    ).fetchall()
    for a in rows:
        sent = "sent" if a["sent_at"] and not a["error"] else (a["error"] or "pending")
        if a["kind"] == "system":
            print(f"{a['created_at'][:16]}  SYSTEM  {a['message']}  [{sent}]")
        else:
            print(f"{a['created_at'][:16]}  {a['kind']:<12} -{a['drop_pct']:.0%}  {_egp(a['price']):>12} "
                  f"(ref {_egp(a['reference_price'])})  score {a['score']:>3}  {a['site']}  {a['title'][:60]}  [{sent}]")
            if args.verbose:
                print(f"    {a['url']}")
    if not rows:
        print("no alerts in that period")


def cmd_movers(args) -> None:
    """Biggest price moves (down and up) in the last N days."""
    cfg = _config(args)
    conn = db.connect(cfg.db_path)
    since = iso(utcnow() - timedelta(days=args.days))
    rows = conn.execute(
        """WITH last AS (
             SELECT h.product_id, h.price, h.observed_at,
                    LAG(h.price) OVER (PARTITION BY h.product_id ORDER BY h.observed_at, h.id) AS before
             FROM price_history h)
           SELECT p.site, p.sku, p.title, p.grp, l.before, l.price, l.observed_at,
                  (l.price - l.before) * 1.0 / l.before AS move
           FROM last l JOIN products p ON p.id = l.product_id
           WHERE l.observed_at >= ? AND l.before > 0 AND l.price IS NOT NULL
             AND (? IS NULL OR p.grp = ?) AND p.price >= ?
           ORDER BY move LIMIT ?""",
        (since, args.group, args.group, args.min_price * 100, args.limit),
    ).fetchall()
    for r in rows:
        print(f"{r['move']:+6.0%}  {_egp(r['before']):>12} -> {_egp(r['price']):>12}  {r['observed_at'][:16]}  "
              f"{r['site']:<9} {r['sku']:<12} {(r['grp'] or ''):<16} {r['title'][:55]}")
    if not rows:
        print("no price moves in that period")


def cmd_product(args) -> None:
    cfg = _config(args)
    conn = db.connect(cfg.db_path)
    p = db.find_product(conn, args.site, args.sku)
    if p is None:  # noon items are tracked per seller: SKU@seller
        offers = conn.execute("SELECT sku, seller, price FROM products WHERE site = ? AND sku LIKE ? ORDER BY price",
                              (args.site, args.sku + "@%")).fetchall()
        if len(offers) == 1:
            p = db.find_product(conn, args.site, offers[0]["sku"])
        elif offers:
            print(f"{args.sku} has several sellers; pick one:")
            for o in offers:
                print(f"  {o['sku']:<40} {_egp(o['price']):>12}  {o['seller']}")
            return
        else:
            sys.exit(f"no product {args.site} {args.sku}")
    s = product_stats(conn, p["id"], utcnow())
    print(f"{p['title']}\n{p['url']}\n{p['site']} {p['sku']}  group {p['grp'] or '-'}  brand {p['brand'] or '-'}")
    stock = "in stock" if p["in_stock"] else "OUT OF STOCK"
    if p["stock_left"]:
        stock += f", only {p['stock_left']} left"
    print(f"\nnow        {_egp(p['price'])}  ({stock}; since {_ago(iso(s.current_since)) if s.current_since else '-'})")
    if p["list_price"]:
        print(f"site says  {_egp(p['list_price'])} ({p['list_price_kind'] or '?'} price)")
    print(f"typical    30d {_egp(s.typical_30d)}   90d {_egp(s.typical_90d)}")
    print(f"range      all-time {_egp(s.low)} – {_egp(s.high)}   90d {_egp(s.low_90d)} – {_egp(s.high_90d)}")
    print(f"tracked    {s.tracked_days:.1f} days, {s.changes_90d} changes in 90 days, last seen {_ago(p['last_seen_at'])}")
    if p["badges"]:
        print(f"badges     {p['badges']}")
    hist = db.history(conn, p["id"])
    prices = [h["price"] for h in hist if h["price"] is not None]
    if len(prices) > 1:
        lo, hi = min(prices), max(prices)
        print("trend      " + "".join(SPARK[0 if hi == lo else int((x - lo) / (hi - lo) * 7)] for x in prices[-60:]))
    print("\nhistory")
    for h in hist[-args.limit:]:
        flag = "" if h["in_stock"] else "  (out of stock)"
        print(f"  {h['observed_at'][:16]}  {_egp(h['price']):>12}{flag}")


# --- setup helpers ------------------------------------------------------------------

def cmd_categories(args) -> None:
    site = SITES[args.site](Path(args.data_dir))
    if args.id:
        result = site.fetch_page(Slice("category", args.id), 1)
        print(f"{args.id}: {result.total if result.total is not None else 'over 1,000'} products")
        for c in result.subcategories:
            print(f"  {c.name:<45} category:{c.id}")
    else:
        print("give a category id to list its children; known roots:")
        for name, cid in {"amazon_eg": [("Electronics", "18018102031"), ("Home", "18021933031"),
                                        ("Appliances", "21863792031"), ("Video Games", "18022560031")],
                          "noon_eg": [("Electronics & Mobiles", "electronics-and-mobiles"),
                                      ("Home & Kitchen", "home-and-kitchen")],
                          "jumia_eg": [("Electronics", "electronics"), ("Phones & Tablets", "phones-tablets"),
                                       ("Computing", "computing"), ("Home & Office", "home-office"),
                                       ("Gaming", "video-games")],
                          "cairosales_eg": [("TVs & Screens", "tvs-screens"), ("Air Conditioners", "air-conditioners"),
                                            ("Refrigerators", "refrigerators"), ("Mobiles", "mobiles-tablets"),
                                            ("Small Appliances", "small-appliances"),
                                            ("Computers", "computer-accessories"), ("Gaming", "gaming-accessories"),
                                            ("Audio", "home-theaters-speakers")],
                          "2b_eg": [("Computer", "118"), ("Mobile & Tablet", "266"), ("Home Appliances", "2370"),
                                    ("Televisions", "1932"), ("Audio", "2382"), ("Gaming", "120"),
                                    ("Electronics", "610")],
                          "btech_eg": [("TVs & Projectors", "tvs-projectors"), ("Large Home Appliances", "large-home-appliances"),
                                       ("Small Home Appliances", "small-home-appliances"), ("Mobiles & Tablets", "mobiles-tablets"),
                                       ("Laptops & PCs", "laptop-pc"), ("Audio & Recording", "audio-recording"),
                                       ("Gaming Area", "gaming-area"), ("Electronics", "electronics"),
                                       ("Wearables", "wearables"), ("Personal Care", "personal-care")],
                          "dream2000_eg": [("TVs", "tvs"), ("Mobiles", "mobiles"), ("Tablets", "tablets"), ("Laptops", "laptop"),
                                           ("Air Conditioners", "air-conditioners"), ("Refrigerators", "refrigerator-and-deep-freezer"),
                                           ("Kitchen Appliances", "kitchen-appliances"), ("Gaming", "gaming"),
                                           ("Audio", "audio"), ("Electronics", "electronics"),
                                           ("every collection (386)", "https://dream2000.com/en/collections.json?limit=250&page=1")],
                          "carrefour_eg": [("Electronics & Appliances", "NFEGY4000000"), ("Mobiles, Tablets & Wearables", "NFEGY1200000"),
                                           ("Small Appliances", "NFEGY4040000"), ("Large Appliances", "NFEGY4100000"),
                                           ("Computers & Office", "NFEGY4070000"), ("TVs & Projectors", "NFEGY4080000")]}[args.site]:
            print(f"  {name:<25} {cid}")


def cmd_plan(args) -> None:
    site = SITES[args.site](Path(args.data_dir))
    planned = planner.plan(site, Slice.from_key(args.root), delay=(args.min_delay, args.max_delay))
    print(f"\n{len(planned)} slices, ~{sum(p.total or 0 for p in planned):,} products (dry run, nothing saved)")


def cmd_crawl(args) -> None:
    site = SITES[args.site](Path(args.data_dir))
    target = Slice.from_key(args.target)
    for page in range(1, args.pages + 1):
        r = site.fetch_page(target, page)
        print(f"page {page}: {len(r.listings)} products, total {r.total}, next {r.has_next}")
        for x in r.listings[: args.show]:
            extra = " ".join(filter(None, [f"left={x.stock_left}" if x.stock_left else "",
                                           ",".join(x.badges), f"{x.list_price_kind}={_egp(x.list_price)}"
                                           if x.list_price else ""]))
            print(f"  {x.sku:<12} {_egp(x.price):>12}  {extra:<40} {x.title[:60]}")
        if not r.has_next:
            break
        time.sleep(3)


def cmd_telegram(args) -> None:
    cfg = _config(args)
    if not cfg.telegram_token:
        sys.exit("set [telegram] bot_token in the config first (create a bot with @BotFather)")
    tg = Telegram(cfg.telegram_token)
    if args.test:
        if not cfg.telegram_chat_id:
            sys.exit("set [telegram] chat_id first (run 'deals telegram' after messaging your bot)")
        tg.send(cfg.telegram_chat_id, "✅ تم ربط بوت العروض بنجاح. ستصلك تنبيهات العروض الحقيقية هنا.")
        print("test message sent")
        return
    chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name") or u["message"]["chat"].get("title")
             for u in tg.updates() if "message" in u}
    if not chats:
        print("no messages yet: open your bot in Telegram, press Start / send it any message, then run this again")
    for chat_id, name in chats.items():
        print(f"chat_id = \"{chat_id}\"   ({name})")


def cmd_health(args) -> None:
    from deals.health import FAIL, ICON, run_checks

    cfg = _config(args)
    checks = run_checks(db.connect(cfg.db_path), cfg, utcnow())
    for c in checks:
        print(f"{ICON[c.level]} {c.name:<18} {c.detail}")
    failed = sum(c.level == FAIL for c in checks)
    warned = sum(c.level == "warn" for c in checks)
    print(f"\n{len(checks)} checks: {failed} failing, {warned} warnings")
    sys.exit(1 if failed else 0)


def cmd_report(args) -> None:
    from deals.health import daily_report

    cfg = _config(args)
    text = daily_report(db.connect(cfg.db_path), cfg, utcnow())
    if args.send:
        Telegram(cfg.telegram_token).send(cfg.telegram_chat_id, text)
        print("report sent to the admin chat")
    else:
        print(text)


def _api(args, path: str, **params) -> dict:
    from curl_cffi import requests

    url = (args.coordinator or os.environ.get("DEALS_COORDINATOR") or "http://127.0.0.1:8787").rstrip("/")
    token = args.token or os.environ.get("DEALS_TOKEN")
    r = requests.get(url + path, params={k: v for k, v in params.items() if v is not None},
                     headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=20)
    if r.status_code != 200:
        sys.exit(f"coordinator answered HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


STATE_ICON = {"ok": "🟢", "crawling": "🔵", "idle": "⚪", "blocked": "🟠", "failing": "🔴", "offline": "🔴"}


def cmd_devices(args) -> None:
    data = _api(args, "/api/devices")
    print(f"devices at {data['generated_at']} UTC\n")
    for d in data["devices"]:
        mark = "online" if d["online"] else "OFFLINE"
        print(f"{d['name']}  {d['ip']}  v{d['version']}  {mark}, seen {d['last_seen_seconds']}s ago, "
              f"{d['pages']:,} pages, {d['jobs_done']:,} jobs")
        for s in d["sites"]:
            print(f"   {STATE_ICON.get(s['state'], '?')} {s['site']:<10} {s['state']:<9} {s['detail']}")
            if s["last_error"] and s["state"] != "failing":
                print(f"      last problem {s['last_error_at'][11:16]} UTC: {s['last_error']}")
        print()
    for s in data["sites"]:
        print(f"{s['site']:<10} {s['products']:,} products, {s['slices']} slices, {s['due']} due now, "
              f"{s['running']} running, {s['failing']} failing, last finished {(s['last_done'] or '-')[11:19]} UTC")
    if data["problems"]:
        print("\nneeds attention:")
        for p in data["problems"]:
            print(f"   {p['level']}: {p['name']}: {p['detail']}")


def cmd_logs(args) -> None:
    after = 0
    while True:
        rows = _api(args, "/api/events", source=args.source, level=args.level, after_id=after,
                    limit=args.limit if not after else 500)["events"]
        for e in rows:
            print(f"{e['at'][5:19].replace('T', ' ')}  {e['source']:<12} {e['level']:<7} {e['message']}")
            after = max(after, e["id"])
        if not args.follow:
            return
        time.sleep(3)


def cmd_backup(args) -> None:
    cfg = _config(args)
    dest = Path(args.dest or cfg.data_dir / "backups" / f"manual-{utcnow():%Y-%m-%d-%H%M}.db.gz")
    db.backup(db.connect(cfg.db_path), dest)
    print(f"backup written to {dest}")


def cmd_token(args) -> None:
    print(secrets.token_urlsafe(24))


def main() -> None:
    parser = argparse.ArgumentParser(prog="deals", description="e-commerce price tracker and deal alerts")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(required=True, metavar="command")

    def add(name, func, help_text, config=True):
        p = sub.add_parser(name, help=help_text)
        if config:
            p.add_argument("--config", help="config file (default: $DEALS_CONFIG or ./config.toml)")
        p.set_defaults(func=func)
        return p

    add("serve", cmd_serve, "run the coordinator (API + scheduler + alerts)")
    p = add("worker", cmd_worker, "run a worker that crawls for a coordinator", config=False)
    p.add_argument("--coordinator", help="e.g. http://207.154.206.127:8787 (or $DEALS_COORDINATOR)")
    p.add_argument("--token", help="this worker's token (or $DEALS_TOKEN)")
    p.add_argument("--sites", help="comma-separated, default all (or $DEALS_SITES)")
    p.add_argument("--data-dir", help="for cookies (or $DEALS_DATA_DIR, default ./data)")
    p = add("local", cmd_local, "coordinator + worker in one process on this machine")
    p.add_argument("--sites", help="comma-separated, default all")

    add("status", cmd_status, "workers, blocks, backlog and freshness")
    add("health", cmd_health, "pass/warn/fail checks of everything (exit code 1 on failure)")
    for name, func, help_text in [("devices", cmd_devices, "every connected device and its fetch state per shop"),
                                  ("logs", cmd_logs, "log lines from all devices and the coordinator")]:
        p = add(name, func, help_text, config=False)
        p.add_argument("--coordinator", help="default $DEALS_COORDINATOR or http://127.0.0.1:8787")
        p.add_argument("--token", help="a worker token (default $DEALS_TOKEN); not needed on the server itself")
        if name == "logs":
            p.add_argument("--source", help="only this device (e.g. mac, droplet) or 'coordinator'")
            p.add_argument("--level", help="info (default), warning or error")
            p.add_argument("--limit", type=int, default=50)
            p.add_argument("-f", "--follow", action="store_true", help="keep printing new lines")
    p = add("report", cmd_report, "the daily report (Arabic); --send to send it to the admin now")
    p.add_argument("--send", action="store_true")
    p = add("alerts", cmd_alerts, "recent deal alerts")
    p.add_argument("--days", type=float, default=1)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--system", action="store_true", help="include system alerts")
    p = add("movers", cmd_movers, "biggest price drops (and rises) recently")
    p.add_argument("--days", type=float, default=1)
    p.add_argument("--group", help="only this watch group, e.g. TVs")
    p.add_argument("--min-price", type=int, default=0, help="EGP")
    p.add_argument("--limit", type=int, default=30)
    p = add("product", cmd_product, "price history and stats of one product")
    p.add_argument("site", choices=sorted(SITES))
    p.add_argument("sku")
    p.add_argument("--limit", type=int, default=30, help="history rows to show")

    for name, func, help_text in [("categories", cmd_categories, "list child categories (to find ids for config)"),
                                  ("plan", cmd_plan, "dry-run the planner for a root"),
                                  ("crawl", cmd_crawl, "fetch and print pages of a slice (parser check)")]:
        p = add(name, func, help_text, config=False)
        p.add_argument("site", choices=sorted(SITES))
        p.add_argument("--data-dir", default="data")
        if name == "categories":
            p.add_argument("id", nargs="?")
        elif name == "plan":
            p.add_argument("root", help="'category:<id>' or 'search:<words>', optional '|price:<min>-<max>'")
            p.add_argument("--min-delay", type=float, default=2.0)
            p.add_argument("--max-delay", type=float, default=4.0)
        else:
            p.add_argument("target")
            p.add_argument("--pages", type=int, default=1)
            p.add_argument("--show", type=int, default=10)

    p = add("telegram", cmd_telegram, "find your chat id, or --test sending")
    p.add_argument("--test", action="store_true")
    p = add("backup", cmd_backup, "write a consistent copy of the database")
    p.add_argument("dest", nargs="?")
    add("token", cmd_token, "generate a random worker token", config=False)

    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args.func(args)


if __name__ == "__main__":
    main()
