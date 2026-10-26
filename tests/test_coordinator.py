from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from deals import db
from deals.coordinator import create_app
from deals.db import iso, utcnow
from deals.models import Listing

W1 = {"Authorization": "Bearer token-1"}
W2 = {"Authorization": "Bearer token-2"}


@pytest.fixture
def client(cfg):
    app = create_app(cfg, background=False)
    with TestClient(app) as c:
        c.conn = app.state.conn
        yield c


def listing(sku="A1", price_egp=5000, **kw):
    return {"site": "amazon_eg", "sku": sku, "title": f"TV {sku}", "url": f"https://x/{sku}",
            "price": price_egp * 100, "list_price": None, "currency": "EGP", "in_stock": True, **kw}


def lease(client, headers=W1, sites=("amazon_eg",)):
    r = client.post("/api/lease", json={"sites": list(sites)}, headers=headers)
    assert r.status_code == 200
    return r.json()["job"]


def plan_amazon(client, slices=("category:111|price:1000-5000", "category:111|price:5000-")):
    job = lease(client)
    assert job["kind"] == "plan" and job["target"] == "category:111|price:1000-"  # watch min_price applied
    r = client.post(f"/api/jobs/{job['id']}/plan", headers=W1, json={
        "lease_token": job["lease_token"], "slices": [{"target": s, "total": 50} for s in slices]})
    assert r.json() == {"ok": True, "slices": len(slices)}


def test_rejects_unknown_tokens(client):
    assert client.post("/api/lease", json={"sites": ["amazon_eg"]}, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_plan_then_crawl_cycle(client):
    plan_amazon(client)
    job = lease(client)
    assert job["kind"] == "crawl" and job["group"] == "TVs"
    r = client.post(f"/api/jobs/{job['id']}/page", headers=W1, json={
        "lease_token": job["lease_token"], "page": 1, "listings": [listing("A1"), listing("A2")], "done": True})
    assert r.json()["ok"]
    row = client.conn.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()
    assert row["lease_token"] is None and row["last_count"] == 2 and row["next_due_at"] > iso(utcnow())
    assert client.conn.execute("SELECT COUNT(*) FROM products WHERE grp = 'TVs'").fetchone()[0] == 2
    worker = client.conn.execute("SELECT * FROM workers WHERE name = 'w1'").fetchone()
    assert worker["pages"] == 1 and worker["jobs_done"] == 2  # the plan + the crawl


def test_one_job_per_site_per_ip(client):
    plan_amazon(client)
    first = lease(client, W1)
    assert lease(client, W2) is None  # same IP, same site: wait
    other = lease(client, W2, sites=("noon_eg",))
    assert other and other["site"] == "noon_eg"  # a different site is fine
    assert first["id"] != other["id"]


def test_same_worker_runs_one_job_per_site_in_parallel(client):
    plan_amazon(client)
    amazon = lease(client, W1, sites=("amazon_eg",))
    noon = lease(client, W1, sites=("noon_eg",))  # the worker's noon loop asks while amazon is running
    assert amazon and noon
    still = client.conn.execute("SELECT lease_token FROM jobs WHERE id = ?", (amazon["id"],)).fetchone()
    assert still["lease_token"] == amazon["lease_token"]


def test_blocked_ip_pauses_site_for_that_ip(client):
    plan_amazon(client)
    job = lease(client)
    r = client.post(f"/api/jobs/{job['id']}/blocked", headers=W1,
                    json={"lease_token": job["lease_token"], "reason": "captcha"})
    assert r.json()["ok"]
    assert lease(client, W1) is None and lease(client, W2) is None  # both workers share this IP
    block = client.conn.execute("SELECT * FROM blocks").fetchone()
    assert block["site"] == "amazon_eg" and block["strikes"] == 1


def test_stale_lease_is_rejected(client):
    plan_amazon(client)
    job = lease(client)
    r = client.post(f"/api/jobs/{job['id']}/page", headers=W1,
                    json={"lease_token": "old", "page": 1, "listings": [], "done": True})
    assert r.status_code == 409


def test_empty_result_for_known_slice_counts_as_failure(client):
    plan_amazon(client)
    job = lease(client)
    client.post(f"/api/jobs/{job['id']}/page", headers=W1,
                json={"lease_token": job["lease_token"], "page": 1, "listings": [], "done": True})
    row = client.conn.execute("SELECT failures, last_error FROM jobs WHERE id = ?", (job["id"],)).fetchone()
    assert row["failures"] == 1 and "layout" in row["last_error"]


def test_price_drop_creates_one_alert(client):
    plan_amazon(client)
    conn = client.conn
    # 60 days of history at 10,000 EGP
    with conn:
        db.record(conn, Listing("amazon_eg", "TV1", "Big TV", "https://x/TV1", 1_000_000, None, "EGP", True),
                  grp="TVs", now=utcnow() - timedelta(days=60))
        conn.execute("UPDATE jobs SET last_done_at = ? WHERE kind = 'crawl'", (iso(utcnow()),))
    for attempt in range(2):
        job = lease(client)
        client.post(f"/api/jobs/{job['id']}/page", headers=W1, json={
            "lease_token": job["lease_token"], "page": 1, "done": True,
            "listings": [listing("TV1", 6000, stock_left=2, badges=["limited_deal"])]})
        conn.execute("UPDATE jobs SET next_due_at = ?", (iso(utcnow() - timedelta(seconds=1)),))
    alerts = conn.execute("SELECT * FROM alerts WHERE kind != 'system'").fetchall()
    assert len(alerts) == 1  # the second crawl at the same price doesn't repeat it
    a = alerts[0]
    assert a["kind"] == "price_drop" and a["drop_pct"] == 0.4 and a["reference_price"] == 1_000_000
    assert "متبقٍ 2 فقط" in a["message"] and "TV TV1" in a["message"] and "انخفاض حقيقي" in a["message"]


def test_config_change_removes_jobs(client, cfg):
    from dataclasses import replace

    from deals import scheduler
    plan_amazon(client)
    scheduler.sync_watches(client.conn, replace(cfg, watches=[]), utcnow())
    assert client.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_new_products_must_pass_price_floor_and_title_words(cfg):
    from dataclasses import replace
    from deals.config import Watch
    consoles = Watch(name="TVs", targets={"amazon_eg": ["category:111"]}, interval_minutes=30, min_price=1000,
                     require_words=("console", "playstation 5"), exclude_words=("controller", "headset", "mount"))
    cfg = replace(cfg, watches=[consoles])
    app = create_app(cfg, background=False)
    with TestClient(app) as client:
        client.conn = app.state.conn
        plan_amazon(client)
        job = lease(client)
        r = client.post(f"/api/jobs/{job['id']}/page", headers=W1, json={
            "lease_token": job["lease_token"], "page": 1, "done": True, "listings": [
                listing("PS5", 25000, title="PlayStation 5 Slim Console"),               # kept
                listing("BUNDLE", 27000, title="PlayStation 5 Console + extra controller"),  # "console" beats exclusions
                listing("MOUNT", 10000, title="Wall Mount Kit for PS5 Slim Console"),      # ...unless it is *for* a console
                listing("PAD", 4000, title="PlayStation 5 DualSense Controller"),        # excluded word
                listing("WHEEL", 14000, title="Logitech G29 Racing Wheel for PS5"),      # no required word
                listing("CHEAP", 500, title="PlayStation 5 Console (used)"),             # under the group minimum
                listing("NOPRICE", 25000, title="PlayStation 5 Console", price=None),   # unknown price
            ]})
        assert r.json()["skipped"] == 5
        kept = sorted(p["sku"] for p in client.conn.execute("SELECT sku FROM products"))
        assert kept == ["BUNDLE", "PS5"]
        # a product we already know keeps being updated even when it drops below the floor / goes unpriced
        job = lease(client)
        client.post(f"/api/jobs/{job['id']}/page", headers=W1, json={
            "lease_token": job["lease_token"], "page": 1, "done": True,
            "listings": [listing("PS5", 25000, title="PlayStation 5 Slim Console", price=None, in_stock=False)]})
        row = client.conn.execute("SELECT in_stock FROM products WHERE sku = 'PS5'").fetchone()
        assert row["in_stock"] == 0
        # a product known from another group is updated but not re-labelled by a group that rejects it
        with client.conn:
            db.record(client.conn, Listing("amazon_eg", "HS1", "Pulse Elite Headset", "https://x/HS1", 1_100_000, None, "EGP", True),
                      grp="Headphones", now=utcnow())
            client.conn.execute("UPDATE jobs SET next_due_at = ?", (iso(utcnow() - timedelta(seconds=1)),))
        job = lease(client)
        client.post(f"/api/jobs/{job['id']}/page", headers=W1, json={
            "lease_token": job["lease_token"], "page": 1, "done": True,
            "listings": [listing("HS1", 9000, title="Pulse Elite Headset")]})
        row = client.conn.execute("SELECT grp, price FROM products WHERE sku = 'HS1'").fetchone()
        assert row["grp"] == "Headphones" and row["price"] == 900_000
