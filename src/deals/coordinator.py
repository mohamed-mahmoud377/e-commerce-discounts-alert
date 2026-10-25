"""The coordinator: owns the database and hands out work to workers over HTTP.

Workers anywhere (droplet, your Mac, a friend's PC) call:
  POST /api/lease               -> next job for the sites this worker can reach
  POST /api/jobs/{id}/page      -> products from one page (also extends the lease)
  POST /api/jobs/{id}/plan      -> slices produced by a plan job
  POST /api/jobs/{id}/blocked   -> the site challenged this worker's IP
  POST /api/jobs/{id}/failed    -> anything else went wrong
  POST /api/jobs/{id}/release   -> worker is shutting down
Each worker authenticates with its own token from [workers] in the config.
"""

import logging
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from importlib.metadata import version as pkg_version

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from deals import alerts, bot, db, scheduler
from deals.config import Config
from deals.db import iso, utcnow
from deals.models import Listing, Slice
from deals.planner import PlannedSlice

log = logging.getLogger(__name__)
VERSION = pkg_version("deals")


class ListingIn(BaseModel):
    site: str
    sku: str
    title: str
    url: str
    price: int | None
    list_price: int | None
    currency: str
    in_stock: bool
    list_price_kind: str | None = None
    brand: str | None = None
    stock_left: int | None = None
    recent_sales: int | None = None
    rating: float | None = None
    reviews: int | None = None
    badges: list[str] = []
    seller: str | None = None

    def to_listing(self) -> Listing:
        return Listing(**{**self.model_dump(), "badges": tuple(self.badges)})


class LeaseIn(BaseModel):
    sites: list[str]  # sites this request may get a job for
    worker_sites: list[str] | None = None  # every site the worker handles (for status)
    version: str | None = None


class PageIn(BaseModel):
    lease_token: str
    page: int
    listings: list[ListingIn]
    done: bool = False


class PlannedIn(BaseModel):
    target: str
    total: int | None
    oversized: bool = False


class PlanIn(BaseModel):
    lease_token: str
    slices: list[PlannedIn]


class ProblemIn(BaseModel):
    lease_token: str
    reason: str


def create_app(cfg: Config, *, background: bool = True) -> FastAPI:
    # One connection, used only from the event loop (all endpoints are async),
    # so requests are applied one at a time. Background threads open their own.
    conn = db.connect(cfg.db_path)
    scheduler.sync_watches(conn, cfg, utcnow())
    bot.ensure_admin(conn, cfg, utcnow())
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        threads = [alerts.AlertSender(cfg, stop), Watchdog(cfg, stop)] if background else []
        if background and cfg.telegram_token and cfg.telegram_chat_id:
            threads.append(bot.BotListener(cfg, stop))
        for t in threads:
            t.start()
        yield
        stop.set()

    app = FastAPI(title="deals coordinator", version=VERSION, lifespan=lifespan)
    app.state.conn = conn
    tokens = {token: name for name, token in cfg.workers.items()}

    def worker_name(request: Request) -> str:
        auth = request.headers.get("authorization", "")
        name = tokens.get(auth.removeprefix("Bearer ").strip())
        if not name:
            raise HTTPException(401, "unknown worker token")
        return name

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def owned_job(job_id: int, token: str) -> sqlite3.Row:
        job = scheduler.check_lease(conn, job_id, token)
        if job is None:
            raise HTTPException(409, "lease lost (expired and taken by another worker, or job removed)")
        return job

    @app.get("/health")
    async def health():
        conn.execute("SELECT 1").fetchone()
        return {"ok": True, "version": VERSION}

    @app.post("/api/lease")
    async def lease(body: LeaseIn, request: Request, worker: str = Depends(worker_name)):
        now, ip = utcnow(), client_ip(request)
        with conn:
            scheduler.worker_seen(conn, worker, ip, now, sites=body.worker_sites or body.sites,
                                  version=body.version)
        job = scheduler.lease(conn, cfg, worker, ip, body.sites, now)
        if job is None:
            due = scheduler.next_due(conn, body.sites) if body.sites else None
            wait = 30 if due is None else (due - now).total_seconds()
            return {"job": None, "retry_after": int(min(60, max(5, wait)))}
        site = cfg.site(job["site"])
        return {"job": {
            "id": job["id"], "kind": job["kind"], "site": job["site"], "target": job["target"],
            "root": job["root"], "group": job["grp"], "lease_token": job["lease_token"],
            "expected_total": job["expected_total"],
            "min_delay": site.min_delay, "max_delay": site.max_delay,
        }}

    @app.post("/api/jobs/{job_id}/page")
    async def page(job_id: int, body: PageIn, request: Request, worker: str = Depends(worker_name)):
        now, ip = utcnow(), client_ip(request)
        job = owned_job(job_id, body.lease_token)
        # Products a slice has never shown before are only "new" to us once the
        # slice has been crawled at least once (otherwise everything is new).
        bootstrap = job["kind"] == "plan" or job["last_done_at"] is None
        changes = deals = 0
        with conn:
            for item in body.listings:
                listing = item.to_listing()
                if listing.site != job["site"]:
                    raise HTTPException(422, f"listing for {listing.site} in a {job['site']} job")
                rec = db.record(conn, listing, grp=job["grp"], now=now)
                if rec.status == "changed" or (rec.status == "new" and not bootstrap):
                    changes += 1
                dropped = (rec.status == "new" or (listing.price is not None and (
                    rec.old_price is None or not rec.old_in_stock or listing.price < rec.old_price)))
                if rec.price_moved and dropped and listing.in_stock:
                    deal = alerts.check_product(conn, cfg, rec.product_id, bootstrap=bootstrap, now=now)
                    deals += deal is not None
            scheduler.extend(conn, cfg, job_id, len(body.listings), changes, now)
            scheduler.unblock(conn, job["site"], ip)
            scheduler.worker_seen(conn, worker, ip, now, pages=1, jobs_done=int(body.done))
            if body.done and job["kind"] == "crawl":
                _finish_crawl(job_id, now)
        return {"ok": True, "changes": changes, "deals": deals}

    def _finish_crawl(job_id: int, now: datetime) -> None:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job["run_count"] == 0 and (job["expected_total"] or 0) > 0:
            # The slice had products when planned but the parser found none:
            # most likely the site changed its page layout.
            reason = f"0 products parsed, slice had {job['expected_total']} (site layout changed?)"
            failures = scheduler.fail(conn, job, reason, now)
            if failures >= 3:
                alerts.system_alert(conn, f"parser:{job['site']}",
                                    f"{_site(job['site'])}: لم نستخرج أي منتج من {job['target']} رغم أنه كان يحتوي "
                                    f"{job['expected_total']} منتج. ربما تغيّر تصميم الموقع.", now)
        else:
            scheduler.finish_crawl(conn, cfg, job, now)

    @app.post("/api/jobs/{job_id}/plan")
    async def plan_done(job_id: int, body: PlanIn, request: Request, worker: str = Depends(worker_name)):
        now = utcnow()
        job = owned_job(job_id, body.lease_token)
        if job["kind"] != "plan":
            raise HTTPException(422, "not a plan job")
        planned = [PlannedSlice(Slice.from_key(s.target), s.total, s.oversized) for s in body.slices]
        with conn:
            scheduler.finish_plan(conn, job, planned, now)
            scheduler.worker_seen(conn, worker, client_ip(request), now, jobs_done=1)
            oversized = [p for p in planned if p.oversized]
            if oversized:
                alerts.system_alert(conn, f"oversized:{job['site']}:{job['root']}",
                                    f"{job['grp']} على {_site(job['site'])}: {len(oversized)} شريحة أكبر من أن "
                                    f"نجمعها بالكامل (مثل {oversized[0].slice})", now, timedelta(days=7))
        return {"ok": True, "slices": len(planned)}

    @app.post("/api/jobs/{job_id}/blocked")
    async def blocked(job_id: int, body: ProblemIn, request: Request, worker: str = Depends(worker_name)):
        now, ip = utcnow(), client_ip(request)
        job = owned_job(job_id, body.lease_token)
        with conn:
            until = scheduler.block(conn, job["site"], ip, body.reason, now)
            scheduler.release(conn, job_id)
            strikes = conn.execute("SELECT strikes FROM blocks WHERE site = ? AND ip = ?",
                                   (job["site"], ip)).fetchone()["strikes"]
            if strikes >= 3:
                alerts.system_alert(conn, f"blocked:{job['site']}:{ip}",
                                    f"{_site(job['site'])} يحظر الجهاز {worker} ({ip}) للمرة {strikes} على التوالي. "
                                    f"متوقف عن هذا الموقع حتى {until:%H:%M} UTC. السبب: {body.reason}", now)
        log.warning("%s blocked on %s (%s) until %s", worker, job["site"], ip, until)
        return {"ok": True, "paused_until": iso(until)}

    @app.post("/api/jobs/{job_id}/failed")
    async def failed(job_id: int, body: ProblemIn, request: Request, worker: str = Depends(worker_name)):
        now = utcnow()
        job = owned_job(job_id, body.lease_token)
        with conn:
            failures = scheduler.fail(conn, job, f"{worker}: {body.reason}", now)
            if failures >= 3:
                alerts.system_alert(conn, f"failing:{job['id']}",
                                    f"مهمة {job['kind']} على {_site(job['site'])} ({job['target']}) فشلت {failures} "
                                    f"مرات: {body.reason}", now, timedelta(hours=24))
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/release")
    async def release(job_id: int, body: ProblemIn, worker: str = Depends(worker_name)):
        job = scheduler.check_lease(conn, job_id, body.lease_token)
        if job:
            with conn:
                scheduler.release(conn, job_id)
        return {"ok": True}

    return app


def _site(name: str) -> str:
    return alerts.SITE_NAME.get(name, name)


class Watchdog(threading.Thread):
    """Notices problems nobody reports: stalled sites, silent workers. Also takes daily backups."""

    def __init__(self, cfg: Config, stop: threading.Event, interval: float = 60):
        super().__init__(name="watchdog", daemon=True)
        self.cfg, self.stop_event, self.interval = cfg, stop, interval

    def run(self) -> None:
        conn = db.connect(self.cfg.db_path)
        while not self.stop_event.wait(self.interval):
            try:
                self.check(conn, utcnow())
            except Exception:
                log.exception("watchdog check failed")

    def check(self, conn: sqlite3.Connection, now: datetime) -> None:
        with conn:
            for row in conn.execute(
                    """SELECT site, MIN(next_due_at) AS oldest_due, MAX(last_done_at) AS last_done
                       FROM jobs GROUP BY site""").fetchall():
                overdue = now - db.parse_iso(row["oldest_due"])
                last_done = db.parse_iso(row["last_done"]) if row["last_done"] else None
                if overdue > timedelta(minutes=45) and (last_done is None or now - last_done > timedelta(hours=1)):
                    blocks = conn.execute("SELECT ip, until FROM blocks WHERE site = ? AND until > ?",
                                          (row["site"], iso(now))).fetchall()
                    detail = "، ".join(f"{b['ip']} حتى {b['until'][11:16]}" for b in blocks) or "لا يوجد"
                    alerts.system_alert(conn, f"stalled:{row['site']}",
                                        f"{_site(row['site'])} متوقف: العمل متأخر {overdue.total_seconds() / 3600:.1f} "
                                        f"ساعة ولم يكتمل شيء خلال الساعة الأخيرة. الأجهزة المحظورة: {detail}. "
                                        f"هل يوجد جهاز (worker) شغّال لهذا الموقع؟ (مثلًا الماك نائم)", now)
            for w in conn.execute("SELECT name, last_seen_at FROM workers").fetchall():
                silent = now - db.parse_iso(w["last_seen_at"])
                if timedelta(minutes=30) < silent < timedelta(days=2):
                    alerts.system_alert(conn, f"offline:{w['name']}",
                                        f"الجهاز {w['name']} لم يتواصل منذ {silent.total_seconds() / 60:.0f} دقيقة",
                                        now, timedelta(hours=12))
        self.backup(conn, now)

    def backup(self, conn: sqlite3.Connection, now: datetime, keep: int = 7) -> None:
        folder = self.cfg.data_dir / "backups"
        target = folder / f"deals-{now:%Y-%m-%d}.db"
        if target.exists():
            return
        db.backup(conn, target)
        for old in sorted(folder.glob("deals-*.db"))[:-keep]:
            old.unlink()
        log.info("backup written to %s", target)


def serve(cfg: Config) -> None:
    import uvicorn

    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="info", access_log=False)
