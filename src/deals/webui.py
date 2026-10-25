"""The web UI at /ui: one page (plain HTML + JS) over read-only JSON endpoints.

Everything here only reads the database; nothing a viewer does can change data
or affect crawling. Login is a secret token kept in an HttpOnly cookie.
"""

import html
import sqlite3
from datetime import datetime, timedelta

from deals import health, monitor
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
            "subscribers": one("SELECT COUNT(*) FROM subscribers WHERE status = 'active'"),
            "warnings_24h": one("SELECT COUNT(*) FROM events WHERE at >= ? AND level IN ('WARNING', 'ERROR')", since),
        },
        "devices": monitor.devices(conn, now),
        "sites": monitor.sites(conn, now),
    }


def alerts(conn: sqlite3.Connection, now: datetime, days: float, kind: str | None, limit: int) -> list[dict]:
    rows = conn.execute(
        """SELECT a.id, a.created_at, a.kind, a.price, a.reference_price, a.drop_pct, a.score, a.message, a.error,
                  p.site, p.title, p.url, p.grp,
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


def products(conn: sqlite3.Connection, q: str, site: str | None, limit: int) -> list[dict]:
    words = [w for w in q.split() if w][:6]
    where = " AND ".join("title LIKE ?" for _ in words) or "1"
    rows = conn.execute(
        f"""SELECT id, site, sku, title, url, grp, price, list_price, in_stock, stock_left, seller, rating, reviews,
                   last_seen_at, last_change_at
            FROM products WHERE {where} AND (? IS NULL OR site = ?) ORDER BY last_change_at DESC LIMIT ?""",
        (*[f"%{w}%" for w in words], site, site, limit)).fetchall()
    return [dict(r) for r in rows]


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
                  "changes_90d": s.changes_90d},
        "history": [dict(h) for h in history],
    }


def login_page(error: str = "") -> str:
    msg = f"<p class='err'>{html.escape(error)}</p>" if error else ""
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>deals · login</title><style>{CSS}
form{{max-width:340px;margin:12vh auto;padding:24px;border:1px solid var(--line);border-radius:12px}}
input{{width:100%;box-sizing:border-box;padding:10px;margin:12px 0;font-size:15px}} button{{width:100%;padding:10px}}
.err{{color:var(--bad)}}</style></head><body>
<form method='post' action='/ui/login'><h2>deals</h2><p class='muted'>Enter your access token.</p>{msg}
<input type='password' name='token' autofocus autocomplete='current-password' placeholder='token'>
<button type='submit'>Log in</button></form></body></html>"""


CSS = """:root{--bg:#fff;--fg:#1f2328;--muted:#57606a;--line:#d0d7de;--soft:#f6f8fa;--ok:#1a7f37;--warn:#9a6700;--bad:#cf222e;--info:#0969da}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#8b949e;--line:#30363d;--soft:#161b22;--ok:#3fb950;--warn:#d29922;--bad:#f85149;--info:#58a6ff}}
body{font:14px -apple-system,system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0}
.muted{color:var(--muted)} a{color:var(--info)} button{cursor:pointer;border:1px solid var(--line);background:var(--soft);color:var(--fg);border-radius:6px;padding:6px 12px}"""

APP = """<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>deals</title><style>__CSS__
header{display:flex;gap:16px;align-items:center;padding:12px 20px;border-bottom:1px solid var(--line);flex-wrap:wrap}
header b{font-size:16px} nav{display:flex;gap:4px;flex-wrap:wrap} nav button.on{background:var(--info);color:#fff;border-color:var(--info)}
main{padding:16px 20px;max-width:1300px} .grow{flex:1}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-bottom:18px}
.card{border:1px solid var(--line);border-radius:10px;padding:10px 12px} .card .n{font-size:22px;font-weight:600}
table{border-collapse:collapse;width:100%;margin:6px 0 20px} td,th{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{background:var(--soft);position:sticky;top:0} h3{margin:18px 0 6px}
.b{border-radius:10px;padding:1px 8px;font-size:12px;color:#fff;white-space:nowrap}
.ok{background:var(--ok)} .crawling{background:var(--info)} .idle{background:#6e7781} .warn,.blocked{background:var(--warn)} .fail,.failing,.offline{background:var(--bad)}
tr.WARNING td{background:rgba(212,167,44,.15)} tr.ERROR td{background:rgba(248,81,73,.15)}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px} input,select{padding:6px;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px}
.msg{white-space:pre-wrap;font-size:13px} .mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
.clickable{cursor:pointer} .clickable:hover td{background:var(--soft)}
</style></head><body>
<header><b>deals</b><nav id='tabs'></nav><span class='grow'></span><span id='stamp' class='muted'></span><a href='/ui/logout'>log out</a></header>
<main id='view'>loading…</main>
<script>
const tabs = ['Overview', 'Devices', 'Logs', 'Alerts', 'Jobs', 'Products'];
let tab = location.hash.slice(1) || 'Overview', timer = null, lastLog = 0;
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const egp = p => p == null ? '-' : (p / 100).toLocaleString('en', {maximumFractionDigits: 0}) + ' EGP';
const ago = t => { if (!t) return 'never'; const s = (Date.now() - Date.parse(t)) / 1000;
  return s < 120 ? Math.round(s) + 's ago' : s < 7200 ? Math.round(s / 60) + ' min ago' : (s / 3600).toFixed(1) + ' h ago'; };
const badge = s => `<span class='b ${esc(s)}'>${esc(s)}</span>`;
async function api(path) {
  const r = await fetch(path, {credentials: 'same-origin'});
  if (r.status === 401) { location.href = '/ui/login'; throw new Error('logged out'); }
  if (!r.ok) throw new Error(path + ': HTTP ' + r.status);
  return r.json();
}
function nav() {
  $('#tabs').innerHTML = tabs.map(t => `<button class='${t === tab ? 'on' : ''}' onclick="go('${t}')">${t}</button>`).join('');
}
function go(t) { tab = t; location.hash = t; lastLog = 0; clearInterval(timer); nav(); render(); }
function every(ms, fn) { clearInterval(timer); timer = setInterval(fn, ms); }

const views = {
  async Overview() {
    const d = await api('/api/overview'), t = d.totals;
    const bad = d.checks.filter(c => c.level !== 'ok');
    $('#stamp').textContent = 'updated ' + d.generated_at.slice(11) + ' UTC · ' + (d.revision || '');
    return `<div class='cards'>
      <div class='card'><div class='muted'>status</div><div class='n'>${bad.some(c => c.level === 'fail') ? '🔴' : bad.length ? '🟠' : '🟢'}</div></div>
      <div class='card'><div class='muted'>products</div><div class='n'>${t.products.toLocaleString()}</div><div class='muted'>${t.in_stock.toLocaleString()} in stock</div></div>
      <div class='card'><div class='muted'>price changes 24h</div><div class='n'>${t.changes_24h.toLocaleString()}</div></div>
      <div class='card'><div class='muted'>deal alerts 24h</div><div class='n'>${t.alerts_24h}</div></div>
      <div class='card'><div class='muted'>subscribers</div><div class='n'>${t.subscribers}</div></div>
      <div class='card'><div class='muted'>warnings 24h</div><div class='n'>${t.warnings_24h}</div></div></div>
      <h3>Health checks</h3><table><tr><th></th><th>Check</th><th>Detail</th></tr>
      ${d.checks.map(c => `<tr><td>${badge(c.level)}</td><td>${esc(c.name)}</td><td>${esc(c.detail)}</td></tr>`).join('')}</table>
      ${devicesTable(d.devices)}${sitesTable(d.sites)}`;
  },
  async Devices() {
    const d = await api('/api/overview');
    $('#stamp').textContent = 'updated ' + d.generated_at.slice(11) + ' UTC';
    return devicesTable(d.devices) + sitesTable(d.sites);
  },
  async Logs() {
    const src = $('#src')?.value ?? '', lvl = $('#lvl')?.value ?? 'info';
    const d = await api(`/api/events?limit=300&level=${lvl}` + (src ? '&source=' + encodeURIComponent(src) : ''));
    const devs = (await api('/api/overview')).devices.map(x => x.name).concat(['coordinator']);
    lastLog = d.events.reduce((m, e) => Math.max(m, e.id), 0);
    setTimeout(() => every(3000, pollLogs), 0);
    return `<div class='row'>From <select id='src' onchange='render()'><option value=''>everyone</option>
      ${devs.map(n => `<option ${n === src ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select>
      Level <select id='lvl' onchange='render()'>${['info', 'warning', 'error'].map(l => `<option ${l === lvl ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <span class='muted'>live, newest first · kept 7 days</span></div>
      <table><thead><tr><th>UTC</th><th>From</th><th>Level</th><th>Message</th></tr></thead><tbody id='logrows'>
      ${d.events.slice().reverse().map(logRow).join('')}</tbody></table>`;
  },
  async Alerts() {
    const days = $('#days')?.value ?? '1';
    const d = await api('/api/alerts?days=' + days);
    return `<div class='row'>Last <select id='days' onchange='render()'>${['1', '3', '7', '30'].map(x => `<option ${x === days ? 'selected' : ''}>${x}</option>`).join('')}</select> days · ${d.alerts.length} alerts</div>
      <table><tr><th>When (UTC)</th><th>Kind</th><th>Drop</th><th>Price</th><th>Reference</th><th>Product</th><th>Sent to</th></tr>
      ${d.alerts.map(a => `<tr><td>${esc(a.created_at.slice(5, 16).replace('T', ' '))}</td><td>${esc(a.kind)}</td>
        <td>${a.drop_pct == null ? '' : Math.round(a.drop_pct * 100) + '%'}</td><td>${egp(a.price)}</td><td>${egp(a.reference_price)}</td>
        <td>${a.url ? `<a href='${esc(a.url)}' target='_blank'>${esc(a.title)}</a> <span class='muted'>${esc(a.site)} · ${esc(a.grp)}</span>` : `<span class='msg' dir='auto'>${esc(a.message.replace(/<[^>]+>/g, ''))}</span>`}</td>
        <td>${a.delivered}</td></tr>`).join('')}</table>`;
  },
  async Jobs() {
    const d = await api('/api/jobs?limit=400');
    const failing = d.jobs.filter(j => j.failures > 0), running = d.jobs.filter(j => j.running);
    every(15000, render);
    return `<p class='muted'>${failing.length} failing · ${running.length} running · ${d.jobs.length} jobs (failing first, then running, then soonest due)</p>
      <table><tr><th>Site</th><th>Kind</th><th>Group</th><th>Slice</th><th>State</th><th>Every</th><th>Next due</th><th>Last done</th><th>Last run</th><th>Error</th></tr>
      ${d.jobs.map(j => `<tr><td>${esc(j.site)}</td><td>${esc(j.kind)}</td><td>${esc(j.grp)}</td><td class='mono'>${esc(j.target)}</td>
        <td>${j.failures ? badge('failing') + ' ×' + j.failures : j.running ? badge('crawling') + ' ' + esc(j.lease_worker) : badge('idle')}</td>
        <td>${j.every_min} min</td><td>${esc((j.next_due_at || '').slice(11, 16))}</td><td>${ago(j.last_done_at)}</td>
        <td>${j.last_count ?? ''} products, ${j.last_changes ?? 0} changes</td><td>${esc(j.last_error || '')}</td></tr>`).join('')}</table>`;
  },
  async Products() {
    const q = $('#q')?.value ?? '', site = $('#site')?.value ?? '';
    const d = q ? await api('/api/products?limit=100&q=' + encodeURIComponent(q) + (site ? '&site=' + site : '')) : {products: []};
    return `<div class='row'><input id='q' placeholder='search titles, e.g. samsung 55' value='${esc(q)}' onkeydown="if(event.key==='Enter')render()" size='34'>
      <select id='site'><option value=''>both shops</option><option ${site === 'amazon_eg' ? 'selected' : ''}>amazon_eg</option><option ${site === 'noon_eg' ? 'selected' : ''}>noon_eg</option></select>
      <button onclick='render()'>Search</button></div><div id='detail'></div>
      <table><tr><th>Product</th><th>Shop</th><th>Group</th><th>Price</th><th>Crossed-out</th><th>Stock</th><th>Last change</th></tr>
      ${d.products.map(p => `<tr class='clickable' onclick='detail(${p.id})'><td>${esc(p.title)}</td><td>${esc(p.site)}${p.seller ? ' · ' + esc(p.seller) : ''}</td>
        <td>${esc(p.grp)}</td><td>${egp(p.price)}</td><td>${egp(p.list_price)}</td><td>${p.in_stock ? (p.stock_left ? 'only ' + p.stock_left : 'yes') : 'no'}</td>
        <td>${ago(p.last_change_at)}</td></tr>`).join('')}</table>`;
  },
};
function devicesTable(devs) {
  return `<h3>Devices</h3><table><tr><th>Device</th><th>Shop</th><th>State</th><th>Detail</th><th>Pages</th><th>Products</th><th>Blocks</th></tr>
    ${devs.map(d => d.sites.map((s, i) => `<tr>${i ? '' : `<td rowspan='${d.sites.length}'><b>${esc(d.name)}</b><br><span class='muted'>${esc(d.ip)} · v${esc(d.version)}<br>seen ${d.last_seen_seconds}s ago</span></td>`}
      <td>${esc(s.site)}</td><td>${badge(s.state)}</td><td>${esc(s.detail)}${s.last_error && s.state !== 'failing' ? `<br><span class='muted'>last problem ${esc((s.last_error_at || '').slice(11, 16))}: ${esc(s.last_error)}</span>` : ''}</td>
      <td>${s.pages}</td><td>${s.products}</td><td>${s.blocks}</td></tr>`).join('')).join('')}</table>`;
}
function sitesTable(sites) {
  return `<h3>Shops</h3><table><tr><th>Shop</th><th>Products</th><th>Slices</th><th>Due now</th><th>Running</th><th>Failing</th><th>Last finished</th></tr>
    ${sites.map(s => `<tr><td>${esc(s.site)}</td><td>${s.products.toLocaleString()}</td><td>${s.slices}</td><td>${s.due}</td><td>${s.running}</td><td>${s.failing}</td><td>${ago(s.last_done)}</td></tr>`).join('')}</table>`;
}
const logRow = e => `<tr class='${esc(e.level)}'><td class='mono'>${esc(e.at.slice(5, 19).replace('T', ' '))}</td><td>${esc(e.source)}</td><td>${esc(e.level)}</td><td class='mono'>${esc(e.message)}</td></tr>`;
async function pollLogs() {
  if (tab !== 'Logs') return;
  const src = $('#src').value, lvl = $('#lvl').value;
  const d = await api(`/api/events?after_id=${lastLog}&level=${lvl}` + (src ? '&source=' + encodeURIComponent(src) : ''));
  if (!d.events.length) return;
  lastLog = d.events.reduce((m, e) => Math.max(m, e.id), lastLog);
  $('#logrows').insertAdjacentHTML('afterbegin', d.events.slice().reverse().map(logRow).join(''));
}
async function detail(id) {
  const d = await api('/api/products/' + id), p = d.product, s = d.stats;
  $('#detail').innerHTML = `<div class='card' style='margin-bottom:14px'><b>${esc(p.title)}</b> · <a href='${esc(p.url)}' target='_blank'>open in shop</a>
    <p>Now <b>${egp(p.price)}</b> · typical 90d ${egp(s.typical_90d)} · 30d ${egp(s.typical_30d)} · range ${egp(s.low)} – ${egp(s.high)} ·
    tracked ${s.tracked_days} days · ${s.changes_90d} changes</p>
    <table><tr><th>When (UTC)</th><th>Price</th><th>Crossed-out</th><th>In stock</th></tr>
    ${d.history.slice().reverse().map(h => `<tr><td>${esc(h.observed_at.slice(0, 16).replace('T', ' '))}</td><td>${egp(h.price)}</td><td>${egp(h.list_price)}</td><td>${h.in_stock ? 'yes' : 'no'}</td></tr>`).join('')}</table></div>`;
}
async function render() {
  try { $('#view').innerHTML = await views[tab](); if (['Overview', 'Devices'].includes(tab)) every(15000, render); }
  catch (e) { $('#view').innerHTML = `<p style='color:var(--bad)'>${esc(e.message)}</p>`; }
}
nav(); render();
</script></body></html>""".replace("__CSS__", CSS)
