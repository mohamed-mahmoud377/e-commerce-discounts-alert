"""A worker: leases jobs from the coordinator, fetches pages, reports results.

Run as many as you like, on any machine that can reach the coordinator. Each one
only does the sites it is told to. Failures are handled locally where possible:
- coordinator unreachable  -> keep retrying with backoff (nothing is lost; the job
                              lease just expires and is re-offered)
- network error on a site  -> retry the page a few times, then report 'failed'
- site challenge / block   -> report 'blocked'; the coordinator pauses this IP for
                              that site and gives the job to someone else
- lease lost (409)         -> drop the job and ask for another
- Ctrl+C / SIGTERM         -> release the current job and exit cleanly
"""

import logging
import random
import signal
import threading
import time
from dataclasses import asdict
from importlib.metadata import version as pkg_version
from pathlib import Path

from curl_cffi import requests

from deals import planner
from deals.models import Blocked, Listing, PageResult, Slice, UnknownTarget
from deals.sites import SITES

log = logging.getLogger(__name__)
VERSION = pkg_version("deals")
PAGE_RETRIES = (5, 20, 60)  # seconds to wait before each retry of a failing page


class LeaseLost(Exception):
    pass


class Coordinator:
    def __init__(self, base_url: str, token: str, stop: threading.Event):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.stop = stop

    def call(self, path: str, payload: dict) -> dict:
        """POST with retries until it works, the lease is gone, or we're stopping."""
        delay = 2.0
        while True:
            try:
                r = requests.post(self.base_url + path, json=payload, headers=self.headers, timeout=30)
            except Exception as exc:  # connection refused, DNS, timeout...
                problem = f"coordinator unreachable ({exc.__class__.__name__})"
            else:
                if r.status_code == 409:
                    raise LeaseLost(r.text)
                if r.status_code == 401:
                    raise SystemExit("coordinator rejected the worker token; check DEALS_TOKEN")
                if r.status_code < 500:
                    r.raise_for_status()
                    return r.json()
                problem = f"coordinator HTTP {r.status_code}"
            if self.stop.is_set():
                raise LeaseLost("stopping")
            log.warning("%s; retrying in %.0fs", problem, delay)
            self.stop.wait(delay)
            delay = min(delay * 2, 60)


class Worker:
    def __init__(self, coordinator_url: str, token: str, sites: list[str], data_dir: Path,
                 site_factory=None):
        unknown = set(sites) - set(SITES)
        if unknown:
            raise ValueError(f"unknown sites {sorted(unknown)}; choose from {sorted(SITES)}")
        self.stop = threading.Event()
        self.api = Coordinator(coordinator_url, token, self.stop)
        self.sites = sites
        self.data_dir = data_dir
        self._site_factory = site_factory or (lambda name: SITES[name](data_dir))
        self._site_objects = {}

    def site(self, name: str):
        if name not in self._site_objects:
            self._site_objects[name] = self._site_factory(name)
        return self._site_objects[name]

    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: self.stop.set())

    def run(self) -> None:
        """One loop per site, in parallel: a slow Amazon crawl never holds up noon."""
        log.info("worker %s started for %s", VERSION, ", ".join(self.sites))
        loops = [threading.Thread(target=self._loop, args=([site],), name=site, daemon=True)
                 for site in self.sites]
        for t in loops:
            t.start()
        while any(t.is_alive() for t in loops):
            for t in loops:
                t.join(timeout=0.5)
        log.info("worker stopped")

    def _loop(self, sites: list[str]) -> None:
        while not self.stop.is_set():
            try:
                reply = self.api.call("/api/lease", {"sites": sites, "worker_sites": self.sites,
                                                      "version": VERSION})
            except LeaseLost:
                break
            job = reply.get("job")
            if job is None:
                self.stop.wait(reply.get("retry_after", 30))
                continue
            self.run_job(job)
            # same politeness between jobs as between pages
            self.stop.wait(random.uniform(job["min_delay"], job["max_delay"]))

    def run_job(self, job: dict) -> None:
        log.info("%s %s %s", job["kind"], job["site"], job["target"])
        token = job["lease_token"]
        path = f"/api/jobs/{job['id']}"
        try:
            if job["kind"] == "plan":
                self._plan(job, path)
            else:
                self._crawl(job, path)
        except LeaseLost:
            log.info("lease lost for job %s; moving on", job["id"])
        except Blocked as exc:
            log.warning("blocked: %s", exc)
            self._report(path + "/blocked", token, str(exc))
        except _Stopping:
            self._report(path + "/release", token, "worker stopping")
        except Exception as exc:
            log.exception("job %s failed", job["id"])
            self._report(path + "/failed", token, f"{exc.__class__.__name__}: {exc}")

    def _crawl(self, job: dict, path: str) -> None:
        site, target = self.site(job["site"]), Slice.from_key(job["target"])
        page = 1
        while True:
            result = self._fetch(site, target, page, job)
            self.api.call(path + "/page", {
                "lease_token": job["lease_token"], "page": page,
                "listings": [_listing_json(x) for x in result.listings], "done": not result.has_next,
            })
            if not result.has_next:
                return
            page += 1
            self._pause(job)

    def _plan(self, job: dict, path: str) -> None:
        site = self.site(job["site"])
        probes = 0

        def report_listings(listings: list[Listing]) -> None:
            nonlocal probes
            probes += 1
            self.api.call(path + "/page", {"lease_token": job["lease_token"], "page": probes,
                                           "listings": [_listing_json(x) for x in listings]})
            if self.stop.is_set():
                raise _Stopping()

        planned = planner.plan(
            _RetryingSite(site, self, job), Slice.from_key(job["root"]), on_listings=report_listings,
            delay=(job["min_delay"], job["max_delay"]), report=log.info,
        )
        self.api.call(path + "/plan", {
            "lease_token": job["lease_token"],
            "slices": [{"target": p.slice.key(), "total": p.total, "oversized": p.oversized} for p in planned],
        })

    def _fetch(self, site, target: Slice, page: int, job: dict) -> PageResult:
        for attempt, wait in enumerate((*PAGE_RETRIES, None), start=1):
            if self.stop.is_set():
                raise _Stopping()
            try:
                return site.fetch_page(target, page)
            except (Blocked, UnknownTarget):
                raise
            except Exception as exc:
                if wait is None:
                    raise
                log.warning("%s page %d attempt %d failed (%s); retrying in %ds",
                            job["site"], page, attempt, exc, wait)
                self.stop.wait(wait)
        raise AssertionError("unreachable")

    def _pause(self, job: dict) -> None:
        if self.stop.wait(random.uniform(job["min_delay"], job["max_delay"])):
            raise _Stopping()

    def _report(self, path: str, token: str, reason: str) -> None:
        try:
            # Don't retry forever on shutdown: a lost report just means the lease expires.
            requests.post(self.api.base_url + path, json={"lease_token": token, "reason": reason[:500]},
                          headers=self.api.headers, timeout=15)
        except Exception as exc:
            log.warning("could not report to coordinator: %s", exc)


class _Stopping(Exception):
    pass


class _RetryingSite:
    """Gives the planner the same page retries the crawler gets."""

    def __init__(self, site, worker: Worker, job: dict):
        self.site, self.worker, self.job = site, worker, job
        self.name = site.name
        self.max_slice_total = site.max_slice_total

    def fetch_page(self, target: Slice, page: int) -> PageResult:
        return self.worker._fetch(self.site, target, page, self.job)


def _listing_json(listing: Listing) -> dict:
    data = asdict(listing)
    data["badges"] = list(listing.badges)
    return data


def run(coordinator_url: str, token: str, sites: list[str], data_dir: Path) -> None:
    worker = Worker(coordinator_url, token, sites, data_dir)
    worker.install_signal_handlers()
    worker.run()
