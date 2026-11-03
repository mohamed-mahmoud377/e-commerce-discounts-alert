"""The coordinator's side of alert screenshots (shots.py): which worker takes one, and the image until
Telegram has it.

A deal alert that is ready to go out gets a `shots` row when a worker that takes screenshots crawls that
shop. The next reply to that worker for the shop (usually the reply to the very page upload that revealed
the deal) carries the job; the alert sender holds the alert until the image arrives or
`alerts.screenshot_wait_seconds` pass, then sends the photo and the text. The image lives only in this
process's memory: once Telegram has it, its file_id is kept instead, and a restart just means the alert
goes out as text.
"""

import threading
import time
from datetime import datetime, timedelta

from deals.config import Config
from deals.db import iso
from deals.shots import SPECS

LEASE = timedelta(seconds=45)  # a worker's time to take it, before another may try
MAX_TRIES = 2
SHOOTER_SEEN = timedelta(minutes=10)  # a screenshot-taking worker is "there" for a shop this long after it asked
KEEP_SECONDS = 3 * 3600  # an image nobody sent (Telegram down) is dropped after this long
MAX_IMAGES = 40  # about 6 MB at most
MAX_BYTES = 2_000_000


class _Images:
    """alert id -> JPEG bytes, shared by the API (which receives them) and the alert sender."""

    def __init__(self):
        self._lock = threading.Lock()
        self._images: dict[int, tuple[bytes, float]] = {}

    def put(self, alert_id: int, image: bytes) -> None:
        with self._lock:
            now = time.monotonic()
            self._images = {k: v for k, v in self._images.items() if now - v[1] < KEEP_SECONDS}
            self._images[alert_id] = (image, now)
            while len(self._images) > MAX_IMAGES:
                del self._images[min(self._images, key=lambda k: self._images[k][1])]

    def get(self, alert_id: int) -> bytes | None:
        with self._lock:
            found = self._images.get(alert_id)
            return found[0] if found else None

    def drop(self, alert_id: int) -> None:
        with self._lock:
            self._images.pop(alert_id, None)

    def clear(self) -> None:
        with self._lock:
            self._images.clear()


IMAGES = _Images()
_shooters: dict[str, datetime] = {}  # shop -> when a screenshot-taking worker last asked for its work


def seen_shooter(sites: list[str], now: datetime) -> None:
    for s in sites:
        _shooters[s] = now


def can_shoot(site: str, now: datetime) -> bool:
    last = _shooters.get(site)
    return last is not None and now - last < SHOOTER_SEEN


def add(conn, cfg: Config, alert_id: int, site: str, url: str, now: datetime, *, force: bool = False,
        wait_seconds: float | None = None) -> bool:
    """Ask for a screenshot of an alert that is about to go out (no-op when nobody can take it: then
    holding the alert would only delay it)."""
    if not (cfg.alerts.screenshots and site in SPECS and (force or can_shoot(site, now))):
        return False
    wait = cfg.alerts.screenshot_wait_seconds if wait_seconds is None else wait_seconds
    conn.execute("INSERT OR IGNORE INTO shots (alert_id, site, url, status, created_at, wait_until) "
                 "VALUES (?, ?, ?, 'pending', ?, ?)", (alert_id, site, url, iso(now), iso(now + timedelta(seconds=wait))))
    return True


def lease(conn, sites: list[str], worker: str, now: datetime) -> dict | None:
    """The oldest screenshot still worth taking on one of these shops, for this worker."""
    if not sites:
        return None
    row = conn.execute(
        f"""SELECT alert_id, site, url FROM shots WHERE site IN ({','.join('?' * len(sites))}) AND wait_until > ?
              AND (status = 'pending' OR (status = 'taking' AND leased_until < ? AND attempts < ?))
            ORDER BY created_at LIMIT 1""", (*sites, iso(now), iso(now), MAX_TRIES)).fetchone()
    if row is None:
        return None
    with conn:
        conn.execute("UPDATE shots SET status = 'taking', worker = ?, leased_until = ?, attempts = attempts + 1 "
                     "WHERE alert_id = ?", (worker, iso(now + LEASE), row["alert_id"]))
    return {"alert_id": row["alert_id"], "site": row["site"], "url": row["url"]}


def record(conn, alert_id: int, worker: str, image: bytes | None, reason: str | None, now: datetime) -> str:
    """A worker's screenshot (or why there is none). Kept in memory only if the alert still waits for it."""
    row = conn.execute("SELECT status, wait_until FROM shots WHERE alert_id = ?", (alert_id,)).fetchone()
    if row is None:
        return "unknown"
    if row["status"] == "ok":
        return "ok"  # a retried upload
    sent = conn.execute("SELECT 1 FROM deliveries WHERE alert_id = ? AND sent_at IS NOT NULL LIMIT 1",
                        (alert_id,)).fetchone()
    if image is None:
        status = "none"
    elif sent or iso(now) > row["wait_until"]:
        status = "late"  # the alert already went out as text
    else:
        status = "ok"
        IMAGES.put(alert_id, image)
    with conn:
        conn.execute("UPDATE shots SET status = ?, worker = ?, taken_at = ?, bytes = ?, error = ? WHERE alert_id = ?",
                     (status, worker, iso(now), len(image) if image else None, (reason or "")[:300] or None, alert_id))
    return status
