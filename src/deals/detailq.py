"""The coordinator's side of reading product pages (details.py): which page a worker may read next,
recording what it found, and refilling the queue. Kept apart from crawl scheduling: nothing here
touches the jobs, leases or crawl blocks, and a page is only offered when the worker has no crawl
job for that shop (the lease came back empty).

Pace per shop and IP: one page per `details.every_seconds` at most, `details.daily_cap` a day, and
after a refusal none for `details.pause_hours`.
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta

from deals.config import Config
from deals.db import iso, parse_iso
from deals.models import valid_gtin

log = logging.getLogger(__name__)

MIN_IDLE = 10  # seconds the worker would otherwise wait: below this a crawl job is about due, so no page
QUEUE_PER_SITE = 2000  # pages queued per shop by each matching pass (more than a day's cap)
RETRY_FAILED = timedelta(days=7)  # a page that failed to parse is tried again after this long
# Learning where pages pay off: once a shop's group has this many pages read, it is read last if fewer
# than LOW_YIELD of them gave a barcode or a model number (Jumia's appliances: sellers leave the model
# blank or write "9", "5 درج"; its phones carry barcodes).
YIELD_SAMPLE = 15
LOW_YIELD = 0.10


def offer(conn: sqlite3.Connection, cfg: Config, ip: str, site: str, now: datetime, idle_seconds: float) -> dict | None:
    """One product page for an idle worker on `site`, or None. Takes it off the queue."""
    rules = cfg.details
    if not rules.enabled or site not in rules.sites or idle_seconds < MIN_IDLE:
        return None
    now_s, today = iso(now), now.date().isoformat()
    if conn.execute("SELECT 1 FROM blocks WHERE site = ? AND ip = ? AND until > ?", (site, ip, now_s)).fetchone():
        return None  # the shop is refusing this IP's crawling: certainly no extra pages
    state = conn.execute("SELECT * FROM detail_state WHERE site = ? AND ip = ?", (site, ip)).fetchone()
    if state:
        if state["paused_until"] and state["paused_until"] > now_s:
            return None
        if state["last_at"] and parse_iso(state["last_at"]) + timedelta(seconds=rules.every_seconds) > now:
            return None
        if state["day"] == today and state["count"] >= rules.daily_cap:
            return None
    row = conn.execute(
        """SELECT q.product_id, p.url FROM detail_queue q JOIN products p ON p.id = q.product_id
           WHERE q.site = ? ORDER BY q.priority, q.product_id LIMIT 1""", (site,)).fetchone()
    if row is None:
        return None
    with conn:
        conn.execute("DELETE FROM detail_queue WHERE site = ? AND product_id = ?", (site, row["product_id"]))
        conn.execute(
            """INSERT INTO detail_state (site, ip, last_at, day, count) VALUES (?, ?, ?, ?, 1)
               ON CONFLICT (site, ip) DO UPDATE SET last_at = excluded.last_at,
                   count = CASE WHEN day = excluded.day THEN count + 1 ELSE 1 END, day = excluded.day""",
            (site, ip, now_s, today))
    return {"product_id": row["product_id"], "site": site, "url": row["url"]}


def record(conn: sqlite3.Connection, cfg: Config, product_id: int, site: str, ip: str, report: dict,
           now: datetime) -> None:
    """Keep what a worker read from a product page (status ok / gone / failed / blocked)."""
    from deals.matching import is_code, norm_code, _SHORT

    status = report.get("status")
    if status == "blocked":
        until = now + timedelta(hours=cfg.details.pause_hours)
        with conn:
            conn.execute(
                """INSERT INTO detail_state (site, ip, paused_until, reason) VALUES (?, ?, ?, ?)
                   ON CONFLICT (site, ip) DO UPDATE SET paused_until = excluded.paused_until, reason = excluded.reason""",
                (site, ip, iso(until), (report.get("reason") or "")[:300]))
        log.warning("product pages of %s paused for %s until %s: %s", site, ip, iso(until)[:16], report.get("reason"))
        return
    title = conn.execute("SELECT title FROM products WHERE id = ? AND site = ?", (product_id, site)).fetchone()
    if title is None:
        return
    gtins = sorted({g for g in (valid_gtin(x) for x in report.get("gtins") or []) if g})
    mpns = [str(m)[:40] for m in (report.get("mpns") or [])][:5]
    codes = []
    for m in mpns:
        c = norm_code(m.split("/")[0])
        if (is_code(c) or _SHORT.match(c)) and c not in codes:
            codes.append(c)
    specs = report.get("specs") or {}
    specs = {str(k)[:40]: str(v)[:80] for k, v in list(specs.items())[:25]} if isinstance(specs, dict) else {}
    with conn:
        conn.execute(
            """INSERT OR REPLACE INTO product_details (product_id, fetched_at, status, title, gtins, mpns, codes,
                                                       model_name, specs) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (product_id, iso(now), status if status in ("ok", "gone") else "failed", title["title"], " ".join(gtins),
             " | ".join(mpns), " ".join(codes), (report.get("model_name") or None) and str(report["model_name"])[:80],
             json.dumps(specs, ensure_ascii=False) if specs else None))
    if gtins:
        from deals.db import record_barcodes
        record_barcodes(conn, product_id, gtins, "page", now)
        conn.commit()


def refill(conn: sqlite3.Connection, now: datetime) -> int:
    """The next pages per shop: in-stock products seen in the last 3 days whose page was never read (or
    whose title changed since, or whose read failed a week ago). Unmatched products first, then
    probable and line matches, then the rest. Run by every matching pass."""
    from deals.details import PARSERS

    sites = list(PARSERS)
    low = [(r[0], r[1]) for r in conn.execute(
        """SELECT p.site, p.grp FROM product_details d JOIN products p ON p.id = d.product_id WHERE d.status = 'ok'
           GROUP BY p.site, p.grp HAVING COUNT(*) >= ? AND AVG(d.gtins != '' OR d.codes != '') < ?""",
        (YIELD_SAMPLE, LOW_YIELD))]
    with conn:
        conn.execute("DELETE FROM detail_queue")
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS low_yield (site TEXT, grp TEXT)")
        conn.execute("DELETE FROM low_yield")
        conn.executemany("INSERT INTO low_yield VALUES (?, ?)", low)
        conn.execute(
            f"""INSERT INTO detail_queue (site, product_id, priority)
                SELECT site, id, priority FROM (
                  SELECT p.site, p.id,
                         CASE WHEN EXISTS (SELECT 1 FROM low_yield l WHERE l.site = p.site AND l.grp IS p.grp) THEN 3
                              WHEN pm.status IN ('no_code', 'ambiguous_brand', 'ambiguous_variant') THEN 0
                              WHEN m.status = 'probable' OR m.kind = 'line' THEN 1 ELSE 2 END AS priority,
                         ROW_NUMBER() OVER (PARTITION BY p.site ORDER BY
                           CASE WHEN EXISTS (SELECT 1 FROM low_yield l WHERE l.site = p.site AND l.grp IS p.grp) THEN 3
                                WHEN pm.status IN ('no_code', 'ambiguous_brand', 'ambiguous_variant') THEN 0
                                WHEN m.status = 'probable' OR m.kind = 'line' THEN 1 ELSE 2 END, p.id) AS n
                  FROM products p LEFT JOIN product_models pm ON pm.product_id = p.id
                  LEFT JOIN models m ON m.model = pm.model
                  LEFT JOIN product_details d ON d.product_id = p.id
                  WHERE p.site IN ({','.join('?' * len(sites))}) AND p.in_stock = 1 AND p.last_seen_at >= ?
                    AND p.url LIKE 'http%'
                    AND COALESCE(pm.status, '') NOT IN ('used', 'accessory', 'bundle')
                    AND (d.product_id IS NULL OR d.title IS NOT p.title
                         OR (d.status = 'failed' AND d.fetched_at < ?)))
                WHERE n <= ?""",
            (*sites, iso(now - timedelta(days=3)), iso(now - RETRY_FAILED), QUEUE_PER_SITE))
    return conn.execute("SELECT COUNT(*) FROM detail_queue").fetchone()[0]


def stats(conn: sqlite3.Connection, now: datetime) -> list[dict]:
    """Per shop: pages read, what they gave, what is queued, pace and pauses (for the CLI)."""
    rows = conn.execute(
        """SELECT p.site, COUNT(*) AS pages, SUM(d.status = 'ok') AS ok, SUM(d.gtins != '') AS barcodes,
                  SUM(d.codes != '') AS models, MAX(d.fetched_at) AS last
           FROM product_details d JOIN products p ON p.id = d.product_id GROUP BY p.site""").fetchall()
    queued = dict(conn.execute("SELECT site, COUNT(*) FROM detail_queue GROUP BY site").fetchall())
    paused = {}
    for r in conn.execute("SELECT site, ip, paused_until, reason FROM detail_state WHERE paused_until > ?", (iso(now),)):
        paused.setdefault(r["site"], []).append(f"{r['ip']} until {r['paused_until'][:16]} ({r['reason']})")
    out = {r["site"]: dict(r) for r in rows}
    for site in set(queued) | set(paused) | set(out):
        out.setdefault(site, {"site": site, "pages": 0, "ok": 0, "barcodes": 0, "models": 0, "last": None})
        out[site]["queued"] = queued.get(site, 0)
        out[site]["paused"] = paused.get(site, [])
    return sorted(out.values(), key=lambda r: r["site"])
