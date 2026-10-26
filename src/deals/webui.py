"""The web UI at /ui: one page (plain HTML + JS) over read-only JSON endpoints.

Everything here only reads the database; nothing a viewer does can change data
or affect crawling. Login is a secret token kept in an HttpOnly cookie.
"""

import html
import re
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


PRODUCT_SORTS = {"recent": "last_change_at DESC", "cheap": "price IS NULL, price", "dear": "price DESC",
                 "rating": "rating IS NULL, rating DESC, reviews DESC", "reviews": "reviews IS NULL, reviews DESC"}


def _product_filter(q: str, site: str | None, grp: str | None, in_stock_only: bool) -> tuple[str, list]:
    words = [w for w in q.split() if w][:6]
    where = " AND ".join("title LIKE ?" for _ in words) or "1"
    if in_stock_only:
        where += " AND in_stock = 1"
    where += " AND (? IS NULL OR site = ?) AND (? IS NULL OR grp = ?)"
    return where, [*[f"%{w}%" for w in words], site, site, grp, grp]


def products(conn: sqlite3.Connection, q: str, site: str | None, limit: int, grp: str | None = None,
             sort: str = "recent", in_stock_only: bool = False, offset: int = 0) -> dict:
    """One page of the product list plus the total match count (for 'showing N of M' and
    endless scrolling: the client asks for the next `offset` as it nears the bottom)."""
    where, params = _product_filter(q, site, grp, in_stock_only)
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
        "other_sellers": [dict(o) for o in conn.execute(
            """SELECT id, seller, price, in_stock FROM products WHERE site = ? AND sku >= ? AND sku < ? AND id != ?
               ORDER BY price IS NULL, price""",
            (row["site"], row["sku"].partition("@")[0] + "@", row["sku"].partition("@")[0] + "A", product_id))]
        if "@" in row["sku"] else [],
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
th{background:var(--soft);position:sticky;top:0;cursor:pointer;user-select:none} h3{margin:18px 0 6px}
.b{border-radius:10px;padding:1px 8px;font-size:12px;color:#fff;white-space:nowrap}
.ok{background:var(--ok)} .crawling{background:var(--info)} .idle{background:#6e7781} .warn,.blocked{background:var(--warn)} .fail,.failing,.offline{background:var(--bad)}
tr.WARNING td{background:rgba(212,167,44,.15)} tr.ERROR td{background:rgba(248,81,73,.15)}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px} input,select{padding:6px;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px}
.msg{white-space:pre-wrap;font-size:13px} .mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
.clickable{cursor:pointer} .clickable:hover td{background:var(--soft)}
th.sortable{cursor:pointer;user-select:none} th.sortable:hover{text-decoration:underline} th .dir{opacity:.6;font-size:11px}
.chart{position:relative;margin:8px 0 16px} .chart svg{width:100%;height:260px;display:block}
.chart .tip{position:absolute;pointer-events:none;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:4px 8px;font-size:12px;white-space:nowrap;box-shadow:0 2px 8px rgba(0,0,0,.15);display:none}
.legend{display:flex;gap:14px;font-size:12px;color:var(--muted);margin:2px 0 8px} .legend i{display:inline-block;width:14px;height:0;border-top:2px solid;vertical-align:middle;margin-right:4px}
.kv{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px;margin:10px 0}
.kv div{border:1px solid var(--line);border-radius:8px;padding:8px 10px} .kv .v{font-size:18px;font-weight:600}
.up{color:var(--bad)} .down{color:var(--ok)} .crumb{margin-bottom:10px}
#bar{position:fixed;top:0;left:0;height:3px;width:0;background:var(--info);transition:width .3s,opacity .4s;opacity:0;z-index:9}
body.loading #bar{opacity:1;width:70%} body.loading main{opacity:.55;transition:opacity .2s} body.loading main{pointer-events:none}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid var(--line);border-top-color:var(--info);border-radius:50%;animation:spin .8s linear infinite;vertical-align:middle;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
</style></head><body>
<header><b>deals</b><nav id='tabs'></nav><span class='grow'></span><span id='stamp' class='muted'></span><a href='/ui/logout'>log out</a></header>
<div id='bar'></div><main id='view'><span class='spinner'></span>loading…</main>
<script>
const tabs = ['Overview', 'Devices', 'Logs', 'Alerts', 'Jobs', 'Products', 'Queries'];
let tab = (location.hash.slice(1).split(':')[0]) || 'Overview', timer = null, lastLog = 0;
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const egp = p => p == null ? '-' : (p / 100).toLocaleString('en', {maximumFractionDigits: 0}) + ' EGP';
const ago = t => { if (!t) return 'never'; const s = (Date.now() - Date.parse(t)) / 1000;
  return s < 120 ? Math.round(s) + 's ago' : s < 7200 ? Math.round(s / 60) + ' min ago' : (s / 3600).toFixed(1) + ' h ago'; };
const badge = s => `<span class='b ${esc(s)}'>${esc(s)}</span>`;
let inflight = 0;
function busy(on) {
  inflight += on ? 1 : -1;
  document.body.classList.toggle('loading', inflight > 0);
  if (inflight === 0) { const b = $('#bar'); b.style.width = '100%'; setTimeout(() => { if (!inflight) b.style.width = '0'; }, 400); }
}
async function api(path) {
  busy(true);
  try {
    const r = await fetch(path, {credentials: 'same-origin'});
    if (r.status === 401) { location.href = '/ui/login'; throw new Error('logged out'); }
    if (!r.ok) throw new Error(path + ': HTTP ' + r.status);
    return await r.json();
  } finally { busy(false); }
}
function nav() {
  $('#tabs').innerHTML = tabs.map(t => `<button class='${t === tab ? 'on' : ''}' onclick="go('${t}')">${t}</button>`).join('');
}
function go(t) { tab = t; location.hash = t; lastLog = 0; clearInterval(timer); nav(); render(); }
// (the Queries tab keeps its chosen query in the hash as Queries:<name>)
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
      <table><tr><th>When (UTC)</th><th>Kind</th><th>State</th><th>Drop</th><th>Score</th><th>Price</th><th>Reference</th><th>Product</th><th>Sent to</th></tr>
      ${d.alerts.map(a => `<tr><td>${esc(a.created_at.slice(5, 16).replace('T', ' '))}</td><td>${esc(a.kind)}</td><td>${badge(a.state === 'sent' ? 'ok' : a.state === 'held' ? 'warn' : a.state === 'cancelled' ? 'fail' : 'idle')} ${esc(a.state)}</td>
        <td>${a.drop_pct == null ? '' : Math.round(a.drop_pct * 100) + '%'}</td><td>${a.score ?? ''}</td><td>${egp(a.price)}</td><td>${egp(a.reference_price)}</td>
        <td>${a.product_id ? `<a href='#Products:${a.product_id}' onclick='openProduct(${a.product_id});return false'>${esc(a.title)}</a> <a href='${esc(a.url)}' target='_blank' class='muted'>shop ↗</a><br><span class='muted'>${esc(a.site)} · ${esc(a.grp)}</span>` : `<span class='msg' dir='auto'>${esc(a.message.replace(/<[^>]+>/g, ''))}</span>`}</td>
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
    const id = location.hash.split(':')[1];
    if (id) return productPage(id);
    const f = k => $('#' + k)?.value ?? '';
    const q = f('q'), site = f('site'), grp = f('grp'), sort = f('sort') || 'recent', stock = $('#stock')?.checked ? 1 : 0;
    const query = `q=${encodeURIComponent(q)}&site=${site}&grp=${encodeURIComponent(grp)}&sort=${sort}&in_stock=${stock}`;
    const d = await api(`/api/products?limit=${PAGE}&offset=0&` + query);
    productFeed = {query, next: d.products.length, total: d.total, busy: false};
    setTimeout(watchScroll, 0);
    return `<div class='row'><input id='q' placeholder='search titles, e.g. samsung 55' value='${esc(q)}' onkeydown="if(event.key==='Enter')render()" size='30'>
      <select id='site' onchange='render()'><option value=''>both shops</option><option ${site === 'amazon_eg' ? 'selected' : ''}>amazon_eg</option><option ${site === 'noon_eg' ? 'selected' : ''}>noon_eg</option></select>
      <select id='grp' onchange='render()'><option value=''>all groups</option>${d.groups.map(g => `<option ${g === grp ? 'selected' : ''}>${esc(g)}</option>`).join('')}</select>
      <select id='sort' onchange='render()'>${[['recent', 'latest change'], ['cheap', 'cheapest'], ['dear', 'dearest'], ['rating', 'best rated'], ['reviews', 'most reviews']].map(([v, l]) => `<option value='${v}' ${v === sort ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <label><input type='checkbox' id='stock' ${stock ? 'checked' : ''} onchange='render()'> in stock only</label>
      <button onclick='render()'>Search</button> <span class='muted' id='pcount'>${d.products.length.toLocaleString()} of ${d.total.toLocaleString()} · click a row for the price history</span></div>
      <table><thead><tr><th>Product</th><th>Shop</th><th>Group</th><th>Price</th><th>Crossed-out</th><th>Stock</th><th>Rating</th><th>Last change</th></tr></thead>
      <tbody id='prows'>${d.products.map(productRow).join('')}</tbody></table>
      <div id='more' class='muted' style='padding:10px 0 30px'>${d.products.length < d.total ? "<span class='spinner'></span>loading more as you scroll…" : d.total ? 'end of list' : 'nothing matches'}</div>`;
  },
  async Queries() {
    const list = (await api('/api/queries')).queries;
    const name = $('#qname')?.value ?? (location.hash.split(':')[1] || list[0].name), hours = $('#hours')?.value ?? '24';
    const grp = $('#qgrp')?.value ?? '', site = $('#qsite')?.value ?? '';
    location.hash = 'Queries:' + name;
    const [d, groups] = await Promise.all([api(`/api/queries/${name}?hours=${hours}&grp=${encodeURIComponent(grp)}&site=${site}`), api('/api/products?limit=0').then(r => r.groups)]);
    const money = c => /^(was|now|price|cheapest|dearest|lowest|highest|crossed_out|previous_low|highest_seen|alert_price|price_now|a|b)$/.test(c);
    const hide = new Set(['url', 'seller', 'product_id']);
    const cols = d.columns.filter(c => !hide.has(c)), iu = d.columns.indexOf('url'), ip = d.columns.indexOf('product_id'), is = d.columns.indexOf('seller');
    const cell = (v, c, row) => c === 'title' && (iu >= 0 || ip >= 0) ? (ip >= 0 && row[ip] ? `<a href='#Products:${row[ip]}' onclick='openProduct(${row[ip]});return false'>${esc(v)}</a>` : `<a href='${esc(row[iu])}' target='_blank'>${esc(v)}</a>`)
        + (iu >= 0 && row[iu] && ip >= 0 ? ` <a href='${esc(row[iu])}' target='_blank' class='muted'>↗</a>` : '') + (is >= 0 && row[is] ? ` <span class='muted'>· ${esc(row[is])}</span>` : '')
      : money(c) ? egp(v) : typeof v === 'string' && /^\\d{4}-\\d\\d-\\d\\dT/.test(v) ? esc(v.slice(5, 16).replace('T', ' ')) : esc(v);
    return `<div class='row'><select id='qname' onchange='render()'>${list.map(q => `<option value='${q.name}' ${q.name === name ? 'selected' : ''}>${esc(q.title)}</option>`).join('')}</select>
      ${d.uses_window ? `last <select id='hours' onchange='render()'>${[['6', '6 h'], ['24', '24 h'], ['72', '3 days'], ['168', '7 days'], ['720', '30 days']].map(([v, l]) => `<option value='${v}' ${v === hours ? 'selected' : ''}>${l}</option>`).join('')}</select>` : ''}
      <select id='qgrp' onchange='render()' ${d.filters.grp ? '' : 'disabled'}><option value=''>all groups</option>${groups.map(g => `<option ${g === grp ? 'selected' : ''}>${esc(g)}</option>`).join('')}</select>
      <select id='qsite' onchange='render()' ${d.filters.site ? '' : 'disabled'}><option value=''>both shops</option><option ${site === 'amazon_eg' ? 'selected' : ''}>amazon_eg</option><option ${site === 'noon_eg' ? 'selected' : ''}>noon_eg</option></select>
      <span class='muted'>${d.rows.length} rows${d.filters.grp || d.filters.site ? '' : ' · this report has no group/shop columns'}</span></div>
      <p class='muted'>${esc(d.about)}</p>
      <table><tr>${cols.map(c => `<th>${esc(c.replace(/_/g, ' '))}</th>`).join('')}</tr>
      ${d.rows.map(r => `<tr>${d.columns.map((c, i) => hide.has(c) ? '' : `<td>${cell(r[i], c, r)}</td>`).join('')}</tr>`).join('')}</table>`;
  },
};
const PAGE = 100;
let productFeed = null;
const productRow = p => `<tr class='clickable' onclick='openProduct(${p.id})'><td>${esc(p.title)}</td><td>${esc(p.site)}${p.seller ? ' · ' + esc(p.seller) : ''}</td>
  <td>${esc(p.grp)}</td><td>${egp(p.price)}</td><td>${egp(p.list_price)}</td><td>${p.in_stock ? (p.stock_left ? 'only ' + p.stock_left : 'yes') : 'no'}</td>
  <td>${p.rating ? p.rating.toFixed(1) + ' (' + (p.reviews || 0).toLocaleString() + ')' : ''}</td><td>${ago(p.last_change_at)}</td></tr>`;
// Endless scrolling for the product list: when the marker under the table comes into view,
// fetch the next page for the same filters and append it. Stops at the total.
function watchScroll() {
  const more = $('#more'); if (!more || !productFeed) return;
  const io = new IntersectionObserver(async entries => {
    if (!entries.some(e => e.isIntersecting) || !productFeed || productFeed.busy || productFeed.next >= productFeed.total) return;
    const feed = productFeed; feed.busy = true;
    try {
      const d = await api(`/api/products?limit=${PAGE}&offset=${feed.next}&` + feed.query);
      if (feed !== productFeed || tab !== 'Products') return;  // filters changed meanwhile
      $('#prows').insertAdjacentHTML('beforeend', d.products.map(productRow).join(''));
      feed.next += d.products.length; feed.total = d.total;
      $('#pcount').textContent = `${feed.next.toLocaleString()} of ${feed.total.toLocaleString()} · click a row for the price history`;
      if (feed.next >= feed.total || !d.products.length) { more.textContent = 'end of list'; io.disconnect(); }
    } finally { feed.busy = false; }
  }, {rootMargin: '400px'});
  io.observe(more);
}
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
function openProduct(id) { tab = 'Products'; location.hash = 'Products:' + id; clearInterval(timer); nav(); $('#view').innerHTML = "<span class='spinner'></span>loading product…"; render(); }
async function productPage(id) {
  const d = await api('/api/products/' + id), p = d.product, s = d.stats;
  const drop = s.drop_vs_typical, dropTxt = drop == null ? '' : `<span class='${drop > 0 ? 'down' : 'up'}'>${drop > 0 ? '−' : '+'}${Math.abs(Math.round(drop * 100))}% vs typical</span>`;
  const badges = (p.badges || '').split(',').filter(Boolean).map(b => `<span class='b idle'>${esc(b)}</span>`).join(' ');
  return `<div class='crumb'><a href='#Products' onclick="location.hash='Products';render();return false">← products</a></div>
    <h3 style='margin-top:0'>${esc(p.title)}</h3>
    <p class='muted'>${esc(p.site)} · ${esc(p.grp || '')}${p.seller ? ' · seller ' + esc(p.seller) : ''}${p.brand ? ' · ' + esc(p.brand) : ''} · <a href='${esc(p.url)}' target='_blank'>open in shop ↗</a>
      ${p.rating ? ` · ★ ${p.rating.toFixed(1)} (${(p.reviews || 0).toLocaleString()})` : ''} ${badges}</p>
    <div class='kv'>
      <div><div class='muted'>price now</div><div class='v'>${egp(p.price)}</div><div class='muted'>${p.in_stock ? (p.stock_left ? 'only ' + p.stock_left + ' left' : 'in stock') : 'out of stock'}${p.list_price ? ' · crossed-out ' + egp(p.list_price) : ''}</div></div>
      <div><div class='muted'>typical (90 d)</div><div class='v'>${egp(s.typical_90d)}</div><div class='muted'>${dropTxt}</div></div>
      <div><div class='muted'>typical (30 d)</div><div class='v'>${egp(s.typical_30d)}</div></div>
      <div><div class='muted'>range seen</div><div class='v'>${egp(s.low)}</div><div class='muted'>to ${egp(s.high)}</div></div>
      <div><div class='muted'>this cheap or cheaper</div><div class='v'>${s.share_at_or_below == null ? '-' : Math.round(s.share_at_or_below * 100) + '%'}</div><div class='muted'>of the time (rarity)</div></div>
      <div><div class='muted'>tracked</div><div class='v'>${s.tracked_days} d</div><div class='muted'>${s.history_days} d in-stock history · ${s.changes_90d} changes</div></div>
    </div>
    <h3>Price history</h3>${chart(d.history, s.typical_90d)}
    ${d.other_sellers.length ? `<h3>Other sellers of this item</h3><table><tr><th>Seller</th><th>Price</th><th>In stock</th></tr>
      ${d.other_sellers.map(o => `<tr class='clickable' onclick='openProduct(${o.id})'><td>${esc(o.seller || '?')}</td><td>${egp(o.price)}</td><td>${o.in_stock ? 'yes' : 'no'}</td></tr>`).join('')}</table>` : ''}
    ${d.alerts.length ? `<h3>Alerts for this product</h3><table><tr><th>When (UTC)</th><th>Kind</th><th>Drop</th><th>Price</th><th>Reference</th><th>Score</th><th>State</th></tr>
      ${d.alerts.map(a => `<tr><td>${esc(a.created_at.slice(0, 16).replace('T', ' '))}</td><td>${esc(a.kind)}</td><td>${Math.round(a.drop_pct * 100)}%</td><td>${egp(a.price)}</td><td>${egp(a.reference_price)}</td><td>${a.score}</td><td>${esc(a.confirm_until ? 'held' : a.error || 'sent')}</td></tr>`).join('')}</table>` : ''}
    <h3>Recorded changes</h3><table><tr><th>When (UTC)</th><th>Price</th><th>Crossed-out</th><th>In stock</th><th>Change</th></tr>
    ${d.history.slice().reverse().map((h, i, arr) => { const prev = arr[i + 1]; const ch = prev && prev.price && h.price ? (h.price - prev.price) / prev.price : null;
      return `<tr><td>${esc(h.observed_at.slice(0, 16).replace('T', ' '))}</td><td>${egp(h.price)}</td><td>${egp(h.list_price)}</td><td>${h.in_stock ? 'yes' : 'no'}</td>
      <td>${ch == null ? '' : `<span class='${ch < 0 ? 'down' : 'up'}'>${ch < 0 ? '' : '+'}${Math.round(ch * 100)}%</span>`}</td></tr>`; }).join('')}</table>`;
}
// Step chart of the recorded prices (a price holds until the next row), with the 90-day typical
// price as a dashed reference and out-of-stock stretches left blank. Hover for the value.
function chart(hist, typical) {
  const pts = hist.map(h => ({t: Date.parse(h.observed_at), p: h.in_stock ? h.price : null, lp: h.list_price}));
  if (!pts.length) return "<p class='muted'>no history yet</p>";
  const now = Date.now(), t0 = pts[0].t, t1 = Math.max(now, pts[pts.length - 1].t + 1), W = 1000, H = 260, L = 70, R = 16, T = 14, B = 30;
  const vals = pts.map(x => x.p).filter(v => v != null).concat(typical ? [typical] : []);
  if (!vals.length) return "<p class='muted'>never in stock while tracked</p>";
  let lo = Math.min(...vals), hi = Math.max(...vals); if (hi === lo) { lo *= 0.95; hi *= 1.05; } const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const X = t => L + (t - t0) / (t1 - t0) * (W - L - R), Y = v => T + (hi - v) / (hi - lo) * (H - T - B);
  let path = '', prev = null;
  pts.forEach((x, i) => { const tEnd = i + 1 < pts.length ? pts[i + 1].t : t1;
    if (x.p == null) { prev = null; return; }
    path += (prev == null ? `M${X(x.t).toFixed(1)},${Y(x.p).toFixed(1)}` : `L${X(x.t).toFixed(1)},${Y(x.p).toFixed(1)}`) + `L${X(tEnd).toFixed(1)},${Y(x.p).toFixed(1)}`; prev = x.p; });
  const ticks = 4, yTicks = Array.from({length: ticks + 1}, (_, i) => lo + (hi - lo) * i / ticks);
  const days = (t1 - t0) / 864e5, step = days > 60 ? 14 : days > 14 ? 7 : days > 3 ? 1 : 0.25, xt = [];
  for (let t = t0; t <= t1; t += step * 864e5) xt.push(t);
  const fmt = t => { const d = new Date(t); return step < 1 ? d.toISOString().slice(5, 16).replace('T', ' ') : d.toISOString().slice(5, 10); };
  const dots = pts.filter(x => x.p != null).map(x => `<circle cx='${X(x.t).toFixed(1)}' cy='${Y(x.p).toFixed(1)}' r='3.5' fill='var(--bg)' stroke='var(--info)' stroke-width='2'/>`).join('');
  const data = JSON.stringify(pts.map(x => [x.t, x.p]));
  return `<div class='legend'><span><i style='border-color:var(--info)'></i>price</span>${typical ? `<span><i style='border-color:var(--muted);border-top-style:dashed'></i>typical 90 d</span>` : ''}<span class='muted'>gaps = out of stock · dots = recorded changes</span></div>
    <div class='chart' onmousemove='chartHover(event,${data},${t0},${t1},${L},${R},${W})' onmouseleave="this.querySelector('.tip').style.display='none'">
    <svg viewBox='0 0 ${W} ${H}' preserveAspectRatio='none'>
      ${yTicks.map(v => `<line x1='${L}' x2='${W - R}' y1='${Y(v).toFixed(1)}' y2='${Y(v).toFixed(1)}' stroke='var(--line)' stroke-width='1'/><text x='${L - 6}' y='${(Y(v) + 4).toFixed(1)}' text-anchor='end' font-size='11' fill='var(--muted)'>${Math.round(v / 100).toLocaleString()}</text>`).join('')}
      ${xt.map(t => `<text x='${X(t).toFixed(1)}' y='${H - 10}' text-anchor='middle' font-size='11' fill='var(--muted)'>${fmt(t)}</text>`).join('')}
      ${typical ? `<line x1='${L}' x2='${W - R}' y1='${Y(typical).toFixed(1)}' y2='${Y(typical).toFixed(1)}' stroke='var(--muted)' stroke-width='1.5' stroke-dasharray='6 4'/>` : ''}
      <path d='${path}' fill='none' stroke='var(--info)' stroke-width='2' vector-effect='non-scaling-stroke'/>${dots}
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
  tip.style.display = 'block'; tip.style.left = Math.min(ev.clientX - rect.left + 12, rect.width - 160) + 'px'; tip.style.top = (ev.clientY - rect.top - 36) + 'px';
  tip.innerHTML = `${new Date(t).toISOString().slice(0, 16).replace('T', ' ')} UTC<br><b>${cur == null ? 'out of stock' : egp(cur)}</b>`;
}
// Click any table header to sort by that column (numbers, money and dates compare as numbers).
document.addEventListener('click', ev => {
  const th = ev.target.closest('th'); if (!th || !th.closest('#view')) return;
  const table = th.closest('table'), tr = th.parentElement, idx = [...tr.children].indexOf(th);
  const rows = [...table.querySelectorAll('tr')].filter(r => r !== tr && r.children.length === tr.children.length && !r.querySelector('th'));
  if (!rows.length) return;
  const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc'; tr.querySelectorAll('th').forEach(h => { delete h.dataset.dir; h.querySelector('.dir')?.remove(); });
  th.dataset.dir = dir; th.insertAdjacentHTML('beforeend', ` <span class='dir'>${dir === 'asc' ? '▲' : '▼'}</span>`);
  const num = s => { const n = parseFloat(String(s).replace(/[^0-9.\\-]/g, '')); return isNaN(n) ? null : n; };
  rows.sort((a, b) => { const x = a.children[idx].textContent.trim(), y = b.children[idx].textContent.trim(), nx = num(x), ny = num(y);
    const c = nx != null && ny != null && /\\d/.test(x) && /\\d/.test(y) ? nx - ny : x.localeCompare(y); return dir === 'asc' ? c : -c; });
  const parent = rows[0].parentElement; rows.forEach(r => parent.appendChild(r));
});
let renderSeq = 0;
async function render() {
  const seq = ++renderSeq;  // a slower, older render must not overwrite a newer one
  try { const html = await views[tab](); if (seq !== renderSeq) return;
    $('#view').innerHTML = html; if (['Overview', 'Devices'].includes(tab)) every(15000, render); }
  catch (e) { if (seq === renderSeq) $('#view').innerHTML = `<p style='color:var(--bad)'>${esc(e.message)}</p>`; }
}
nav(); render();
</script></body></html>""".replace("__CSS__", CSS)


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
                              FROM price_history)
                   SELECT {_PRODUCT}, h.prev AS was, h.price AS now, ROUND(100.0 * (h.prev - h.price) / h.prev, 1) AS drop_pct,
                          h.observed_at AS changed_at
                   FROM h JOIN products p ON p.id = h.product_id
                   WHERE h.observed_at >= ? AND h.in_stock = 1 AND h.price IS NOT NULL AND h.prev IS NOT NULL
                     AND h.price < h.prev AND h.price = p.price
                   ORDER BY drop_pct DESC LIMIT 200""",
    },
    "biggest_rises": {
        "title": "Biggest price rises",
        "about": "The opposite of the drops: where a shop raised a price in the window (a rise followed by a 'cut' is the classic fake discount).",
        "sql": f"""WITH h AS (SELECT product_id, price, observed_at, in_stock,
                                   LAG(price) OVER (PARTITION BY product_id ORDER BY observed_at, id) prev
                              FROM price_history)
                   SELECT {_PRODUCT}, h.prev AS was, h.price AS now, ROUND(100.0 * (h.price - h.prev) / h.prev, 1) AS rise_pct,
                          h.observed_at AS changed_at
                   FROM h JOIN products p ON p.id = h.product_id
                   WHERE h.observed_at >= ? AND h.price IS NOT NULL AND h.prev IS NOT NULL AND h.price > h.prev AND h.price = p.price
                   ORDER BY rise_pct DESC LIMIT 200""",
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
                              FROM price_history WINDOW w AS (PARTITION BY product_id ORDER BY observed_at, id))
                   SELECT {_PRODUCT}, h.p2 AS a, h.p1 AS b, ROUND(100.0 * ABS(h.p1 - h.p2) / h.p2, 1) AS swing_pct, h.observed_at AS back_at
                   FROM h JOIN products p ON p.id = h.product_id
                   WHERE h.observed_at >= ? AND h.price = h.p2 AND h.p1 IS NOT NULL AND ABS(h.p1 - h.p2) >= h.p2 * 0.05
                   ORDER BY swing_pct DESC LIMIT 200""",
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
                              FROM price_history WINDOW w AS (PARTITION BY product_id ORDER BY observed_at, id))
                   SELECT h.observed_at AS back_at, {_PRODUCT}, h.price AS now, p.price AS price_now
                   FROM h JOIN products p ON p.id = h.product_id
                   WHERE h.observed_at >= ? AND h.in_stock = 1 AND h.prev_stock = 0
                   ORDER BY h.observed_at DESC LIMIT 200""",
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


def run_query(conn: sqlite3.Connection, name: str, now: datetime, hours: float,
              grp: str | None = None, site: str | None = None) -> dict:
    """Rows of one saved query; `hours` is the look-back window where the query has one.

    `grp` / `site` filter the result on its `grp` / `site` columns when it has them (the
    query's own LIMIT is applied after filtering, so a filtered report is still full).
    """
    q = QUERIES[name]
    if "cutoff_days" in q:
        cutoff = iso(now - timedelta(days=q["cutoff_days"]))
    elif "cutoff_hours_fixed" in q:
        cutoff = iso(now - timedelta(hours=q["cutoff_hours_fixed"]))
    else:
        cutoff = iso(now - timedelta(hours=hours))
    params: list = [cutoff, iso(now)] if q.get("now_twice") else [cutoff]
    m = _LIMIT.search(q["sql"])
    inner, limit = (q["sql"][:m.start()], int(m.group(1))) if m else (q["sql"], 1000)
    columns = [c[0] for c in conn.execute(f"SELECT * FROM ({inner}) LIMIT 0", params).description]
    where = []
    if grp and "grp" in columns:
        where.append("grp = ?"); params.append(grp)
    if site and "site" in columns:
        where.append("site = ?"); params.append(site)
    sql = f"SELECT * FROM ({inner}) {'WHERE ' + ' AND '.join(where) if where else ''} LIMIT {limit}"
    rows = [list(r) for r in conn.execute(sql, params).fetchall()]
    return {"name": name, "title": q["title"], "about": q["about"], "columns": columns, "rows": rows,
            "uses_window": not ("cutoff_days" in q or "cutoff_hours_fixed" in q),
            "filters": {"grp": "grp" in columns, "site": "site" in columns}}


def query_list() -> list[dict]:
    return [{"name": k, "title": v["title"]} for k, v in QUERIES.items()]
