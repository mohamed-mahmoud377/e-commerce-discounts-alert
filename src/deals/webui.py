"""The web UI at /ui: one page (plain HTML + JS) over JSON endpoints.

Viewers only read. The admin token can also do what the bot's /subscribers buttons do
(approve, reject, remove, helper admin) and send the daily report now; nothing here can
affect crawling. Login is a secret token kept in an HttpOnly cookie.

Built for the 1 GB droplet: the server sends one static page and small JSON; lists are
paged in SQL, and the browser does the drawing (charts, filters over a loaded page).
"""

import html
import os
import re
import sqlite3
from datetime import datetime, timedelta

from deals import db, health, monitor, scheduler
from deals.alerts import product_stats
from deals.config import Config
from deals.db import iso

COOKIE = "deals_ui"


def overview(conn: sqlite3.Connection, cfg: Config, now: datetime, revision: str) -> dict:
    since = iso(now - timedelta(days=1))
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    checks = health.run_checks(conn, cfg, now)
    return {
        "generated_at": iso(now)[:19],
        "revision": revision,
        "checks": [{"name": c.name, "level": c.level, "detail": c.detail} for c in checks],
        "totals": {
            "products": one("SELECT COUNT(*) FROM products"),
            "in_stock": one("SELECT COUNT(*) FROM products WHERE in_stock"),
            "changes_24h": one("SELECT COUNT(*) FROM price_history WHERE observed_at >= ?", since),
            "alerts_24h": one("SELECT COUNT(*) FROM alerts WHERE created_at >= ? AND kind != 'system'", since),
            "atl_24h": one("SELECT COUNT(*) FROM alerts WHERE created_at >= ? AND kind = 'all_time_low'", since),
            "subscribers": one("SELECT COUNT(*) FROM subscribers WHERE status = 'active'"),
            "pending": one("SELECT COUNT(*) FROM subscribers WHERE status = 'pending'"),
            "warnings_24h": one("SELECT COUNT(*) FROM events WHERE at >= ? AND level IN ('WARNING', 'ERROR')", since),
            "errors_24h": one("SELECT COUNT(*) FROM events WHERE at >= ? AND level = 'ERROR'", since),
        },
        "latest": [dict(r) for r in conn.execute(
            """SELECT a.id, a.created_at, a.kind, a.price, a.drop_pct, a.product_id, p.site, p.title, p.grp
               FROM alerts a JOIN products p ON p.id = a.product_id
               WHERE a.kind != 'system' ORDER BY a.created_at DESC LIMIT 5""")],
        "devices": monitor.devices(conn, now),
        "sites": monitor.sites(conn, now),
        "server": server_stats(),
    }


def server_stats() -> dict:
    """Load and memory of the machine the coordinator runs on (Linux /proc; empty elsewhere)."""
    out: dict = {"cpus": os.cpu_count()}
    try:
        out["load"] = round(os.getloadavg()[0], 2)
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, value = line.split(":", 1)
                mem[key] = int(value.split()[0]) // 1024  # kB -> MB
        out.update(mem_total_mb=mem["MemTotal"], mem_used_mb=mem["MemTotal"] - mem["MemAvailable"],
                   swap_used_mb=mem.get("SwapTotal", 0) - mem.get("SwapFree", 0))
    except (OSError, KeyError, ValueError):
        pass
    return out


def alerts(conn: sqlite3.Connection, now: datetime, days: float, kind: str | None, limit: int) -> list[dict]:
    rows = conn.execute(
        """SELECT a.id, a.created_at, a.kind, a.price, a.reference_price, a.drop_pct, a.score, a.message, a.error,
                  a.product_id, p.site, p.title, p.url, p.grp,
                  CASE WHEN a.confirm_until IS NOT NULL THEN 'held' WHEN a.error LIKE 'cancelled%' THEN 'cancelled'
                       WHEN a.sent_at IS NULL THEN 'queued' ELSE 'sent' END AS state,
                  (SELECT COUNT(*) FROM deliveries d WHERE d.alert_id = a.id AND d.sent_at IS NOT NULL) delivered
           FROM alerts a LEFT JOIN products p ON p.id = a.product_id
           WHERE a.created_at >= ? AND (? IS NULL OR a.kind = ?)
           ORDER BY a.created_at DESC LIMIT ?""",
        (iso(now - timedelta(days=days)), kind, kind, limit)).fetchall()
    return [dict(r) for r in rows]


def jobs(conn: sqlite3.Connection, now: datetime, limit: int) -> list[dict]:
    rows = conn.execute(
        """SELECT id, kind, site, grp, target, expected_total, interval_s / 60 AS every_min, next_due_at,
                  last_done_at, last_count, last_changes, failures, last_error, lease_worker, lease_expires_at > ? AS running
           FROM jobs ORDER BY failures DESC, (lease_expires_at > ?) DESC, next_due_at LIMIT ?""",
        (iso(now), iso(now), limit)).fetchall()
    return [dict(r) for r in rows]


# Every product sort is done by the database, so it holds across all pages of the list.
PRODUCT_SORTS = {
    "recent": "last_change_at DESC", "oldest": "last_change_at",
    "cheap": "price IS NULL, price", "dear": "price IS NULL, price DESC",
    "rating": "rating IS NULL, rating DESC, reviews DESC", "rating_low": "rating IS NULL, rating, reviews",
    "reviews": "reviews IS NULL, reviews DESC", "reviews_few": "reviews IS NULL, reviews",
    "title": "title COLLATE NOCASE", "title_desc": "title COLLATE NOCASE DESC",
    "site": "site, title COLLATE NOCASE", "site_desc": "site DESC, title COLLATE NOCASE",
    "grp": "grp, title COLLATE NOCASE", "grp_desc": "grp DESC, title COLLATE NOCASE",
    "crossed": "list_price IS NULL, list_price", "crossed_desc": "list_price IS NULL, list_price DESC",
    "stock": "in_stock DESC, stock_left IS NULL, stock_left", "stock_desc": "in_stock, stock_left IS NULL, stock_left DESC",
}


def _product_filter(q: str, site: str | None, grp: str | None, in_stock_only: bool,
                    changed_since: str | None = None) -> tuple[str, list]:
    words = [w for w in q.split() if w][:6]
    where = " AND ".join("title LIKE ?" for _ in words) or "1"
    if in_stock_only:
        where += " AND in_stock = 1"
    where += " AND (? IS NULL OR site = ?) AND (? IS NULL OR grp = ?) AND (? IS NULL OR last_change_at >= ?)"
    return where, [*[f"%{w}%" for w in words], site, site, grp, grp, changed_since, changed_since]


def products(conn: sqlite3.Connection, q: str, site: str | None, limit: int, grp: str | None = None,
             sort: str = "recent", in_stock_only: bool = False, offset: int = 0,
             changed_since: str | None = None) -> dict:
    """One page of the product list plus the total match count (for 'showing N of M' and
    endless scrolling: the client asks for the next `offset` as it nears the bottom).
    `changed_since` keeps products whose price or stock last changed at or after that time."""
    where, params = _product_filter(q, site, grp, in_stock_only, changed_since)
    order = PRODUCT_SORTS.get(sort, PRODUCT_SORTS["recent"])
    total = conn.execute(f"SELECT COUNT(*) FROM products WHERE {where}", params).fetchone()[0]
    rows = conn.execute(
        f"""SELECT id, site, sku, title, url, grp, price, list_price, in_stock, stock_left, seller, rating, reviews,
                   badges, last_seen_at, last_change_at, first_seen_at
            FROM products WHERE {where} ORDER BY {order}, id LIMIT ? OFFSET ?""",
        (*params, limit, offset)).fetchall()
    return {"products": [dict(r) for r in rows], "total": total, "offset": offset, "limit": limit}


def groups(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT grp FROM products WHERE grp IS NOT NULL ORDER BY grp")]


def product(conn: sqlite3.Connection, product_id: int, now: datetime) -> dict | None:
    row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if row is None:
        return None
    s = product_stats(conn, product_id, now)
    history = conn.execute("SELECT price, list_price, in_stock, observed_at FROM price_history "
                           "WHERE product_id = ? ORDER BY observed_at, id", (product_id,)).fetchall()
    return {
        "product": dict(row),
        "stats": {"typical_30d": s.typical_30d, "typical_90d": s.typical_90d, "low": s.low, "high": s.high,
                  "tracked_days": round(s.tracked_days, 1), "history_days": round(s.history_days, 1),
                  "changes_90d": s.changes_90d, "share_at_or_below": s.share_at_or_below,
                  "drop_vs_typical": (round(1 - row["price"] / s.typical_90d, 4)
                                      if row["price"] and s.typical_90d else None)},
        "history": [dict(h) for h in history],
        "alerts": [dict(a) for a in conn.execute(
            """SELECT id, created_at, kind, price, reference_price, drop_pct, score, error, confirm_until
               FROM alerts WHERE product_id = ? ORDER BY created_at DESC LIMIT 20""", (product_id,))],
        "other_sellers": [dict(o) for o in db.sibling_offers(conn, row["site"], row["sku"], now)],
    }


def subscribers(conn: sqlite3.Connection, now: datetime) -> list[dict]:
    """Everyone the bot knows except removed people, join requests first."""
    rows = conn.execute(
        """SELECT s.chat_id, s.name, s.username, s.status, s.is_admin, s.is_manager, s.created_at, s.approved_at,
                  (SELECT COUNT(*) FROM deliveries d WHERE d.chat_id = s.chat_id AND d.sent_at >= ?) AS sent_7d
           FROM subscribers s WHERE s.status != 'removed'
           ORDER BY s.status = 'pending' DESC, s.is_admin DESC, s.is_manager DESC, s.status, s.created_at""",
        (iso(now - timedelta(days=7)),)).fetchall()
    return [dict(r) for r in rows]


def rules(cfg: Config) -> dict:
    """The alert rules, schedule and watched groups from config.toml (shown, not edited)."""
    from dataclasses import asdict
    return {
        "alerts": asdict(cfg.alerts),
        "schedule": asdict(cfg.schedule),
        "watches": [{
            "name": w.name, "label": w.label, "interval_minutes": w.interval_minutes,
            "max_interval_minutes": w.max_interval_minutes or round(w.interval_minutes * cfg.schedule.max_interval_factor),
            "min_price": w.min_price, "min_drop": w.min_drop, "sites": sorted(w.targets),
            "fast_lanes": sum(scheduler.is_deal_feed(key) for keys in w.targets.values() for key in keys),
        } for w in cfg.watches],
    }


def login_page(error: str = "") -> str:
    msg = f"<p class='err' role='alert'>{html.escape(error)}</p>" if error else ""
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>deals · log in</title>{THEME_INIT}<style>{CSS}
body{{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px 16px;box-sizing:border-box}}
.box{{width:100%;max-width:400px;display:flex;flex-direction:column;gap:20px}}
form{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:28px;display:flex;flex-direction:column;gap:14px}}
h1{{margin:0;font-size:20px}} label{{font-size:13px;font-weight:500}} .field{{min-height:44px;font-size:15px}}
.err{{margin:0;padding:10px 12px;border-radius:8px;background:var(--fail-bg);color:var(--fail-fg);font-size:13px}}
.btn.pri{{min-height:44px;justify-content:center}}</style></head><body><div class='box'>
<div class='brand' style='padding:0'><div class='logo' style='width:40px;height:40px;font-size:20px'>d</div>
<div><div style='font-weight:600;font-size:20px'>deals</div><div class='muted' style='font-size:13px'>price tracker for 8 Egyptian shops</div></div></div>
<form method='post' action='/ui/login'><h1>Log in</h1>
<p class='muted' style='margin:0;line-height:1.5'>Paste the access token from <span class='mono'>make ui-token</span>. It is kept in a cookie on this device only.</p>
{msg}<label for='token'>Access token</label>
<input id='token' class='field' type='password' name='token' autofocus autocomplete='current-password'>
<button class='btn pri' type='submit'>Log in</button></form>
<p class='muted' style='margin:0;text-align:center;font-size:12px'>Worker tokens can look; only the admin token can approve people or send the report.</p>
</div></body></html>"""


# Light, dark, or the device's own setting (auto). Set before the page paints, so no flash.
THEME_INIT = """<script>try{const t=localStorage.getItem('theme');if(t==='light'||t==='dark')document.documentElement.dataset.theme=t}catch(e){}</script>"""

_LIGHT = """--bg:#F4F4F1;--card:#FFFFFF;--fg:#16181D;--muted:#5B616E;--line:#E3E4E8;--line2:#EEEFF2;--soft:#FAFAF8;--field:#FFFFFF;
--fieldline:#C9CCD3;--btnline:#D5D7DD;--link:#2348C9;--linkhover:#16308F;--pri:#2348C9;--sel:#EEF2FF;--selline:#C9D4FF;--selfg:#1D3FB8;
--chipon:#16181D;--chiponfg:#FFFFFF;--down:#0B6B3A;--up:#B23A12;--chart:#2348C9;--typical:#8A8F99;--bar:#ECEDF0;--bubble:#EAF3FB;
--ok-bg:#E2F3E9;--ok-fg:#0B6B3A;--warn-bg:#FFF0D4;--warn-fg:#7A4D00;--fail-bg:#FDE4E1;--fail-fg:#A3231A;--info-bg:#E3EAFF;--info-fg:#1D3FB8;
--idle-bg:#ECEDF0;--idle-fg:#474D59;--new-bg:#F1E8FF;--new-fg:#5B2BA6;--banner-warn:#FFFBF0;--banner-warnline:#F1DDA8;
--banner-fail:#FFF5F4;--banner-failline:#F3C7C1;--rowwarn:#FFF8E8;--rowfail:#FFF1EF;--side:#16181D;--sideline:#16181D"""
_DARK = """--bg:#0E1014;--card:#171A21;--fg:#E8EAEE;--muted:#9AA0AC;--line:#2A2E38;--line2:#22262F;--soft:#1C2028;--field:#0E1014;
--fieldline:#3A3F4B;--btnline:#3A3F4B;--link:#9DB1FF;--linkhover:#C3D0FF;--pri:#3B5BFF;--sel:#182040;--selline:#2E3A6E;--selfg:#C3D0FF;
--chipon:#E8EAEE;--chiponfg:#0E1014;--down:#5CCB8A;--up:#FF8A5C;--chart:#7C93FF;--typical:#7A808C;--bar:#262A33;--bubble:#1C2A3A;
--ok-bg:#12301F;--ok-fg:#6FD39A;--warn-bg:#3A2A0A;--warn-fg:#F0C060;--fail-bg:#3D1714;--fail-fg:#FF9187;--info-bg:#1B2550;--info-fg:#9DB1FF;
--idle-bg:#262A33;--idle-fg:#B5BAC4;--new-bg:#2B1D47;--new-fg:#C7A8FF;--banner-warn:#221B0B;--banner-warnline:#4A3A12;
--banner-fail:#241312;--banner-failline:#5A2520;--rowwarn:#1F1A0E;--rowfail:#221312;--side:#08090C;--sideline:#1C2028"""

CSS = """:root{""" + _LIGHT + """}
:root[data-theme=dark]{""" + _DARK + """}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){""" + _DARK + """}}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--fg)}
a{color:var(--link)} a:hover{color:var(--linkhover)}
.muted{color:var(--muted)} .mono,.num{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-variant-numeric:tabular-nums} .mono{font-size:12.5px}
.num{font-size:.95em}
.down{color:var(--down)} .up{color:var(--up)}
.btn{display:inline-flex;align-items:center;gap:8px;min-height:40px;padding:0 14px;border-radius:8px;border:1px solid var(--btnline);background:var(--card);color:var(--fg);
  font:inherit;font-size:13px;font-weight:500;cursor:pointer;text-decoration:none;white-space:nowrap}
.btn:hover{border-color:var(--muted);color:var(--fg)} .btn:disabled{opacity:.5;cursor:default}
.btn.pri{background:var(--pri);border-color:var(--pri);color:#fff} .btn.pri:hover{filter:brightness(1.1);color:#fff}
.btn.danger{color:var(--fail-fg)} .btn.small{min-height:32px;padding:0 10px;font-size:12px}
.field{min-height:40px;padding:0 12px;border:1px solid var(--fieldline);border-radius:8px;background:var(--field);color:var(--fg);font:inherit;font-size:13px}
.brand{display:flex;align-items:center;gap:10px;padding:4px 12px 18px}
.logo{width:30px;height:30px;border-radius:8px;background:#3B5BFF;display:flex;align-items:center;justify-content:center;font-weight:600;color:#fff}"""

APP = r"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>deals</title>__THEME__<style>__CSS__
.app{display:flex;min-height:100vh}
.side{flex:0 0 232px;padding:20px 12px;background:var(--side);border-right:1px solid var(--sideline);color:#E8EAEE;display:flex;flex-direction:column;gap:6px;position:sticky;top:0;height:100vh;overflow-y:auto}
.side .nav{display:flex;flex-direction:column;gap:2px}
.side .nav a{display:flex;justify-content:space-between;align-items:center;min-height:42px;padding:0 12px;border-radius:8px;color:#C4C7CF;text-decoration:none}
.side .nav a:hover{background:#23262E;color:#fff} .side .nav a.on{background:#2C3243;color:#fff}
.cnt{font-size:11px;font-family:ui-monospace,Menlo,monospace;background:#2E3340;color:#E9EAEE;border-radius:999px;padding:1px 7px}
.cnt.hot{background:#3B5BFF;color:#fff}
.side h4{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#9A9FAA;margin:0;padding:18px 12px 6px;font-weight:500}
.foot{margin-top:auto;padding:14px 12px 4px;display:flex;flex-direction:column;gap:8px;font-size:12px;color:#9A9FAA}
.foot a{color:#C4C7CF} .foot .num{color:#E8EAEE}
.meter{height:6px;border-radius:3px;background:#2E3340;overflow:hidden} .meter i{display:block;height:6px;background:#7C93FF}
.meter.light{background:var(--bar)} .meter.light i{background:var(--chart)}
.theme{display:flex;gap:4px;background:#23262E;border-radius:8px;padding:3px}
.theme button{flex:1;min-height:30px;border:0;border-radius:6px;background:none;color:#C4C7CF;font:inherit;font-size:12px;cursor:pointer}
.theme button:hover{color:#fff} .theme button.on{background:#3B4152;color:#fff}
main{flex:1;min-width:0;max-width:1280px;padding:28px 32px 48px;display:flex;flex-direction:column;gap:18px}
.head{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;justify-content:space-between}
.head h1{margin:0;font-size:26px;font-weight:600;letter-spacing:-.01em} .head p{margin:4px 0 0;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px}
.cardhead{padding:14px 16px;display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px}
.cardhead h2{margin:0;font-size:15px;font-weight:600}
.cardfoot{padding:12px 16px;border-top:1px solid var(--line2);display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:13px;color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px}
.kpi{padding:14px 16px;text-decoration:none;color:var(--fg);display:block}
.kpi .k{font-size:12px;color:var(--muted)} .kpi .v{font:500 24px/1.25 ui-monospace,Menlo,monospace;margin-top:4px} .kpi .s{font-size:12px;color:var(--muted)}
a.kpi:hover{border-color:var(--muted);color:var(--fg)}
.kpi.hi{background:var(--sel);border-color:var(--selline)} .kpi.hi .k,.kpi.hi .s{color:var(--selfg)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(420px,100%),1fr));gap:20px;align-items:start}
.split{display:flex;flex-wrap:wrap;gap:20px;align-items:flex-start}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-weight:500;color:var(--muted);font-size:12px;padding:10px 14px;border-bottom:1px solid var(--line);background:var(--soft);white-space:nowrap}
td{padding:10px 14px;border-bottom:1px solid var(--line2);vertical-align:middle}
tr:last-child td{border-bottom:0}
.r{text-align:right} .c{text-align:center} .nw{white-space:nowrap}
th.sortable{cursor:pointer;user-select:none} th.sortable:hover{color:var(--fg)} th .dir{font-size:10px}
tr.click{cursor:pointer} tr.click:hover td{background:var(--soft)}
td.mono{overflow-wrap:anywhere} tr.WARNING td{background:var(--rowwarn)} tr.ERROR td,tr.bad td{background:var(--rowfail)}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:500;padding:3px 9px;border-radius:999px;white-space:nowrap}
.pill i{width:7px;height:7px;border-radius:50%;background:currentColor}
.ok,.sent,.active{background:var(--ok-bg);color:var(--ok-fg)} .warn,.blocked,.held{background:var(--warn-bg);color:var(--warn-fg)}
.fail,.failing,.offline{background:var(--fail-bg);color:var(--fail-fg)} .info,.crawling,.queued{background:var(--info-bg);color:var(--info-fg)}
.idle,.cancelled{background:var(--idle-bg);color:var(--idle-fg)} .new{background:var(--new-bg);color:var(--new-fg)}
.na{color:var(--muted);font-size:12px}
.banner{padding:16px 20px;display:flex;flex-wrap:wrap;gap:14px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:12px}
.banner.warn{background:var(--banner-warn);border-color:var(--banner-warnline);color:var(--fg)}
.banner.fail{background:var(--banner-fail);border-color:var(--banner-failline);color:var(--fg)}
.banner .msg{flex:1;min-width:200px}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center} .grow{flex:1 1 auto}
.chip{display:inline-flex;align-items:center;gap:6px;min-height:36px;padding:0 12px;border-radius:999px;border:1px solid var(--btnline);background:var(--card);font:inherit;font-size:13px;color:var(--fg);cursor:pointer}
.chip:hover{border-color:var(--muted)} .chip.on{background:var(--chipon);border-color:var(--chipon);color:var(--chiponfg)}
.chip .num{opacity:.7} .chip.small{min-height:30px;font-size:12px;padding:0 10px}
.tag{display:inline-flex;align-items:center;gap:4px;min-height:28px;padding:0 4px 0 10px;border-radius:999px;background:var(--sel);color:var(--selfg);font-size:12px;font-weight:500}
.tag button{width:24px;height:24px;border:0;border-radius:50%;background:none;color:inherit;font:inherit;font-size:14px;cursor:pointer}
.rowlink{color:var(--fg);text-decoration:none;font-weight:500} .rowlink:hover{color:var(--link);text-decoration:underline}
.sub{font-size:12px;color:var(--muted)}
.item{display:flex;gap:16px;align-items:center;width:100%;text-align:left;background:var(--card);border:0;border-top:1px solid var(--line2);padding:14px 16px;font:inherit;color:var(--fg);cursor:pointer}
.item:first-child{border-top:0} .item:hover{background:var(--soft)} .item.sel{background:var(--sel)}
.item .main{flex:1 1 auto;min-width:0;display:flex;flex-direction:column;gap:5px}
.item .t{font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item .p{display:flex;flex-direction:column;align-items:flex-end;gap:4px;flex:none}
.bubble{background:var(--bubble);border-radius:12px 12px 4px 12px;padding:14px 16px;font-size:14px;line-height:1.75;white-space:pre-line;direction:rtl;text-align:right;overflow-wrap:anywhere}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.stat .k{font-size:12px;color:var(--muted)} .stat .v{font:500 18px ui-monospace,Menlo,monospace;margin-top:4px}
.box3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.box3>div{background:var(--soft);border-radius:8px;padding:10px} .box3 .k{font-size:11px;color:var(--muted)} .box3 .v{font:500 16px ui-monospace,Menlo,monospace}
.box3 .good{background:var(--ok-bg)} .box3 .good .k,.box3 .good .v{color:var(--ok-fg)}
.q{display:block;width:100%;text-align:left;background:none;border:0;border-radius:8px;padding:9px 12px;font:inherit;font-size:13px;color:var(--fg);cursor:pointer}
.q:hover{background:var(--soft)} .q.on{background:var(--sel);color:var(--selfg);font-weight:500}
.q small{display:block;font-size:12px;color:var(--muted);font-weight:400;margin-top:2px}
.qh{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);padding:12px 12px 4px}
.lv{display:inline-block;min-width:62px;font-size:11px;font-weight:500;padding:2px 6px;border-radius:4px;text-align:center}
.sh{display:inline-block;min-width:22px;height:22px;border-radius:6px;font-size:10px;font-weight:600;line-height:22px;text-align:center;margin-right:3px;padding:0 3px}
.sh.on{background:var(--chipon);color:var(--chiponfg)} .sh.off{background:var(--bar);color:var(--muted)}
.av{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:600;background:var(--info-bg);color:var(--info-fg);flex:none}
.empty{padding:28px 16px;text-align:center;color:var(--muted)}
.rule{padding:16px;display:flex;flex-direction:column;gap:10px} .rule h3{margin:0;font-size:14px}
.kv{display:flex;justify-content:space-between;gap:12px;font-size:13px} .kv b{font-family:ui-monospace,Menlo,monospace;font-weight:500}
.sr{position:absolute;left:-9999px}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--fg);color:var(--bg);padding:10px 16px;border-radius:8px;z-index:50;display:none;font-size:13px}
#bar{position:fixed;top:0;left:0;height:3px;width:0;background:#3B5BFF;transition:width .3s,opacity .4s;opacity:0;z-index:60}
body.loading #bar{opacity:1;width:70%}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid var(--line);border-top-color:var(--chart);border-radius:50%;animation:spin .8s linear infinite;vertical-align:middle;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
.chart{position:relative} .chart svg{width:100%;height:280px;display:block}
.chart .tip{position:absolute;pointer-events:none;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-size:12px;white-space:nowrap;box-shadow:0 4px 14px rgba(0,0,0,.15);display:none}
.legend{display:flex;flex-wrap:wrap;gap:16px;font-size:12px;color:var(--muted)} .legend i{display:inline-block;width:16px;height:0;border-top:2px solid;vertical-align:middle;margin-right:6px}
.bottom{display:none}
@media (max-width:800px){
  .side{display:none}
  body.menu .side{display:flex;position:fixed;inset:0 0 64px 0;height:auto;z-index:30}
  main{padding:18px 16px 92px} .head h1{font-size:22px}
  .bottom{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:40;background:var(--card);border-top:1px solid var(--line);padding-bottom:env(safe-area-inset-bottom)}
  .bottom a,.bottom button{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;min-height:60px;font:inherit;font-size:11px;color:var(--muted);text-decoration:none;background:none;border:0;cursor:pointer}
  .bottom .on{color:var(--link);font-weight:600}
  .item .st{display:none}
}
</style></head><body>
<div id='bar'></div>
<div class='app'>
<nav class='side' id='side' aria-label='Main'>
  <div class='brand'><div class='logo'>d</div><div><div style='font-weight:600;font-size:15px;color:#fff'>deals</div><div style='font-size:11px;color:#9A9FAA'>price tracker, Egypt</div></div></div>
  <div class='nav' id='nav'></div>
  <h4>Admin</h4>
  <div class='nav' id='nav2'></div>
  <div class='foot'>
    <div id='ram'></div>
    <div class='theme' role='group' aria-label='Theme'><button data-t='light' onclick="setTheme('light')">Light</button><button data-t='dark' onclick="setTheme('dark')">Dark</button><button data-t='auto' onclick="setTheme('auto')">Auto</button></div>
    <div style='display:flex;justify-content:space-between'><span id='rev'></span><a href='/ui/logout'>Log out</a></div>
  </div>
</nav>
<main id='view'><span class='spinner'></span>loading…</main>
</div>
<nav class='bottom' aria-label='Main'>
  <a href='#Overview' data-tab='Overview'>__I_GRID__Overview</a><a href='#Deals' data-tab='Deals'>__I_TAG__Deals</a>
  <a href='#Products' data-tab='Products'>__I_SEARCH__Products</a><a href='#Devices' data-tab='Devices'>__I_SCREEN__Devices</a>
  <button type='button' onclick="document.body.classList.toggle('menu')" aria-label='More screens'>__I_MENU__More</button>
</nav>
<div class='toast' id='toast' role='status'></div>
<script>
const TABS = ['Overview', 'Deals', 'Products', 'Devices', 'Jobs', 'Logs', 'Queries'], ADMIN = ['Subscribers', 'Rules'];
const LABEL = {Rules: 'Rules & watches'};
const SITES = [['amazon_eg', 'Amazon', 'A'], ['noon_eg', 'noon', 'N'], ['jumia_eg', 'Jumia', 'J'], ['cairosales_eg', 'Cairo Sales', 'C'],
  ['2b_eg', '2B', '2B'], ['btech_eg', 'B.TECH', 'BT'], ['dream2000_eg', 'Dream 2000', 'D'], ['carrefour_eg', 'Carrefour', 'CF']];
const KINDS = {price_drop: ['Price drop', 'info'], all_time_low: ['All-time low', 'ok'], early_drop: ['Early drop', 'idle'],
  new_product: ['New product', 'new'], price_error: ['Price error', 'fail'], system: ['System', 'warn']};
let tab = 'Overview', timer = null, ov = null, toastTimer = null;
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const shop = s => (SITES.find(x => x[0] === s) || [s, s])[1];
const money = p => p == null ? '—' : Math.round(p / 100).toLocaleString('en');
const pct = x => x == null ? '' : Math.round(x * 100) + '%';
const ago = t => { if (!t) return 'never'; const s = (Date.now() - Date.parse(t)) / 1000;
  return s < 120 ? Math.round(s) + ' s' : s < 7200 ? Math.round(s / 60) + ' min' : s < 172800 ? (s / 3600).toFixed(1) + ' h' : Math.round(s / 86400) + ' d'; };
const hhmm = t => t ? new Date(t).toLocaleString('en-GB', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}) : '';
const pill = (cls, text, dot) => `<span class='pill ${esc(cls)}'>${dot ? '<i></i>' : ''}${esc(text)}</span>`;
const kind = k => pill((KINDS[k] || [k, 'idle'])[1], (KINDS[k] || [k])[0]);
const plain = h => new DOMParser().parseFromString(h || '', 'text/html').body.textContent;
const head = (title, sub, actions = '') => `<header class='head'><div><h1>${esc(title)}</h1>${sub ? `<p>${sub}</p>` : ''}</div>${actions ? `<div class='row'>${actions}</div>` : ''}</header>`;
const options = (list, cur, all) => `<option value=''>${esc(all)}</option>` + list.map(([v, l]) => `<option value='${esc(v)}' ${v === cur ? 'selected' : ''}>${esc(l)}</option>`).join('');
const shopOptions = cur => options(SITES.map(s => [s[0], s[1]]), cur, 'All shops');
let inflight = 0;
function busy(on) { inflight += on ? 1 : -1; document.body.classList.toggle('loading', inflight > 0); }
async function api(path, method = 'GET') {
  busy(true);
  try {
    const r = await fetch(path, {method, credentials: 'same-origin'});
    if (r.status === 401) { location.href = '/ui/login'; throw new Error('logged out'); }
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || path + ': HTTP ' + r.status);
    return body;
  } finally { busy(false); }
}
function toast(text) { const t = $('#toast'); t.textContent = text; t.style.display = 'block'; clearTimeout(toastTimer); toastTimer = setTimeout(() => t.style.display = 'none', 3500); }
function setTheme(t) {
  try { localStorage.setItem('theme', t); } catch (e) {}
  if (t === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  side();
}
function side() {
  const t = ov?.totals || {}, link = name => {
    const n = name === 'Deals' ? t.alerts_24h : name === 'Logs' ? t.warnings_24h : name === 'Subscribers' ? t.pending : 0;
    return `<a href='#${name}' class='${name === tab ? 'on' : ''}' ${name === tab ? "aria-current='page'" : ''}>${esc(LABEL[name] || name)}${n ? ` <span class='cnt ${name === 'Subscribers' ? 'hot' : ''}'>${n}</span>` : ''}</a>`; };
  $('#nav').innerHTML = TABS.map(link).join(''); $('#nav2').innerHTML = ADMIN.map(link).join('');
  let theme = 'auto'; try { theme = localStorage.getItem('theme') || 'auto'; } catch (e) {}
  document.querySelectorAll('.theme button').forEach(b => b.classList.toggle('on', b.dataset.t === theme));
  document.querySelectorAll('.bottom a').forEach(a => a.classList.toggle('on', a.dataset.tab === tab));
  const s = ov?.server || {};
  $('#ram').innerHTML = s.mem_total_mb ? `<div style='display:flex;justify-content:space-between;margin-bottom:6px'><span>Server RAM</span><span class='num'>${s.mem_used_mb} / ${s.mem_total_mb} MB</span></div>
    <div class='meter'><i style='width:${Math.min(100, Math.round(100 * s.mem_used_mb / s.mem_total_mb))}%'></i></div>` : '';
  $('#rev').textContent = ov?.revision ? 'rev ' + ov.revision.slice(0, 7) : '';
}
function every(ms, fn) { clearInterval(timer); timer = setInterval(fn, ms); }
function route() {
  const h = decodeURIComponent(location.hash.slice(1)).split(':')[0];
  tab = h === 'Alerts' ? 'Deals' : TABS.concat(ADMIN).includes(h) ? h : 'Overview';
  clearInterval(timer); feed = null; document.body.classList.remove('menu'); side(); render();
}
window.addEventListener('hashchange', route);
function openProduct(id) { location.hash = 'Products:' + id; }

// ---------------------------------------------------------------- Overview
function shopRows(sites, checks) {
  const blocked = new Set(checks.filter(c => c.name.startsWith('blocked ')).map(c => c.name.slice(8)));
  return sites.slice().sort((a, b) => b.products - a.products).map(s => {
    const age = s.last_done ? (Date.now() - Date.parse(s.last_done)) / 60000 : 1e9;
    const state = blocked.has(s.site) ? pill('warn', 'blocked', 1) : pill(age > 60 ? 'warn' : 'ok', s.last_done ? ago(s.last_done) + ' ago' : 'never', 1);
    return `<tr><td><b style='font-weight:500'>${esc(shop(s.site))}</b></td><td class='num r'>${s.products.toLocaleString()}</td><td>${state}</td><td class='num r'>${s.running}</td><td class='num r'>${s.failing}</td></tr>`; }).join('');
}
function deviceState(d) {
  if (!d.online) return ['offline', 'offline'];
  const by = st => d.sites.find(s => s.state === st);
  const c = by('crawling'); if (c) return ['crawling', 'crawling ' + shop(c.site)];
  if (by('failing')) return ['failing', 'failing'];
  if (by('blocked')) return ['blocked', 'blocked on ' + shop(by('blocked').site)];
  return ['ok', 'ok'];
}
function serverBox(s) {
  if (!s.mem_total_mb) return '';
  return `<div style='padding:14px 16px;border-top:1px solid var(--line2);display:flex;flex-direction:column;gap:10px'>
    <div class='row' style='justify-content:space-between;font-size:12px'><span class='muted'>Server load (${s.cpus} vCPU)</span><span class='num'>${s.load}</span></div>
    <div class='meter light'><i style='width:${Math.min(100, Math.round(100 * s.load / (s.cpus || 1)))}%'></i></div>
    <div class='row' style='justify-content:space-between;font-size:12px'><span class='muted'>RAM</span><span class='num'>${s.mem_used_mb} / ${s.mem_total_mb} MB · swap ${s.swap_used_mb} MB</span></div>
    <div class='meter light'><i style='width:${Math.round(100 * s.mem_used_mb / s.mem_total_mb)}%'></i></div></div>`;
}
const views = {
  async Overview() {
    const d = await api('/api/overview'); ov = d; side();
    const t = d.totals, bad = d.checks.filter(c => c.level !== 'ok'), fail = bad.some(c => c.level === 'fail');
    const banner = bad.length
      ? `<section class='banner ${fail ? 'fail' : 'warn'}' aria-label='System status'>${pill(fail ? 'fail' : 'warn', fail ? 'Something is broken' : `Working, ${bad.length} warning${bad.length > 1 ? 's' : ''}`, 1)}
         <span class='msg'>${bad.slice(0, 3).map(c => `<b style='font-weight:500'>${esc(c.name)}</b>: ${esc(c.detail)}`).join(' · ')}</span><a href='#checks' onclick="document.getElementById('checks').scrollIntoView();return false">See all checks</a></section>`
      : `<section class='banner' aria-label='System status'>${pill('ok', 'All working', 1)}<span class='msg muted'>Every health check passed.</span></section>`;
    const kpi = (k, v, s, href, hi) => `<${href ? `a href='${href}'` : 'div'} class='card kpi ${hi ? 'hi' : ''}'><div class='k'>${k}</div><div class='v'>${v}</div><div class='s'>${s}</div></${href ? 'a' : 'div'}>`;
    const latest = d.latest.length ? d.latest.map(a => `<tr class='click' onclick='openProduct(${a.product_id})'><td><a class='rowlink' href='#Products:${a.product_id}'>${esc(a.title)}</a><div class='sub'>${esc(shop(a.site))} · ${esc(a.grp || '')} · ${esc((KINDS[a.kind] || [a.kind])[0].toLowerCase())}</div></td>
        <td class='num r'>${money(a.price)}</td><td class='num r down'>${a.drop_pct ? '▼ ' + pct(a.drop_pct) : ''}</td><td class='muted nw'>${ago(a.created_at)}</td></tr>`).join('')
      : `<tr><td class='empty' colspan='4'>No deal alerts yet</td></tr>`;
    const devs = d.devices.map(x => { const [cls, label] = deviceState(x);
      return `<div class='row' style='justify-content:space-between;padding:12px 16px;border-top:1px solid var(--line2)'><div><div style='font-weight:500'>${esc(x.name)}</div>
        <div class='sub'>${x.sites.length} shop${x.sites.length === 1 ? '' : 's'} · seen ${ago(x.last_seen_at)} ago</div></div>${pill(cls, label, 1)}</div>`; }).join('');
    return head('Overview', `${new Date().toLocaleString('en-GB', {weekday: 'long', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'})} · refreshes every 15 s`,
        d.admin ? `<button class='btn pri' type='button' onclick='sendReport(this)'>Send daily report now</button>` : '') + banner +
      `<section class='cards' aria-label='Totals'>
        ${kpi('Products tracked', t.products.toLocaleString(), `${d.sites.length} shops`)}
        ${kpi('In stock', t.in_stock.toLocaleString(), t.products ? Math.round(100 * t.in_stock / t.products) + '% of tracked' : '')}
        ${kpi('Price changes, 24 h', t.changes_24h.toLocaleString(), Math.round(t.changes_24h / 24).toLocaleString() + ' per hour')}
        ${kpi('Deals, 24 h', t.alerts_24h, `${t.atl_24h} all-time low${t.atl_24h === 1 ? '' : 's'}`, '#Deals', true)}
        ${kpi('Subscribers', t.subscribers, t.pending ? `${t.pending} waiting for approval` : 'none waiting', '#Subscribers')}
        ${kpi('Warnings, 24 h', t.warnings_24h, `${t.errors_24h} error${t.errors_24h === 1 ? '' : 's'}`, '#Logs')}
      </section>
      <div class='grid2'>
        <section id='checks' class='card' style='overflow:hidden'><div class='cardhead'><h2>Health checks</h2><span class='sub'>same as make health</span></div>
          <div class='scroll'><table><tr><th>State</th><th>Check</th><th>Detail</th></tr>
          ${d.checks.slice().sort((a, b) => (a.level === 'ok') - (b.level === 'ok')).map(c => `<tr><td>${pill(c.level, c.level, 1)}</td><td class='nw'>${esc(c.name)}</td><td class='muted'>${esc(c.detail)}</td></tr>`).join('')}</table></div></section>
        <section class='card' style='overflow:hidden'><div class='cardhead'><h2>Shops</h2><a href='#Jobs' style='font-size:13px'>Crawl schedule</a></div>
          <div class='scroll'><table><tr><th>Shop</th><th class='r'>Products</th><th>Last pass</th><th class='r'>Running</th><th class='r'>Failing</th></tr>${shopRows(d.sites, d.checks)}</table></div></section>
      </div>
      <div class='grid2'>
        <section class='card' style='overflow:hidden'><div class='cardhead'><h2>Latest deals</h2><a href='#Deals' style='font-size:13px'>All deals</a></div>
          <div class='scroll'><table><tr><th>Product</th><th class='r'>Now</th><th class='r'>Drop</th><th>When</th></tr>${latest}</table></div></section>
        <section class='card' style='overflow:hidden'><div class='cardhead'><h2>Devices</h2><a href='#Devices' style='font-size:13px'>Details</a></div>${devs}${serverBox(d.server)}</section>
      </div>`;
  },

  // ---------------------------------------------------------------- Deals
  async Deals() {
    if (dz.loaded !== dz.days) { dz.data = (await api('/api/alerts?limit=1000&days=' + dz.days)).alerts; dz.loaded = dz.days; }
    const q = dz.q.trim().toLowerCase();
    const base = dz.data.filter(a => (!dz.site || a.site === dz.site) && (!dz.state || a.state === dz.state)
      && (!q || (a.title || plain(a.message)).toLowerCase().includes(q)));
    const rows = base.filter(a => dz.kind === 'all' || a.kind === dz.kind), sel = rows.find(a => a.id === dz.sel) || rows[0];
    const count = st => dz.data.filter(a => a.state === st).length, sent = dz.data.reduce((n, a) => n + (a.delivered || 0), 0);
    const chips = ['all', ...Object.keys(KINDS)].map(k => `<button type='button' class='chip ${dz.kind === k ? 'on' : ''}' aria-pressed='${dz.kind === k}' onclick="dz.kind='${k}';dz.sel=null;render()">${k === 'all' ? 'All' : KINDS[k][0]} <span class='num'>${k === 'all' ? base.length : base.filter(a => a.kind === k).length}</span></button>`).join('');
    const item = a => `<button type='button' class='item ${a === sel ? 'sel' : ''}' onclick='dz.sel=${a.id};render()'>
        <span class='main'><span class='t'>${esc(a.title || plain(a.message).split('\n')[0])}</span>
        <span class='row' style='font-size:12px;gap:8px'>${kind(a.kind)}<span class='muted'>${a.site ? esc(shop(a.site)) + ' · ' + esc(a.grp || '') + ' · ' : ''}${hhmm(a.created_at)}</span></span></span>
        ${a.price ? `<span class='p'><span class='num' style='font-size:16px;font-weight:500'>${money(a.price)}</span><span class='num' style='font-size:12px'><s class='muted'>${money(a.reference_price)}</s> <b class='down'>▼ ${pct(a.drop_pct)}</b></span></span>` : ''}
        <span class='st' style='flex:none;width:92px;text-align:right'>${pill(a.state, a.state, 1)}</span></button>`;
    const detail = !sel ? `<div class='empty'>Nothing matches these filters</div>` : `
      <div class='row' style='justify-content:space-between'>${kind(sel.kind)}<span class='sub'>${hhmm(sel.created_at)}${sel.score != null ? ' · score <span class="num">' + esc(sel.score) + '</span>' : ''}</span></div>
      <h2 style='margin:0;font-size:17px;line-height:1.35'>${esc(sel.title || 'System message')}</h2>
      ${sel.price ? `<div class='box3'><div><div class='k'>Now</div><div class='v'>${money(sel.price)}</div></div><div><div class='k'>Typical, 90 d</div><div class='v'>${money(sel.reference_price)}</div></div>
        <div class='good'><div class='k'>Real drop</div><div class='v'>▼ ${pct(sel.drop_pct)}</div></div></div>` : ''}
      <div class='sub' style='font-weight:500'>Telegram message</div><div class='bubble' dir='rtl'>${esc(plain(sel.message))}</div>
      <div class='sub'>${sel.state === 'sent' ? `Delivered to ${sel.delivered} ${sel.delivered === 1 ? 'person' : 'people'}` : sel.state === 'held' ? 'Held until the price is seen again, then sent' : sel.state === 'queued' ? 'Waiting to be sent' : esc(sel.error || sel.state)}</div>
      ${sel.product_id ? `<div class='row'><a class='btn pri' href='#Products:${sel.product_id}'>Price history</a><a class='btn' href='${esc(sel.url)}' target='_blank' rel='noopener'>Open in shop ↗</a></div>` : ''}`;
    return head('Deals', 'Every alert the rules produced, with the exact Telegram message that went out',
        `<span><b class='num'>${dz.data.length - count('held') - count('queued') - count('cancelled')}</b> <span class='muted'>sent</span></span>&nbsp;
         <span><b class='num'>${count('held')}</b> <span class='muted'>held</span></span>&nbsp;<span><b class='num'>${count('cancelled')}</b> <span class='muted'>cancelled</span></span>&nbsp;
         <span><b class='num'>${sent.toLocaleString()}</b> <span class='muted'>messages delivered</span></span>`) +
      `<form class='row' role='search' onsubmit='return false'>
        <label class='sr' for='dq'>Search deals</label><input id='dq' class='field grow' style='flex:1 1 220px' type='search' placeholder='Search product titles' value='${esc(dz.q)}' onchange='dz.q=this.value;render()'>
        <label class='sr' for='dd'>Period</label><select id='dd' class='field' onchange='dz.days=this.value;render()'>${[['1', 'Last 24 hours'], ['3', 'Last 3 days'], ['7', 'Last 7 days'], ['30', 'Last 30 days']].map(([v, l]) => `<option value='${v}' ${v === dz.days ? 'selected' : ''}>${l}</option>`).join('')}</select>
        <label class='sr' for='ds'>Shop</label><select id='ds' class='field' onchange='dz.site=this.value;render()'>${shopOptions(dz.site)}</select>
        <label class='sr' for='dst'>State</label><select id='dst' class='field' onchange='dz.state=this.value;render()'>${options([['sent', 'Sent'], ['queued', 'Queued'], ['held', 'Held'], ['cancelled', 'Cancelled']], dz.state, 'Any state')}</select></form>
      <div class='row' role='group' aria-label='Alert kind'>${chips}</div>
      <div class='split'>
        <section class='card' aria-label='Alerts' style='flex:999 1 520px;min-width:0;overflow:hidden'>${rows.slice(0, dz.shown).map(item).join('') || `<div class='empty'>No alerts in this period</div>`}
          <div class='cardfoot'><span>Showing ${Math.min(rows.length, dz.shown)} of ${rows.length}</span>${rows.length > dz.shown ? `<button class='btn' type='button' onclick='dz.shown+=50;render()'>Show 50 more</button>` : ''}</div></section>
        <aside class='card' aria-label='Selected alert' style='flex:1 1 360px;min-width:0;padding:18px;display:flex;flex-direction:column;gap:14px'>${detail}</aside>
      </div>`;
  },

  // ---------------------------------------------------------------- Products
  async Products() {
    const id = location.hash.split(':')[1];
    if (id) return productPage(id);
    const since = pz.changed ? new Date(Date.now() - pz.changed * 3600e3).toISOString() : '';
    const query = `q=${encodeURIComponent(pz.q)}&site=${pz.site}&grp=${encodeURIComponent(pz.grp)}&sort=${pz.sort}&in_stock=${pz.stock ? 1 : 0}&changed_since=${encodeURIComponent(since)}`;
    const d = await api(`/api/products?limit=${PAGE}&offset=0&` + query);
    const periods = [[24, 'Last 24 h'], [168, '7 days'], [720, '30 days'], [0, 'Any time']];
    const nextFeed = {url: o => `/api/products?limit=${PAGE}&offset=${o}&` + query, row: productRow, rows: '#prows', count: '#pcount', more: '#more', next: d.products.length, total: d.total, busy: false};
    const label = {24: 'Changed in the last 24 h', 168: 'Changed in the last 7 days', 720: 'Changed in the last 30 days'}[pz.changed];
    return {feed: nextFeed, html: head('Products', 'Every product we track across all eight shops. Sorting and filtering run in the database.') +
      `<form class='card' role='search' style='padding:14px;display:flex;flex-direction:column;gap:12px' onsubmit='pz.q=this.q.value;render();return false'>
        <div class='row'>
          <label class='sr' for='pq'>Search titles</label><input id='pq' name='q' class='field' style='flex:1 1 240px' type='search' placeholder='Search titles, e.g. samsung 55' value='${esc(pz.q)}' onchange='pz.q=this.value;render()'>
          <label class='sr' for='ps'>Shop</label><select id='ps' class='field' onchange='pz.site=this.value;render()'>${shopOptions(pz.site)}</select>
          <label class='sr' for='pg'>Group</label><select id='pg' class='field' onchange='pz.grp=this.value;render()'>${options(d.groups.map(g => [g, g]), pz.grp, 'All groups')}</select>
          <label class='sr' for='po'>Sort</label><select id='po' class='field' onchange='pz.sort=this.value;render()'>${PRODUCT_SORT_NAMES.map(([v, l]) => `<option value='${v}' ${v === pz.sort ? 'selected' : ''}>${l}</option>`).join('')}</select>
          <label class='row' style='gap:8px;min-height:40px;font-size:13px'><input type='checkbox' ${pz.stock ? 'checked' : ''} onchange='pz.stock=this.checked;render()' style='width:18px;height:18px'>In stock only</label>
        </div>
        <div class='row'><span class='muted' style='font-size:13px;margin-right:4px'>Price changed</span>
          ${periods.map(([h, l]) => `<button type='button' class='chip ${pz.changed === h ? 'on' : ''}' aria-pressed='${pz.changed === h}' onclick='pz.changed=${h};render()'>${l}</button>`).join('')}</div>
      </form>
      <div class='row' style='justify-content:space-between;font-size:13px'><span class='row'><span id='pcount'><b class='num'>${d.products.length.toLocaleString()}</b> <span class='muted'>of ${d.total.toLocaleString()} shown</span></span>
        ${label ? `<span class='tag'>${label}<button type='button' aria-label='Show any time' onclick='pz.changed=0;render()'>×</button></span>` : ''}</span>
        <span class='muted'>Click a column title to sort all pages by it</span></div>
      <section class='card' aria-label='Product list' style='overflow:hidden'><div class='scroll'><table><thead><tr>${PRODUCT_COLS.map(([l, asc, desc]) => `<th class='sortable ${l === 'Crossed-out' || l === 'Rating' || l === 'Last change' ? 'r' : ''}' data-asc='${asc}' data-desc='${desc}'>${l}${pz.sort === asc ? " <span class='dir'>▲</span>" : pz.sort === desc ? " <span class='dir'>▼</span>" : ''}</th>`).join('')}</tr></thead>
        <tbody id='prows'>${d.products.map(productRow).join('')}</tbody></table></div>
        <div id='more' class='cardfoot'>${d.products.length < d.total ? "<span><span class='spinner'></span>loading more as you scroll…</span>" : d.total ? 'end of list' : (pz.changed ? 'No price changes in this period' : 'Nothing matches')}</div></section>`};
  },

  // ---------------------------------------------------------------- Devices
  async Devices() {
    const d = await api('/api/overview'); ov = d; side();
    const card = x => { const [cls, label] = deviceState(x);
      return `<div class='card' style='padding:16px;display:flex;flex-direction:column;gap:10px'><div class='row' style='justify-content:space-between'><span style='font-weight:600;font-size:15px'>${esc(x.name)}</span>${pill(cls, label, 1)}</div>
        <div class='sub' style='font-size:13px;line-height:1.6'>${x.sites.map(s => shop(s.site)).join(', ') || 'no shops'}<br>Seen ${ago(x.last_seen_at)} ago · ${esc(x.ip || '')} · v${esc(x.version || '?')}</div>
        <div class='row' style='gap:18px'><div><div class='num' style='font-size:18px'>${(x.pages || 0).toLocaleString()}</div><div class='sub'>pages</div></div>
        <div><div class='num' style='font-size:18px'>${(x.jobs_done || 0).toLocaleString()}</div><div class='sub'>jobs done</div></div>
        <div><div class='num' style='font-size:18px'>${x.sites.reduce((n, s) => n + s.blocks, 0)}</div><div class='sub'>blocks</div></div></div></div>`; };
    const cell = (x, site) => { const s = x.sites.find(y => y.site === site); return `<td class='c'>${s ? pill(x.online ? s.state : 'offline', x.online ? s.state : 'offline', 1) : "<span class='na'>—</span>"}</td>`; };
    every(15000, render);
    return head('Devices', 'Every worker and what it is doing on each shop · refreshes every 15 s', `<a class='btn' href='#Logs'>Logs from all devices</a>`) +
      `<section class='cards' style='grid-template-columns:repeat(auto-fill,minmax(260px,1fr))' aria-label='Workers'>${d.devices.map(card).join('') || "<div class='empty'>No worker has connected yet</div>"}</section>
      <section class='card' style='overflow:hidden'><div class='cardhead'><h2>Who can fetch what</h2><div class='row' style='gap:6px'>${['ok', 'crawling', 'idle', 'blocked', 'failing'].map(s => pill(s, s, 1)).join('')}<span class='na'>— not assigned</span></div></div>
        <div class='scroll'><table><tr><th>Device</th>${SITES.map(s => `<th class='c'>${s[1]}</th>`).join('')}</tr>
        ${d.devices.map(x => `<tr><td style='font-weight:500'>${esc(x.name)}</td>${SITES.map(s => cell(x, s[0])).join('')}</tr>`).join('')}</table></div></section>
      <section class='card' style='overflow:hidden'><div class='cardhead'><h2>Fetch state per shop</h2></div><div class='scroll'><table>
        <tr><th>Device</th><th>Shop</th><th>State</th><th>Detail</th><th class='r'>Pages</th><th class='r'>Products</th><th class='r'>Blocks</th></tr>
        ${d.devices.flatMap(x => x.sites.map(s => `<tr><td>${esc(x.name)}</td><td>${esc(shop(s.site))}</td><td>${pill(s.state, s.state, 1)}</td>
          <td class='muted'>${esc(s.detail)}${s.last_error && s.state !== 'failing' ? `<div class='sub'>last problem ${esc((s.last_error_at || '').slice(11, 16))}: ${esc(s.last_error)}</div>` : ''}</td>
          <td class='num r'>${s.pages.toLocaleString()}</td><td class='num r'>${s.products.toLocaleString()}</td><td class='num r'>${s.blocks}</td></tr>`)).join('')}</table></div></section>
      <section class='card' style='overflow:hidden'><div class='cardhead'><h2>Shops</h2></div><div class='scroll'><table><tr><th>Shop</th><th class='r'>Products</th><th>Last pass</th><th class='r'>Running</th><th class='r'>Failing</th></tr>${shopRows(d.sites, d.checks)}</table></div></section>`;
  },

  // ---------------------------------------------------------------- Jobs
  async Jobs() {
    const d = await api('/api/jobs?limit=2000'), now = Date.now();
    const fast = j => /^deals:|p_n_deal_type|min_offer_price|flash-sales/.test(j.target || '');
    const due = j => j.next_due_at && Date.parse(j.next_due_at) <= now && !j.running;
    const groups = [...new Set(d.jobs.map(j => j.grp).filter(Boolean))].sort();
    const keep = {all: () => true, failing: j => j.failures > 0, running: j => j.running, due, fast};
    const rows = d.jobs.filter(j => keep[jz.only](j) && (!jz.site || j.site === jz.site) && (!jz.grp || j.grp === jz.grp));
    const card = (k, v, color) => `<div class='card kpi'><div class='k'>${k}</div><div class='v' ${color ? `style='color:var(--fail-fg)'` : ''}>${v}</div></div>`;
    every(15000, render);
    return head('Jobs', 'The crawl schedule: each category is split by price into slices, and each slice is a job a worker leases') +
      `<section class='cards'>${card('Slices', d.jobs.length.toLocaleString())}${card('Running now', d.jobs.filter(j => j.running).length)}${card('Due now', d.jobs.filter(due).length)}
        ${card('Failing', d.jobs.filter(j => j.failures > 0).length, d.jobs.some(j => j.failures > 0))}${card('Deal-feed fast lanes', d.jobs.filter(fast).length)}</section>
      <div class='row'><div class='row' role='group' aria-label='Show'>${[['all', 'All, failing first'], ['failing', 'Failing'], ['running', 'Running'], ['due', 'Due now'], ['fast', 'Deal feeds']].map(([k, l]) => `<button type='button' class='chip ${jz.only === k ? 'on' : ''}' onclick="jz.only='${k}';render()">${l}</button>`).join('')}</div>
        <span class='grow'></span><label class='sr' for='js'>Shop</label><select id='js' class='field' onchange='jz.site=this.value;render()'>${shopOptions(jz.site)}</select>
        <label class='sr' for='jg'>Group</label><select id='jg' class='field' onchange='jz.grp=this.value;render()'>${options(groups.map(g => [g, g]), jz.grp, 'All groups')}</select></div>
      <section class='card' style='overflow:hidden'><div class='scroll'><table><tr><th>Shop</th><th>Group</th><th>Slice</th><th>State</th><th class='r'>Every</th><th>Next due</th><th>Last done</th><th class='r'>Last run</th><th>Error</th></tr>
        ${rows.slice(0, 300).map(j => `<tr class='${j.failures ? 'bad' : ''}'><td>${esc(shop(j.site))}</td><td>${esc(j.grp || '')}</td><td class='mono'>${esc(j.target)}${j.kind !== 'crawl' ? ` <span class='sub'>(${esc(j.kind)})</span>` : ''}</td>
          <td class='nw'>${fast(j) ? pill('new', 'fast lane') + ' ' : ''}${j.failures ? pill('fail', 'failed ×' + j.failures, 1) : j.running ? pill('info', j.lease_worker, 1) : due(j) ? pill('idle', 'due', 1) : pill('ok', 'waiting', 1)}</td>
          <td class='num r'>${j.every_min} m</td><td class='num'>${j.running ? 'running' : due(j) ? 'now' : esc((j.next_due_at || '').slice(11, 16))}</td><td class='muted nw'>${ago(j.last_done_at)}</td>
          <td class='num r'>${j.last_count ?? '—'} / ${j.last_changes ?? 0}</td><td class='muted' style='max-width:260px'>${esc(j.last_error || '')}</td></tr>`).join('') || `<tr><td class='empty' colspan='9'>No jobs match</td></tr>`}</table></div>
        <div class='cardfoot'>Showing ${Math.min(300, rows.length)} of ${rows.length} · "Last run" is products seen / prices changed · times in UTC</div></section>`;
  },

  // ---------------------------------------------------------------- Logs
  async Logs() {
    const d = await api(`/api/events?limit=300&level=${lz.level}` + (lz.src ? '&source=' + encodeURIComponent(lz.src) : ''));
    const devs = (ov || await api('/api/overview')).devices.map(x => x.name).concat(['coordinator']);
    lastLog = d.events.reduce((m, e) => Math.max(m, e.id), 0);
    setTimeout(() => every(3000, pollLogs), 0);
    return head('Logs', 'Live lines from the server and every worker, newest first · kept 7 days',
        `<span class='row' style='font-size:13px'><span style='width:8px;height:8px;border-radius:50%;background:${lz.paused ? 'var(--muted)' : 'var(--down)'};display:inline-block'></span>${lz.paused ? 'Paused' : 'Live, every 3 s'}</span>
         <button class='btn' type='button' onclick='lz.paused=!lz.paused;render()'>${lz.paused ? 'Resume' : 'Pause'}</button>`) +
      `<div class='row'><div class='row' role='group' aria-label='Level'>${[['info', 'All'], ['warning', 'Warnings'], ['error', 'Errors']].map(([v, l]) => `<button type='button' class='chip ${lz.level === v ? 'on' : ''}' onclick="lz.level='${v}';render()">${l}</button>`).join('')}</div>
        <label class='sr' for='lsrc'>Source</label><select id='lsrc' class='field' onchange='lz.src=this.value;render()'>${options(devs.map(n => [n, n]), lz.src, 'All sources')}</select>
        <label class='sr' for='lq'>Filter text</label><input id='lq' class='field' style='flex:1 1 220px' type='search' placeholder='Filter text, e.g. jumia' value='${esc(lz.q)}' oninput='lz.q=this.value;filterLogs()'></div>
      <section class='card' style='overflow:hidden'><div class='scroll'><table><thead><tr><th>Time (UTC)</th><th>From</th><th>Level</th><th>Message</th></tr></thead><tbody id='logrows'>
        ${d.events.slice().reverse().map(logRow).join('')}</tbody></table></div><div class='cardfoot'>The newest 300 lines, then new ones as they arrive</div></section>`;
  },

  // ---------------------------------------------------------------- Queries
  async Queries() {
    qz.list = qz.list || (await api('/api/queries')).queries;
    const fromHash = location.hash.split(':')[1];
    if (fromHash && qz.list.some(x => x.name === fromHash)) qz.name = fromHash;
    if (!qz.name) qz.name = qz.list[0].name;
    history.replaceState(null, '', '#Queries:' + qz.name);
    const query = `hours=${qz.hours}&grp=${encodeURIComponent(qz.grp)}&site=${qz.site}&q=${encodeURIComponent(qz.q)}` + (qz.sort ? `&sort=${encodeURIComponent(qz.sort)}&desc=${qz.desc ? 1 : 0}` : '');
    const [d, groups] = await Promise.all([api(`/api/queries/${qz.name}?limit=${PAGE}&offset=0&` + query), qz.groups ? qz.groups : api('/api/groups').then(r => qz.groups = r.groups)]);
    const isMoney = c => /^(was|now|price|cheapest|dearest|lowest|highest|crossed_out|previous_low|highest_seen|alert_price|price_now|a|b)$/.test(c);
    const hide = new Set(['url', 'seller', 'product_id']);
    const cols = d.columns.filter(c => !hide.has(c)), iu = d.columns.indexOf('url'), ip = d.columns.indexOf('product_id'), is = d.columns.indexOf('seller');
    const cell = (v, c, row) => c === 'title' && (iu >= 0 || ip >= 0)
        ? (ip >= 0 && row[ip] ? `<a class='rowlink' href='#Products:${row[ip]}'>${esc(v)}</a>` : `<a class='rowlink' href='${esc(row[iu])}' target='_blank' rel='noopener'>${esc(v)}</a>`)
          + (iu >= 0 && row[iu] && ip >= 0 ? ` <a href='${esc(row[iu])}' target='_blank' rel='noopener' class='muted' aria-label='open in shop'>↗</a>` : '') + (is >= 0 && row[is] ? ` <span class='sub'>· ${esc(row[is])}</span>` : '')
      : c === 'site' ? esc(shop(v)) : isMoney(c) ? `<span class='num'>${money(v)}</span>` : typeof v === 'number' ? `<span class='num'>${v.toLocaleString()}</span>`
      : typeof v === 'string' && /^\d{4}-\d\d-\d\dT/.test(v) ? `<span class='num'>${esc(v.slice(5, 16).replace('T', ' '))}</span>` : esc(v);
    const qrow = r => `<tr>${d.columns.map((c, i) => hide.has(c) ? '' : `<td>${cell(r[i], c, r)}</td>`).join('')}</tr>`;
    const nextFeed = {url: o => `/api/queries/${qz.name}?limit=${PAGE}&offset=${o}&total=${d.total}&` + query, row: qrow, rows: '#qrows', count: '#qcount', more: '#qmore', next: d.rows.length, total: d.total, busy: false};
    const lq = qz.listQ.trim().toLowerCase();
    const listed = qz.list.filter(x => (qz.topic === 'all' || topicOf(x.name) === qz.topic) && (!lq || x.title.toLowerCase().includes(lq) || x.name.includes(lq)));
    const menu = ['Prices', 'Alerts', 'Crawling'].map(t => { const xs = listed.filter(x => topicOf(x.name) === t); return xs.length ? `<div class='qh'>${t}</div>` +
      xs.map(x => `<button type='button' class='q ${x.name === qz.name ? 'on' : ''}' onclick="pickQuery('${x.name}')">${esc(x.title)}</button>`).join('') : ''; }).join('');
    const tags = [qz.q && ['Text: ' + qz.q, 'q', ''], qz.site && ['Shop: ' + shop(qz.site), 'site', ''], qz.grp && ['Group: ' + qz.grp, 'grp', '']].filter(Boolean);
    return {feed: nextFeed, html: head('Queries', 'Ready-made questions about the data. Read-only; filtering and sorting run in the database.') +
      `<div class='split'>
        <aside class='card' aria-label='Saved queries' style='flex:1 1 250px;padding:12px 8px 8px;display:flex;flex-direction:column;gap:10px'>
          <div style='padding:0 4px;display:flex;flex-direction:column;gap:10px'><label class='sr' for='qls'>Search queries</label>
            <input id='qls' class='field' type='search' placeholder='Search queries, e.g. stock' value='${esc(qz.listQ)}' oninput='qz.listQ=this.value;render().then(()=>{const i=$("#qls");i.focus();i.setSelectionRange(i.value.length,i.value.length)})'>
            <div class='row' role='group' aria-label='Topic' style='gap:6px'>${['all', 'Prices', 'Alerts', 'Crawling'].map(t => `<button type='button' class='chip small ${qz.topic === t ? 'on' : ''}' onclick="qz.topic='${t}';render()">${t === 'all' ? 'All' : t}</button>`).join('')}</div></div>
          ${menu || `<div class='empty'>No saved query matches</div>`}</aside>
        <section style='flex:999 1 420px;min-width:0;display:flex;flex-direction:column;gap:12px'>
          <div class='row' style='justify-content:space-between'><div><h2 style='margin:0;font-size:18px'>${esc(d.title)}</h2><div class='sub' style='font-size:13px' id='qcount'>${d.rows.length.toLocaleString()} of ${d.total.toLocaleString()} rows</div></div>
            <button class='btn' type='button' onclick='downloadCsv()'>Download CSV</button></div>
          <p class='muted' style='margin:0;font-size:13px'>${esc(d.about)}</p>
          <form class='card row' role='search' style='padding:12px' onsubmit='return false'>
            ${d.filters.q ? `<label class='sr' for='qq'>Search in results</label><input id='qq' class='field' style='flex:1 1 200px' type='search' placeholder='Search in results' value='${esc(qz.q)}' onchange='qz.q=this.value;render()'>` : ''}
            ${d.uses_window ? `<label class='sr' for='qhours'>Window</label><select id='qhours' class='field' onchange='qz.hours=this.value;render()'>${[['6', 'Last 6 h'], ['24', 'Last 24 h'], ['72', 'Last 3 days'], ['168', 'Last 7 days'], ['720', 'Last 30 days']].map(([v, l]) => `<option value='${v}' ${v === qz.hours ? 'selected' : ''}>${l}</option>`).join('')}</select>` : ''}
            ${d.filters.site ? `<label class='sr' for='qsite'>Shop</label><select id='qsite' class='field' onchange='qz.site=this.value;render()'>${shopOptions(qz.site)}</select>` : ''}
            ${d.filters.grp ? `<label class='sr' for='qgrp'>Group</label><select id='qgrp' class='field' onchange='qz.grp=this.value;render()'>${options(groups.map(g => [g, g]), qz.grp, 'All groups')}</select>` : ''}
            ${!d.filters.q && !d.uses_window && !d.filters.site && !d.filters.grp ? `<span class='muted' style='font-size:13px'>This report has no filters</span>` : ''}</form>
          ${tags.length ? `<div class='row'>${tags.map(([l, k]) => `<span class='tag'>${esc(l)}<button type='button' aria-label='Remove filter' onclick="qz.${k}='';render()">×</button></span>`).join('')}
            <button class='btn small' type='button' onclick="qz.q=qz.site=qz.grp='';render()">Clear all</button></div>` : ''}
          <div class='card' style='overflow:hidden'><div class='scroll'><table><thead><tr>${cols.map(c => `<th class='sortable' data-qcol='${esc(c)}'>${esc(c.replace(/_/g, ' '))}${qz.sort === c ? (qz.desc ? " <span class='dir'>▼</span>" : " <span class='dir'>▲</span>") : ''}</th>`).join('')}</tr></thead>
            <tbody id='qrows'>${d.rows.map(qrow).join('')}</tbody></table></div>
            <div id='qmore' class='cardfoot'>${d.rows.length < d.total ? "<span><span class='spinner'></span>loading more as you scroll…</span>" : d.total ? 'end of list' : 'No rows match'}</div></div>
        </section></div>`};
  },

  // ---------------------------------------------------------------- Subscribers
  async Subscribers() {
    const d = await api('/api/subscribers'), people = d.people; sz.people = people;
    const pending = people.filter(p => p.status === 'pending'), rest = people.filter(p => p.status !== 'pending');
    const who = p => `<div class='row' style='gap:10px;flex-wrap:nowrap'><div class='av'>${esc((p.name || '?').slice(0, 1).toUpperCase())}</div><div><div style='font-weight:500'>${esc(p.name || p.chat_id)}${p.is_admin ? ' (you)' : ''}</div>
      <div class='sub'>${p.username ? '@' + esc(p.username) : 'id ' + p.chat_id}</div></div></div>`;
    const btn = (p, action, label, cls = '') => d.admin ? `<button class='btn small ${cls}' type='button' onclick="manage(${p.chat_id},'${action}')">${label}</button>` : '';
    const status = {active: ['ok', 'active'], stopped: ['warn', 'paused (/stop)'], rejected: ['idle', 'rejected'], blocked: ['fail', 'blocked the bot']};
    return head('Subscribers', 'People who get deal alerts from the Telegram bot. The same actions as the bot\'s /subscribers buttons.') +
      (d.admin ? '' : `<section class='banner'>${pill('idle', 'Read-only')}<span class='msg muted'>You are logged in with a worker token. Log in with the admin token to approve or remove people.</span></section>`) +
      (pending.length ? `<section class='card' style='overflow:hidden;border-color:var(--selline)'><div class='cardhead' style='background:var(--sel)'><h2 style='color:var(--selfg)'>Waiting for approval</h2><span class='num' style='color:var(--selfg)'>${pending.length}</span></div>
        ${pending.map(p => `<div class='row' style='padding:14px 16px;border-top:1px solid var(--line);gap:12px'><div class='grow'>${who(p)}</div><span class='sub'>pressed Start ${ago(p.created_at)} ago</span>
          ${btn(p, 'approve', 'Approve', 'pri')}${btn(p, 'reject', 'Reject')}</div>`).join('')}</section>` : '') +
      `<section class='cards'>
        <div class='card kpi'><div class='k'>Active</div><div class='v'>${people.filter(p => p.status === 'active').length}</div></div>
        <div class='card kpi'><div class='k'>Paused (/stop)</div><div class='v'>${people.filter(p => p.status === 'stopped').length}</div></div>
        <div class='card kpi'><div class='k'>Helper admins</div><div class='v'>${people.filter(p => p.is_manager).length}</div></div>
        <div class='card kpi'><div class='k'>Messages, 7 days</div><div class='v'>${people.reduce((n, p) => n + p.sent_7d, 0).toLocaleString()}</div></div></section>
      <section class='card' style='overflow:hidden'><div class='cardhead'><h2>Everyone</h2></div><div class='scroll'><table>
        <tr><th>Person</th><th>Role</th><th>Status</th><th>Since</th><th class='r'>Messages, 7 d</th><th class='r'>Actions</th></tr>
        ${rest.map(p => `<tr><td>${who(p)}</td><td>${p.is_admin ? pill('info', 'Admin') : p.is_manager ? pill('new', 'Helper admin') : pill('idle', 'Subscriber')}</td>
          <td>${pill(...(status[p.status] || ['idle', p.status]), 1)}</td><td class='num nw'>${esc((p.approved_at || p.created_at || '').slice(0, 10))}</td><td class='num r'>${p.sent_7d}</td>
          <td class='r nw'>${p.is_admin ? "<span class='muted'>—</span>" : (p.is_manager ? btn(p, 'demote', 'Make subscriber') : p.status === 'active' ? btn(p, 'promote', 'Make helper') : '') + ' ' + btn(p, 'remove', 'Remove', 'danger')}</td></tr>`).join('') || `<tr><td class='empty' colspan='6'>Nobody yet. Share t.me/your_bot and they will appear here.</td></tr>`}
      </table></div></section>`;
  },

  // ---------------------------------------------------------------- Rules
  async Rules() {
    const d = await api('/api/rules'), a = d.alerts, s = d.schedule;
    const kv = (label, value) => `<div class='kv'><span>${label}</span><b>${value}</b></div>`;
    const p = x => Math.round(x * 100) + '%', yes = x => x ? 'yes' : 'no';
    const rule = (title, lines) => `<div class='card rule'><h3>${title}</h3>${lines.join('')}</div>`;
    return head('Rules & watches', 'What counts as a real discount, and how often each group is checked. Change them in <span class="mono">/etc/deals/config.toml</span> and restart the coordinator.') +
      `<h2 style='margin:4px 0 0;font-size:17px'>Alert rules</h2>
      <section class='stats' style='grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px'>
        ${rule('Real price drop', [kv('Below the typical 90-day price by at least', p(a.min_drop)), kv('History needed', a.min_history_days + ' days'), kv('Skip if this cheap more than', p(a.max_share_at_or_below) + ' of the time')])}
        ${rule('All-time low', [kv('New lowest price, below typical by', p(a.atl_min_drop)), kv('History needed', a.atl_min_history_days + ' days')])}
        ${rule('Early drop (short history)', [kv('Below everything seen by', p(a.early_min_drop)), kv('From this much history', a.early_min_history_days + ' day'), kv('Hold until seen again', yes(a.confirm_early_drops))])}
        ${rule('New product with a shop discount', [kv('Shop-claimed discount at least', p(a.new_product_min_drop)), kv('Reject claims bigger than', p(a.list_max_claimed_drop)), kv('Hold until seen again', yes(a.confirm_low_confidence)), kv('Send anyway after', a.confirm_minutes + ' min')])}
        ${rule('Possible price error', [kv('Below typical by at least', p(a.price_error_drop))])}
        ${rule('Repeats', [kv("Don't alert the same product again for", a.realert_after_days + ' days'), kv('Unless it drops another', p(a.realert_extra_drop))])}
      </section>
      <div><h2 style='margin:4px 0 0;font-size:17px'>Watches</h2><p class='muted' style='margin:4px 0 0;font-size:13px'>"Worst time to notice" is the backoff cap: a flash sale in that group is seen within it.</p></div>
      <section class='card' style='overflow:hidden'><div class='scroll'><table><tr><th>Group</th><th class='r'>Every</th><th class='r'>Worst time to notice</th><th class='r'>Min price</th><th class='r'>Min drop</th><th>Shops</th><th>Fast lanes</th></tr>
        ${d.watches.map(w => `<tr><td><b style='font-weight:500'>${esc(w.name)}</b>${w.label && w.label !== w.name ? ` <span class='muted' dir='rtl'>${esc(w.label)}</span>` : ''}</td>
          <td class='num r'>${w.interval_minutes} min</td><td class='num r'>${w.max_interval_minutes} min</td><td class='num r'>${w.min_price ? w.min_price.toLocaleString() : '—'}</td>
          <td class='num r'>${w.min_drop ? p(w.min_drop) : 'default'}</td><td class='nw'>${SITES.map(x => `<span class='sh ${w.sites.includes(x[0]) ? 'on' : 'off'}' title='${x[1]}'>${x[2]}</span>`).join('')}</td>
          <td>${w.fast_lanes ? pill('new', w.fast_lanes + ' feed' + (w.fast_lanes > 1 ? 's' : '')) : pill('idle', 'none')}</td></tr>`).join('')}</table></div>
        <div class='cardfoot'>${SITES.map(x => x[2] + ' ' + x[1]).join(' · ')}</div></section>
      <h2 style='margin:4px 0 0;font-size:17px'>Schedule</h2>
      <section class='card rule' style='display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px 28px'>
        ${kv('Fastest re-crawl of a slice', s.min_interval_minutes + ' min')}${kv('Quiet slices back off to interval ×', s.max_interval_factor)}
        ${kv('Re-split categories every', s.replan_days + ' days')}${kv('Job lease length', s.lease_minutes + ' min')}
        ${s.feed_max_interval_minutes ? kv('Deal feeds never slower than', s.feed_max_interval_minutes + ' min') : ''}</section>`;
  },
};
const dz = {days: '1', loaded: null, data: [], kind: 'all', site: '', state: '', q: '', sel: null, shown: 50};
const pz = {q: '', site: '', grp: '', sort: 'recent', stock: false, changed: 24};
const jz = {only: 'all', site: '', grp: ''}, lz = {level: 'info', src: '', q: '', paused: false};
const qz = {list: null, groups: null, name: null, hours: '24', grp: '', site: '', q: '', sort: null, desc: false, listQ: '', topic: 'all'};
const TOPICS = {Alerts: ['alerts_by_day', 'held_and_cancelled', 'deliveries'], Crawling: ['coverage', 'schedule', 'device_hours', 'tables']};
const topicOf = n => Object.keys(TOPICS).find(t => TOPICS[t].includes(n)) || 'Prices';
function pickQuery(name) { qz.name = name; qz.sort = null; history.replaceState(null, '', '#Queries:' + name); render(); }
async function downloadCsv() {
  const query = `hours=${qz.hours}&grp=${encodeURIComponent(qz.grp)}&site=${qz.site}&q=${encodeURIComponent(qz.q)}` + (qz.sort ? `&sort=${encodeURIComponent(qz.sort)}&desc=${qz.desc ? 1 : 0}` : '');
  const d = await api(`/api/queries/${qz.name}?limit=500&offset=0&` + query);
  const cell = v => v == null ? '' : /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v);
  const csv = [d.columns, ...d.rows].map(r => r.map(cell).join(',')).join('\n');
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['﻿' + csv], {type: 'text/csv'})); a.download = qz.name + '.csv'; a.click();
}
const sz = {people: []};
async function manage(chat, action) {
  const name = (sz.people.find(p => p.chat_id === chat) || {}).name || chat;
  const ask = {remove: `Remove ${name}? They stop getting alerts and can ask again with /start.`, reject: `Reject ${name}'s request?`}[action];
  if (ask && !confirm(ask)) return;
  try { await api(`/api/subscribers/${chat}/${action}`, 'POST'); toast('Done: ' + action + ' ' + name); render(); } catch (e) { toast(e.message); }
}
async function sendReport(b) {
  b.disabled = true;
  try { await api('/api/report', 'POST'); toast('The daily report is on its way to Telegram'); } catch (e) { toast(e.message); } finally { b.disabled = false; }
}
const PAGE = 100;
let feed = null, lastLog = 0;
const PRODUCT_COLS = [['Product', 'title', 'title_desc'], ['Shop', 'site', 'site_desc'], ['Group', 'grp', 'grp_desc'], ['Price', 'cheap', 'dear'],
  ['Crossed-out', 'crossed', 'crossed_desc'], ['Stock', 'stock', 'stock_desc'], ['Rating', 'rating_low', 'rating'], ['Last change', 'oldest', 'recent']];
const PRODUCT_SORT_NAMES = [['recent', 'Recently changed'], ['oldest', 'Oldest change'], ['cheap', 'Cheapest first'], ['dear', 'Most expensive'], ['rating', 'Best rated'],
  ['rating_low', 'Worst rated'], ['reviews', 'Most reviews'], ['reviews_few', 'Fewest reviews'], ['title', 'Title A→Z'], ['title_desc', 'Title Z→A'],
  ['site', 'Shop'], ['grp', 'Group'], ['crossed', 'Crossed-out price ↑'], ['crossed_desc', 'Crossed-out price ↓'], ['stock', 'In stock first'], ['stock_desc', 'Out of stock first']];
const stockPill = p => p.in_stock ? (p.stock_left ? pill('warn', p.stock_left + ' left') : pill('ok', 'in stock')) : pill('idle', 'sold out');
const productRow = p => `<tr class='click' onclick='openProduct(${p.id})'><td><a class='rowlink' href='#Products:${p.id}' onclick='event.stopPropagation()'>${esc(p.title)}</a><div class='sub'>${esc(p.sku)}${p.seller ? ' · ' + esc(p.seller) : ''}</div></td>
  <td class='nw'>${esc(shop(p.site))}</td><td>${esc(p.grp || '')}</td><td class='num nw'>${money(p.price)}</td><td class='num r muted'>${p.list_price ? '<s>' + money(p.list_price) + '</s>' : '—'}</td>
  <td>${stockPill(p)}</td><td class='num r nw'>${p.rating ? p.rating.toFixed(1) + ` <span class='muted'>(${(p.reviews || 0).toLocaleString()})</span>` : '—'}</td><td class='num r muted nw'>${ago(p.last_change_at)}</td></tr>`;
// Endless scrolling for paged lists (products, saved queries): when the marker under the
// table comes into view, fetch the next page for the same filters and sort, append it, and
// stop at the total. `feed` is replaced on every render, so a stale page is dropped.
function watchScroll() {
  const f = feed, more = f && $(f.more); if (!more) return;
  const io = new IntersectionObserver(async entries => {
    if (!entries.some(e => e.isIntersecting) || f !== feed || f.busy || f.next >= f.total) return;
    f.busy = true;
    try {
      const d = await api(f.url(f.next)), items = d.products || d.rows;
      if (f !== feed) return;  // filters or tab changed meanwhile
      $(f.rows).insertAdjacentHTML('beforeend', items.map(f.row).join(''));
      f.next += items.length; f.total = d.total;
      $(f.count).innerHTML = f.rows === '#prows' ? `<b class='num'>${f.next.toLocaleString()}</b> <span class='muted'>of ${f.total.toLocaleString()} shown</span>` : `${f.next.toLocaleString()} of ${f.total.toLocaleString()} rows`;
      if (f.next >= f.total || !items.length) { more.textContent = 'end of list'; io.disconnect(); }
    } finally { f.busy = false; }
  }, {rootMargin: '400px'});
  io.observe(more);
}
const logRow = e => `<tr class='${esc(e.level)}' data-text='${esc((e.source + ' ' + e.message).toLowerCase())}' ${lz.q && !(e.source + ' ' + e.message).toLowerCase().includes(lz.q.toLowerCase()) ? "style='display:none'" : ''}>
  <td class='mono muted nw'>${esc(e.at.slice(5, 19).replace('T', ' '))}</td><td class='nw'>${esc(e.source)}</td><td><span class='lv ${e.level === 'ERROR' ? 'fail' : e.level === 'WARNING' ? 'warn' : 'idle'}'>${esc(e.level)}</span></td><td class='mono'>${esc(e.message)}</td></tr>`;
function filterLogs() { const q = lz.q.toLowerCase(); document.querySelectorAll('#logrows tr').forEach(r => r.style.display = !q || r.dataset.text.includes(q) ? '' : 'none'); }
async function pollLogs() {
  if (tab !== 'Logs' || lz.paused) return;
  const d = await api(`/api/events?after_id=${lastLog}&level=${lz.level}` + (lz.src ? '&source=' + encodeURIComponent(lz.src) : ''));
  if (!d.events.length || tab !== 'Logs') return;
  lastLog = d.events.reduce((m, e) => Math.max(m, e.id), lastLog);
  $('#logrows').insertAdjacentHTML('afterbegin', d.events.slice().reverse().map(logRow).join(''));
}
async function productPage(id) {
  const d = await api('/api/products/' + id), p = d.product, s = d.stats;
  const drop = s.drop_vs_typical, inflated = p.list_price && s.high && p.list_price > s.high * 1.02;
  const badges = (p.badges || '').split(',').filter(Boolean).map(b => pill('idle', b)).join(' ');
  const stat = (k, v, cls = '') => `<div class='stat'><div class='k'>${k}</div><div class='v ${cls}'>${v}</div></div>`;
  return `<nav aria-label='Breadcrumb' style='font-size:13px'><a href='#Products'>Products</a> <span class='muted'>/ ${esc(p.grp || '')} / ${esc(shop(p.site))}</span></nav>
    <header class='row' style='gap:20px;justify-content:space-between;align-items:flex-start'>
      <div style='flex:1 1 480px;min-width:0;display:flex;flex-direction:column;gap:10px'>
        <h1 style='margin:0;font-size:24px;line-height:1.3;letter-spacing:-.01em'>${esc(p.title)}</h1>
        <div class='row' style='gap:6px'>${pill('idle', shop(p.site))}${p.grp ? pill('idle', p.grp) : ''}${pill('idle', p.sku)}${p.seller ? pill('idle', 'Sold by ' + p.seller) : ''}${p.rating ? pill('idle', `${p.rating.toFixed(1)} stars · ${(p.reviews || 0).toLocaleString()} reviews`) : ''}${stockPill(p)} ${badges}</div>
        <div class='row'><a class='btn pri' href='${esc(p.url)}' target='_blank' rel='noopener'>Open in ${esc(shop(p.site))} ↗</a></div>
      </div>
      <div class='card' style='flex:0 1 340px;padding:18px;display:flex;flex-direction:column;gap:8px'>
        <div class='sub'>Price now</div>
        <div class='row' style='align-items:baseline;gap:10px'><span class='num' style='font-size:32px;font-weight:500'>${money(p.price)}</span><span class='muted'>EGP</span>
          ${drop != null && Math.abs(drop) >= 0.005 ? pill(drop > 0 ? 'ok' : 'warn', `${drop > 0 ? '▼' : '▲'} ${Math.abs(Math.round(drop * 100))}% vs typical`) : ''}</div>
        ${p.list_price ? `<div style='font-size:13px;line-height:1.5'><span class='muted'>Shop shows</span> <s class='num'>${money(p.list_price)}</s>${p.price && p.list_price > p.price ? ` <span class='muted'>(claims ${Math.round(100 * (1 - p.price / p.list_price))}% off).</span>` : ''}
          ${inflated ? `<span class='muted'> We never saw it above ${money(s.high)}, so the crossed-out price is ignored.</span>` : ''}</div>` : ''}
      </div></header>
    <section class='stats' aria-label='Price statistics'>${stat('Typical, 30 days', money(s.typical_30d))}${stat('Typical, 90 days', money(s.typical_90d))}${stat('Lowest seen', money(s.low), 'down')}
      ${stat('Highest seen', money(s.high))}${stat('Tracked for', s.tracked_days + ' days')}${stat('Changes, 90 days', s.changes_90d)}
      ${stat('Time at or below now', s.share_at_or_below == null ? '—' : Math.round(s.share_at_or_below * 100) + '%')}${stat('Last checked', ago(p.last_seen_at))}</section>
    <section class='card' style='padding:16px 18px;display:flex;flex-direction:column;gap:12px'><div class='row' style='justify-content:space-between'><h2 style='margin:0;font-size:15px'>Price history</h2></div>${chart(d.history, s.typical_90d, d.alerts)}</section>
    <div class='grid2'>
      ${d.alerts.length ? `<section class='card' style='overflow:hidden'><div class='cardhead'><h2>Alerts for this product</h2></div><div class='scroll'><table><tr><th>When</th><th>Kind</th><th class='r'>Price</th><th class='r'>Drop</th><th>State</th></tr>
        ${d.alerts.map(a => `<tr><td class='num nw'>${hhmm(a.created_at)}</td><td>${kind(a.kind)}</td><td class='num r'>${money(a.price)}</td><td class='num r down'>${a.drop_pct ? '▼ ' + pct(a.drop_pct) : ''}</td>
          <td>${a.confirm_until ? pill('held', 'held') : a.error ? pill('idle', a.error.slice(0, 30)) : pill('ok', 'sent')}</td></tr>`).join('')}</table></div></section>` : ''}
      ${d.other_sellers.length ? `<section class='card' style='overflow:hidden'><div class='cardhead'><h2>Other sellers of this item</h2></div><div class='scroll'><table><tr><th>Seller</th><th class='r'>Price</th><th>Stock</th><th class='r'>vs this</th></tr>
        ${d.other_sellers.map(o => `<tr class='click' onclick='openProduct(${o.id})'><td>${esc(o.seller || '?')}</td><td class='num r'>${money(o.price)}</td><td>${o.in_stock ? pill('ok', 'in stock') : pill('idle', 'sold out')}</td>
          <td class='num r ${o.price > p.price ? 'up' : 'down'}'>${o.price && p.price ? (o.price > p.price ? '+' : '') + money(o.price - p.price) : ''}</td></tr>`).join('')}</table></div></section>` : ''}
    </div>
    <section class='card' style='overflow:hidden'><div class='cardhead'><h2>Recorded changes</h2><span class='sub'>only changes are stored, not every visit</span></div><div class='scroll'><table>
      <tr><th>When</th><th class='r'>Price</th><th class='r'>Crossed-out</th><th>Stock</th><th class='r'>Change</th></tr>
      ${d.history.slice().reverse().map((h, i, arr) => { const prev = arr[i + 1], ch = prev && prev.price && h.price ? h.price - prev.price : null;
        return `<tr><td class='num nw'>${hhmm(h.observed_at)}</td><td class='num r'>${money(h.price)}</td><td class='num r muted'>${money(h.list_price)}</td><td>${h.in_stock ? 'in stock' : 'sold out'}</td>
        <td class='num r ${ch < 0 ? 'down' : 'up'}'>${ch ? (ch < 0 ? '▼ ' : '▲ ') + money(Math.abs(ch)) : ''}</td></tr>`; }).join('')}</table></div></section>`;
}
// Step chart of the recorded prices (a price holds until the next row), with the 90-day typical
// price dashed, out-of-stock stretches left blank, and a dot where an alert went out. Hover for the value.
function chart(hist, typical, alerts) {
  const pts = hist.map(h => ({t: Date.parse(h.observed_at), p: h.in_stock ? h.price : null}));
  if (!pts.length) return "<p class='muted'>no history yet</p>";
  const now = Date.now(), t0 = pts[0].t, t1 = Math.max(now, pts[pts.length - 1].t + 1), W = 1000, H = 280, L = 64, R = 14, T = 14, B = 30;
  const vals = pts.map(x => x.p).filter(v => v != null).concat(typical ? [typical] : []);
  if (!vals.length) return "<p class='muted'>never in stock while tracked</p>";
  let lo = Math.min(...vals), hi = Math.max(...vals); if (hi === lo) { lo *= 0.95; hi *= 1.05; } const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const X = t => L + (t - t0) / (t1 - t0) * (W - L - R), Y = v => T + (hi - v) / (hi - lo) * (H - T - B);
  let path = '', prev = null;
  pts.forEach((x, i) => { const tEnd = i + 1 < pts.length ? pts[i + 1].t : t1;
    if (x.p == null) { prev = null; return; }
    path += (prev == null ? `M${X(x.t).toFixed(1)},${Y(x.p).toFixed(1)}` : `L${X(x.t).toFixed(1)},${Y(x.p).toFixed(1)}`) + `L${X(tEnd).toFixed(1)},${Y(x.p).toFixed(1)}`; prev = x.p; });
  const yTicks = Array.from({length: 5}, (_, i) => lo + (hi - lo) * i / 4);
  const days = (t1 - t0) / 864e5, step = days > 60 ? 14 : days > 14 ? 7 : days > 3 ? 1 : 0.25, xt = [];
  for (let t = t0; t <= t1; t += step * 864e5) xt.push(t);
  const fmt = t => { const d = new Date(t); return step < 1 ? d.toISOString().slice(5, 16).replace('T', ' ') : d.toLocaleDateString('en-GB', {day: 'numeric', month: 'short'}); };
  const marks = (alerts || []).filter(a => a.price && Date.parse(a.created_at) >= t0).map(a => `<circle cx='${X(Date.parse(a.created_at)).toFixed(1)}' cy='${Y(a.price).toFixed(1)}' r='5' fill='var(--down)' stroke='var(--card)' stroke-width='2'><title>${esc(a.kind)} alert, ${money(a.price)}</title></circle>`).join('');
  const data = JSON.stringify(pts.map(x => [x.t, x.p]));
  return `<div class='legend'><span><i style='border-color:var(--chart)'></i>price</span>${typical ? `<span><i style='border-color:var(--typical);border-top-style:dashed'></i>typical, 90 d</span>` : ''}<span><i style='border:0;width:9px;height:9px;border-radius:50%;background:var(--down)'></i>alert sent</span><span>gaps = sold out</span></div>
    <div class='chart' onmousemove='chartHover(event,${data},${t0},${t1},${L},${R},${W})' onmouseleave="this.querySelector('.tip').style.display='none'">
    <svg viewBox='0 0 ${W} ${H}' preserveAspectRatio='none' role='img' aria-label='Price history chart'>
      ${yTicks.map(v => `<line x1='${L}' x2='${W - R}' y1='${Y(v).toFixed(1)}' y2='${Y(v).toFixed(1)}' stroke='var(--line2)' stroke-width='1'/><text x='${L - 8}' y='${(Y(v) + 4).toFixed(1)}' text-anchor='end' font-size='11' fill='var(--muted)'>${Math.round(v / 100).toLocaleString()}</text>`).join('')}
      ${xt.map(t => `<text x='${X(t).toFixed(1)}' y='${H - 8}' text-anchor='middle' font-size='11' fill='var(--muted)'>${fmt(t)}</text>`).join('')}
      ${typical ? `<line x1='${L}' x2='${W - R}' y1='${Y(typical).toFixed(1)}' y2='${Y(typical).toFixed(1)}' stroke='var(--typical)' stroke-width='1.5' stroke-dasharray='6 5'/>` : ''}
      <path d='${path}' fill='none' stroke='var(--chart)' stroke-width='2' vector-effect='non-scaling-stroke'/>${marks}
      <line id='xhair' x1='0' x2='0' y1='${T}' y2='${H - B}' stroke='var(--muted)' stroke-width='1' style='display:none'/>
    </svg><div class='tip'></div></div>`;
}
function chartHover(ev, pts, t0, t1, L, R, W) {
  const box = ev.currentTarget, svg = box.querySelector('svg'), rect = svg.getBoundingClientRect();
  const fx = (ev.clientX - rect.left) / rect.width * W; if (fx < L || fx > W - R) return;
  const t = t0 + (fx - L) / (W - L - R) * (t1 - t0);
  let cur = null; for (const [pt, pp] of pts) { if (pt <= t) cur = pp; else break; }
  const tip = box.querySelector('.tip'), xh = svg.querySelector('#xhair');
  xh.setAttribute('x1', fx); xh.setAttribute('x2', fx); xh.style.display = '';
  tip.style.display = 'block'; tip.style.left = Math.min(ev.clientX - rect.left + 12, rect.width - 170) + 'px'; tip.style.top = (ev.clientY - rect.top - 40) + 'px';
  tip.innerHTML = `<div class='muted'>${new Date(t).toLocaleString('en-GB', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'})}</div><b class='num'>${cur == null ? 'sold out' : money(cur) + ' EGP'}</b>`;
}
// Column titles sort: products and saved queries on the server (so it holds across every page),
// any other table in the page.
document.addEventListener('click', ev => {
  const th = ev.target.closest('th'); if (!th || !th.closest('#view')) return;
  if (th.dataset.qcol) { qz.desc = qz.sort === th.dataset.qcol ? !qz.desc : false; qz.sort = th.dataset.qcol; render(); return; }
  if (th.dataset.asc) { pz.sort = pz.sort === th.dataset.desc ? th.dataset.asc : th.dataset.desc; render(); return; }
  const table = th.closest('table'), tr = th.parentElement, idx = [...tr.children].indexOf(th);
  const rows = [...table.querySelectorAll('tr')].filter(r => r !== tr && r.children.length === tr.children.length && !r.querySelector('th'));
  if (!rows.length) return;
  const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc'; tr.querySelectorAll('th').forEach(h => { delete h.dataset.dir; h.querySelector('.dir')?.remove(); });
  th.dataset.dir = dir; th.insertAdjacentHTML('beforeend', ` <span class='dir'>${dir === 'asc' ? '▲' : '▼'}</span>`);
  const num = s => { const n = parseFloat(String(s).replace(/[^0-9.\-]/g, '')); return isNaN(n) ? null : n; };
  rows.sort((a, b) => { const x = a.children[idx].textContent.trim(), y = b.children[idx].textContent.trim(), nx = num(x), ny = num(y);
    const c = nx != null && ny != null && /\d/.test(x) && /\d/.test(y) ? nx - ny : x.localeCompare(y); return dir === 'asc' ? c : -c; });
  const parent = rows[0].parentElement; rows.forEach(r => parent.appendChild(r));
});
let renderSeq = 0;
async function render() {
  const seq = ++renderSeq;  // a slower, older render must not overwrite a newer one
  try {
    const out = await views[tab](); if (seq !== renderSeq) return;
    const page = typeof out === 'string' ? {html: out, feed: null} : out;
    const y = window.scrollY, key = location.hash.split(':').slice(0, 2).join(':'), same = $('#view').dataset.key === key;
    $('#view').innerHTML = page.html; $('#view').dataset.key = key; feed = page.feed; if (feed) setTimeout(watchScroll, 0);
    if (!same) window.scrollTo(0, 0); else window.scrollTo(0, y);
    if (tab === 'Overview') every(15000, render);
  } catch (e) { if (seq === renderSeq) $('#view').innerHTML = `<p style='color:var(--fail-fg)'>${esc(e.message)}</p>`; }
}
api('/api/overview').then(d => { ov = d; side(); }).catch(() => {});
route();
</script></body></html>"""

def _icon(paths: str) -> str:
    return (f"<svg width='22' height='22' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.8' "
            f"aria-hidden='true'>{paths}</svg>")


ICONS = {
    "__I_GRID__": _icon("<rect x='3' y='3' width='7' height='7' rx='1.5'/><rect x='14' y='3' width='7' height='7' rx='1.5'/>"
                        "<rect x='3' y='14' width='7' height='7' rx='1.5'/><rect x='14' y='14' width='7' height='7' rx='1.5'/>"),
    "__I_TAG__": _icon("<path d='M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9-9-9z'/><circle cx='8' cy='8' r='1.5'/>"),
    "__I_SEARCH__": _icon("<circle cx='11' cy='11' r='7'/><path d='M20 20l-4-4'/>"),
    "__I_SCREEN__": _icon("<rect x='3' y='4' width='18' height='12' rx='2'/><path d='M8 20h8M12 16v4'/>"),
    "__I_MENU__": _icon("<path d='M4 6h16M4 12h16M4 18h16'/>"),
}
APP = APP.replace("__CSS__", CSS).replace("__THEME__", THEME_INIT)
for _mark, _svg in ICONS.items():
    APP = APP.replace(_mark, _svg)


# ---------------------------------------------------------------------------
# Saved queries (the "Queries" tab): fixed read-only SQL, chosen by name. `?`
# placeholders receive the cutoff time computed from `hours`. No user SQL ever
# runs; the viewer can only pick a name and a time window.
# ---------------------------------------------------------------------------

_PRODUCT = "p.id AS product_id, p.title, p.site, p.grp, p.url, p.seller"

QUERIES: dict[str, dict] = {
    "biggest_drops": {
        "title": "Biggest price drops",
        "about": "Products whose last recorded change in the window was a fall: previous recorded price versus now. "
                 "Sorted by the size of the fall. Click a title to open the shop page.",
        "sql": f"""WITH h AS (SELECT product_id, price, observed_at, in_stock,
                                   LAG(price) OVER (PARTITION BY product_id ORDER BY observed_at, id) prev
                              FROM price_history WHERE product_id IN (SELECT product_id FROM price_history WHERE observed_at >= ?))
                   SELECT {_PRODUCT}, h.prev AS was, h.price AS now, ROUND(100.0 * (h.prev - h.price) / h.prev, 1) AS drop_pct,
                          h.observed_at AS changed_at
                   FROM h JOIN products p ON p.id = h.product_id
                   WHERE h.observed_at >= ? AND h.in_stock = 1 AND h.price IS NOT NULL AND h.prev IS NOT NULL
                     AND h.price < h.prev AND h.price = p.price
                   ORDER BY drop_pct DESC LIMIT 200""",
        "cutoff_twice": True,
    },
    "biggest_rises": {
        "title": "Biggest price rises",
        "about": "The opposite of the drops: where a shop raised a price in the window (a rise followed by a 'cut' is the classic fake discount).",
        "sql": f"""WITH h AS (SELECT product_id, price, observed_at, in_stock,
                                   LAG(price) OVER (PARTITION BY product_id ORDER BY observed_at, id) prev
                              FROM price_history WHERE product_id IN (SELECT product_id FROM price_history WHERE observed_at >= ?))
                   SELECT {_PRODUCT}, h.prev AS was, h.price AS now, ROUND(100.0 * (h.price - h.prev) / h.prev, 1) AS rise_pct,
                          h.observed_at AS changed_at
                   FROM h JOIN products p ON p.id = h.product_id
                   WHERE h.observed_at >= ? AND h.price IS NOT NULL AND h.prev IS NOT NULL AND h.price > h.prev AND h.price = p.price
                   ORDER BY rise_pct DESC LIMIT 200""",
        "cutoff_twice": True,
    },
    "all_time_lows": {
        "title": "At their lowest price ever",
        "about": "In-stock products whose current price is below every earlier recorded price, tracked for at least 3 days. "
                 "'below_pct' is how far below the previous low. These are the candidates for real deals.",
        "sql": f"""SELECT {_PRODUCT}, p.price AS now, MIN(h.price) AS previous_low,
                          ROUND(100.0 * (MIN(h.price) - p.price) / MIN(h.price), 1) AS below_pct,
                          ROUND(JULIANDAY('now') - JULIANDAY(p.first_seen_at), 1) AS tracked_days
                   FROM products p JOIN price_history h ON h.product_id = p.id
                   WHERE p.in_stock = 1 AND p.price IS NOT NULL AND h.price IS NOT NULL AND h.observed_at < p.last_change_at
                     AND p.first_seen_at <= ?
                   GROUP BY p.id HAVING p.price < MIN(h.price)
                   ORDER BY below_pct DESC LIMIT 200""",
        "cutoff_days": 3,
    },
    "shop_claims": {
        "title": "What the shops claim right now",
        "about": "Biggest discounts the shops themselves advertise (crossed-out price versus price), with their badges. "
                 "Useful to see claims we did NOT alert on, and why (no evidence, inflated RRP, a cheaper seller).",
        "sql": f"""SELECT {_PRODUCT}, p.price AS now, p.list_price AS crossed_out, p.list_price_kind AS kind,
                          ROUND(100.0 * (p.list_price - p.price) / p.list_price, 1) AS claimed_pct, p.badges, p.stock_left,
                          (SELECT COUNT(*) FROM alerts a WHERE a.product_id = p.id) AS alerts
                   FROM products p
                   WHERE p.in_stock = 1 AND p.price IS NOT NULL AND p.list_price > p.price AND p.last_seen_at >= ?
                   ORDER BY claimed_pct DESC LIMIT 300""",
    },
    "alerts_by_day": {
        "title": "Alerts per day",
        "about": "How many deal alerts were created each day by kind, and how many were held for confirmation or cancelled "
                 "because the price went back up before the next crawl.",
        "sql": """SELECT SUBSTR(created_at, 1, 10) AS day, kind, COUNT(*) AS created,
                         SUM(confirm_until IS NOT NULL) AS still_held,
                         SUM(error IS NOT NULL AND error LIKE 'cancelled%') AS cancelled,
                         SUM(sent_at IS NOT NULL AND (error IS NULL OR error NOT LIKE 'cancelled%')) AS sent,
                         ROUND(AVG(drop_pct) * 100) AS avg_drop_pct, ROUND(AVG(score)) AS avg_score
                  FROM alerts WHERE kind != 'system' AND created_at >= ?
                  GROUP BY day, kind ORDER BY day DESC, created DESC""",
    },
    "held_and_cancelled": {
        "title": "Alerts held or cancelled",
        "about": "Low-confidence alerts waiting for the next crawl to confirm the price, and those cancelled because the price "
                 "reverted. A high cancel rate means the shop (or a seller) flips prices.",
        "sql": f"""SELECT a.created_at, a.kind, a.price AS alert_price, p.price AS price_now, p.in_stock,
                          CASE WHEN a.confirm_until IS NOT NULL THEN 'held until ' || SUBSTR(a.confirm_until, 12, 5) ELSE a.error END AS state,
                          {_PRODUCT}
                   FROM alerts a JOIN products p ON p.id = a.product_id
                   WHERE a.created_at >= ? AND (a.confirm_until IS NOT NULL OR a.error LIKE 'cancelled%')
                   ORDER BY a.created_at DESC LIMIT 200""",
    },
    "coverage": {
        "title": "Coverage per group and shop",
        "about": "How many products each group holds per shop, how many are in stock, how many show a crossed-out price, "
                 "and how fresh they are (not seen for 3 h means the slice is behind).",
        "sql": """SELECT grp, site, COUNT(*) AS products, SUM(in_stock) AS in_stock, SUM(list_price IS NOT NULL) AS with_crossed_out,
                         SUM(last_seen_at < ?) AS not_seen_3h, MIN(price) / 100 AS cheapest_egp,
                         ROUND(AVG(price) / 100) AS average_egp
                  FROM products GROUP BY grp, site ORDER BY grp, site""",
        "cutoff_hours_fixed": 3,
    },
    "schedule": {
        "title": "Crawl schedule per group",
        "about": "How often each group is being revisited right now (adaptive: busy slices speed up to 10 min, quiet ones slow "
                 "down to 3x the configured interval), and how far behind the queue is.",
        "sql": """SELECT grp, site, COUNT(*) AS slices, SUM(last_count) AS products,
                         ROUND(MIN(interval_s) / 60.0) AS fastest_min, ROUND(AVG(interval_s) / 60.0) AS average_min,
                         ROUND(MAX(interval_s) / 60.0) AS slowest_min,
                         SUM(next_due_at <= ?) AS due_now,
                         ROUND(MAX((JULIANDAY(?) - JULIANDAY(next_due_at)) * 1440)) AS most_overdue_min,
                         SUM(failures > 0) AS failing
                  FROM jobs WHERE kind = 'crawl' GROUP BY grp, site ORDER BY average_min""",
        "now_twice": True,
    },
    "volatile": {
        "title": "Most volatile products",
        "about": "Products with the most recorded price changes in the window (moves under 1% are not recorded). "
                 "Seller switching and A/B pricing show up here.",
        "sql": f"""SELECT {_PRODUCT}, COUNT(*) AS changes, MIN(h.price) AS lowest, MAX(h.price) AS highest, p.price AS now
                   FROM price_history h JOIN products p ON p.id = h.product_id
                   WHERE h.observed_at >= ? AND h.price IS NOT NULL
                   GROUP BY p.id HAVING changes >= 3 ORDER BY changes DESC LIMIT 200""",
    },
    "flipflops": {
        "title": "Price flip-flops (A → B → A)",
        "about": "Products that went to a price and straight back (5% or more). On Amazon this is usually the featured seller "
                 "switching; on noon, the same seller flipping. The rarity rule keeps these from alerting repeatedly.",
        "sql": f"""WITH h AS (SELECT product_id, price, observed_at,
                                   LAG(price) OVER w AS p1, LAG(price, 2) OVER w AS p2
                              FROM price_history WHERE product_id IN (SELECT product_id FROM price_history WHERE observed_at >= ?)
                              WINDOW w AS (PARTITION BY product_id ORDER BY observed_at, id))
                   SELECT {_PRODUCT}, h.p2 AS a, h.p1 AS b, ROUND(100.0 * ABS(h.p1 - h.p2) / h.p2, 1) AS swing_pct, h.observed_at AS back_at
                   FROM h JOIN products p ON p.id = h.product_id
                   WHERE h.observed_at >= ? AND h.price = h.p2 AND h.p1 IS NOT NULL AND ABS(h.p1 - h.p2) >= h.p2 * 0.05
                   ORDER BY swing_pct DESC LIMIT 200""",
        "cutoff_twice": True,
    },
    "noon_sellers": {
        "title": "noon: same item, several sellers",
        "about": "Items noon lists under more than one seller, with the gap between the cheapest and dearest in-stock offer. "
                 "A 'drop' by the dear seller is not a deal while the cheap one exists (the rules check this).",
        "sql": """SELECT SUBSTR(p.sku, 1, INSTR(p.sku, '@') - 1) AS item, MIN(p.title) AS title, COUNT(*) AS sellers,
                         MIN(p.price) AS cheapest, MAX(p.price) AS dearest,
                         ROUND(100.0 * (MAX(p.price) - MIN(p.price)) / MAX(p.price), 1) AS gap_pct,
                         MIN(p.url) AS url, MIN(p.grp) AS grp
                  FROM products p WHERE p.site = 'noon_eg' AND p.in_stock = 1 AND p.price IS NOT NULL AND p.last_seen_at >= ?
                  GROUP BY item HAVING sellers > 1 ORDER BY gap_pct DESC LIMIT 200""",
    },
    "inflated_crossed_out": {
        "title": "Inflated crossed-out prices",
        "about": "Products whose crossed-out price is more than 15% above anything we ever recorded for them (after 3 days of "
                 "tracking): the shop's 'was' price is not a price it ever had while we watched.",
        "sql": f"""SELECT {_PRODUCT}, p.price AS now, p.list_price AS crossed_out, MAX(h.price) AS highest_seen,
                          ROUND(100.0 * (p.list_price - MAX(h.price)) / MAX(h.price)) AS above_pct
                   FROM products p JOIN price_history h ON h.product_id = p.id
                   WHERE p.list_price IS NOT NULL AND h.price IS NOT NULL AND p.first_seen_at <= ?
                   GROUP BY p.id HAVING p.list_price > MAX(h.price) * 1.15 ORDER BY above_pct DESC LIMIT 200""",
        "cutoff_days": 3,
    },
    "new_products": {
        "title": "New products",
        "about": "Products first seen in the window, newest first. Many at once in one group usually means a re-plan or a "
                 "new sub-category, not a launch.",
        "sql": f"""SELECT p.first_seen_at, {_PRODUCT}, p.price AS now, p.list_price AS crossed_out, p.badges
                   FROM products p WHERE p.first_seen_at >= ? ORDER BY p.first_seen_at DESC LIMIT 300""",
    },
    "back_in_stock": {
        "title": "Back in stock",
        "about": "Products whose latest change in the window was out-of-stock to in-stock, with the price they came back at "
                 "versus the price before they went out.",
        "sql": f"""WITH h AS (SELECT product_id, price, in_stock, observed_at,
                                   LAG(in_stock) OVER w AS prev_stock, LAG(price) OVER w AS prev_price
                              FROM price_history WHERE product_id IN (SELECT product_id FROM price_history WHERE observed_at >= ?)
                              WINDOW w AS (PARTITION BY product_id ORDER BY observed_at, id))
                   SELECT h.observed_at AS back_at, {_PRODUCT}, h.price AS now, p.price AS price_now
                   FROM h JOIN products p ON p.id = h.product_id
                   WHERE h.observed_at >= ? AND h.in_stock = 1 AND h.prev_stock = 0
                   ORDER BY h.observed_at DESC LIMIT 200""",
        "cutoff_twice": True,
    },
    "deliveries": {
        "title": "Telegram deliveries per person",
        "about": "Messages handed to each subscriber in the window: sent, still pending, and given up after 10 attempts.",
        "sql": """SELECT s.chat_id, s.name, s.status, COUNT(d.id) AS messages, SUM(d.sent_at IS NOT NULL) AS sent,
                         SUM(d.sent_at IS NULL AND d.attempts < 10) AS pending, SUM(d.sent_at IS NULL AND d.attempts >= 10) AS gave_up,
                         MAX(d.error) AS last_error
                  FROM subscribers s LEFT JOIN deliveries d ON d.chat_id = s.chat_id
                       AND d.alert_id IN (SELECT id FROM alerts WHERE created_at >= ?)
                  GROUP BY s.chat_id ORDER BY messages DESC""",
    },
    "device_hours": {
        "title": "Work per device per hour",
        "about": "Jobs finished by each device in each hour of the window (from the shipped logs). Gaps are sleep, "
                 "blocks or outages.",
        "sql": """SELECT SUBSTR(at, 1, 13) || ':00' AS hour, source, COUNT(*) AS jobs_done
                  FROM events WHERE at >= ? AND message LIKE '[%] done %' GROUP BY hour, source ORDER BY hour DESC, source""",
    },
    "tables": {
        "title": "Database size",
        "about": "Row counts per table and the file size. History grows with price changes only (not with crawls).",
        "sql": """SELECT 'products' AS tbl, COUNT(*) AS rows FROM products
                  UNION ALL SELECT 'price_history', COUNT(*) FROM price_history
                  UNION ALL SELECT 'alerts', COUNT(*) FROM alerts
                  UNION ALL SELECT 'deliveries', COUNT(*) FROM deliveries
                  UNION ALL SELECT 'jobs', COUNT(*) FROM jobs
                  UNION ALL SELECT 'events', COUNT(*) FROM events
                  UNION ALL SELECT 'subscribers', COUNT(*) FROM subscribers
                  UNION ALL SELECT 'file size (MB)', (SELECT page_count * page_size / 1048576 FROM pragma_page_count(), pragma_page_size())
                  WHERE ? IS NOT NULL""",
    },
}


_LIMIT = re.compile(r"\s+LIMIT\s+(\d+)\s*$", re.I)
_COLUMNS: dict[str, list[str]] = {}


def run_query(conn: sqlite3.Connection, name: str, now: datetime, hours: float,
              grp: str | None = None, site: str | None = None, *, sort: str | None = None,
              desc: bool = False, limit: int = 100, offset: int = 0, known_total: int | None = None,
              search: str = "") -> dict:
    """One page of a saved query plus the total row count; `hours` is the look-back window
    where the query has one.

    `grp` / `site` filter the result on its `grp` / `site` columns when it has them, and the
    words of `search` must all appear in its `title` column when it has one. `sort`
    names a result column (anything else is ignored); without it the report keeps its own
    order. Filtering and sorting happen in the database, so they hold across every page.
    """
    q = QUERIES[name]
    if "cutoff_days" in q:
        cutoff = iso(now - timedelta(days=q["cutoff_days"]))
    elif "cutoff_hours_fixed" in q:
        cutoff = iso(now - timedelta(hours=q["cutoff_hours_fixed"]))
    else:
        cutoff = iso(now - timedelta(hours=hours))
    params: list = [cutoff, iso(now)] if q.get("now_twice") else [cutoff, cutoff] if q.get("cutoff_twice") else [cutoff]
    m = _LIMIT.search(q["sql"])
    inner = q["sql"][:m.start()] if m else q["sql"]  # the report's own LIMIT is replaced by paging
    if name not in _COLUMNS:  # the column list never changes: probe once
        _COLUMNS[name] = [c[0] for c in conn.execute(f"SELECT * FROM ({inner}) LIMIT 0", params).description]
    columns = _COLUMNS[name]
    where = []
    if grp and "grp" in columns:
        where.append("grp = ?"); params.append(grp)
    if site and "site" in columns:
        where.append("site = ?"); params.append(site)
    if "title" in columns:
        for word in search.split()[:6]:
            where.append("title LIKE ?"); params.append(f"%{word}%")
    filtered = f"SELECT * FROM ({inner}) {'WHERE ' + ' AND '.join(where) if where else ''}"
    # the count is only needed for the first page; later pages carry it from the client
    total = known_total if offset and known_total is not None else conn.execute(f"SELECT COUNT(*) FROM ({filtered})", params).fetchone()[0]
    order = ""
    if sort in columns:
        # the other columns break ties so paging is stable
        rest = ", ".join(f'"{c}"' for c in columns if c != sort)
        order = f' ORDER BY "{sort}" IS NULL, "{sort}" {"DESC" if desc else "ASC"}{", " + rest if rest else ""}'
    rows = [list(r) for r in conn.execute(f"{filtered}{order} LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()]
    return {"name": name, "title": q["title"], "about": q["about"], "columns": columns, "rows": rows,
            "total": total, "offset": offset, "sort": sort if sort in columns else None, "desc": desc,
            "uses_window": not ("cutoff_days" in q or "cutoff_hours_fixed" in q),
            "filters": {"grp": "grp" in columns, "site": "site" in columns, "q": "title" in columns}}


def query_list() -> list[dict]:
    return [{"name": k, "title": v["title"]} for k, v in QUERIES.items()]
