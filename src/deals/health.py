"""What "working" means, in one place: health checks and the daily report.

`deals health` prints the checks (exit code 1 if any fail); `make health` adds
server/log/code checks around it. The watchdog sends the daily report to the
admin on Telegram every morning.
"""

import html
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from deals import db, scheduler
from deals.alerts import KIND_TITLE, SITE_NAME
from deals.config import Config
from deals.db import iso, parse_iso

CAIRO = ZoneInfo("Africa/Cairo")
OK, WARN, FAIL = "ok", "warn", "fail"
ICON = {OK: "✅", WARN: "⚠️", FAIL: "❌"}


@dataclass(frozen=True)
class Check:
    name: str
    level: str
    detail: str  # English, for the terminal
    ar: str = ""  # Arabic, for the daily report (only shown when not ok)


def run_checks(conn: sqlite3.Connection, cfg: Config, now: datetime) -> list[Check]:
    checks: list[Check] = []
    add = checks.append
    one = lambda sql, *a: conn.execute(sql, a).fetchone()  # noqa: E731

    # database
    quick = one("PRAGMA quick_check")[0]
    version = one("PRAGMA user_version")[0]
    size_mb = cfg.db_path.stat().st_size / 1e6 if cfg.db_path.exists() else 0
    counts = one("SELECT (SELECT COUNT(*) FROM products) p, (SELECT COUNT(*) FROM price_history) h")
    if quick != "ok":
        add(Check("database", FAIL, f"integrity problem: {quick}", "مشكلة في سلامة قاعدة البيانات"))
    elif version < db.schema_version():
        add(Check("database", FAIL, f"schema v{version}, code expects v{db.schema_version()}", "إصدار قاعدة البيانات لا يطابق الكود"))
    elif version > db.schema_version():  # older code on a newer database (a rollback): it still works
        add(Check("database", WARN, f"schema v{version} is newer than this code (v{db.schema_version()}): "
                                    f"rolled back? Fine to run; deploy the newer code to clear this",
                  "قاعدة البيانات أحدث من الكود (رجوع لإصدار سابق؟)"))
    else:
        add(Check("database", OK, f"intact, schema v{version}, {size_mb:.0f} MB, "
                                  f"{counts['p']:,} products, {counts['h']:,} price points"))

    # telegram
    if not (cfg.telegram_token and cfg.telegram_chat_id):
        add(Check("telegram", FAIL, "not configured: alerts are only stored", "تيليجرام غير مربوط"))
    else:
        subs = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM subscribers GROUP BY status")}
        failed = one("""SELECT COUNT(*) FROM deliveries WHERE sent_at IS NULL AND attempts >= 10 AND chat_id IN
                        (SELECT chat_id FROM subscribers WHERE status = 'active' OR is_admin = 1)
                        AND next_attempt_at >= ?""", iso(now - timedelta(days=1)))[0]
        stuck = one("SELECT COUNT(*) FROM deliveries WHERE sent_at IS NULL AND attempts < 10 AND id IN "
                    "(SELECT d.id FROM deliveries d JOIN alerts a ON a.id = d.alert_id WHERE a.created_at < ?)",
                    iso(now - timedelta(minutes=15)))[0]
        detail = f"{subs.get('active', 0)} subscribed, {subs.get('pending', 0)} waiting for approval"
        if failed:
            add(Check("telegram", FAIL, f"{failed} messages failed for good in 24h; {detail}", f"{failed} رسالة فشل إرسالها"))
        elif stuck:
            add(Check("telegram", WARN, f"{stuck} messages waiting > 15 min; {detail}", f"{stuck} رسالة متأخرة في الإرسال"))
        else:
            add(Check("telegram", OK, detail))

    # workers
    recent = conn.execute("SELECT * FROM workers WHERE last_seen_at >= ?", (iso(now - timedelta(days=7)),)).fetchall()
    if not recent:
        add(Check("workers", FAIL, "no worker has connected in 7 days", "لا يوجد أي جهاز يعمل"))
    for w in recent:
        silent = (now - parse_iso(w["last_seen_at"])).total_seconds() / 60
        level = OK if silent < 15 else WARN if silent < 60 else FAIL
        add(Check(f"worker {w['name']}", level, f"last seen {silent:.0f} min ago ({w['sites']}), "
                                                f"{w['pages']:,} pages, {w['jobs_done']:,} jobs",
                  f"الجهاز {w['name']} لم يتواصل منذ {silent:.0f} دقيقة"))

    # crawling, per site
    for r in conn.execute(
            """SELECT site, MAX(last_done_at) last_done, SUM(failures >= 3) broken, SUM(failures > 0) failing,
                      MIN(CASE WHEN kind = 'crawl' THEN next_due_at END) oldest_due, SUM(oversized) oversized,
                      SUM(kind = 'crawl') slices, SUM(kind = 'crawl' AND last_done_at IS NULL) never
               FROM jobs GROUP BY site""").fetchall():
        site = r["site"]
        ago = (now - parse_iso(r["last_done"])).total_seconds() / 60 if r["last_done"] else None
        overdue = (now - parse_iso(r["oldest_due"])).total_seconds() / 60 if r["oldest_due"] else 0
        detail = (f"last slice finished {'never' if ago is None else f'{ago:.0f} min ago'}, {r['slices']} slices "
                  f"({r['never']} not crawled yet), most overdue {max(0, overdue):.0f} min")
        name_ar = SITE_NAME.get(site, site)
        if hold := scheduler.site_paused(conn, site, now):
            add(Check(f"crawl {site}", WARN, f"paused by hand until {hold['until'][:16]} UTC"
                      + (f" ({hold['reason']})" if hold["reason"] else "") + f"; {detail}",
                      f"{name_ar}: الجمع متوقف يدويًا حتى {hold['until'][11:16]}"))
        elif ago is None or ago > 180:
            add(Check(f"crawl {site}", FAIL, detail, f"{name_ar}: لم يكتمل أي جمع منذ أكثر من 3 ساعات"))
        elif ago > 60 or overdue > 120:
            add(Check(f"crawl {site}", WARN, detail, f"{name_ar}: الجمع متأخر"))
        else:
            add(Check(f"crawl {site}", OK, detail))
        if r["broken"]:
            add(Check(f"jobs {site}", FAIL, f"{r['broken']} slices failed 3+ times in a row (deals status shows why)",
                      f"{name_ar}: {r['broken']} شريحة تفشل باستمرار"))
        elif r["failing"]:
            add(Check(f"jobs {site}", WARN, f"{r['failing']} slices failed recently and are retrying",
                      f"{name_ar}: {r['failing']} شريحة فشلت وتعيد المحاولة"))
        if r["oversized"]:
            add(Check(f"coverage {site}", WARN, f"{r['oversized']} slices too big to crawl completely",
                      f"{name_ar}: {r['oversized']} شريحة أكبر من أن تُجمع بالكامل"))

    for b in conn.execute("SELECT * FROM blocks WHERE until > ?", (iso(now),)).fetchall():
        add(Check(f"blocked {b['site']}", WARN, f"{b['ip']} paused until {b['until'][11:16]} UTC "
                                                f"(strike {b['strikes']}): {b['reason']}",
                  f"{SITE_NAME.get(b['site'], b['site'])} يحظر {b['ip']} مؤقتًا"))

    # data quality
    bad = one("SELECT COUNT(*) FROM products WHERE price <= 0 OR (list_price IS NOT NULL AND list_price <= 0)")[0]
    flips = one("""WITH h AS (SELECT product_id, price, LAG(price) OVER w prev, LAG(price, 2) OVER w prev2
                              FROM price_history WHERE observed_at >= ?
                              WINDOW w AS (PARTITION BY product_id ORDER BY observed_at, id))
                   SELECT COUNT(DISTINCT product_id) FROM h
                   WHERE prev2 = price AND ABS(prev - price) >= price * 0.05""",  # amazon's featured offer wobbles 2-5%
                iso(now - timedelta(days=1)))[0]
    if bad:
        add(Check("data", FAIL, f"{bad} products with zero/negative prices (parser bug?)", "أسعار غير صحيحة في البيانات"))
    elif flips > 50:
        add(Check("data", WARN, f"{flips} products flip-flopped A→B→A in 24h (mixed offers?)", "أسعار تتذبذب بشكل غير طبيعي"))
    else:
        add(Check("data", OK, f"no bad prices, {flips} flip-flops in 24h"))

    # disk and backups
    usage = shutil.disk_usage(cfg.data_dir)
    free = usage.free / usage.total
    add(Check("disk", OK if free >= 0.2 else WARN if free >= 0.1 else FAIL,
              f"{usage.free / 1e9:.1f} GB free ({free:.0%})",
              f"المساحة على الخادم قليلة: {usage.free / 1e9:.1f} جيجابايت متبقية ({free:.0%})"))
    backups = sorted((cfg.data_dir / "backups").glob("deals-*.db*"))
    if not backups:
        add(Check("backup", WARN, "no daily backup yet", "لا توجد نسخة احتياطية بعد"))
    else:
        age = (now.timestamp() - backups[-1].stat().st_mtime) / 3600
        add(Check("backup", OK if age < 26 else WARN, f"latest {backups[-1].name}, {age:.0f} h old, {len(backups)} kept",
                  "آخر نسخة احتياطية أقدم من يوم"))
    return checks


def daily_report(conn: sqlite3.Connection, cfg: Config, now: datetime) -> str:
    """The admin's morning summary on Telegram (Arabic)."""
    since = iso(now - timedelta(days=1))
    one = lambda sql, *a: conn.execute(sql, a).fetchone()  # noqa: E731
    checks = run_checks(conn, cfg, now)
    problems = [c for c in checks if c.level != OK]

    local = now.astimezone(CAIRO)
    lines = [f"📋 <b>التقرير اليومي</b> · {local:%Y-%m-%d}"]
    if problems:
        worst = FAIL if any(c.level == FAIL for c in problems) else WARN
        lines.append(f"{ICON[worst]} يحتاج انتباه:")
        lines += [f"  {ICON[c.level]} {html.escape(c.ar or c.detail, quote=False)}" for c in problems]
    else:
        lines.append("✅ كل شيء يعمل بشكل طبيعي")

    per_site = {r["site"]: r["n"] for r in conn.execute("SELECT site, COUNT(*) n FROM products GROUP BY site")}
    sites = " · ".join(f"{SITE_NAME.get(s, s)} {n:,}" for s, n in per_site.items())
    changes = one("SELECT COUNT(*) FROM price_history WHERE observed_at >= ?", since)[0]
    by_kind = {r["kind"]: r["n"] for r in conn.execute(
        "SELECT kind, COUNT(*) n FROM alerts WHERE created_at >= ? AND kind != 'system' "
        "AND (error IS NULL OR error NOT LIKE 'cancelled%') GROUP BY kind", (since,))}
    subs = one("SELECT COUNT(*) FROM subscribers WHERE status = 'active'")[0]
    lines += [
        "",
        f"📦 منتجات نتابعها: {sum(per_site.values()):,} ({sites})",
        f"🔄 تغييرات أسعار خلال 24 ساعة: {changes:,}",
        f"🔔 عروض أُرسلت: {sum(by_kind.values())}"
        + (" (" + " · ".join(f"{KIND_TITLE[k].split(' ', 1)[1]} {n}" for k, n in by_kind.items()) + ")" if by_kind else ""),
        f"👥 المشتركون: {subs}",
    ]
    workers = conn.execute("SELECT name, last_seen_at FROM workers WHERE last_seen_at >= ?",
                           (iso(now - timedelta(days=7)),)).fetchall()
    if workers:
        state = lambda w: "✅" if now - parse_iso(w["last_seen_at"]) < timedelta(minutes=15) else "💤"  # noqa: E731
        lines.append("🖥️ الأجهزة: " + " · ".join(f"{html.escape(w['name'])} {state(w)}" for w in workers))

    top = conn.execute(
        """SELECT a.drop_pct, a.price, p.title, p.url FROM alerts a JOIN products p ON p.id = a.product_id
           WHERE a.created_at >= ? AND a.kind != 'system' AND (a.error IS NULL OR a.error NOT LIKE 'cancelled%')
           ORDER BY a.score DESC, a.drop_pct DESC LIMIT 5""",
        (since,)).fetchall()
    if top:
        lines += ["", "🔥 <b>أفضل عروض آخر 24 ساعة</b>"]
        for i, t in enumerate(top, 1):
            lines.append(f"{i}. <a href=\"{html.escape(t['url'])}\">{html.escape(t['title'][:60], quote=False)}</a>"
                         f" · خصم {t['drop_pct']:.0%} · {t['price'] / 100:,.0f} جنيه")
    return "\n".join(lines)


def report_due(conn: sqlite3.Connection, now: datetime, hour: int = 9) -> str | None:
    """The dedupe key for today's report if it's time to send it, else None."""
    local = now.astimezone(CAIRO)
    key = f"report:{local:%Y-%m-%d}"
    if local.hour < hour:
        return None
    if conn.execute("SELECT 1 FROM alerts WHERE dedupe_key = ?", (key,)).fetchone():
        return None
    return key
