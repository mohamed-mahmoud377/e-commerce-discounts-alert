from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from deals import db, matching
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
    assert d["filters"] == {"grp": True, "site": True, "q": False} and len(d["rows"]) == 1
    assert client.get("/api/queries/coverage?grp=Phones").json()["rows"] == []
    assert client.get("/api/queries/new_products?site=noon_eg&hours=48").json()["rows"] == []
    assert client.get("/api/queries/new_products?site=amazon_eg&hours=48").json()["rows"][0][2] == "Samsung 55 inch TV"
    d = client.get("/api/queries/tables?grp=TVs").json()  # no such columns: filter ignored, not an error
    assert d["filters"] == {"grp": False, "site": False, "q": False} and len(d["rows"]) == 8


def test_product_page_data_and_filters(client):
    client.post("/ui/login", data={"token": "admin-secret"}, follow_redirects=False)
    pid = client.get("/api/products?q=samsung").json()["products"][0]["id"]
    d = client.get(f"/api/products/{pid}").json()
    assert set(d) == {"product", "stats", "history", "alerts", "other_sellers", "same_model"}
    assert "share_at_or_below" in d["stats"] and "drop_vs_typical" in d["stats"] and len(d["history"]) == 1
    r = client.get("/api/products?limit=0").json()
    assert r["products"] == [] and r["groups"] == ["TVs"]
    assert client.get("/api/products?grp=TVs&sort=cheap&in_stock=1").json()["products"][0]["id"] == pid
    assert client.get("/api/products?grp=Phones").json()["products"] == []
    assert client.get("/api/products?q=samsung&sort=bogus").status_code == 200  # unknown sort falls back
    assert d["same_model"] is None  # not matched yet: the hourly rebuild has not run
    conn = client.app.state.conn
    with conn:
        db.record(conn, Listing("amazon_eg", "TV2", "Samsung 55 inch TV UA55DU7000", "https://x/TV2", 2_000_000, None,
                                "EGP", True), grp="TVs", now=utcnow())
        db.record(conn, Listing("noon_eg", "TV3@s", "55 inch Crystal UHD TV UA55DU7000", "https://x/TV3", 1_900_000,
                                None, "EGP", True), grp="TVs", now=utcnow())
    matching.rebuild(conn, utcnow())
    tv2 = client.get("/api/products?q=UA55DU7000&sort=dear").json()["products"][0]["id"]
    same = client.get(f"/api/products/{tv2}").json()["same_model"]
    assert same["model"]["model"] == "UA55DU7000" and [o["site"] for o in same["others"]] == ["noon_eg"]
    assert client.get(f"/api/products/{pid}").json()["same_model"]["me"]["status"] == "no_code"


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


def test_every_product_sort_holds_across_pages(client):
    from deals.webui import PRODUCT_SORTS
    client.post("/ui/login", data={"token": "admin-secret"}, follow_redirects=False)
    conn = client.app.state.conn
    with conn:
        for i in range(120):
            db.record(conn, Listing("noon_eg" if i % 2 else "amazon_eg", f"S{i}", f"Item {i:03d}", f"https://x/S{i}",
                                    (500_000 - i * 1000) if i % 7 else None, 600_000 - i * 500 if i % 4 else None, "EGP",
                                    bool(i % 3), rating=(i % 5) + 0.5, reviews=i), grp="TVs", now=utcnow())
    for sort in PRODUCT_SORTS:
        pages = [client.get(f"/api/products?limit=50&offset={o}&sort={sort}").json()["products"] for o in (0, 50, 100)]
        rows = [p for page in pages for p in page]
        assert len(rows) == 121 and len({p["id"] for p in rows}) == 121, sort  # no gaps, no repeats
    cheap = [p["price"] for p in client.get("/api/products?limit=500&sort=cheap").json()["products"] if p["price"]]
    assert cheap == sorted(cheap)
    titles = [p["title"] for p in client.get("/api/products?limit=500&sort=title_desc").json()["products"]]
    assert titles == sorted(titles, key=str.lower, reverse=True)
    assert client.get("/api/products?limit=5&sort=nonsense").status_code == 200


def test_saved_queries_page_and_sort_on_the_server(client):
    client.post("/ui/login", data={"token": "admin-secret"}, follow_redirects=False)
    conn = client.app.state.conn
    with conn:
        for i in range(230):
            db.record(conn, Listing("noon_eg", f"Q{i}@s", f"Gadget {i:03d}", f"https://x/Q{i}", 100_000 + i * 1000, None, "EGP", True),
                      grp="TVs", now=utcnow())
    first = client.get("/api/queries/new_products?hours=24&limit=100&offset=0").json()
    assert first["total"] == 231 and len(first["rows"]) == 100 and first["sort"] is None
    pages = [client.get(f"/api/queries/new_products?hours=24&limit=100&offset={o}&sort=now&desc=0").json() for o in (0, 100, 200)]
    prices = [r[pages[0]["columns"].index("now")] for p in pages for r in p["rows"]]
    assert len(prices) == 231 and prices == sorted(prices, key=lambda v: (v is None, v))  # ascending across pages
    titles = [r[pages[0]["columns"].index("title")] for p in pages for r in p["rows"]]
    assert len(set(titles)) == 231  # no gaps, no repeats
    d = client.get("/api/queries/new_products?hours=24&limit=5&sort=title&desc=1").json()
    assert d["sort"] == "title" and d["desc"] is True and d["rows"][0][d["columns"].index("title")] == "Samsung 55 inch TV"
    assert client.get("/api/queries/new_products?hours=24&sort=drop%20table").json()["sort"] is None  # unknown column ignored
    assert client.get("/api/queries/new_products?hours=24&limit=50&offset=5000").json()["rows"] == []


def test_products_changed_within_a_period(client):
    client.post("/ui/login", data={"token": "admin-secret"}, follow_redirects=False)
    conn = client.app.state.conn
    old = utcnow() - timedelta(days=3)
    with conn:
        db.record(conn, Listing("noon_eg", "OLD@s", "Old Samsung fridge", "https://x/old", 900_000, None, "EGP", True),
                  grp="TVs", now=old)
    recent = (utcnow() - timedelta(hours=24)).isoformat()
    titles = [p["title"] for p in client.get("/api/products", params={"changed_since": recent}).json()["products"]]
    assert titles == ["Samsung 55 inch TV"]
    assert client.get("/api/products", params={"changed_since": ""}).json()["total"] == 2  # empty = any time


def test_saved_query_text_search(client):
    client.post("/ui/login", data={"token": "admin-secret"}, follow_redirects=False)
    d = client.get("/api/queries/new_products?hours=48&q=samsung 55").json()
    assert d["filters"]["q"] is True and len(d["rows"]) == 1
    assert client.get("/api/queries/new_products?hours=48&q=iphone").json()["rows"] == []
    assert len(client.get("/api/queries/tables?q=whatever").json()["rows"]) == 8  # no title column: ignored


def test_new_screens_data(client):
    client.post("/ui/login", data={"token": "admin-secret"}, follow_redirects=False)
    page = client.get("/ui").text
    for screen in ("Deals", "Subscribers", "Rules", "setTheme"):
        assert screen in page
    o = client.get("/api/overview").json()
    assert o["admin"] is True and "server" in o and o["latest"] == [] and o["totals"]["pending"] == 0
    rules = client.get("/api/rules").json()
    assert rules["alerts"]["min_drop"] > 0 and rules["schedule"]["min_interval_minutes"] > 0
    assert {w["name"] for w in rules["watches"]} and all("max_interval_minutes" in w for w in rules["watches"])


def test_admin_manages_subscribers_and_workers_cannot(cfg):
    cfg = replace(cfg, admin_token="admin-secret", telegram_token="")
    app = create_app(cfg, background=False)
    now = utcnow().isoformat()
    with db.connect(cfg.db_path) as conn:
        conn.execute("INSERT INTO subscribers (chat_id, name, status, created_at, updated_at) VALUES (7, 'Omar', 'pending', ?, ?)", (now, now))
    with TestClient(app) as c:
        assert c.post("/api/subscribers/7/approve", params={"token": "token-1"}).status_code == 403  # worker token: look only
        c.post("/ui/login", data={"token": "admin-secret"}, follow_redirects=False)
        people = c.get("/api/subscribers").json()
        assert people["admin"] is True and [p["status"] for p in people["people"] if p["chat_id"] == 7] == ["pending"]
        assert c.post("/api/subscribers/7/approve").json() == {"ok": True}
        assert c.post("/api/subscribers/7/approve").status_code == 409  # already decided
        assert c.post("/api/subscribers/7/promote").status_code == 200
        assert c.post("/api/subscribers/7/explode").status_code == 404
        assert c.post("/api/subscribers/999/remove").status_code == 404
        row = c.app.state.conn.execute("SELECT status, is_manager FROM subscribers WHERE chat_id = 7").fetchone()
        assert (row["status"], row["is_manager"]) == ("active", 1)
        assert c.post("/api/subscribers/7/remove").status_code == 200
        assert 7 not in [p["chat_id"] for p in c.get("/api/subscribers").json()["people"]]
        assert c.post("/api/report").json() == {"ok": True}
        report = c.app.state.conn.execute("SELECT kind, dedupe_key FROM alerts").fetchone()
        assert report["kind"] == "system" and report["dedupe_key"].startswith("report-now:")


def test_products_carry_their_previous_price(client):
    client.post("/ui/login", data={"token": "admin-secret"}, follow_redirects=False)
    conn = client.app.state.conn
    with conn:
        db.record(conn, Listing("amazon_eg", "TV1", "Samsung 55 inch TV", "https://x/TV1", 1_600_000, None, "EGP", True),
                  grp="TVs", now=utcnow() + timedelta(minutes=5))
        db.record(conn, Listing("amazon_eg", "TV1", "Samsung 55 inch TV", "https://x/TV1", 1_600_000, None, "EGP", False),
                  grp="TVs", now=utcnow() + timedelta(minutes=10))  # a stock change at the same price
    p = client.get("/api/products?q=samsung").json()["products"][0]
    assert (p["price"], p["prev_price"]) == (1_600_000, 2_000_000)  # the last *different* price
