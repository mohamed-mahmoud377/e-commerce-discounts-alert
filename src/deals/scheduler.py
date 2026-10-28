"""Job queue on SQLite: what to crawl next, who is crawling it, and who is blocked.

- Every watch root has a 'plan' job (re-planned weekly); planning produces
  'crawl' jobs, one per slice.
- A worker leases one job at a time. A lease expires unless the worker keeps
  reporting pages, so a crashed worker's job simply becomes available again.
- At most one job per (site, IP) runs at a time, so two workers behind the same
  home router never hammer a site together.
- When a site blocks an IP, that (site, IP) pair cools down with exponential
  backoff (15 min, 30 min, 1 h ... up to 24 h); other IPs keep going.
- Crawl intervals adapt: slices where prices moved are revisited sooner (down to
  min_interval), quiet ones back off (up to watch interval x max_interval_factor).
"""

import random
import secrets
import sqlite3
from datetime import datetime, timedelta

from deals.config import Config
from deals.db import iso
from deals.planner import PlannedSlice

BLOCK_BASE = timedelta(minutes=15)
BLOCK_MAX = timedelta(hours=24)
FAIL_BASE = timedelta(minutes=5)
FAIL_MAX = timedelta(hours=6)


def sync_watches(conn: sqlite3.Connection, cfg: Config, now: datetime) -> None:
    """Make plan jobs match the config: add new roots, drop removed ones."""
    wanted = set()
    with conn:
        for watch in cfg.watches:
            base = watch.interval_minutes * 60
            for site, root in watch.roots():
                key = root.key()
                wanted.add((site, key))
                conn.execute(
                    """INSERT INTO jobs (kind, site, target, root, grp, base_interval_s, interval_s,
                                         next_due_at, created_at)
                       VALUES ('plan', ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (kind, site, target) DO UPDATE SET grp = excluded.grp,
                           base_interval_s = excluded.base_interval_s""",
                    (site, key, key, watch.name, base, int(cfg.schedule.replan_days * 86400), iso(now), iso(now)),
                )
                conn.execute("UPDATE jobs SET grp = ?, base_interval_s = ? WHERE kind = 'crawl' AND site = ? AND root = ?",
                             (watch.name, base, site, key))
        for row in conn.execute("SELECT site, root FROM jobs WHERE kind = 'plan'").fetchall():
            if (row["site"], row["root"]) not in wanted:
                conn.execute("DELETE FROM jobs WHERE site = ? AND root = ?", (row["site"], row["root"]))


def lease(conn: sqlite3.Connection, cfg: Config, worker: str, ip: str, sites: list[str],
          now: datetime) -> sqlite3.Row | None:
    now_s = iso(now)
    conn.commit()  # BEGIN fails inside an implicit transaction
    conn.execute("BEGIN IMMEDIATE")  # serialise leasing so two workers never get the same job
    try:
        blocked = {r["site"] for r in conn.execute(
            "SELECT site FROM blocks WHERE ip = ? AND until > ?", (ip, now_s))}
        marks = ",".join("?" * len(sites))
        # A worker asking for work on these sites has finished (or abandoned)
        # whatever it held on them. Its loops for other sites keep their jobs.
        conn.execute(f"""UPDATE jobs SET lease_worker = NULL, lease_ip = NULL, lease_token = NULL,
                                lease_expires_at = NULL WHERE lease_worker = ? AND site IN ({marks})""",
                     (worker, *sites))
        busy = {r["site"] for r in conn.execute(
            "SELECT site FROM jobs WHERE lease_ip = ? AND lease_expires_at > ?", (ip, now_s))}
        eligible = [s for s in sites if s not in blocked and s not in busy]
        if not eligible:
            conn.execute("COMMIT")
            return None
        # Plan jobs first, then the job that is latest *relative to its own interval*: a
        # 10-minute slice 6 minutes late (60%) beats a 3-hour slice 14 minutes late (8%),
        # so fast groups keep their pace when the queue is behind.
        job = conn.execute(
            f"""SELECT * FROM jobs
                WHERE site IN ({','.join('?' * len(eligible))}) AND next_due_at <= ?
                  AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                ORDER BY kind = 'plan' DESC,
                         (julianday(?) - julianday(next_due_at)) * 86400.0 / interval_s DESC, next_due_at
                LIMIT 1""",
            (*eligible, now_s, now_s, now_s),
        ).fetchone()
        if job is None:
            conn.execute("COMMIT")
            return None
        token = secrets.token_urlsafe(12)
        conn.execute(
            """UPDATE jobs SET lease_worker = ?, lease_ip = ?, lease_token = ?, lease_expires_at = ?,
                               run_count = 0, run_changes = 0 WHERE id = ?""",
            (worker, ip, token, iso(now + _lease(cfg)), job["id"]),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()


def next_due(conn: sqlite3.Connection, sites: list[str]) -> datetime | None:
    row = conn.execute(
        f"SELECT MIN(next_due_at) AS t FROM jobs WHERE site IN ({','.join('?' * len(sites))})", sites
    ).fetchone()
    return datetime.fromisoformat(row["t"]) if row and row["t"] else None


def check_lease(conn: sqlite3.Connection, job_id: int, token: str) -> sqlite3.Row | None:
    """The job, if this lease token still owns it."""
    return conn.execute("SELECT * FROM jobs WHERE id = ? AND lease_token = ?", (job_id, token)).fetchone()


def extend(conn: sqlite3.Connection, cfg: Config, job_id: int, count: int, changes: int, now: datetime) -> None:
    conn.execute(
        """UPDATE jobs SET lease_expires_at = ?, run_count = run_count + ?, run_changes = run_changes + ?
           WHERE id = ?""",
        (iso(now + _lease(cfg)), count, changes, job_id),
    )


def finish_crawl(conn: sqlite3.Connection, cfg: Config, job: sqlite3.Row, now: datetime) -> None:
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()
    lowest = cfg.schedule.min_interval_minutes * 60
    watch = cfg.watch(job["grp"])
    # A group can cap how far its quiet slices back off: a flash sale always starts in a
    # quiet slice, so the cap is the worst-case time to notice it.
    if watch and watch.max_interval_minutes:
        highest = max(lowest, watch.max_interval_minutes * 60)
    else:
        highest = max(lowest, int(job["base_interval_s"] * cfg.schedule.max_interval_factor))
    if cfg.schedule.feed_max_interval_minutes and is_deal_feed(job["root"] or job["target"]):
        highest = max(lowest, min(highest, cfg.schedule.feed_max_interval_minutes * 60))
    if job["run_changes"]:
        interval = job["interval_s"] / 2
    else:
        interval = job["interval_s"] * 1.25
    interval = int(min(highest, max(lowest, interval)))
    conn.execute(
        """UPDATE jobs SET interval_s = ?, next_due_at = ?, last_done_at = ?, last_count = run_count,
                           last_changes = run_changes, failures = 0, last_error = NULL,
                           lease_worker = NULL, lease_ip = NULL, lease_token = NULL, lease_expires_at = NULL
           WHERE id = ?""",
        (interval, iso(now + _jitter(interval)), iso(now), job["id"]),
    )


def finish_plan(conn: sqlite3.Connection, job: sqlite3.Row, planned: list[PlannedSlice], now: datetime) -> None:
    keys = [p.slice.key() for p in planned]
    for p in planned:
        conn.execute(
            """INSERT INTO jobs (kind, site, target, root, grp, expected_total, oversized,
                                 base_interval_s, interval_s, next_due_at, created_at)
               VALUES ('crawl', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (kind, site, target) DO UPDATE SET root = excluded.root, grp = excluded.grp,
                   expected_total = excluded.expected_total, oversized = excluded.oversized,
                   base_interval_s = excluded.base_interval_s""",
            (job["site"], p.slice.key(), job["root"], job["grp"], p.total, p.oversized,
             job["base_interval_s"], job["base_interval_s"], iso(now), iso(now)),
        )
    conn.execute(
        f"""DELETE FROM jobs WHERE kind = 'crawl' AND site = ? AND root = ?
            AND target NOT IN ({','.join('?' * len(keys)) or "''"})""",
        (job["site"], job["root"], *keys),
    )
    conn.execute(
        """UPDATE jobs SET next_due_at = ?, last_done_at = ?, expected_total = ?, last_count = ?,
                           failures = 0, last_error = NULL,
                           lease_worker = NULL, lease_ip = NULL, lease_token = NULL, lease_expires_at = NULL
           WHERE id = ?""",
        (iso(now + timedelta(seconds=job["interval_s"])), iso(now),
         sum(p.total or 0 for p in planned), len(planned), job["id"]),
    )


def release(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute("""UPDATE jobs SET lease_worker = NULL, lease_ip = NULL, lease_token = NULL,
                           lease_expires_at = NULL WHERE id = ?""", (job_id,))


def fail(conn: sqlite3.Connection, job: sqlite3.Row, error: str, now: datetime) -> int:
    """Back the job off exponentially. Returns the new failure count."""
    failures = job["failures"] + 1
    delay = min(FAIL_MAX, FAIL_BASE * 2 ** (failures - 1))
    conn.execute(
        """UPDATE jobs SET failures = ?, last_error = ?, next_due_at = ?,
                           lease_worker = NULL, lease_ip = NULL, lease_token = NULL, lease_expires_at = NULL
           WHERE id = ?""",
        (failures, error[:500], iso(now + delay), job["id"]),
    )
    return failures


def block(conn: sqlite3.Connection, site: str, ip: str, reason: str, now: datetime) -> datetime:
    row = conn.execute("SELECT strikes FROM blocks WHERE site = ? AND ip = ?", (site, ip)).fetchone()
    strikes = (row["strikes"] if row else 0) + 1
    until = now + min(BLOCK_MAX, BLOCK_BASE * 2 ** (strikes - 1))
    conn.execute(
        """INSERT INTO blocks (site, ip, strikes, until, reason, updated_at) VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (site, ip) DO UPDATE SET strikes = excluded.strikes, until = excluded.until,
               reason = excluded.reason, updated_at = excluded.updated_at""",
        (site, ip, strikes, iso(until), reason[:300], iso(now)),
    )
    return until


def unblock(conn: sqlite3.Connection, site: str, ip: str) -> None:
    conn.execute("DELETE FROM blocks WHERE site = ? AND ip = ?", (site, ip))


def worker_seen(conn: sqlite3.Connection, name: str, ip: str, now: datetime, *, sites: list[str] | None = None,
                version: str | None = None, pages: int = 0, jobs_done: int = 0) -> None:
    conn.execute(
        """INSERT INTO workers (name, last_seen_at, last_ip, sites, version, pages, jobs_done)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (name) DO UPDATE SET last_seen_at = excluded.last_seen_at, last_ip = excluded.last_ip,
               sites = COALESCE(excluded.sites, sites), version = COALESCE(excluded.version, version),
               pages = pages + excluded.pages, jobs_done = jobs_done + excluded.jobs_done""",
        (name, iso(now), ip, ",".join(sites) if sites else None, version, pages, jobs_done),
    )


# Root keys that read a shop's own deal feed rather than a whole category: Amazon's Today's
# Deals API and "all discounts" filter, noon's "lowest price in 7 days", Jumia's flash sales.
DEAL_FEED_MARKS = ("deals:", "p_n_deal_type", "min_offer_price", "flash-sales")


def is_deal_feed(key: str) -> bool:
    return any(mark in key for mark in DEAL_FEED_MARKS)


def _lease(cfg: Config) -> timedelta:
    return timedelta(minutes=cfg.schedule.lease_minutes)


def _jitter(seconds: float) -> timedelta:
    """+-10% so slices planned together don't stay in lockstep forever."""
    return timedelta(seconds=seconds * random.uniform(0.9, 1.1))


def worker_site_ok(conn: sqlite3.Connection, worker: str, site: str, now: datetime, products: int) -> None:
    conn.execute(
        """INSERT INTO worker_sites (worker, site, last_ok_at, pages, products) VALUES (?, ?, ?, 1, ?)
           ON CONFLICT (worker, site) DO UPDATE SET last_ok_at = excluded.last_ok_at,
               pages = pages + 1, products = products + excluded.products""",
        (worker, site, iso(now), products),
    )


def worker_site_problem(conn: sqlite3.Connection, worker: str, site: str, now: datetime, error: str,
                        blocked: bool = False) -> None:
    conn.execute(
        """INSERT INTO worker_sites (worker, site, last_error_at, last_error, blocks) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (worker, site) DO UPDATE SET last_error_at = excluded.last_error_at,
               last_error = excluded.last_error, blocks = blocks + excluded.blocks""",
        (worker, site, iso(now), error[:300], int(blocked)),
    )


def add_events(conn: sqlite3.Connection, source: str, events: list[tuple[str, str, str]]) -> None:
    """events: (at, level, message)."""
    conn.executemany("INSERT INTO events (at, source, level, message) VALUES (?, ?, ?, ?)",
                     [(at, source, level, message[:1000]) for at, level, message in events])
