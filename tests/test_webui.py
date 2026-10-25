from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from deals import db
from deals.coordinator import create_app
from deals.db import utcnow
from deals.models import Listing


@pytest.fixture
def client(cfg):
    cfg = replace(cfg, admin_token="admin-secret")
    app = create_app(cfg, background=False)
    with db.connect(cfg.db_path) as conn:
        db.record(conn, Listing("amazon_eg", "TV1", "Samsung 55 inch TV", "https://x/TV1", 2_000_000, None, "EGP", True),
                  grp="TVs", now=utcnow())
    with TestClient(app) as c:
        yield c


def test_ui_needs_login(client):
    r = client.get("/ui", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/ui/login"
    assert client.get("/api/overview").status_code == 401


def test_wrong_token_is_refused(client):
    r = client.post("/ui/login", data={"token": "nope"}, follow_redirects=False)
    assert r.status_code == 401 and "Wrong token" in r.text
    assert "deals_ui" not in r.cookies


def test_login_sets_cookie_and_everything_opens(client):
    r = client.post("/ui/login", data={"token": "admin-secret"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/ui"
    assert client.get("/ui").status_code == 200 and "deals" in client.get("/ui").text
    overview = client.get("/api/overview").json()
    assert overview["totals"]["products"] == 1 and overview["checks"]
    assert client.get("/api/alerts").json() == {"alerts": []}
    assert isinstance(client.get("/api/jobs").json()["jobs"], list)
    found = client.get("/api/products", params={"q": "samsung 55"}).json()["products"]
    assert [p["sku"] for p in found] == ["TV1"]
    detail = client.get(f"/api/products/{found[0]['id']}").json()
    assert detail["product"]["title"] == "Samsung 55 inch TV" and len(detail["history"]) == 1
    client.get("/ui/logout")
    assert client.get("/api/overview").status_code == 401


def test_one_click_link_and_worker_tokens(client):
    r = client.get("/ui/login", params={"token": "admin-secret"}, follow_redirects=False)
    assert r.status_code == 303 and "deals_ui" in r.cookies
    assert client.get("/api/overview", params={"token": "token-1"}).status_code == 200  # worker tokens still read
