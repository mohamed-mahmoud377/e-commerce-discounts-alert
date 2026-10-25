"""Who is connected and how each device's fetching is going, plus everyone's logs.

Served by the coordinator as JSON (/api/devices, /api/events) and as a page
(/dashboard); `deals devices` and `deals logs` print the same in a terminal.
"""

import html
import logging
import queue
import sqlite3
import threading
from datetime import datetime, timedelta

from deals import db, scheduler
from deals.db import iso, parse_iso, utcnow

ONLINE = timedelta(minutes=15)


def devices(conn: sqlite3.Connection, now: datetime) -> list[dict]:
    out = []
    for w in conn.execute("SELECT * FROM workers ORDER BY name").fetchall():
        seen = parse_iso(w["last_seen_at"])
        online = now - seen < ONLINE
        sites = []
        for site in (w["sites"] or "").split(","):
            if site:
                sites.append(_site_state(conn, w, site, online, now))
        out.append({
            "name": w["name"], "ip": w["last_ip"], "version": w["version"], "online": online,
            "last_seen_at": w["last_seen_at"], "last_seen_seconds": int((now - seen).total_seconds()),
            "pages": w["pages"], "jobs_done": w["jobs_done"], "sites": sites,
        })
    return out


def _site_state(conn, w: sqlite3.Row, site: str, online: bool, now: datetime) -> dict:
    ws = conn.execute("SELECT * FROM worker_sites WHERE worker = ? AND site = ?", (w["name"], site)).fetchone()
    block = conn.execute("SELECT * FROM blocks WHERE site = ? AND ip = ? AND until > ?",
                         (site, w["last_ip"], iso(now))).fetchone()
    job = conn.execute("SELECT * FROM jobs WHERE lease_worker = ? AND site = ? AND lease_expires_at > ?",
                       (w["name"], site, iso(now))).fetchone()
    last_ok = parse_iso(ws["last_ok_at"]) if ws and ws["last_ok_at"] else None
    last_err = parse_iso(ws["last_error_at"]) if ws and ws["last_error_at"] else None

    if not online:
        state, detail = "offline", f"last seen {_ago(parse_iso(w['last_seen_at']), now)}"
    elif block:
        state, detail = "blocked", (f"paused until {block['until'][11:16]} UTC (block #{block['strikes']}): "
                                    f"{block['reason']}")
    elif job:
        state, detail = "crawling", f"{job['kind']} {job['target']} ({job['run_count']} products so far)"
    elif last_err and (not last_ok or last_err > last_ok) and now - last_err < timedelta(hours=1):
        state, detail = "failing", ws["last_error"]
    elif last_ok and now - last_ok < timedelta(minutes=30):
        state, detail = "ok", f"last page {_ago(last_ok, now)}"
    else:
        state, detail = "idle", "nothing due for this shop right now"
    return {
        "site": site, "state": state, "detail": detail,
        "last_ok_at": ws["last_ok_at"] if ws else None,
        "last_error_at": ws["last_error_at"] if ws else None, "last_error": ws["last_error"] if ws else None,
        "pages": ws["pages"] if ws else 0, "products": ws["products"] if ws else 0,
        "blocks": ws["blocks"] if ws else 0,
    }


def sites(conn: sqlite3.Connection, now: datetime) -> list[dict]:
    rows = conn.execute(
        """SELECT site, SUM(kind = 'crawl') slices, SUM(next_due_at <= ?) due,
                  SUM(lease_expires_at > ?) running, SUM(failures > 0) failing, MAX(last_done_at) last_done,
                  (SELECT COUNT(*) FROM products p WHERE p.site = j.site) products
           FROM jobs j GROUP BY site""", (iso(now), iso(now))).fetchall()
    return [dict(r) for r in rows]


def events(conn: sqlite3.Connection, *, source: str | None = None, level: str | None = None,
           after_id: int = 0, limit: int = 200) -> list[dict]:
    levels = {"debug": 0, "info": 1, "warning": 2, "error": 3}
    allowed = [k.upper() for k, v in levels.items() if v >= levels.get((level or "info").lower(), 1)]
    rows = conn.execute(
        f"""SELECT * FROM events WHERE id > ? AND (? IS NULL OR source = ?)
            AND level IN ({','.join('?' * len(allowed))}) ORDER BY id DESC LIMIT ?""",
        (after_id, source, source, *allowed, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def _ago(t: datetime, now: datetime) -> str:
    s = (now - t).total_seconds()
    return f"{s:.0f}s ago" if s < 120 else f"{s / 60:.0f} min ago" if s < 7200 else f"{s / 3600:.1f} h ago"


# --- the coordinator's own log lines go to the events table too -------------------

class EventLogHandler(logging.Handler):
    """Queues log records; a background thread writes them (never blocks the caller)."""

    def __init__(self, db_path, source: str = "coordinator"):
        super().__init__(level=logging.INFO)
        self.db_path, self.source = db_path, source
        self.queue: queue.SimpleQueue = queue.SimpleQueue()
        threading.Thread(target=self._writer, name="event-writer", daemon=True).start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put((iso(utcnow()), record.levelname, f"{record.name}: {record.getMessage()}"))
        except Exception:
            pass

    def _writer(self) -> None:
        conn = db.connect(self.db_path)
        while True:
            batch = [self.queue.get()]
            while not self.queue.empty() and len(batch) < 200:
                batch.append(self.queue.get())
            try:
                with conn:
                    scheduler.add_events(conn, self.source, batch)
            except Exception:
                pass  # logging must never take the coordinator down


# --- the page ---------------------------------------------------------------------

STATE_COLOR = {"ok": "#1a7f37", "crawling": "#0969da", "idle": "#6e7781", "blocked": "#bf8700",
               "failing": "#cf222e", "offline": "#cf222e"}


def render_page(data: dict, logs: list[dict], source: str | None, token: str | None) -> str:
    e = lambda v: html.escape(str(v if v is not None else ""))  # noqa: E731
    q = f"&token={e(token)}" if token else ""
    badge = lambda s: f"<span class='b' style='background:{STATE_COLOR.get(s, '#6e7781')}'>{e(s)}</span>"  # noqa: E731
    rows = []
    for d in data["devices"]:
        first = True
        for s in d["sites"] or [{"site": "-", "state": "idle", "detail": "", "pages": 0, "products": 0, "blocks": 0}]:
            rows.append(
                "<tr>" + (f"<td rowspan='{max(1, len(d['sites']))}'><b>{e(d['name'])}</b><br>"
                          f"<small>{e(d['ip'])} · v{e(d['version'])}<br>seen {e(d['last_seen_seconds'])}s ago<br>"
                          f"<a href='?source={e(d['name'])}{q}'>logs</a></small></td>" if first else "")
                + f"<td>{e(s['site'])}</td><td>{badge(s['state'])}</td><td>{e(s['detail'])}</td>"
                f"<td>{e(s['pages'])}</td><td>{e(s['products'])}</td><td>{e(s['blocks'])}</td></tr>")
            first = False
    site_rows = "".join(
        f"<tr><td>{e(s['site'])}</td><td>{e(s['products'])}</td><td>{e(s['slices'])}</td><td>{e(s['due'])}</td>"
        f"<td>{e(s['running'])}</td><td>{e(s['failing'])}</td><td>{e((s['last_done'] or '')[11:19])}</td></tr>"
        for s in data["sites"])
    problems = "".join(f"<li>{e(p['level'])}: {e(p['name'])} — {e(p['detail'])}</li>" for p in data["problems"])
    log_rows = "".join(
        f"<tr class='{e(x['level'].lower())}'><td>{e(x['at'][11:19])}</td><td>{e(x['source'])}</td>"
        f"<td>{e(x['level'])}</td><td>{e(x['message'])}</td></tr>" for x in reversed(logs))
    filters = " · ".join([f"<a href='?{q[1:]}'>all</a>"] + [f"<a href='?source={e(d['name'])}{q}'>{e(d['name'])}</a>"
                                                         for d in data["devices"]] + [f"<a href='?source=coordinator{q}'>coordinator</a>"])
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='15'>
<title>deals · devices</title><style>
body{{font:14px -apple-system,system-ui,sans-serif;margin:24px;color:#1f2328}} table{{border-collapse:collapse;width:100%;margin:8px 0 24px}}
td,th{{border-bottom:1px solid #d0d7de;padding:6px 8px;text-align:left;vertical-align:top}} th{{background:#f6f8fa}}
.b{{color:#fff;border-radius:10px;padding:2px 8px;font-size:12px}} small{{color:#57606a}}
tr.warning td{{background:#fff8c5}} tr.error td{{background:#ffebe9}} h2{{margin-top:28px}}
</style></head><body><h1>deals · devices</h1><small>updated {e(data['generated_at'])} UTC · refreshes every 15 s ·
JSON: <a href='/api/devices?{q[1:]}'>/api/devices</a>, <a href='/api/events?{q[1:]}'>/api/events</a></small>
{f"<h2>Needs attention</h2><ul>{problems}</ul>" if problems else "<p>✅ all checks pass</p>"}
<h2>Devices</h2><table><tr><th>Device</th><th>Shop</th><th>State</th><th>Detail</th><th>Pages</th><th>Products</th><th>Blocks</th></tr>{''.join(rows)}</table>
<h2>Shops</h2><table><tr><th>Shop</th><th>Products</th><th>Slices</th><th>Due now</th><th>Running</th><th>Failing</th><th>Last finished (UTC)</th></tr>{site_rows}</table>
<h2>Logs {('· ' + e(source)) if source else ''}</h2><small>{filters}</small>
<table><tr><th>UTC</th><th>From</th><th>Level</th><th>Message</th></tr>{log_rows}</table></body></html>"""
