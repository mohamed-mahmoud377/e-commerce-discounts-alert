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


def test_saved_queries_run_and_need_login(client):
    assert client.get("/api/queries").status_code == 401
    client.post("/ui/login", data={"token": "admin-secret"}, follow_redirects=False)
    names = [q["name"] for q in client.get("/api/queries").json()["queries"]]
    assert "biggest_drops" in names and "schedule" in names
    for name in names:  # every saved query must at least run on an almost-empty database
        r = client.get(f"/api/queries/{name}?hours=24")
        assert r.status_code == 200, name
        body = r.json()
        assert body["columns"] and isinstance(body["rows"], list) and body["title"], name
    assert client.get("/api/queries/drop_table").status_code == 404
    rows = client.get("/api/queries/coverage").json()["rows"]
    assert rows and rows[0][0] == "TVs" and rows[0][2] == 1  # the one recorded product
    d = client.get("/api/queries/coverage?grp=TVs&site=amazon_eg").json()
    assert d["filters"] == {"grp": True, "site": True} and len(d["rows"]) == 1
    assert client.get("/api/queries/coverage?grp=Phones").json()["rows"] == []
    assert client.get("/api/queries/new_products?site=noon_eg&hours=48").json()["rows"] == []
    assert client.get("/api/queries/new_products?site=amazon_eg&hours=48").json()["rows"][0][2] == "Samsung 55 inch TV"
    d = client.get("/api/queries/tables?grp=TVs").json()  # no such columns: filter ignored, not an error
    assert d["filters"] == {"grp": False, "site": False} and len(d["rows"]) == 8


def test_product_page_data_and_filters(client):
    client.post("/ui/login", data={"token": "admin-secret"}, follow_redirects=False)
    pid = client.get("/api/products?q=samsung").json()["products"][0]["id"]
    d = client.get(f"/api/products/{pid}").json()
    assert set(d) == {"product", "stats", "history", "alerts", "other_sellers"}
    assert "share_at_or_below" in d["stats"] and "drop_vs_typical" in d["stats"] and len(d["history"]) == 1
    r = client.get("/api/products?limit=0").json()
    assert r["products"] == [] and r["groups"] == ["TVs"]
    assert client.get("/api/products?grp=TVs&sort=cheap&in_stock=1").json()["products"][0]["id"] == pid
    assert client.get("/api/products?grp=Phones").json()["products"] == []
    assert client.get("/api/products?q=samsung&sort=bogus").status_code == 200  # unknown sort falls back


def test_products_are_paged_with_a_total(client):
    client.post("/ui/login", data={"token": "admin-secret"}, follow_redirects=False)
    conn = client.app.state.conn
    with conn:
        for i in range(250):
            db.record(conn, Listing("noon_eg", f"N{i}@s", f"Phone model {i}", f"https://x/N{i}", 100_000 + i * 100, None, "EGP", True),
                      grp="Phones", now=utcnow())
    first = client.get("/api/products?limit=100&offset=0&grp=Phones&sort=cheap").json()
    assert first["total"] == 250 and len(first["products"]) == 100 and first["products"][0]["title"] == "Phone model 0"
    second = client.get("/api/products?limit=100&offset=100&grp=Phones&sort=cheap").json()
    assert second["products"][0]["title"] == "Phone model 100" and second["offset"] == 100
    last = client.get("/api/products?limit=100&offset=200&grp=Phones&sort=cheap").json()
    assert len(last["products"]) == 50
    assert client.get("/api/products?limit=100&offset=300&grp=Phones").json()["products"] == []
    # pages never overlap or skip under a stable sort
    ids = [p["id"] for page in (first, second, last) for p in page["products"]]
    assert len(ids) == 250 == len(set(ids))
    # the whole catalogue is browsable without a search term
    assert client.get("/api/products?limit=10").json()["total"] == 251
