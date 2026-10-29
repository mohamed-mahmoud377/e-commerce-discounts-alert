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
import shutil
import sqlite3
import threading
from pathlib import Path
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta
from importlib.metadata import version as pkg_version

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from deals import alerts, bot, db, detailq, health, matching, monitor, scheduler, webui
from deals.config import Config
from deals.db import iso, utcnow
from deals.models import Listing, Slice
from deals.planner import PlannedSlice
from deals.sites import SITES

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
    gtins: list[str] = []  # optional: older workers don't send it

    def to_listing(self) -> Listing:
        return Listing(**{**self.model_dump(), "badges": tuple(self.badges), "gtins": tuple(self.gtins)})


class LeaseIn(BaseModel):
    sites: list[str]  # sites this request may get a job for
    worker_sites: list[str] | None = None  # every site the worker handles (for status)
    version: str | None = None


class PageIn(BaseModel):
    lease_token: str
    page: int
    listings: list[ListingIn]
    done: bool = False
    no_results: bool = False  # the site said the slice is empty now (not a parse failure)


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


class LogLine(BaseModel):
    at: str
    level: str
    message: str


class LogsIn(BaseModel):
    events: list[LogLine]


def create_app(cfg: Config, *, background: bool = True) -> FastAPI:
    # One connection, used only from the event loop (all endpoints are async),
    # so requests are applied one at a time. Background threads open their own.
    conn = db.connect(cfg.db_path)

    def read_conn() -> sqlite3.Connection:
        """A private read-only connection for a viewer request.

        Viewer endpoints are plain `def`, so FastAPI runs them in a worker thread; with
        their own connection they never block the event loop (and the workers' page
        uploads) however slow a report is. WAL readers don't block the writer either.
        """
        c = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True, timeout=30)
        c.row_factory = sqlite3.Row
        return c
    scheduler.sync_watches(conn, cfg, utcnow())
    bot.ensure_admin(conn, cfg, utcnow())
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        threads = [alerts.AlertSender(cfg, stop), Watchdog(cfg, stop)] if background else []
        app.state.watchdog = next((t for t in threads if isinstance(t, Watchdog)), None)
        if background:  # the coordinator's own log lines show up on the dashboard too
            handler = monitor.EventLogHandler(cfg.db_path)
            logging.getLogger("deals").addHandler(handler)
        if background and cfg.telegram_token and cfg.telegram_chat_id:
            threads.append(bot.BotListener(cfg, stop))
        for t in threads:
            t.start()
        yield
        stop.set()

    app = FastAPI(title="deals coordinator", version=VERSION, lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=2000)  # the web UI page and big JSON lists, for phones
    app.state.conn = conn
    tokens = {token: name for name, token in cfg.workers.items()}

    def worker_name(request: Request) -> str:
        auth = request.headers.get("authorization", "")
        name = tokens.get(auth.removeprefix("Bearer ").strip())
        if not name:
            raise HTTPException(401, "unknown worker token")
        return name

    view_tokens = set(tokens) | ({cfg.admin_token} if cfg.admin_token else set())

    def can_view(request: Request) -> bool:
        """Read-only views: open from the server itself (SSH tunnel); otherwise the admin
        token or a worker token, as a cookie (web UI), ?token= or a Bearer header."""
        if client_ip(request) in ("127.0.0.1", "::1"):
            return True
        token = (request.cookies.get(webui.COOKIE) or request.query_params.get("token")
                 or request.headers.get("authorization", "").removeprefix("Bearer ").strip())
        return token in view_tokens

    def is_admin(request: Request) -> bool:
        """Only the admin token may change anything (approve people, send the report)."""
        token = (request.cookies.get(webui.COOKIE) or request.query_params.get("token")
                 or request.headers.get("authorization", "").removeprefix("Bearer ").strip())
        return bool(cfg.admin_token) and token == cfg.admin_token

    def admin(request: Request) -> None:
        if not is_admin(request):
            raise HTTPException(403, "only the admin token can do this")

    def viewer(request: Request) -> None:
        if not can_view(request):
            raise HTTPException(401, "log in at /ui/login, add ?token=<token>, or use make dashboard")

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def owned_job(job_id: int, token: str) -> sqlite3.Row:
        job = scheduler.check_lease(conn, job_id, token)
        if job is None:
            raise HTTPException(409, "lease lost (expired and taken by another worker, or job removed)")
        return job

    @app.get("/health")
    async def health_endpoint():
        conn.execute("SELECT 1").fetchone()
        return {"ok": True, "version": VERSION}

    @app.post("/api/lease")
    async def lease(body: LeaseIn, request: Request, worker: str = Depends(worker_name)):
        now, ip = utcnow(), client_ip(request)
        with conn:
            scheduler.worker_seen(conn, worker, ip, now, sites=body.worker_sites or body.sites,
                                  version=body.version)
        # Shops whose crawling is paused for this device (by hand, crawl_pauses) get no crawl jobs.
        holds = {s: scheduler.paused(conn, worker, s, now) for s in body.sites}
        active = [s for s in body.sites if holds[s] is None]
        job = scheduler.lease(conn, cfg, worker, ip, active, now) if active else None
        if job is None:
            due = scheduler.next_due(conn, active) if active else None
            wait = 30 if due is None else (due - now).total_seconds()
            reply = {"job": None, "retry_after": int(min(60, max(5, wait)))}
            # Nothing to crawl on this shop now: maybe one product page to read meanwhile (details.py).
            # Never lets a problem here cost the worker its answer.
            if len(body.sites) == 1 and not (holds[body.sites[0]] and holds[body.sites[0]]["pages"]):
                try:
                    if page := detailq.offer(conn, cfg, ip, body.sites[0], now, reply["retry_after"]):
                        reply["detail"] = page
                except Exception:
                    log.exception("product page offer for %s", body.sites[0])
            return reply
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
        watch = cfg.watch(job["grp"])
        floor = (watch.min_price or 0) * 100 if watch else 0
        changes = drops = deals = skipped = 0
        with conn:
            for item in body.listings:
                listing = item.to_listing()
                if listing.site != job["site"]:
                    raise HTTPException(422, f"listing for {listing.site} in a {job['site']} job")
                # Sponsored cards ignore the price filter and searches match accessories, so a
                # product we don't know yet must be priced, at or above the group's minimum, and
                # pass the group's title words. Known products are always updated (stock, price).
                unwanted = (listing.price is None or listing.price < floor
                            or (watch is not None and not watch.accepts(listing.title)))
                if unwanted and db.find_product(conn, listing.site, listing.sku) is None:
                    skipped += 1
                    continue
                # A known product that this group would not accept (a headset the console search
                # also returns) keeps the group that accepted it first.
                rec = db.record(conn, listing, grp=None if unwanted else job["grp"], now=now)
                if listing.gtins:
                    db.record_barcodes(conn, rec.product_id, listing.gtins, "feed", now)
                if rec.status == "changed" or (rec.status == "new" and not bootstrap):
                    changes += 1
                dropped = (rec.status == "new" or (listing.price is not None and (
                    rec.old_price is None or not rec.old_in_stock or listing.price < rec.old_price)))
                # What speeds the slice up: only what could become an alert (a new product, a
                # restock, a drop of DROP_SPEEDUP or more), never rises or small repricing moves.
                if listing.in_stock and (
                        (rec.status == "new" and not bootstrap)
                        or (rec.status == "changed" and listing.price is not None and (
                            rec.old_price is None or not rec.old_in_stock
                            or listing.price <= rec.old_price * (1 - scheduler.DROP_SPEEDUP)))):
                    drops += 1
                if rec.price_moved and dropped and listing.in_stock:
                    deal = alerts.check_product(conn, cfg, rec.product_id, bootstrap=bootstrap, now=now)
                    deals += deal is not None
            scheduler.extend(conn, cfg, job_id, len(body.listings), drops, now)
            if body.no_results and not body.listings:
                # The slice genuinely emptied (amazon: "No results for your search query"),
                # so finding nothing is not a parser failure.
                conn.execute("UPDATE jobs SET expected_total = 0 WHERE id = ?", (job_id,))
            scheduler.unblock(conn, job["site"], ip)
            scheduler.worker_site_ok(conn, worker, job["site"], now, len(body.listings))
            scheduler.worker_seen(conn, worker, ip, now, pages=1, jobs_done=int(body.done))
            if body.done and job["kind"] == "crawl":
                _finish_crawl(job_id, now)
        return {"ok": True, "changes": changes, "deals": deals, "skipped": skipped}

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
            scheduler.worker_site_problem(conn, worker, job["site"], now, f"blocked: {body.reason}", blocked=True)
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
            scheduler.worker_site_problem(conn, worker, job["site"], now, body.reason)
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

    @app.post("/api/logs")
    async def logs_in(body: LogsIn, worker: str = Depends(worker_name)):
        with conn:
            scheduler.add_events(conn, worker, [(e.at, e.level.upper(), e.message) for e in body.events[:500]])
        return {"ok": True}

    def overview(rc: sqlite3.Connection, now: datetime) -> dict:
        problems = [{"name": c.name, "level": c.level, "detail": c.detail}
                    for c in health.run_checks(rc, cfg, now) if c.level != "ok"]
        return {"generated_at": iso(now)[:19], "devices": monitor.devices(rc, now),
                "sites": monitor.sites(rc, now), "problems": problems}

    @app.get("/api/devices", dependencies=[Depends(viewer)])
    def devices_api():
        with closing(read_conn()) as rc:
            return overview(rc, utcnow())

    @app.get("/api/events", dependencies=[Depends(viewer)])
    def events_api(source: str | None = None, level: str | None = None, after_id: int = 0, limit: int = 200):
        with closing(read_conn()) as rc:
            return {"events": monitor.events(rc, source=source, level=level, after_id=after_id, limit=min(limit, 2000))}

    @app.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(viewer)])
    def dashboard(request: Request, source: str | None = None, level: str | None = None):
        with closing(read_conn()) as rc:
            logs = monitor.events(rc, source=source, level=level, limit=300)
            return monitor.render_page(overview(rc, utcnow()), logs, source, request.query_params.get("token"))

    # --- web UI (viewers read; the admin token can also manage subscribers) -------
    revision_file = Path(__file__).resolve().parents[2] / "REVISION"

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/ui")

    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    async def ui(request: Request):
        if not can_view(request):
            return RedirectResponse("/ui/login")
        return webui.APP

    @app.get("/ui/login", response_class=HTMLResponse, include_in_schema=False)
    async def ui_login_page(request: Request, token: str | None = None):
        if token:  # one-click link (make ui): set the cookie, then drop the token from the address bar
            return _login(token)
        return webui.login_page()

    @app.post("/ui/login", include_in_schema=False)
    async def ui_login(request: Request):
        form = await request.body()
        from urllib.parse import parse_qs
        token = (parse_qs(form.decode()).get("token") or [""])[0].strip()
        return _login(token)

    def _login(token: str):
        if token not in view_tokens:
            log.warning("web UI: failed login")
            return HTMLResponse(webui.login_page("Wrong token."), status_code=401)
        response = RedirectResponse("/ui", status_code=303)
        response.set_cookie(webui.COOKIE, token, max_age=30 * 86400, httponly=True, samesite="strict")
        return response

    @app.get("/ui/logout", include_in_schema=False)
    async def ui_logout():
        response = RedirectResponse("/ui/login", status_code=303)
        response.delete_cookie(webui.COOKIE)
        return response

    @app.get("/api/overview", dependencies=[Depends(viewer)])
    def overview_api(request: Request):
        revision = revision_file.read_text().split(" deployed")[0] if revision_file.exists() else ""
        with closing(read_conn()) as rc:
            return {**webui.overview(rc, cfg, utcnow(), revision), "admin": is_admin(request),
                    "pauses": scheduler.active_pauses(rc, utcnow())}

    @app.get("/api/alerts", dependencies=[Depends(viewer)])
    def alerts_api(days: float = 1, kind: str | None = None, limit: int = 300):
        with closing(read_conn()) as rc:
            return {"alerts": webui.alerts(rc, utcnow(), days, kind, min(limit, 2000))}

    @app.get("/api/jobs", dependencies=[Depends(viewer)])
    def jobs_api(limit: int = 400):
        with closing(read_conn()) as rc:
            return {"jobs": webui.jobs(rc, utcnow(), min(limit, 2000))}

    @app.get("/api/products", dependencies=[Depends(viewer)])
    def products_api(q: str = "", site: str | None = None, limit: int = 100, grp: str | None = None,
                     sort: str = "recent", in_stock: int = 0, offset: int = 0, changed_since: str | None = None):
        with closing(read_conn()) as rc:
            page = webui.products(rc, q, site or None, max(0, min(limit, 500)), grp or None, sort, bool(in_stock),
                                  max(0, offset), changed_since or None)
            return {**page, "groups": webui.groups(rc)}

    @app.get("/api/queries", dependencies=[Depends(viewer)])
    def queries_api():
        return {"queries": webui.query_list()}

    @app.get("/api/queries/{name}", dependencies=[Depends(viewer)])
    def query_api(name: str, hours: float = 24, grp: str | None = None, site: str | None = None,
                  sort: str | None = None, desc: int = 0, limit: int = 100, offset: int = 0, total: int | None = None,
                  q: str = ""):
        if name not in webui.QUERIES:
            raise HTTPException(404, "no such query")
        with closing(read_conn()) as rc:
            return webui.run_query(rc, name, utcnow(), max(1.0, min(hours, 24 * 90)), grp or None, site or None,
                                   sort=sort or None, desc=bool(desc), limit=max(1, min(limit, 500)), offset=max(0, offset),
                                   known_total=total, search=q)

    @app.get("/api/groups", dependencies=[Depends(viewer)])
    def groups_api():
        with closing(read_conn()) as rc:
            return {"groups": webui.groups(rc)}

    @app.get("/api/products/{product_id}", dependencies=[Depends(viewer)])
    def product_api(product_id: int, request: Request):
        with closing(read_conn()) as rc:
            found = webui.product(rc, product_id, utcnow())
        if found is None:
            raise HTTPException(404, "no such product")
        return {**found, "admin": is_admin(request)}

    @app.get("/api/subscribers", dependencies=[Depends(viewer)])
    def subscribers_api(request: Request):
        with closing(read_conn()) as rc:
            return {"admin": is_admin(request), "people": webui.subscribers(rc, utcnow())}

    @app.post("/api/subscribers/{chat_id}/{action}", dependencies=[Depends(admin)])
    def subscriber_action(chat_id: int, action: str):
        # A thread of its own (plain def): telling the person on Telegram is a network call.
        if action not in bot.DECISIONS:
            raise HTTPException(404, "no such action")
        with closing(db.connect(cfg.db_path)) as wc:
            sub = wc.execute("SELECT * FROM subscribers WHERE chat_id = ?", (chat_id,)).fetchone()
            if sub is None or sub["is_admin"] or sub["status"] == "removed":
                raise HTTPException(404, "no such person")
            if action in ("approve", "reject") and sub["status"] != "pending":
                raise HTTPException(409, f"already {sub['status']}")
            if action == "promote" and sub["status"] != "active":
                raise HTTPException(409, "only active subscribers can be helper admins")
            tg = alerts.Telegram(cfg.telegram_token) if cfg.telegram_token else None
            bot.decide(wc, tg, sub, action, utcnow())
        return {"ok": True}

    class DetailIn(BaseModel):
        site: str
        status: str  # ok | gone | failed | blocked
        gtins: list[str] = []
        mpns: list[str] = []
        model_name: str | None = None
        specs: dict[str, str] = {}
        reason: str | None = None

    @app.post("/api/details/{product_id}")
    async def detail_done(product_id: int, body: DetailIn, request: Request, worker: str = Depends(worker_name)):
        """What a worker read from a product page (or that the shop refused it: pauses that shop's pages)."""
        detailq.record(conn, cfg, product_id, body.site, client_ip(request), body.model_dump(), utcnow())
        return {"ok": True}

    class PauseIn(BaseModel):
        worker: str = "*"  # a device name, or '*' for all
        site: str = "*"  # a shop, or '*' for all
        hours: float = 2
        pages: bool = False  # also stop product-page reading
        reason: str | None = None

    @app.get("/api/pauses", dependencies=[Depends(viewer)])
    def pauses_api():
        with closing(read_conn()) as rc:
            return {"pauses": scheduler.active_pauses(rc, utcnow())}

    @app.post("/api/pauses", dependencies=[Depends(admin)])
    def pause_api(body: PauseIn):
        """Pause crawling by hand (ends by itself after `hours`)."""
        if body.site != "*" and body.site not in SITES:
            raise HTTPException(422, f"unknown shop {body.site!r}")
        with closing(db.connect(cfg.db_path)) as wc:
            try:
                pid = scheduler.pause(wc, utcnow(), worker=body.worker, site=body.site, hours=body.hours,
                                      pages=body.pages, reason=body.reason or "web UI")
            except ValueError as e:
                raise HTTPException(422, str(e))
        log.info("crawling paused by hand: device %s, shop %s, %.1f h%s", body.worker, body.site, body.hours,
                 " (product pages too)" if body.pages else "")
        return {"ok": True, "id": pid}

    @app.post("/api/pauses/{pause_id}/resume", dependencies=[Depends(admin)])
    def resume_api(pause_id: int):
        with closing(db.connect(cfg.db_path)) as wc:
            n = scheduler.resume(wc, utcnow(), pause_id=pause_id)
        if not n:
            raise HTTPException(404, "no such active pause")
        log.info("crawling resumed by hand (pause %d)", pause_id)
        return {"ok": True}

    class MatchIn(BaseModel):
        other: int
        verdict: str | None  # 'same', 'different', or None to forget a hand-made match

    @app.post("/api/products/{product_id}/match", dependencies=[Depends(admin)])
    def match_override(product_id: int, body: MatchIn):
        """Set by hand that two listings are (or are not) the same product; re-matched within a minute."""
        if product_id == body.other:
            raise HTTPException(422, "a product is always the same as itself")
        with closing(db.connect(cfg.db_path)) as wc:
            if wc.execute("SELECT COUNT(*) FROM products WHERE id IN (?, ?)", (product_id, body.other)).fetchone()[0] != 2:
                raise HTTPException(404, "no such product")
            try:
                matching.set_override(wc, product_id, body.other, body.verdict, "web UI", utcnow())
            except ValueError as e:
                raise HTTPException(422, str(e))
        if getattr(app.state, "watchdog", None):
            app.state.watchdog.next_match = None  # the next watchdog round (within a minute) re-matches
        return {"ok": True}

    @app.post("/api/report", dependencies=[Depends(admin)])
    def report_now():
        """Queue the daily report for the admins now (the alert sender delivers it)."""
        now = utcnow()
        with closing(db.connect(cfg.db_path)) as wc, wc:
            alerts.system_alert(wc, f"report-now:{iso(now)}", health.daily_report(wc, cfg, now), now,
                                timedelta(0), raw_html=True)
        return {"ok": True}

    @app.get("/api/rules", dependencies=[Depends(viewer)])
    def rules_api():
        return webui.rules(cfg)

    return app


def _site(name: str) -> str:
    return alerts.SITE_NAME.get(name, name)


class Watchdog(threading.Thread):
    """Notices problems nobody reports (stalled sites, silent workers, low disk), sends the
    admin's daily report, does daily maintenance (compressed backup, planner stats), and
    matches products across shops every hour."""

    MATCH_EVERY = timedelta(hours=1)

    def __init__(self, cfg: Config, stop: threading.Event, interval: float = 60):
        super().__init__(name="watchdog", daemon=True)
        self.cfg, self.stop_event, self.interval = cfg, stop, interval
        self.next_match: datetime | None = None  # first run on the first round after a start

    def run(self) -> None:
        conn = db.connect(self.cfg.db_path)
        while not self.stop_event.wait(self.interval):
            try:
                self.check(conn, utcnow())
            except Exception:
                log.exception("watchdog check failed")
            try:  # on its own: a matching bug must not stop the checks above
                self.match(conn, utcnow())
            except Exception:
                log.exception("product matching failed")

    def match(self, conn: sqlite3.Connection, now: datetime) -> None:
        """Recompute which listings are the same product (matching.py), in a low-priority process."""
        if self.next_match is not None and now < self.next_match:
            return
        self.next_match = now + self.MATCH_EVERY
        s = matching.rebuild_in_process(self.cfg.db_path)
        log.info("matching: %d titles read; %d of %d products in a model, %d of them on 2+ shops; %d rows changed in %.1fs",
                 s.extracted, s.in_model, s.products, s.cross_shop, s.changed, s.seconds)

    def check(self, conn: sqlite3.Connection, now: datetime) -> None:
        with conn:
            for row in conn.execute(
                    """SELECT site, MIN(next_due_at) AS oldest_due, MAX(last_done_at) AS last_done
                       FROM jobs GROUP BY site""").fetchall():
                if scheduler.site_paused(conn, row["site"], now):
                    continue  # paused by hand: quiet on purpose
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
            usage = shutil.disk_usage(self.cfg.data_dir)
            if usage.free / usage.total < 0.1:
                alerts.system_alert(conn, "disk", f"المساحة على الخادم أوشكت على النفاد: "
                                                  f"{usage.free / 1e9:.1f} GB متبقية", now, timedelta(hours=12))
            if key := health.report_due(conn, now):
                alerts.system_alert(conn, key, health.daily_report(conn, self.cfg, now), now,
                                    timedelta(hours=20), raw_html=True)
        self.maintain(conn, now)

    def maintain(self, conn: sqlite3.Connection, now: datetime, keep: int = 7) -> None:
        """Once a day: compressed backup (7 kept) and fresh query-planner statistics."""
        folder = self.cfg.data_dir / "backups"
        target = folder / f"deals-{now:%Y-%m-%d}.db.gz"
        if target.exists():
            return
        db.backup(conn, target)
        for old in sorted(folder.glob("deals-*.db.gz"))[:-keep] + sorted(folder.glob("deals-*.db")):
            old.unlink()  # (uncompressed ones are from before backups were compressed)
        monitor.archive_events(conn, self.cfg.data_dir / "logs", now.date())  # before pruning: kept 90 days
        conn.execute("DELETE FROM events WHERE at < ?", (iso(now - timedelta(days=7)),))
        conn.commit()
        conn.execute("PRAGMA optimize")
        log.info("daily maintenance done: backup %s (%.1f MB)", target.name, target.stat().st_size / 1e6)


def serve(cfg: Config) -> None:
    import uvicorn

    # Restarts must be quick: an open browser tab or a long poll must not hold a deploy for
    # systemd's 90 s kill timeout (SQLite in WAL mode is safe either way).
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="info", access_log=False,
                timeout_graceful_shutdown=10)
