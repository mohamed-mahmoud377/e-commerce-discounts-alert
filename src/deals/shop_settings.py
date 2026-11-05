"""What the admins switch per shop from the web UI (Settings page, and each shop's page).

Everything here lives in the database, takes effect at once and stays until changed, with no restart and
no change on the workers: the coordinator just stops handing out work, or stops writing alerts.

- crawl: whole-shop on/off. Off also stops the jobs in progress, at their next page (their leases go).
- device_shops: one device off for one shop. A device's DEALS_SITES is what it *may* crawl, so a shop has to
  be added on the device first; from here it can only be taken away and given back.
- alerts: 'on'; 'silent' (written and shown in the web UI, nothing reaches Telegram); 'off' (not even written).
- screenshots, pages (product-page reading), pace (delays times 1, 2 or 4: only ever slower than the config).
- min_drop, min_price: this shop's own floor for alerts, checked after the deal rules, so they only remove
  alerts and never make a new one.

Unlike a pause (scheduler.pause) these never end by themselves, so `deals health`, the daily report and the
web UI keep naming whatever is off (summary).
"""

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime

from deals import shops
from deals.db import iso

ALERT_MODES = ("on", "silent", "off")
PACES = (1, 2, 4)
NOTE_MAX = 300
SILENT = "silent: this shop's alerts are not sent"  # alerts.error of an alert kept but not sent


@dataclass(frozen=True)
class Settings:
    crawl: bool = True
    alerts: str = "on"
    screenshots: bool = True
    pages: bool = True
    pace: int = 1
    min_drop: float | None = None  # 0.25 = at least 25% off
    min_price: int | None = None  # EGP
    note: str = ""
    updated_at: str | None = None
    updated_by: str | None = None


def get(conn: sqlite3.Connection, site: str) -> Settings:
    row = conn.execute("SELECT * FROM shop_settings WHERE site = ?", (site,)).fetchone()
    return _settings(row) if row else Settings()


def _settings(row: sqlite3.Row) -> Settings:
    return Settings(bool(row["crawl"]), row["alerts"], bool(row["screenshots"]), bool(row["pages"]), row["pace"],
                    row["min_drop"], row["min_price"], row["note"] or "", row["updated_at"], row["updated_by"])


def device_off(conn: sqlite3.Connection, worker: str, site: str) -> bool:
    return conn.execute("SELECT 1 FROM device_shops WHERE worker = ? AND site = ?", (worker, site)).fetchone() is not None


def stopped(conn: sqlite3.Connection, worker: str, site: str) -> str | None:
    """Why this device must not crawl this shop, if it was switched off (worker '*': the shop as a whole)."""
    s = get(conn, site)
    if not s.crawl:
        return "shop switched off" + (f": {s.note}" if s.note else "")
    if worker != "*" and device_off(conn, worker, site):
        return "switched off for this device"
    return None


def update(conn: sqlite3.Connection, sites: list[str], changes: dict, who: str, now: datetime) -> None:
    """Change some settings of these shops, and act on what the change means for work already under way."""
    with conn:
        for site in sites:
            old = get(conn, site)
            new = {**asdict(old), **changes, "updated_at": iso(now), "updated_by": who}
            conn.execute(
                """INSERT OR REPLACE INTO shop_settings (site, crawl, alerts, screenshots, pages, pace, min_drop, min_price,
                                                         note, updated_at, updated_by)
                   VALUES (:site, :crawl, :alerts, :screenshots, :pages, :pace, :min_drop, :min_price, :note,
                           :updated_at, :updated_by)""", {**new, "site": site})
            if old.crawl and not new["crawl"]:
                _drop_leases(conn, site)
            elif new["crawl"] and not old.crawl:
                _due_now(conn, site, now)
            if old.alerts == "on" and new["alerts"] != "on":
                _cancel_queued(conn, site, now)
            if old.screenshots and not new["screenshots"]:
                _drop_shots(conn, site)


def set_device(conn: sqlite3.Connection, worker: str, site: str, on: bool, who: str, now: datetime) -> None:
    with conn:
        if on:
            if conn.execute("DELETE FROM device_shops WHERE worker = ? AND site = ?", (worker, site)).rowcount:
                _due_now(conn, site, now)
        else:
            conn.execute("INSERT OR REPLACE INTO device_shops (worker, site, updated_at, updated_by) VALUES (?, ?, ?, ?)",
                         (worker, site, iso(now), who))
            _drop_leases(conn, site, worker)


def _drop_leases(conn: sqlite3.Connection, site: str, worker: str | None = None) -> None:
    """The worker's next page gets a 409 and it drops the job; the slice stays due. Nothing waits out a lease."""
    conn.execute("""UPDATE jobs SET lease_worker = NULL, lease_ip = NULL, lease_token = NULL, lease_expires_at = NULL
                    WHERE site = ? AND lease_token IS NOT NULL AND (? IS NULL OR lease_worker = ?)""", (site, worker, worker))


def _due_now(conn: sqlite3.Connection, site: str, now: datetime) -> None:
    """Slices left overdue while it was off count from now, or the shop reads as stalled the moment it is back."""
    conn.execute("UPDATE jobs SET next_due_at = ? WHERE site = ? AND next_due_at < ?", (iso(now), site, iso(now)))


# A shop's automatic alerts (the admins' own and system messages are not this switch's business).
_ITS_ALERTS = ("(SELECT a.id FROM alerts a JOIN products p ON p.id = a.product_id "
               "WHERE p.site = ? AND a.kind NOT IN ('manual', 'system'))")


def _cancel_queued(conn: sqlite3.Connection, site: str, now: datetime) -> None:
    """Whoever silences a shop is usually stopping a wave of bad alerts: what has not gone out stays in.
    Alerts sent by hand stay queued, and so do cards already in the review group: a person decides those."""
    conn.execute(f"""UPDATE alerts SET sent_at = ?, confirm_until = NULL, error = ?
                     WHERE sent_at IS NULL AND id IN {_ITS_ALERTS}""", (iso(now), SILENT, site))
    conn.execute(f"DELETE FROM deliveries WHERE sent_at IS NULL AND alert_id IN {_ITS_ALERTS}", (site,))
    conn.execute(f"""UPDATE reviews SET status = 'skipped', reason = ?
                     WHERE status = 'pending' AND message_id IS NULL AND alert_id IN {_ITS_ALERTS}""", (SILENT, site))
    conn.execute(f"""UPDATE shots SET status = 'none', error = ?
                     WHERE status IN ('pending', 'taking') AND alert_id IN {_ITS_ALERTS}""", (SILENT, site))


def _drop_shots(conn: sqlite3.Connection, site: str) -> None:
    conn.execute("UPDATE shots SET status = 'none', error = ? WHERE site = ? AND status IN ('pending', 'taking')",
                 ("switched off in the shop's settings", site))


def listing(conn: sqlite3.Connection) -> list[dict]:
    """Every shop's settings, in SHOPS order."""
    rows = {r["site"]: _settings(r) for r in conn.execute("SELECT * FROM shop_settings")}
    return [{"name": name, **asdict(rows.get(name, Settings()))} for name in shops.SHOPS]


def devices_off(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT worker, site FROM device_shops ORDER BY worker, site")]


def summary(conn: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """What is switched off, in English and in Arabic: these don't end by themselves, so they stay in view."""
    en, ar = [], []
    for s in listing(conn):
        name, arabic = s["name"], shops.arabic(s["name"])
        if not s["crawl"]:
            en.append(f"{name} crawling off")
            ar.append(f"{arabic}: الجمع متوقف")
        if s["alerts"] != "on":
            en.append(f"{name} alerts {'kept, not sent' if s['alerts'] == 'silent' else 'off'}")
            ar.append(f"{arabic}: " + ("التنبيهات تُحفظ ولا تُرسل" if s["alerts"] == "silent" else "التنبيهات متوقفة"))
        if s["pace"] > 1:
            en.append(f"{name} pace x{s['pace']}")
            ar.append(f"{arabic}: الجمع أبطأ {s['pace']} مرات")
    for d in devices_off(conn):
        en.append(f"{d['site']} off on {d['worker']}")
        ar.append(f"{shops.arabic(d['site'])}: متوقف على الجهاز {d['worker']}")
    return en, ar
