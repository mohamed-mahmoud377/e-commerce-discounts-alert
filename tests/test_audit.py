from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from deals import audit, db, scheduler
from deals.coordinator import create_app
from deals.db import utcnow

W1 = {"Authorization": "Bearer token-1"}


def lease(client):
    return client.post("/api/lease", json={"sites": ["amazon_eg"]}, headers=W1).json()["job"]


def plan_amazon(client):
    job = lease(client)
    client.post(f"/api/jobs/{job['id']}/plan", headers=W1, json={
        "lease_token": job["lease_token"], "slices": [{"target": t, "total": 50}
                                    for t in ("category:111|price:1000-5000", "category:111|price:5000-")]})


@pytest.fixture
def client(cfg):
    app = create_app(cfg, background=False)
    with TestClient(app) as c:
        c.conn = app.state.conn
        yield c


@pytest.mark.parametrize("message, category", [  # real lines from the devices' logs
    ("coordinator unreachable (ConnectionError) for 62s; retrying in 60s", "server_unreachable"),
    ("dubaiphone_eg page 1 attempt 1 failed (dubaiphone_eg: no shopData in the page (bad parameter, a shop error, "
     "or the layout changed)); retrying in 5s", "parser"),
    ("cairosales_eg page 1 attempt 1 failed (Failed to perform, curl: (28) Operation timed out after 30002 "
     "milliseconds with 0 bytes received); retrying in 5s", "timeout"),
    ("jumia_eg page 5 attempt 1 failed (Failed to perform, curl: (6) Could not resolve host: www.jumia.com.eg)", "network"),
    ("jumia_eg page 7 attempt 1 failed (Failed to perform, curl: (56) Recv failure: Connection reset by peer.)", "network"),
    ("cairosales_eg: Cloudflare challenge", "blocked"),
    ("carrefour_eg page 2 attempt 2 failed (HTTP Error 500: ); retrying in 20s", "http"),
    ("product page refused, pausing them: btech_eg: product page refused (HTTP 400, 1721 bytes)", "pages_refused"),
    ("UnknownTarget: jumia_eg: /tvs/ redirected to /electronics/", "config"),
    ("deals.alerts: alert delivery 5 to 123 failed (HTTP 400); retrying in 0:01:00", "telegram"),
    ("deals.coordinator: watchdog check failed (OperationalError: database is locked)", "bug"),
    ("deals.coordinator: web UI: failed login", "login"),
])
def test_categories_of_real_problems(message, category):
    assert audit.categorize(message) == category


def test_the_same_problem_is_counted_on_one_row(cfg):
    conn = db.connect(cfg.db_path)
    with conn:
        scheduler.add_events(conn, "mac", [
            ("2026-11-04T10:00:00+00:00", "WARNING", "[jumia_eg] jumia_eg page 3 attempt 1 failed (curl: (28) "
                                                     "Operation timed out after 30002 milliseconds)"),
            ("2026-11-04T10:05:00+00:00", "WARNING", "[jumia_eg] jumia_eg page 5 attempt 2 failed (curl: (28) "
                                                     "Operation timed out after 30001 milliseconds)"),
            ("2026-11-04T10:06:00+00:00", "INFO", "[jumia_eg] done jumia_eg category:tvs"),  # not a problem
            ("2026-11-04T10:07:00+00:00", "ERROR", "[jumia_eg] job 935 failed (ValueError: x)"),  # the report has it
        ])
    rows = conn.execute("SELECT * FROM errors").fetchall()
    assert len(rows) == 1
    e = rows[0]
    assert (e["site"], e["source"], e["category"], e["count"]) == ("jumia_eg", "mac", "timeout", 2)
    assert e["first_seen_at"].startswith("2026-11-04T10:00") and "page 5" in e["message"]
    with conn:
        audit.record(conn, "mac", "WARNING", "[noon_eg] noon_eg page 2 attempt 1 failed (curl: (6) Could not resolve "
                     "host: www.noon.com. See https://curl.se/libcurl/c/libcurl-errors.html first for more details.)",
                     "2026-11-04T10:08:00+00:00")
    assert conn.execute("SELECT message FROM errors WHERE category = 'network'").fetchone()[0].endswith("www.noon.com.)")


def test_worker_reports_are_recorded_with_their_reason(client):
    plan_amazon(client)
    job = lease(client)
    client.post(f"/api/jobs/{job['id']}/failed", headers=W1,
                json={"lease_token": job["lease_token"], "reason": "ValueError: amazon_eg: no results bar (layout changed?)"})
    job = lease(client)
    client.post(f"/api/jobs/{job['id']}/blocked", headers=W1, json={"lease_token": job["lease_token"], "reason": "captcha"})
    got = {r["category"]: r for r in client.conn.execute("SELECT * FROM errors")}
    assert set(got) == {"parser", "blocked"}
    assert got["parser"]["source"] == "w1" and got["parser"]["site"] == "amazon_eg" and got["parser"]["level"] == "ERROR"


def test_a_resolved_problem_opens_again_when_it_happens_again(cfg):
    conn = db.connect(cfg.db_path)
    now = utcnow()
    with conn:
        audit.record(conn, "mac", "WARNING", "[noon_eg] blocked: noon_eg: captcha", "2026-11-04T10:00:00+00:00")
    eid = conn.execute("SELECT id FROM errors").fetchone()[0]
    assert audit.resolve(conn, eid, now)
    assert audit.listing(conn, now, days=3650, open_only=True) == []
    with conn:
        audit.record(conn, "mac", "WARNING", "[noon_eg] blocked: noon_eg: captcha", "2099-01-01T00:00:00+00:00")
    [e] = audit.listing(conn, now, days=3650, open_only=True)
    assert e["open"] and e["count"] == 2 and e["cause"].startswith("The shop's bot protection")


def test_web_ui_endpoints(cfg):
    app = create_app(replace(cfg, admin_token="admin-secret"), background=False)
    with TestClient(app) as c:
        conn = app.state.conn
        with conn:
            audit.record(conn, "mac", "WARNING", "[noon_eg] noon_eg: captcha", utcnow().isoformat())
        eid = conn.execute("SELECT id FROM errors").fetchone()[0]
        assert c.get("/api/errors", params={"token": "token-1"}).status_code == 403  # admins only
        assert c.post(f"/api/errors/{eid}/resolve", headers=W1).status_code == 403
        assert c.get("/api/uptime", params={"token": "token-1"}).status_code == 403
        c.get("/ui/login", params={"token": "admin-secret"}, follow_redirects=False)
        got = c.get("/api/errors").json()
        assert got["errors"][0]["category"] == "blocked" and got["admin"] and "blocked" in got["categories"]
        assert set(c.get("/api/uptime").json()) == {"status", "changes", "outages"}
        assert c.post(f"/api/errors/{eid}/resolve").json() == {"ok": True}
        assert c.get("/api/errors", params={"only_open": 1}).json()["errors"] == []
        assert c.post(f"/api/errors/{eid}/reopen").json() == {"ok": True}
        assert c.post("/api/errors/999/resolve").status_code == 404
