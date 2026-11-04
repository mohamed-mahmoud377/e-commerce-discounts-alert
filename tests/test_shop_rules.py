"""Shop rules (shop_rules.py): placeholder prices and doubtful references, from real Jumia cases (4 Nov 2026)."""

from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from deals import db, shop_rules
from deals.alerts import check_product, product_stats
from deals.config import AlertRules, SiteSettings, Watch
from deals.coordinator import create_app
from deals.db import iso, utcnow
from deals.models import LIMITED_DEAL, Listing
from deals.pricing import ProductView, evaluate

W1 = {"Authorization": "Bearer token-1"}


def jumia(sku="SH287HA1QMP8VNAFAMZ", price_egp=30_397, list_egp=None, site="jumia_eg", **kw):
    return Listing(site, sku, kw.pop("title", f"Sharp AC {sku}"), f"https://x/{sku}", round(price_egp * 100),
                   list_egp and list_egp * 100, "EGP", kw.pop("in_stock", True),
                   list_price_kind="list" if list_egp else None, **kw)


@pytest.fixture
def conn(cfg):
    return db.connect(cfg.db_path)


def hits(conn, rule):
    return {r["sku"]: dict(r) for r in conn.execute("SELECT * FROM shop_rule_hits WHERE rule = ?", (rule,))}


def test_rule_catalogue_is_well_formed():
    assert len({r.id for r in shop_rules.RULES}) == len(shop_rules.RULES)
    for r in shop_rules.RULES:
        assert r.kind in shop_rules.KINDS and r.title and r.action and r.why and r.found
        if r.kind in ("placeholder", "doubtful_reference"):
            assert r.prices or r.below is not None
    assert shop_rules.low_stock_is_noise("jumia_eg") and not shop_rules.low_stock_is_noise("noon_eg")


def test_jumia_placeholders_become_no_price():
    for egp in (100_000, 50_000):
        cleaned, rule, caught = shop_rules.clean(jumia(price_egp=egp))
        assert rule.id == "jumia-placeholder-prices" and caught == egp * 100
        assert cleaned.price is None and not cleaned.in_stock and cleaned.list_price is None
    # A placeholder crossed-out price (Beko mixer 7,449 "was 50,000") is dropped, the price kept
    cleaned, rule, caught = shop_rules.clean(jumia(price_egp=7_449, list_egp=50_000))
    assert rule and cleaned.price == 744_900 and cleaned.in_stock and cleaned.list_price is None
    assert cleaned.list_price_kind is None
    # Real prices: 99,999 (iPhone 17 Pro Max), 10,000 (only doubtful), and the same value on another shop
    for listing in (jumia(price_egp=99_999), jumia(price_egp=10_000), jumia(price_egp=100_000, site="noon_eg")):
        cleaned, rule, _ = shop_rules.clean(listing)
        assert rule is None and cleaned == listing


def test_dubaiphone_one_pound_is_a_placeholder():
    assert shop_rules.clean(jumia(site="dubaiphone_eg", price_egp=1))[0].price is None
    assert shop_rules.clean(jumia(site="dubaiphone_eg", price_egp=35))[1] is None


def test_new_listing_at_placeholder_then_real_price_sends_no_alert(cfg, conn):
    """The Honor X7e case (alert 459, 30 Oct): first seen at 100,000, real price 12,620 a day later,
    alerted as an 87% early drop. With the old history cleaned there is nothing to measure against."""
    cfg = replace(cfg, alerts=AlertRules(confirm_low_confidence=False))
    t = utcnow()
    with conn:  # stored the way it was before the rule existed
        db.record(conn, jumia("HO474MP33YC0LNAFAMZ", 100_000), now=t - timedelta(days=2))
        rec = db.record(conn, jumia("HO474MP33YC0LNAFAMZ", 12_620), now=t)
    # the bug: the deal rules alone see an 87% drop below everything seen
    view = ProductView(price=1_262_000, in_stock=True, site="jumia_eg")
    assert evaluate(view, product_stats(conn, rec.product_id, t), cfg.alerts).kind == "early_drop"
    # two layers stop it: no alert measured against a placeholder, even while it is still stored...
    with conn:
        assert check_product(conn, cfg, rec.product_id, bootstrap=False, now=t) is None
    assert "stopped early_drop" in hits(conn, "jumia-placeholder-prices")["HO474MP33YC0LNAFAMZ"]["detail"]
    # ...and the stored placeholders are cleaned at start-up
    assert shop_rules.clean_history(conn) == 1
    assert [(r["price"], r["in_stock"]) for r in db.history(conn, rec.product_id)] == [(None, 0), (1_262_000, 1)]
    assert check_product(conn, cfg, rec.product_id, bootstrap=False, now=t) is None
    assert hits(conn, "jumia-placeholder-prices")["HO474MP33YC0LNAFAMZ"]["price"] == 10_000_000
    assert shop_rules.clean_history(conn) == 0  # idempotent: runs at every start


def test_parked_products_coming_back_send_no_alert(cfg, conn):
    """The air conditioners parked at 100,000 on 29 Oct: weeks at the placeholder outweigh the real
    price in the typical price, so coming back to the real price looked like a 70% drop."""
    cfg = replace(cfg, alerts=AlertRules(confirm_low_confidence=False))
    t = utcnow()
    with conn:
        db.record(conn, jumia(price_egp=30_397), now=t - timedelta(days=20))
        db.record(conn, jumia(price_egp=100_000), now=t - timedelta(days=15))
    shop_rules.clean_history(conn)
    p = db.find_product(conn, "jumia_eg", "SH287HA1QMP8VNAFAMZ")
    assert p["price"] is None and p["in_stock"] == 0  # the product shows as not for sale now
    with conn:
        rec = db.record(conn, jumia(price_egp=30_397), now=t)
    assert rec.status == "changed" and check_product(conn, cfg, rec.product_id, bootstrap=False, now=t) is None
    # ...and a real drop on it later is measured against its real price
    with conn:
        rec = db.record(conn, jumia(price_egp=21_000), now=t + timedelta(hours=1))
    deal = check_product(conn, cfg, rec.product_id, bootstrap=False, now=t + timedelta(hours=1))
    assert deal and deal.reference == 3_039_700


def test_no_alert_measured_against_a_doubtful_10000(cfg, conn):
    """Fresh microwave (alert 211, 27 Oct): listed at 10,000, then 5,941, alerted as 41% off."""
    cfg = replace(cfg, alerts=AlertRules(confirm_low_confidence=False))
    t = utcnow()
    for site in ("jumia_eg", "noon_eg"):
        with conn:
            db.record(conn, jumia("FR057MW", 10_000, site=site), now=t - timedelta(days=5))
            rec = db.record(conn, jumia("FR057MW", 5_941, site=site), now=t)
        deal = check_product(conn, cfg, rec.product_id, bootstrap=False, now=t)
        if site == "jumia_eg":
            assert deal is None
            hit = hits(conn, "jumia-doubtful-10000")["FR057MW"]
            assert hit["price"] == 1_000_000 and "price_drop" in hit["detail"] and "5,941" in hit["detail"]
        else:
            assert deal and deal.reference == 1_000_000  # 10,000 is a normal price elsewhere


@pytest.fixture
def jumia_client(cfg):
    cfg = replace(cfg, sites={**cfg.sites, "jumia_eg": SiteSettings(0, 0)},
                  watches=[Watch(name="ACs", targets={"jumia_eg": ["category:air-conditioners"]},
                                 interval_minutes=30, min_price=1000)])
    app = create_app(cfg, background=False)
    with TestClient(app) as c:
        c.conn = app.state.conn
        yield c


def post_page(client, listings):
    job = client.post("/api/lease", json={"sites": ["jumia_eg"]}, headers=W1).json()["job"]
    if job["kind"] == "plan":
        client.post(f"/api/jobs/{job['id']}/plan", headers=W1, json={
            "lease_token": job["lease_token"], "slices": [{"target": "category:air-conditioners", "total": 50}]})
        job = client.post("/api/lease", json={"sites": ["jumia_eg"]}, headers=W1).json()["job"]
    body = {"lease_token": job["lease_token"], "page": 1, "done": True, "listings": [
        {"site": x.site, "sku": x.sku, "title": x.title, "url": x.url, "price": x.price, "list_price": x.list_price,
         "currency": "EGP", "in_stock": x.in_stock, "list_price_kind": x.list_price_kind} for x in listings]}
    r = client.post(f"/api/jobs/{job['id']}/page", headers=W1, json=body)
    client.conn.execute("UPDATE jobs SET next_due_at = ?", (iso(utcnow() - timedelta(seconds=1)),))
    return r.json()


def test_coordinator_applies_placeholders_for_any_worker(jumia_client):
    conn = jumia_client.conn
    post_page(jumia_client, [jumia("KNOWN", 30_397)])
    # an old worker sends the placeholder as a price: stored as no price; a new product at it is not stored
    r = post_page(jumia_client, [jumia("KNOWN", 100_000), jumia("NEW", 50_000), jumia("MIXER", 7_449, list_egp=50_000)])
    assert r["ok"] and r["skipped"] == 1
    known = db.find_product(conn, "jumia_eg", "KNOWN")
    assert known["price"] is None and known["in_stock"] == 0
    assert db.find_product(conn, "jumia_eg", "NEW") is None
    mixer = db.find_product(conn, "jumia_eg", "MIXER")
    assert mixer["price"] == 744_900 and mixer["list_price"] is None
    caught = hits(conn, "jumia-placeholder-prices")
    assert set(caught) == {"KNOWN", "NEW", "MIXER"}
    assert caught["KNOWN"]["detail"] == "price" and caught["MIXER"]["detail"] == "crossed-out price"
    assert all(p is None or p < 10_000_000 for (p,) in conn.execute("SELECT price FROM price_history"))


def test_noon_new_listings_never_alert_from_the_sellers_claim(cfg, conn):
    """Unionaire 440L fridge (4 Nov): a new noon offer at 18,999 'was 30,000' with a Deal badge, while
    other shops sold it at 21,249. The same listing on amazon.eg would still alert."""
    cfg = replace(cfg, alerts=AlertRules(confirm_low_confidence=False))
    t = utcnow()
    for site, sku in (("amazon_eg", "B0FRIDGE"), ("noon_eg", "N440L@shop-a")):
        with conn:
            rec = db.record(conn, jumia(sku, 18_999, list_egp=30_000, site=site, badges=(LIMITED_DEAL,)), now=t)
        deal = check_product(conn, cfg, rec.product_id, bootstrap=False, now=t)
        if site == "amazon_eg":
            assert deal and deal.kind == "new_product"
        else:
            assert deal is None
            assert "stopped new_product" in hits(conn, "noon-no-new-listing-alerts")["N440L@shop-a"]["detail"]
    # noon still alerts from our own history: three days at 18,999, then 13,999
    with conn:
        db.record(conn, jumia("N440L@shop-a", 13_999, list_egp=30_000, site="noon_eg"), now=t + timedelta(days=3))
    deal = check_product(conn, cfg, rec.product_id, bootstrap=False, now=t + timedelta(days=3))
    assert deal and deal.kind == "price_drop" and deal.reference == 1_899_900
