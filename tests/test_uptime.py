from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from deals import db, scheduler, uptime
from deals.coordinator import create_app
from deals.db import iso, utcnow


@pytest.fixture
def client(cfg):
    app = create_app(cfg, background=False)
    with TestClient(app) as c:
        c.conn = app.state.conn
        yield c


W1 = {"Authorization": "Bearer token-1"}


def lease(client):
    return client.post("/api/lease", json={"sites": ["amazon_eg"]}, headers=W1).json()["job"]


def plan_amazon(client):
    job = lease(client)
    client.post(f"/api/jobs/{job['id']}/plan", headers=W1, json={
        "lease_token": job["lease_token"], "slices": [{"target": t, "total": 50}
                                    for t in ("category:111|price:1000-5000", "category:111|price:5000-")]})


def listing(sku):
    return {"site": "amazon_eg", "sku": sku, "title": f"TV {sku}", "url": f"https://x/{sku}", "price": 500000,
            "list_price": None, "currency": "EGP", "in_stock": True}


def track(conn, now, **kw):
    """A watchdog round, as if the one before ran a minute ago (a long gap means the server was down)."""
    with conn:
        conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES ('uptime:last_round', ?)",
                     (iso(now - timedelta(minutes=1)),))
    return uptime.track(conn, now, **kw)


def system_texts(conn):
    return [r["message"] for r in conn.execute("SELECT message FROM alerts WHERE kind = 'system' ORDER BY id")]


def state(conn, subject):
    return conn.execute("SELECT state FROM status WHERE subject = ?", (subject,)).fetchone()["state"]


def setup(cfg, now):
    conn = db.connect(cfg.db_path)
    scheduler.sync_watches(conn, cfg, now)
    with conn:
        scheduler.worker_seen(conn, "mac", "1.1.1.1", now, sites=["amazon_eg"])
        conn.execute("UPDATE jobs SET last_done_at = ?, next_due_at = ?", (iso(now), iso(now + timedelta(minutes=30))))
    track(conn, now)  # first sight: quiet
    return conn


def test_a_stalled_shop_is_told_when_it_goes_down_and_when_it_is_back(cfg):
    now = utcnow()
    conn = setup(cfg, now)
    assert state(conn, "shop:amazon_eg") == "up" and system_texts(conn) == []

    down = now + timedelta(hours=2)
    with conn:  # nothing finished since, and the work is overdue
        scheduler.worker_seen(conn, "mac", "1.1.1.1", down)
    assert track(conn, down) == ["shop:amazon_eg", "shop:noon_eg"]
    assert state(conn, "shop:amazon_eg") == "down"
    assert any(t.startswith("🔴") and "متوقف" in t for t in system_texts(conn))
    assert track(conn, down + timedelta(minutes=1)) == []  # still down: told once

    back = down + timedelta(minutes=50)
    with conn:
        scheduler.worker_seen(conn, "mac", "1.1.1.1", back)
        conn.execute("UPDATE jobs SET last_done_at = ? WHERE site = 'amazon_eg'", (iso(back),))
    assert track(conn, back) == ["shop:amazon_eg"]
    assert system_texts(conn)[-1].startswith("✅") and "عاد للعمل" in system_texts(conn)[-1]
    change = conn.execute("SELECT * FROM status_changes WHERE subject = 'shop:amazon_eg' ORDER BY id DESC").fetchone()
    assert (change["old_state"], change["new_state"], change["lasted_s"], change["notified"]) == ("down", "up", 3000, 1)


def test_a_device_going_offline_and_coming_back(cfg):
    now = utcnow()
    conn = setup(cfg, now)
    later = now + timedelta(minutes=40)
    with conn:
        conn.execute("UPDATE jobs SET last_done_at = ?, next_due_at = ?", (iso(later), iso(later + timedelta(minutes=30))))
    track(conn, later)
    assert state(conn, "device:mac") == "offline" and state(conn, "device_shop:mac/amazon_eg") == "offline"
    assert "الجهاز mac" in system_texts(conn)[-1]
    back = later + timedelta(minutes=20)
    with conn:
        scheduler.worker_seen(conn, "mac", "1.1.1.1", back)
    track(conn, back)
    assert state(conn, "device:mac") == "online" and state(conn, "device_shop:mac/amazon_eg") == "ok"
    assert system_texts(conn)[-1] == "✅ الجهاز mac عاد للاتصال بعد انقطاع دام 20 دقيقة."
    assert uptime.outages(conn, back)[0]["name"] == "mac"


def test_nothing_is_marked_down_while_workers_reconnect_after_a_start(cfg):
    now = utcnow()
    conn = setup(cfg, now)
    later = now + timedelta(hours=3)
    assert uptime.track(conn, later, settling=True) == []
    assert state(conn, "device:mac") == "online" and system_texts(conn)[-1].startswith("✅ الخادم")  # the gap


def test_the_server_says_it_is_back_after_an_outage(cfg):
    now = utcnow()
    conn = setup(cfg, now)
    uptime.track(conn, now + timedelta(minutes=5))
    assert system_texts(conn) == []  # a normal gap between rounds
    uptime.track(conn, now + timedelta(minutes=35), settling=True)
    assert system_texts(conn)[-1].startswith("✅ الخادم عاد للعمل بعد توقف دام 30 دقيقة")
    row = conn.execute("SELECT * FROM status_changes WHERE kind = 'server'").fetchone()
    assert (row["old_state"], row["new_state"], row["lasted_s"]) == ("down", "up", 1800)


def test_a_shop_paused_while_down_does_not_claim_to_be_back(cfg):
    now = utcnow()
    conn = setup(cfg, now)
    down = now + timedelta(hours=2)
    with conn:
        scheduler.worker_seen(conn, "mac", "1.1.1.1", down)
    track(conn, down)
    told = len(system_texts(conn))
    scheduler.pause(conn, down, site="amazon_eg", hours=1)
    track(conn, down + timedelta(minutes=1))
    assert state(conn, "shop:amazon_eg") == "paused" and len(system_texts(conn)) == told


def test_a_device_shop_block_is_announced_back_only_after_the_three_strike_warning(client):
    conn = client.conn
    plan_amazon(client)
    now = utcnow()
    track(conn, now)
    for strike in range(3):
        with conn:  # the pause after each strike has passed
            conn.execute("UPDATE blocks SET until = ?", (iso(now),))
        job = lease(client)
        client.post(f"/api/jobs/{job['id']}/blocked", headers=W1, json={"lease_token": job["lease_token"], "reason": "captcha"})
        track(conn, utcnow())
    assert state(conn, "device_shop:w1/amazon_eg") == "blocked"
    assert conn.execute("SELECT notified FROM status WHERE subject = 'device_shop:w1/amazon_eg'").fetchone()[0] == 1
    with conn:
        conn.execute("UPDATE blocks SET until = ?", (iso(now),))
    job = lease(client)
    client.post(f"/api/jobs/{job['id']}/page", headers=W1,
                json={"lease_token": job["lease_token"], "page": 1, "listings": [listing("A1")], "done": False})
    track(conn, utcnow())
    assert state(conn, "device_shop:w1/amazon_eg") == "ok"
    assert "لم يعد يحظر الجهاز w1" in system_texts(conn)[-1]
