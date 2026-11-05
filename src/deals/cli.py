import argparse
import html
import logging
import os
import secrets
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

from deals import config as config_mod
from curl_cffi import requests as curl_requests

from deals import audit, coupons, db, matching, planner, uptime, webui
from deals.alerts import Telegram, product_stats
from deals.db import iso, parse_iso, utcnow
from deals.models import Slice
from deals.shops import SHOPS
from deals.sites import SITES, noon_eg

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
        Path(args.data_dir or os.environ.get("DEALS_DATA_DIR", "data")),
        screenshots=os.environ.get("DEALS_SCREENSHOTS", "0") == "1")  # a headless browser per alert: not on the droplet


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
        elif a["kind"] == "coupon":
            print(f"{a['created_at'][:16]}  coupon       best {a['drop_pct']:.0%}  {a['message'].splitlines()[0]}  [{sent}]")
        else:
            print(f"{a['created_at'][:16]}  {a['kind']:<12} -{a['drop_pct']:.0%}  {_egp(a['price']):>12} "
                  f"(ref {_egp(a['reference_price'])})  score {a['score']:>3}  {a['site']}  {a['title'][:60]}  [{sent}]")
            if args.verbose:
                print(f"    {a['url']}")
    if not rows:
        print("no alerts in that period")


def cmd_errors(args) -> None:
    """The error book: one line per kind of problem, newest first, then what each kind means."""
    conn = db.connect(_config(args).db_path)
    rows = audit.listing(conn, utcnow(), days=args.days, site=args.site, category=args.kind, source=args.device,
                         open_only=not args.all, limit=args.limit)
    for e in rows:
        print(f"{_ago(e['last_seen_at']):>9}  {e['count']:>6}x  {e['category']:<18} {(e['site'] or '-'):<17} "
              f"{e['source']:<16} {e['message'][:150]}{'' if e['open'] else '  [resolved]'}")
    if not rows:
        print(f"no {'' if args.all else 'open '}problems seen in {args.days:g} days")
        return
    print("\nwhat they mean:")
    for cat in sorted({e["category"] for e in rows}):
        title, cause, fix = audit.CATEGORIES.get(cat, audit.CATEGORIES["other"])
        print(f"  {cat} ({title}): {cause}\n      -> {fix}")


def cmd_uptime(args) -> None:
    """What is up and down now, the last day's outages, and the history of changes."""
    conn = db.connect(_config(args).db_path)
    now = utcnow()
    print("now:")
    for s in uptime.current(conn, now):
        if args.all or not s["good"] or s["kind"] != "device_shop":
            print(f"  {'  ' if s['good'] else '!!'} {s['kind']:<11} {s['name']:<32} {s['state']:<8} "
                  f"since {s['since'][5:16].replace('T', ' ')} UTC  {s['reason'] or ''}")
    if outages := uptime.outages(conn, now):
        print("\ndowntime in the last 24 h:")
        for o in outages:
            print(f"  {o['kind']:<7} {o['name']:<20} down {o['times']}x, {o['down_s'] / 60:.0f} min"
                  + ("  (still down)" if o["ongoing"] else ""))
    print(f"\nchanges in {args.days:g} days (after = how long the state before lasted):")
    for c in uptime.changes(conn, now, days=args.days, name=args.name, limit=args.limit):
        if args.all or c["kind"] != "device_shop":
            after = f"{(c['lasted_s'] or 0) / 60:.0f} min" if c["lasted_s"] is not None else "-"
            print(f"  {c['at'][5:16].replace('T', ' ')}  {c['kind']:<11} {c['name']:<32} "
                  f"{c['old_state'] or '-'} -> {c['new_state']:<8} after {after:>8}{'  [told]' if c['notified'] else ''}"
                  f"  {c['reason'] or ''}")


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


def cmd_models(args) -> None:
    """Products matched across shops by model code (matching.py): coverage, one model, or a sample to check."""
    cfg = _config(args)
    conn = db.connect(cfg.db_path)
    q = lambda sql, *a: conn.execute(sql, a).fetchall()  # noqa: E731
    if args.same or args.different or args.forget:
        a, b = args.same or args.different or args.forget
        verdict = "same" if args.same else "different" if args.different else None
        matching.set_override(conn, a, b, verdict, "cli", utcnow())
        print(f"#{a} and #{b}: " + (f"{verdict} product" if verdict else "hand-made match forgotten") +
              "; applies at the next rebuild (the server's is hourly; --rebuild runs one now)")
    if args.overrides:
        for r in q("""SELECT o.a, o.b, o.verdict, o.note, o.created_at, pa.site sa, pa.title ta, pb.site sb, pb.title tb
                      FROM match_overrides o JOIN products pa ON pa.id = o.a JOIN products pb ON pb.id = o.b
                      ORDER BY o.created_at"""):
            print(f"{r['created_at'][:16]}  {r['verdict']:<9} #{r['a']} {r['sa']}: {r['ta'][:50]}  |  #{r['b']} {r['sb']}: {r['tb'][:50]}")
        return
    if args.rebuild:
        s = matching.rebuild(conn, utcnow())
        print(f"rebuilt in {s.seconds}s: {s.extracted:,} titles read; {s.in_model:,} of {s.products:,} products in a "
              f"model, {s.cross_shop:,} on 2+ shops; {s.models:,} models; {s.changed:,} rows changed")

    def show(model: str) -> None:
        m = q("SELECT * FROM models WHERE model = ?", model)[0]
        price = f"EGP {m['low'] / 100:,.0f}-{m['high'] / 100:,.0f}" if m["low"] else "no fresh price"
        how = {"mpn": "model code", "gtin": "barcode", "line": "product line + variant", "similar": "PROBABLE: type + specs",
               "manual": "set by hand"}.get(m["kind"], m["kind"])
        print(f"\n{m['model']}  (by {how}; confidence {m['confidence']} = {m['evidence']}; "
              f"{m['sites']} shops, {m['products']} listings, {price})"
              f"{'  ' + m['status'].upper() + ': ' + m['note'] if m['status'] != 'ok' else ''}")
        for r in q("""SELECT p.site, p.sku, p.price, p.in_stock, p.title, pm.status, pm.note FROM product_models pm
                      JOIN products p ON p.id = pm.product_id WHERE pm.model = ?
                      ORDER BY pm.status != 'model', p.site, p.price""", model):
            flag = "" if r["status"] == "model" else f"  [left out: {r['note']}]"
            print(f"  {r['site']:<14} {_egp(r['price']):>12} {'' if r['in_stock'] else 'sold out':<8} {r['title'][:90]}{flag}")

    if args.find:
        code = matching.norm_code(args.find.replace(" ", ""))
        found = [r[0] for r in q("SELECT model FROM models WHERE status != 'spec' AND (code = ? OR model LIKE ?) "
                                  "ORDER BY sites DESC LIMIT 20", code, f"%{code}%")]
        if not found:  # by title words: the models of the products that match
            words = args.find.split()
            found = [r[0] for r in q(
                "SELECT DISTINCT pm.model FROM products p JOIN product_models pm ON pm.product_id = p.id "
                "WHERE pm.model IS NOT NULL AND " + " AND ".join("p.title LIKE ?" for _ in words) + " LIMIT 20",
                *[f"%{w}%" for w in words])]
        for model in found:
            show(model)
        if not found:
            print(f"no model for {args.find!r}")
        return
    if args.sample:
        for r in q("SELECT model FROM models WHERE status = 'ok' AND sites >= 2 ORDER BY random() LIMIT ?", args.sample):
            show(r[0])
        return

    print(f"{'shop':<14} {'listings':>9} {'in a model':>11} {'by line':>8} {'on 2+ shops':>12}   (seen in the last 3 days)")
    for r in q("""SELECT p.site, COUNT(*) n, SUM(pm.status = 'model') model, SUM(pm.status = 'model' AND m.kind = 'line') line,
                         SUM(pm.status = 'model' AND m.status = 'ok' AND m.sites >= 2) cross_shop
                  FROM products p JOIN product_models pm ON pm.product_id = p.id LEFT JOIN models m ON m.model = pm.model
                  WHERE p.last_seen_at >= ? GROUP BY p.site ORDER BY p.site""", iso(utcnow() - timedelta(days=3))):
        print(f"{r['site']:<14} {r['n']:>9,} {r['model'] / r['n']:>10.0%} {r['line'] / r['n']:>7.0%} {r['cross_shop'] / r['n']:>11.0%}")
    print("\nlistings by status: " + ", ".join(f"{r[0]} {r[1]:,}" for r in q(
        "SELECT status, COUNT(*) FROM product_models GROUP BY status ORDER BY COUNT(*) DESC")))
    print("models by status:   " + ", ".join(f"{r[0]} {r[1]:,} ({r[2]:,} on 2+ shops)" for r in q(
        "SELECT status, COUNT(*), SUM(sites >= 2) FROM models GROUP BY status ORDER BY COUNT(*) DESC")))
    from deals import detailq
    pages = detailq.stats(conn, utcnow())
    if pages:
        print(f"\nproduct pages read (config [details]: {'on' if cfg.details.enabled else 'OFF'}, "
              f"shops {', '.join(cfg.details.sites)}):")
        for r in pages:
            paused = f"  PAUSED: {'; '.join(r['paused'])}" if r["paused"] else ""
            print(f"  {r['site']:<14} {r['pages']:>6,} read ({r['barcodes'] or 0:,} with a barcode, {r['models'] or 0:,} "
                  f"with a model number), {r['queued']:,} queued, last {_ago(r['last'])}{paused}")
    last = q("SELECT MAX(updated_at) FROM product_models")[0][0]
    print(f"last change {_ago(last)}; `deals models --sample 10` to check matches by eye, `deals models <code>` for one")


def cmd_coupons(args) -> None:
    conn = db.connect(_config(args).db_path)
    if args.find:
        rows = conn.execute("""SELECT site, sku, title, price, coupon_kind, coupon_code, coupon_title, coupon_amount, coupon_percent,
                                      coupon_cap, coupon_min_order, coupon_detail, coupon_url, coupon_min_quantity, coupon_typed FROM products
                               WHERE coupon_kind IS NOT NULL AND (sku = ? OR title LIKE ?) ORDER BY site, price LIMIT ?""",
                            (args.find, f"%{args.find}%", args.limit)).fetchall()
        for r in rows:
            after = coupons.after(r)
            print(f"{r['site']:<14} {_egp(r['price']):>12}  {f'-> {_egp(after)}' if after else '':<16} "
                  f"{r['coupon_kind']:<9} {r['coupon_code'] or '-':<12} {r['coupon_title'][:30]:<30}  {r['title'][:50]}")
        print(f"{len(rows)} products" if rows else f"no product matching {args.find!r} has a coupon")
        return
    if args.code:
        codes = [c for c in webui.coupon_codes(conn, args.site, args.code, args.limit)]
        if not codes:
            sys.exit(f"no code {args.code} seen")
        for c in codes:
            print(f"{c['site']} {c['code']}: {c['title']} ({c['kind']}, {c['state']}, first seen {_ago(c['first_seen_at'])}, "
                  f"last seen {_ago(c['last_seen_at'])}, {c['in_stock']} in stock)")
            if c["group_url"]:
                print(f"all its products: {c['group_url']}")
            for p in webui.coupon_products(conn, c["site"], c["code"], args.limit):
                print(f"  {_egp(p['price']):>12}  {p['title'][:80]}  {p['url']}")
        return
    print("products with a coupon now, by shop and kind:")
    for r in conn.execute("""SELECT site, coupon_kind k, COUNT(*) n, SUM(in_stock) stocked FROM products
                             WHERE coupon_kind IS NOT NULL GROUP BY site, k ORDER BY site, n DESC"""):
        print(f"  {r['site']:<14} {r['k']:<9} {r['n']:>7,} products, {r['stocked']:>7,} in stock")
    print("\ncodes, newest first (state: what the coupon message rule decided):")
    for c in webui.coupon_codes(conn, args.site, None, args.limit):
        best = f"best {c['best_discount']:.0%}" if c["best_discount"] is not None else ""
        print(f"  {c['site']:<14} {c['code']:<14} {c['state']:<12} {c['in_stock']:>5} in stock  {best:<9} "
              f"seen {_ago(c['last_seen_at']):<8} {c['title'][:50]}")


def cmd_coupon_group(args) -> None:
    if args.site != "noon_eg":
        sys.exit("only noon has a page of a coupon's products so far")
    session = curl_requests.Session(impersonate="chrome")
    for page in range(1, args.pages + 1):
        r = noon_eg.coupon_page(session, args.path, page)
        for x in r.listings:
            code = x.coupon.code if x.coupon else "-"
            print(f"{x.sku:<34} {_egp(x.price):>12}  {code:<12} {x.title[:70]}")
        if page == 1:
            print(f"# {r.total} products in all", file=sys.stderr)
        if not r.has_next:
            break
        time.sleep(3)


def cmd_pause(args) -> None:
    """Pause crawling by hand: a device, a shop, or both; it ends by itself."""
    from deals import scheduler
    cfg = _config(args)
    conn = db.connect(cfg.db_path)
    now = utcnow()
    if args.list:
        pass
    elif args.resume:
        n = scheduler.resume(conn, now, worker=args.device, site=args.site)
        print(f"{n} pause(s) ended" + ("" if n else " (nothing matched: see `deals pause --list`)"))
    else:
        scheduler.pause(conn, now, worker=args.device or "*", site=args.site or "*", hours=args.hours,
                        pages=args.pages, reason=args.reason or "cli")
        print(f"crawling paused: device {args.device or 'all'}, shop {args.site or 'all'}, for {args.hours:g} h"
              + ("; product pages too" if args.pages else "; product pages still read when idle"))
    pauses = scheduler.active_pauses(conn, now)
    print("\nactive pauses:" if pauses else "\nno active pauses")
    for p in pauses:
        print(f"  #{p['id']}  device {p['worker']:<10} shop {p['site']:<14} until {p['until'][:16]} UTC"
              f"{'  (pages too)' if p['pages'] else ''}  {p['reason'] or ''}")


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
        for name, cid in SHOPS[args.site].roots:
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


def cmd_passwd(args) -> None:
    """Set an admin's password (adds the admin if new). Logs them out everywhere."""
    from getpass import getpass

    from deals import auth

    password = args.password or getpass(f"new password for {args.username}: ")
    if len(password) < 6:
        sys.exit("a password needs at least 6 characters")
    conn = db.connect(_config(args).db_path)
    new = auth.set_password(conn, args.username, password, utcnow())
    print(f"{'added' if new else 'changed the password of'} {args.username}")


def cmd_shot(args) -> None:
    """Take one alert screenshot here (no coordinator), to check a shop's spec."""
    from deals import shots

    if args.site not in shots.SPECS:
        sys.exit(f"no screenshot spec for {args.site}; shops with one: {', '.join(shots.SPECS)}")
    shooter = shots.Shooter(lambda *_: None, threading.Event())
    started = time.monotonic()
    try:
        image = shooter.shoot(shots.SPECS[args.site], args.url)
    finally:
        shooter._close()
    if image is None:
        sys.exit("price block not found on the page: the alert would go out without a screenshot")
    Path(args.out).write_bytes(image)
    print(f"{args.out}: {len(image) // 1024} KB in {time.monotonic() - started:.1f}s")


def cmd_test_alert(args) -> None:
    """On the server: send the admin alone a test alert for one product, with its screenshot (taken by a
    worker started with DEALS_SCREENSHOTS=1 on its next request for that shop)."""
    from deals import shotq

    cfg, now = _config(args), utcnow()
    conn = db.connect(cfg.db_path)
    p = db.find_product(conn, args.site, args.sku)
    if p is None or not cfg.telegram_chat_id:
        sys.exit("no such product" if p is None else "no [telegram] chat_id in the config")
    last = conn.execute("SELECT message FROM alerts WHERE product_id = ? ORDER BY id DESC LIMIT 1", (p["id"],)).fetchone()
    body = last["message"] if last else f"<a href=\"{html.escape(p['url'])}\">{html.escape(p['title'][:150], quote=False)}</a>\n💰 {_egp(p['price'])}"
    with conn:
        # A system alert (admins only, never counted as a deal) handed to the admin alone, already "fanned out".
        alert_id = conn.execute(
            "INSERT INTO alerts (kind, dedupe_key, message, created_at, next_attempt_at, sent_at) VALUES ('system', ?, ?, ?, ?, ?)",
            (f"test-alert:{iso(now)}", "🧪 <b>تجربة: تنبيه مع صورة الصفحة</b>\n\n" + body, iso(now), iso(now), iso(now))).lastrowid
        conn.execute("INSERT INTO deliveries (alert_id, chat_id, next_attempt_at) VALUES (?, ?, ?)",
                     (alert_id, int(cfg.telegram_chat_id), iso(now)))
        queued = shotq.add(conn, cfg, alert_id, p["site"], p["url"], now, force=True, wait_seconds=args.wait)
    print(f"test alert {alert_id} queued for the admin; " + (
        f"waiting up to {args.wait}s for a worker's screenshot (SELECT * FROM shots WHERE alert_id = {alert_id})"
        if queued else "no screenshot (no spec for this shop, or [alerts] screenshots = false)"))


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
    p = add("coupons", cmd_coupons, "coupons seen on the shops; --code for one code's products, --find for a product's coupon")
    p.add_argument("--site")
    p.add_argument("--code")
    p.add_argument("--find", help="a sku or words of the title")
    p.add_argument("--limit", type=int, default=100)
    p = add("coupon-group", cmd_coupon_group, "fetch every product a coupon covers from the shop (run where the shop answers: the Mac)", config=False)
    p.add_argument("site")
    p.add_argument("path", help="the code's page, e.g. /eg-nov26-mobile-200")
    p.add_argument("--pages", type=int, default=5)
    p = add("report", cmd_report, "the daily report (Arabic); --send to send it to the admin now")
    p.add_argument("--send", action="store_true")
    p = add("alerts", cmd_alerts, "recent deal alerts")
    p.add_argument("--days", type=float, default=1)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--system", action="store_true", help="include system alerts")
    p = add("errors", cmd_errors, "the error book: every kind of problem seen, counted, with cause and fix")
    p.add_argument("--days", type=float, default=7, help="seen within this many days (default 7)")
    p.add_argument("--site", choices=sorted(SITES))
    p.add_argument("--kind", choices=sorted(audit.CATEGORIES), help="only this kind of problem")
    p.add_argument("--device", help="only from this worker (or 'coordinator')")
    p.add_argument("--all", action="store_true", help="resolved ones too")
    p.add_argument("--limit", type=int, default=100)
    p = add("uptime", cmd_uptime, "what is up and down now, and when shops and devices went down and came back")
    p.add_argument("--days", type=float, default=7)
    p.add_argument("--name", help="only this shop or device (e.g. jumia_eg, mac)")
    p.add_argument("--all", action="store_true", help="also every device-on-shop row (blocks)")
    p.add_argument("--limit", type=int, default=200)
    p = add("movers", cmd_movers, "biggest price drops (and rises) recently")
    p.add_argument("--days", type=float, default=1)
    p.add_argument("--group", help="only this watch group, e.g. TVs")
    p.add_argument("--min-price", type=int, default=0, help="EGP")
    p.add_argument("--limit", type=int, default=30)
    p = add("models", cmd_models, "the same product on several shops (by model code): coverage, one model, a sample")
    p.add_argument("find", nargs="?", help="a model code (WH-1000XM5) or title words")
    p.add_argument("--sample", type=int, metavar="N", help="N random models on 2+ shops with all their listings")
    p.add_argument("--rebuild", action="store_true", help="recompute the matches now (the server does it hourly)")
    p.add_argument("--same", nargs=2, type=int, metavar=("A", "B"), help="product ids A and B are the same product (B joins A)")
    p.add_argument("--different", nargs=2, type=int, metavar=("A", "B"), help="product ids A and B are different products")
    p.add_argument("--forget", nargs=2, type=int, metavar=("A", "B"), help="drop a hand-made match between A and B")
    p.add_argument("--overrides", action="store_true", help="list the hand-made matches")
    p = add("pause", cmd_pause, "pause crawling for a device and/or shop (ends by itself); --resume, --list")
    p.add_argument("--device", help="a worker name (mac, droplet...); default all")
    p.add_argument("--site", choices=sorted(SITES), help="default all shops")
    p.add_argument("--hours", type=float, default=2, help="how long (default 2, at most 168)")
    p.add_argument("--pages", action="store_true", help="also stop reading product pages")
    p.add_argument("--reason")
    p.add_argument("--resume", action="store_true", help="end the matching pauses now")
    p.add_argument("--list", action="store_true", help="only list active pauses")
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

    p = add("shot", cmd_shot, "take an alert screenshot of a product page here (checks a shop's boxes)", config=False)
    p.add_argument("site")
    p.add_argument("url", help="the product's link as stored (the English one; the Arabic page is opened)")
    p.add_argument("--out", default="shot.jpg")
    p = add("test-alert", cmd_test_alert, "send the admin alone a test alert for a product, with its screenshot")
    p.add_argument("site", choices=sorted(SITES))
    p.add_argument("sku")
    p.add_argument("--wait", type=int, default=180, help="seconds to wait for the screenshot (default 180)")
    p = add("telegram", cmd_telegram, "find your chat id, or --test sending")
    p.add_argument("--test", action="store_true")
    p = add("backup", cmd_backup, "write a consistent copy of the database")
    p.add_argument("dest", nargs="?")
    add("token", cmd_token, "generate a random worker token", config=False)
    p = add("passwd", cmd_passwd, "set an admin's web UI / API password (adds the admin if new)")
    p.add_argument("username")
    p.add_argument("--password", help="default: ask for it")

    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args.func(args)


if __name__ == "__main__":
    main()
