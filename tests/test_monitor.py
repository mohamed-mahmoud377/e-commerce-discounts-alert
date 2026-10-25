import pytest
from fastapi.testclient import TestClient

from deals.coordinator import create_app

W1 = {"Authorization": "Bearer token-1"}


@pytest.fixture
def client(cfg):
    app = create_app(cfg, background=False)
    with TestClient(app) as c:
        yield c


def listing(sku):
    return {"site": "amazon_eg", "sku": sku, "title": sku, "url": "https://x", "price": 500000,
            "list_price": None, "currency": "EGP", "in_stock": True}


def lease(client):
    return client.post("/api/lease", json={"sites": ["amazon_eg"], "worker_sites": ["amazon_eg", "noon_eg"]},
                       headers=W1).json()["job"]


def device(client, name="w1"):
    data = client.get("/api/devices", params={"token": "token-1"}).json()
    return next(d for d in data["devices"] if d["name"] == name)


def state(client, site):
    return next(s for s in device(client)["sites"] if s["site"] == site)


def test_views_need_a_token_from_outside(client):
    assert client.get("/api/devices").status_code == 401
    assert client.get("/dashboard").status_code == 401
    assert client.get("/api/devices", params={"token": "token-1"}).status_code == 200


def test_device_states_follow_what_happens(client):
    job = lease(client)
    assert state(client, "amazon_eg")["state"] == "crawling"
    client.post(f"/api/jobs/{job['id']}/page", headers=W1, json={
        "lease_token": job["lease_token"], "page": 1, "listings": [listing("A1"), listing("A2")], "done": False})
    s = state(client, "amazon_eg")
    assert s["state"] == "crawling" and s["pages"] == 1 and s["products"] == 2
    client.post(f"/api/jobs/{job['id']}/blocked", headers=W1, json={"lease_token": job["lease_token"], "reason": "captcha"})
    s = state(client, "amazon_eg")
    assert s["state"] == "blocked" and "captcha" in s["detail"] and s["blocks"] == 1
    assert state(client, "noon_eg")["state"] == "idle"  # the other shop is unaffected


def test_logs_from_devices_are_readable(client):
    lease(client)  # registers the worker
    client.post("/api/logs", headers=W1, json={"events": [
        {"at": "2026-10-26T10:00:00+00:00", "level": "INFO", "message": "crawl amazon_eg category:1"},
        {"at": "2026-10-26T10:00:05+00:00", "level": "WARNING", "message": "blocked: captcha"}]})
    events = client.get("/api/events", params={"token": "token-1", "source": "w1"}).json()["events"]
    assert [e["message"] for e in events] == ["crawl amazon_eg category:1", "blocked: captcha"]
    warnings = client.get("/api/events", params={"token": "token-1", "level": "warning"}).json()["events"]
    assert [e["level"] for e in warnings] == ["WARNING"]
    page = client.get("/dashboard", params={"token": "token-1"}).text
    assert "w1" in page and "blocked: captcha" in page and "amazon_eg" in page
