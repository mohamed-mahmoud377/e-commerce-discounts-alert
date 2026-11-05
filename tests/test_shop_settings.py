"""What the admins switch per shop from the web UI: crawling (whole shop, or one device), alerts, pace."""
import threading
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from deals import alerts, db, detailq, health, scheduler, shop_settings, shotq, uptime
from deals.coordinator import create_app
from deals.db import iso, utcnow
from deals.models import Listing
from test_coordinator import W1, W2, lease, listing, plan_amazon

ADMIN = {"Authorization": "Bearer admin-secret"}


@pytest.fixture
def client(cfg):
    app = create_app(replace(cfg, admin_token="admin-secret"), background=False)
    with TestClient(app) as c:
        c.conn = app.state.conn
        yield c


def set_shop(client, site="amazon_eg", **fields):
    r = client.post(f"/api/settings/shops/{site}", headers=ADMIN, json=fields)
    assert r.status_code == 200, r.text
    return r.json()


def set_device(client, worker, on, site="amazon_eg"):
    return client.post(f"/api/settings/shops/{site}/devices/{worker}", headers=ADMIN, json={"on": on})


def history(client, sku="TV1"):
    """60 days at 10,000 EGP, so a drop to 6,000 is a real discount."""
    plan_amazon(client)
    with client.conn:
        db.record(client.conn, Listing("amazon_eg", sku, "Big TV", f"https://x/{sku}", 1_000_000, None, "EGP", True),
                  grp="TVs", now=utcnow() - timedelta(days=60))
        client.conn.execute("UPDATE jobs SET last_done_at = ? WHERE kind = 'crawl'", (iso(utcnow()),))


def drop(client, sku="TV1", price_egp=6000):
    job = lease(client)
    client.post(f"/api/jobs/{job['id']}/page", headers=W1, json={
        "lease_token": job["lease_token"], "page": 1, "done": True, "listings": [listing(sku, price_egp)]})
    with client.conn:
        client.conn.execute("UPDATE jobs SET next_due_at = ?", (iso(utcnow() - timedelta(seconds=1)),))


def deals(client):
    return client.conn.execute("SELECT * FROM alerts WHERE kind != 'system' ORDER BY id").fetchall()


# ------------------------------------------------------------------ crawling

def test_a_shop_switched_off_gets_no_work_on_any_device_and_comes_back(client):
    plan_amazon(client)
    set_shop(client, crawl=False)
    for who in (W1, W2):
        reply = client.post("/api/lease", json={"sites": ["amazon_eg"]}, headers=who).json()
        assert reply["job"] is None and reply["retry_after"] >= 5  # an old worker just waits
    assert client.post("/api/lease", json={"sites": ["noon_eg"]}, headers=W1).json()["job"] is not None  # others go on
    set_shop(client, crawl=True)
    assert lease(client, W2) is not None


def test_switching_a_shop_off_stops_the_job_in_progress_at_its_next_page(client):
    plan_amazon(client)
    job = lease(client)
    set_shop(client, crawl=False)
    r = client.post(f"/api/jobs/{job['id']}/page", headers=W1, json={
        "lease_token": job["lease_token"], "page": 1, "listings": [listing()]})
    assert r.status_code == 409  # the worker's LeaseLost: it drops the job
    due = client.conn.execute("SELECT next_due_at, lease_token FROM jobs WHERE id = ?", (job["id"],)).fetchone()
    assert due["lease_token"] is None  # the slice stays due for when it is back on


def test_a_device_switched_off_loses_only_that_shop(client):
    plan_amazon(client)
    assert set_device(client, "w1", False).status_code == 200
    assert client.post("/api/lease", json={"sites": ["amazon_eg"]}, headers=W1).json()["job"] is None
    assert client.post("/api/lease", json={"sites": ["noon_eg"]}, headers=W1).json()["job"] is not None
    assert lease(client, W2) is not None  # the other device still crawls it
    assert set_device(client, "w1", True).status_code == 200
    assert shop_settings.device_off(client.conn, "w1", "amazon_eg") is False


def test_a_device_is_only_switched_for_shops_it_has_in_deals_sites(client):
    client.post("/api/lease", json={"sites": ["noon_eg"], "worker_sites": ["noon_eg"]}, headers=W1)
    assert set_device(client, "w1", False).status_code == 409  # amazon isn't in its DEALS_SITES
    assert set_device(client, "w1", True).status_code == 409  # and the UI can't add it either
    assert set_device(client, "w1", False, site="noon_eg").status_code == 200
    assert set_device(client, "nobody", False).status_code == 404
    assert set_device(client, "w1", False, site="nope").status_code == 422


def test_a_switched_off_device_keeps_its_deals_sites_and_shows_as_off(client):
    from deals import monitor
    client.post("/api/lease", json={"sites": ["amazon_eg"], "worker_sites": ["amazon_eg", "noon_eg"]}, headers=W1)
    set_device(client, "w1", False)
    client.post("/api/lease", json={"sites": ["amazon_eg"], "worker_sites": ["amazon_eg", "noon_eg"]}, headers=W1)
    w1 = next(d for d in monitor.devices(client.conn, utcnow()) if d["name"] == "w1")
    assert {s["site"]: s["state"] for s in w1["sites"]}["amazon_eg"] == "off"
    assert [s["site"] for s in w1["sites"]] == ["amazon_eg", "noon_eg"]  # still offered as a switch in the UI


def test_a_switched_off_device_takes_no_screenshots_of_that_shop(client):
    with client.conn:
        client.conn.execute("INSERT INTO alerts (id, kind, message, created_at, next_attempt_at) VALUES (9, 'price_drop', 'x', ?, ?)",
                            (iso(utcnow()), iso(utcnow())))
    shotq._shooters.clear()
    client.post("/api/lease", json={"sites": ["amazon_eg"], "worker_sites": ["amazon_eg"]}, headers=W1)
    set_device(client, "w1", False)
    reply = client.post("/api/lease", json={"sites": ["amazon_eg"], "shoot": True}, headers=W1).json()
    assert "shot" not in reply and not shotq.can_shoot("amazon_eg", utcnow())


def test_slow_pace_multiplies_the_delays_it_never_speeds_up(cfg):
    from deals.config import SiteSettings
    cfg = replace(cfg, admin_token="admin-secret", sites={"amazon_eg": SiteSettings(2, 4)})
    app = create_app(cfg, background=False)
    with TestClient(app) as c:
        c.conn = app.state.conn
        set_shop(c, pace=4)
        job = c.post("/api/lease", json={"sites": ["amazon_eg"]}, headers=W1).json()["job"]
        assert (job["min_delay"], job["max_delay"]) == (8, 16)
        assert c.post("/api/settings/shops/amazon_eg", headers=ADMIN, json={"pace": 0}).status_code == 422
        assert c.post("/api/settings/shops/amazon_eg", headers=ADMIN, json={"pace": 3}).status_code == 422


def test_product_pages_and_screenshots_have_their_own_switches(client, cfg):
    now = utcnow()
    with client.conn:
        db.record(client.conn, Listing("jumia_eg", "J1", "Kettle", "https://x/J1", 100_000, None, "EGP", True), now=now)
    detailq.refill(client.conn, now)
    client.conn.commit()
    set_shop(client, "jumia_eg", pages=False)
    assert detailq.offer(client.conn, cfg, "1.1.1.1", "jumia_eg", now, 60) is None
    set_shop(client, "jumia_eg", pages=True)
    assert detailq.offer(client.conn, cfg, "1.1.1.1", "jumia_eg", now, 60) is not None

    shotq.seen_shooter(["amazon_eg"], now)
    with client.conn:
        client.conn.execute("INSERT INTO alerts (id, kind, message, created_at, next_attempt_at) VALUES (9, 'price_drop', 'x', ?, ?)",
                            (iso(now), iso(now)))
    set_shop(client, screenshots=False)
    assert shotq.add(client.conn, cfg, 9, "amazon_eg", "https://x", now) is False
    set_shop(client, screenshots=True)
    assert shotq.add(client.conn, cfg, 9, "amazon_eg", "https://x", now) is True
    client.conn.commit()
    set_shop(client, screenshots=False)  # one already waiting stops holding its alert
    assert client.conn.execute("SELECT status FROM shots WHERE alert_id = 9").fetchone()["status"] == "none"


# ------------------------------------------------------------------ alerts

def test_alerts_off_writes_no_alert_but_prices_still_update(client):
    history(client)
    set_shop(client, alerts="off")
    drop(client)
    assert deals(client) == []
    assert client.conn.execute("SELECT price FROM products WHERE sku = 'TV1'").fetchone()["price"] == 600_000


def test_silent_alerts_are_kept_but_never_reach_telegram(client, cfg):
    history(client)
    with client.conn:
        client.conn.execute("INSERT INTO subscribers (chat_id, status, created_at, updated_at) VALUES (5, 'active', ?, ?)",
                            (iso(utcnow()), iso(utcnow())))
    set_shop(client, alerts="silent")
    shotq.seen_shooter(["amazon_eg"], utcnow())
    drop(client)
    a = deals(client)[0]
    assert a["kind"] == "price_drop" and a["sent_at"] and a["error"].startswith("silent")
    assert client.conn.execute("SELECT COUNT(*) FROM shots").fetchone()[0] == 0  # no browser for nobody
    sender = alerts.AlertSender(replace(cfg, telegram_token="t", telegram_chat_id=1), threading.Event())
    sender.fan_out(client.conn, utcnow())
    assert client.conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0


def test_a_silent_alert_does_not_stop_the_real_one_after_switching_back_on(client):
    history(client)
    set_shop(client, alerts="silent")
    drop(client)
    assert len(deals(client)) == 1
    drop(client, price_egp=5900)  # silent mode doesn't repeat itself either: within 5% of the last one
    assert len(deals(client)) == 1
    set_shop(client, alerts="on")
    drop(client, price_egp=5800)  # also within 5% and within the week, but nobody heard of the silent ones
    rows = deals(client)
    assert len(rows) == 2 and rows[1]["error"] is None and rows[1]["sent_at"] is None  # goes out as usual
    assert "silent" not in rows[1]["message"]


def test_silencing_a_shop_cancels_what_is_still_queued_but_not_what_an_admin_sent(client):
    history(client)
    now = iso(utcnow())
    pid = client.conn.execute("SELECT id FROM products WHERE sku = 'TV1'").fetchone()["id"]
    with client.conn:
        client.conn.executemany(
            "INSERT INTO alerts (id, product_id, kind, message, created_at, next_attempt_at, sent_at) VALUES (?, ?, ?, 'x', ?, ?, ?)",
            [(1, pid, "price_drop", now, now, None), (2, pid, "price_drop", now, now, now), (3, pid, "manual", now, now, None),
             (4, pid, "price_drop", now, now, now)])
        client.conn.execute("INSERT INTO subscribers (chat_id, status, created_at, updated_at) VALUES (5, 'active', ?, ?)", (now, now))
        client.conn.execute("INSERT INTO deliveries (alert_id, chat_id, next_attempt_at) VALUES (2, 5, ?)", (now,))
        client.conn.executemany("INSERT INTO shots (alert_id, site, url, status, created_at, wait_until) "
                                "VALUES (?, 'amazon_eg', 'https://x', 'pending', ?, ?)", [(1, now, now), (3, now, now)])
        client.conn.execute("INSERT INTO reviews (alert_id, status, card, tier, basis, created_at, expires_at, next_attempt_at) "
                            "VALUES (4, 'pending', 'c', 'good', 'history', ?, ?, ?)", (now, now, now))
    set_shop(client, alerts="silent")
    states = {r["id"]: r for r in client.conn.execute("SELECT * FROM alerts")}
    assert states[1]["sent_at"] and states[1]["error"].startswith("silent")
    assert states[3]["sent_at"] is None and states[3]["error"] is None  # by hand: not ours to cancel
    assert client.conn.execute("SELECT COUNT(*) FROM deliveries WHERE sent_at IS NULL").fetchone()[0] == 0
    assert client.conn.execute("SELECT status FROM reviews WHERE alert_id = 4").fetchone()["status"] == "skipped"
    shots = {r["alert_id"]: r["status"] for r in client.conn.execute("SELECT alert_id, status FROM shots")}
    assert shots == {1: "none", 3: "pending"}  # the screenshot of the one sent by hand still comes


def test_a_shops_own_floor_only_ever_removes_alerts(client):
    history(client)
    set_shop(client, min_drop=0.5)
    drop(client)  # 40% below typical
    assert deals(client) == []
    set_shop(client, min_drop=0.3, min_price=7000)
    drop(client, price_egp=5900)  # enough drop, but under the shop's 7,000 EGP floor
    assert deals(client) == []
    set_shop(client, min_drop=None, min_price=None)
    drop(client, price_egp=5800)
    assert len(deals(client)) == 1


def test_an_alert_by_hand_ignores_the_shops_switch_and_says_so(client, cfg):
    history(client)
    pid = client.conn.execute("SELECT id FROM products WHERE sku = 'TV1'").fetchone()["id"]
    set_shop(client, alerts="off")
    preview = client.get(f"/api/products/{pid}/alert", headers=ADMIN).json()
    assert preview["shop_alerts"] == "off"
    assert client.post(f"/api/products/{pid}/alert", headers=ADMIN, json={"note": ""}).status_code == 200
    assert deals(client)[0]["kind"] == "manual"


# ------------------------------------------------------------------ nobody forgets, nobody gets alarmed

def test_a_shop_switched_off_is_quiet_and_named_not_stalled(client, cfg):
    plan_amazon(client)
    now = utcnow()
    with client.conn:
        client.conn.execute("UPDATE jobs SET next_due_at = ?, failures = 5 WHERE site = 'amazon_eg'", (iso(now - timedelta(days=2)),))
    set_shop(client, crawl=False, note="testing")
    seen = {s.subject: s for s in uptime.observe(client.conn, now)}
    assert seen["shop:amazon_eg"].state == "paused" and "switched off" in seen["shop:amazon_eg"].reason
    checks = {c.name: c for c in health.run_checks(client.conn, cfg, now)}
    assert checks["crawl amazon_eg"].level == "ok" and "switched off" in checks["crawl amazon_eg"].detail
    assert "jobs amazon_eg" not in checks  # its old failures are not news while it is off
    assert "amazon_eg" in checks["shop settings"].detail
    assert "أمازون مصر" in health.daily_report(client.conn, cfg, now)


def test_every_device_switched_off_counts_as_the_shop_being_off(client):
    plan_amazon(client)
    client.post("/api/lease", json={"sites": ["amazon_eg"], "worker_sites": ["amazon_eg"]}, headers=W2)
    set_device(client, "w1", False)
    assert scheduler.site_paused(client.conn, "amazon_eg", utcnow()) is None  # w2 still has it
    set_device(client, "w2", False)
    assert scheduler.site_paused(client.conn, "amazon_eg", utcnow()) is not None
    seen = {s.subject: s for s in uptime.observe(client.conn, utcnow())}
    assert seen["device_shop:w1/amazon_eg"].state == "paused"


def test_switching_back_on_is_not_a_stalled_shop(client):
    plan_amazon(client)
    now = utcnow()
    set_shop(client, crawl=False)
    with client.conn:
        client.conn.execute("UPDATE jobs SET next_due_at = ?, last_done_at = ? WHERE site = 'amazon_eg'",
                            (iso(now - timedelta(days=2)), iso(now - timedelta(days=2))))
    set_shop(client, crawl=True)
    seen = {s.subject: s for s in uptime.observe(client.conn, utcnow())}
    assert seen["shop:amazon_eg"].state == "up"


def test_only_admins_change_settings_and_bad_values_are_refused(client):
    assert client.post("/api/settings/shops/amazon_eg", json={"crawl": False}, headers=W1).status_code == 403
    assert client.post("/api/settings/shops/amazon_eg", json={"crawl": False}).status_code == 401
    assert client.post("/api/settings/shops/amazon_eg/devices/w1", json={"on": False}, headers=W1).status_code == 403
    assert client.get("/api/settings/shops", headers=W1).status_code == 403
    for bad in ({"alerts": "maybe"}, {"min_drop": 1.5}, {"min_drop": 0}, {"min_price": -1}, {"note": "x" * 301}):
        assert client.post("/api/settings/shops/amazon_eg", headers=ADMIN, json=bad).status_code == 422, bad
    assert client.post("/api/settings/shops/nope", headers=ADMIN, json={"crawl": False}).status_code == 422
    assert client.conn.execute("SELECT COUNT(*) FROM shop_settings").fetchone()[0] == 0


def test_changing_one_setting_leaves_the_others_and_every_shop_changes_all(client):
    set_shop(client, pace=2, note="slow it")
    set_shop(client, alerts="silent")
    s = shop_settings.get(client.conn, "amazon_eg")
    assert (s.pace, s.note, s.alerts, s.crawl) == (2, "slow it", "silent", True) and s.updated_by == "admin_token"
    set_shop(client, "*", alerts="off")
    listed = client.get("/api/settings/shops", headers=ADMIN).json()
    assert {x["alerts"] for x in listed["shops"]} == {"off"} and listed["admin"] is True
    assert client.post("/api/settings/shops/*", headers=ADMIN, json={"note": "x"}).status_code == 422  # one shop's note
    device = {"name": "w1", "sites": ["amazon_eg", "noon_eg"], "off": ["amazon_eg"]}
    client.post("/api/lease", json={"sites": ["amazon_eg"], "worker_sites": device["sites"]}, headers=W1)
    set_device(client, "w1", False)
    assert next(d for d in client.get("/api/settings/shops", headers=ADMIN).json()["devices"] if d["name"] == "w1")["off"] == ["amazon_eg"]


def test_the_page_has_the_settings_screen(client):
    page = client.get("/ui", headers=ADMIN).text
    assert "Settings" in page and "/api/settings/shops" in page
