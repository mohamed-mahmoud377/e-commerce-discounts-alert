import gzip
import shutil
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from deals import alerts, bot, db, health, scheduler
from deals.db import iso, utcnow
from deals.models import Listing


def levels(checks):
    return {c.name: c.level for c in checks}


def test_fresh_install_reports_what_is_missing(cfg):
    conn = db.connect(cfg.db_path)
    got = levels(health.run_checks(conn, cfg, utcnow()))
    assert got["database"] == "ok"
    assert got["telegram"] == "fail"  # not configured
    assert got["workers"] == "fail"  # nobody connected yet


def test_healthy_system(cfg, monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda _: shutil._ntuple_diskusage(100, 50, 50))
    cfg = replace(cfg, telegram_token="t", telegram_chat_id="100")
    conn = db.connect(cfg.db_path)
    now = utcnow()
    bot.ensure_admin(conn, cfg, now)
    scheduler.sync_watches(conn, cfg, now)
    with conn:
        scheduler.worker_seen(conn, "mac", "1.2.3.4", now, sites=["amazon_eg", "noon_eg"])
        conn.execute("UPDATE jobs SET last_done_at = ?, next_due_at = ?", (iso(now), iso(now + timedelta(minutes=30))))
        db.record(conn, Listing("amazon_eg", "A1", "TV", "https://x", 500000, None, "EGP", True), grp="TVs", now=now)
    db.backup(conn, cfg.data_dir / "backups" / f"deals-{now:%Y-%m-%d}.db.gz")
    checks = health.run_checks(conn, cfg, now)
    assert all(c.level == "ok" for c in checks), [c for c in checks if c.level != "ok"]


def test_stalled_site_and_silent_worker_are_flagged(cfg):
    conn = db.connect(cfg.db_path)
    now = utcnow()
    scheduler.sync_watches(conn, cfg, now)
    with conn:
        scheduler.worker_seen(conn, "mac", "1.2.3.4", now - timedelta(hours=2))
        conn.execute("UPDATE jobs SET last_done_at = ?", (iso(now - timedelta(hours=5)),))
    got = levels(health.run_checks(conn, cfg, now))
    assert got["worker mac"] == "fail" and got["crawl amazon_eg"] == "fail"


def test_daily_report_once_per_morning_in_cairo(cfg):
    conn = db.connect(cfg.db_path)
    early = datetime(2026, 10, 26, 5, 0, tzinfo=UTC)  # 08:00 in Cairo (UTC+3)
    later = datetime(2026, 10, 26, 6, 30, tzinfo=UTC)  # 09:30 in Cairo
    assert health.report_due(conn, early) is None
    key = health.report_due(conn, later)
    assert key == "report:2026-10-26"
    with conn:
        alerts.system_alert(conn, key, health.daily_report(conn, cfg, later), later, raw_html=True)
    assert health.report_due(conn, later + timedelta(hours=3)) is None
    message = conn.execute("SELECT message FROM alerts WHERE dedupe_key = ?", (key,)).fetchone()["message"]
    assert message.startswith("📋 <b>التقرير اليومي</b>") and "منتجات نتابعها" in message


def test_compressed_backup_restores(cfg, tmp_path):
    conn = db.connect(cfg.db_path)
    with conn:
        db.record(conn, Listing("noon_eg", "N1@x", "Fridge", "https://x", 900000, None, "EGP", True))
    dest = tmp_path / "b" / "deals-test.db.gz"
    db.backup(conn, dest)
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(dest.read_bytes()))
    assert sqlite3.connect(restored).execute("SELECT title FROM products").fetchone()[0] == "Fridge"
