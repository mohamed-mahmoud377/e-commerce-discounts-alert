"""The parts of deal detection that live outside pricing.py: history noise, other sellers, confirmation."""

import threading
from dataclasses import replace
from datetime import timedelta

import pytest

from deals import db
from deals.alerts import AlertSender, check_product
from deals.config import AlertRules
from deals.db import iso, utcnow
from deals.models import LIMITED_DEAL, Listing


def listing(site="noon_eg", sku="N1@shop-a", price_egp=5000, **kw):
    return Listing(site, sku, kw.pop("title", f"TV {sku}"), f"https://x/{sku}", price_egp * 100, kw.pop("list_price", None),
                   "EGP", kw.pop("in_stock", True), **kw)


@pytest.fixture
def conn(cfg):
    return db.connect(cfg.db_path)


def history(conn, product_id):
    return [r["price"] for r in db.history(conn, product_id)]


def test_penny_moves_are_not_changes(conn):
    t = utcnow()
    with conn:
        rec = db.record(conn, listing("amazon_eg", "A1", 96.46), now=t)
        assert rec.status == "new"
        # creeping by a piastre each crawl: no new rows, no "changed"
        for i in range(1, 40):
            rec = db.record(conn, listing("amazon_eg", "A1", 96.46 + i / 100), now=t + timedelta(minutes=10 * i))
            assert rec.status == "same"
        assert history(conn, rec.product_id) == [9646]
        # ...but once the creep adds up to 1% it is recorded against the last recorded price
        rec = db.record(conn, listing("amazon_eg", "A1", 97.50), now=t + timedelta(days=1))
        assert rec.status == "changed" and rec.old_price == 9646
        assert history(conn, rec.product_id) == [9646, 9750]
        # a real change is still immediate
        rec = db.record(conn, listing("amazon_eg", "A1", 80), now=t + timedelta(days=2))
        assert rec.status == "changed" and rec.old_price == 9750
        # stock changes always count
        rec = db.record(conn, listing("amazon_eg", "A1", 80, in_stock=False), now=t + timedelta(days=3))
        assert rec.status == "changed"


def test_no_alert_when_another_seller_is_cheaper(cfg, conn):
    cfg = replace(cfg, alerts=AlertRules(confirm_low_confidence=False))
    t = utcnow()
    with conn:
        db.record(conn, listing(sku="N1@shop-b", price_egp=3000), grp="TVs", now=t)  # always cheap
        a = db.record(conn, listing(sku="N1@shop-a", price_egp=5000), grp="TVs", now=t - timedelta(days=10))
        db.record(conn, listing(sku="N1@shop-a", price_egp=3200), grp="TVs", now=t)
        assert check_product(conn, cfg, a.product_id, bootstrap=False, now=t) is None  # shop-b is still cheaper
        db.record(conn, listing(sku="N1@shop-a", price_egp=2900), grp="TVs", now=t + timedelta(hours=1))
        deal = check_product(conn, cfg, a.product_id, bootstrap=False, now=t + timedelta(hours=1))
        assert deal is not None and deal.kind == "price_drop"  # now the cheapest way to buy it
    assert db.best_other_offer(conn, "noon_eg", "N1@shop-a") == 300000
    assert db.best_other_offer(conn, "noon_eg", "N10@shop-a") is None  # prefix must not match N1
    assert db.best_other_offer(conn, "amazon_eg", "A1") is None


def held(conn):
    return conn.execute("SELECT id, sent_at, error, confirm_until FROM alerts ORDER BY id").fetchall()


def test_low_confidence_alert_waits_for_a_second_sighting(cfg, conn):
    t = utcnow()
    sender = AlertSender(cfg, threading.Event())
    with conn:
        p = db.record(conn, listing(sku="N2@shop-a", price_egp=1500, list_price=250000, badges=(LIMITED_DEAL,),
                                    list_price_kind="list"), grp="TVs", now=t)
        deal = check_product(conn, cfg, p.product_id, bootstrap=False, now=t)
    assert deal.kind == "new_product" and deal.confidence == "low"
    assert held(conn)[0]["confirm_until"] is not None
    sender.confirm(conn, t + timedelta(minutes=1))
    sender.fan_out(conn, t + timedelta(minutes=1))
    assert held(conn)[0]["sent_at"] is None  # not seen again yet: still held
    # seen again at the same price: released and handed out
    with conn:
        db.record(conn, listing(sku="N2@shop-a", price_egp=1500, list_price=250000, badges=(LIMITED_DEAL,),
                                list_price_kind="list"), grp="TVs", now=t + timedelta(minutes=20))
    sender.confirm(conn, t + timedelta(minutes=21))
    assert held(conn)[0]["confirm_until"] is None
    sender.fan_out(conn, t + timedelta(minutes=21))
    assert held(conn)[0]["sent_at"] is not None and held(conn)[0]["error"] != "cancelled: price not confirmed"


def test_low_confidence_alert_cancelled_when_price_reverts(cfg, conn):
    t = utcnow()
    sender = AlertSender(cfg, threading.Event())
    with conn:
        p = db.record(conn, listing(sku="N3@shop-a", price_egp=1500, list_price=250000, badges=(LIMITED_DEAL,),
                                    list_price_kind="list"), grp="TVs", now=t)
        check_product(conn, cfg, p.product_id, bootstrap=False, now=t)
        db.record(conn, listing(sku="N3@shop-a", price_egp=2500, list_price=250000), grp="TVs",
                  now=t + timedelta(minutes=15))
    sender.confirm(conn, t + timedelta(minutes=16))
    row = held(conn)[0]
    assert row["sent_at"] is not None and row["error"] == "cancelled: price not confirmed"
    # a cancelled alert doesn't block the next real one for this product
    with conn:
        db.record(conn, listing(sku="N3@shop-a", price_egp=1500, list_price=250000, badges=(LIMITED_DEAL,),
                                list_price_kind="list"), grp="TVs", now=t + timedelta(minutes=30))
        assert check_product(conn, cfg, p.product_id, bootstrap=False, now=t + timedelta(minutes=30)) is not None


def test_held_alert_goes_out_when_nobody_rechecks_in_time(cfg, conn):
    t = utcnow()
    sender = AlertSender(cfg, threading.Event())
    with conn:
        p = db.record(conn, listing(sku="N4@shop-a", price_egp=1500, list_price=250000, badges=(LIMITED_DEAL,),
                                    list_price_kind="list"), grp="TVs", now=t)
        check_product(conn, cfg, p.product_id, bootstrap=False, now=t)
    hold = max(cfg.alerts.confirm_minutes, cfg.watches[0].interval_minutes * 3 + 10)  # covers the slowest revisit
    sender.confirm(conn, t + timedelta(minutes=hold - 1))
    assert held(conn)[0]["confirm_until"] is not None  # still waiting for a recrawl
    sender.confirm(conn, t + timedelta(minutes=hold + 1))
    assert held(conn)[0]["confirm_until"] is None


def test_early_drop_is_sent_at_once_unless_configured(cfg, conn):
    t = utcnow()
    for sku, rules in [("N8@shop-a", cfg.alerts), ("N9@shop-a", replace(cfg.alerts, confirm_early_drops=True))]:
        with conn:
            p = db.record(conn, listing(sku=sku, price_egp=5000), grp="TVs", now=t - timedelta(days=2))
            db.record(conn, listing(sku=sku, price_egp=3000), grp="TVs", now=t)
            deal = check_product(conn, replace(cfg, alerts=rules), p.product_id, bootstrap=False, now=t)
        assert deal.kind == "early_drop" and deal.confidence == "low"
    rows = held(conn)
    assert rows[0]["confirm_until"] is None  # default: no hold
    assert rows[1]["confirm_until"] is not None  # confirm_early_drops = true: held like a new listing


def test_high_confidence_alert_is_not_held(cfg, conn):
    t = utcnow()
    with conn:
        p = db.record(conn, listing(sku="N5@shop-a", price_egp=10000), grp="TVs", now=t - timedelta(days=30))
        db.record(conn, listing(sku="N5@shop-a", price_egp=6000), grp="TVs", now=t)
        deal = check_product(conn, cfg, p.product_id, bootstrap=False, now=t)
    assert deal.kind == "price_drop" and deal.confidence == "high"
    assert held(conn)[0]["confirm_until"] is None


def test_vanished_seller_no_longer_counts_as_a_cheaper_offer(cfg, conn):
    cfg = replace(cfg, alerts=AlertRules(confirm_low_confidence=False))
    t = utcnow()
    with conn:
        db.record(conn, listing(sku="N7@shop-b", price_egp=3000), grp="TVs", now=t - timedelta(days=3))  # seen 3 days ago, then gone
        a = db.record(conn, listing(sku="N7@shop-a", price_egp=5000), grp="TVs", now=t - timedelta(days=10))
        db.record(conn, listing(sku="N7@shop-a", price_egp=3500), grp="TVs", now=t)
        assert db.best_other_offer(conn, "noon_eg", "N7@shop-a", t) is None
        assert check_product(conn, cfg, a.product_id, bootstrap=False, now=t).kind == "price_drop"
        db.record(conn, listing(sku="N7@shop-b", price_egp=3000), grp="TVs", now=t)  # back on the page today
        assert db.best_other_offer(conn, "noon_eg", "N7@shop-a", t) == 300000


def test_list_price_change_does_not_move_the_recorded_price(conn):
    t = utcnow()
    with conn:
        p = db.record(conn, listing("amazon_eg", "A9", 100, list_price=1200000), now=t)
        rec = db.record(conn, listing("amazon_eg", "A9", 100.5, list_price=1300000), now=t + timedelta(hours=1))  # +0.5% price, new list price
        assert rec.status == "same"  # a list-price-only change never triggers a deal check
        row = conn.execute("SELECT price, list_price, last_change_at FROM products WHERE sku = 'A9'").fetchone()
        assert row["price"] == 10000 and row["list_price"] == 1300000 and row["last_change_at"] == db.iso(t)
        assert [h["price"] for h in db.history(conn, p.product_id)] == [10000, 10000]


def test_other_shops_are_appended_without_touching_the_decision(cfg, conn, monkeypatch):
    from deals import matching

    cfg = replace(cfg, alerts=AlertRules(confirm_low_confidence=False))
    t = utcnow()
    with conn:
        a = db.record(conn, listing("amazon_eg", "A9", 5000, title="Sharp AC AH-XP12BME"), grp="TVs", now=t - timedelta(days=10))
        db.record(conn, listing("noon_eg", "N9@s", 3600, title="Sharp AC AH-XP12BME"), grp="TVs", now=t)
        db.record(conn, listing("jumia_eg", "J9", 4100, title="Sharp AC AH-XP12BME"), grp="TVs", now=t)
        db.record(conn, listing("amazon_eg", "A9", 3900, title="Sharp AC AH-XP12BME"), grp="TVs", now=t)
    matching.rebuild(conn, t)
    with conn:
        deal = check_product(conn, cfg, a.product_id, bootstrap=False, now=t)
    assert deal is not None and deal.kind == "price_drop"
    message = conn.execute("SELECT message FROM alerts").fetchone()[0]
    assert "نفس المنتج في محلات تانية" in message and "نون مصر" in message and "جوميا مصر" in message
    assert "أرخص عند نون مصر بفارق 300 جنيه" in message and message.index("🏪") < message.index("🔖")

    # the same decision, byte for byte, with the section off, and when the lookup blows up
    def again(cfg):
        with conn:
            conn.execute("DELETE FROM alerts")
            d = check_product(conn, cfg, a.product_id, bootstrap=False, now=t)
        return d, conn.execute("SELECT message FROM alerts").fetchone()[0]
    off, plain = again(replace(cfg, alerts=replace(cfg.alerts, show_other_shops=False)))
    assert off == deal and "🏪" not in plain and plain == message.split("\n\n🏪")[0] + "\n\n🔖" + message.rpartition("\n\n🔖")[2]

    def boom(conn, product_id, now):
        raise RuntimeError("matching tables gone")
    monkeypatch.setattr(matching, "other_shops", boom)
    crashed, text = again(cfg)
    assert crashed == deal and text == plain  # alerted all the same, without the section


def test_alert_for_an_unmatched_product_has_no_other_shops_section(cfg, conn):
    cfg = replace(cfg, alerts=AlertRules(confirm_low_confidence=False))
    t = utcnow()
    with conn:
        a = db.record(conn, listing("amazon_eg", "A8", 5000, title="Kettle"), grp="TVs", now=t - timedelta(days=10))
        db.record(conn, listing("amazon_eg", "A8", 3900, title="Kettle"), grp="TVs", now=t)
        assert check_product(conn, cfg, a.product_id, bootstrap=False, now=t) is not None
    assert "🏪" not in conn.execute("SELECT message FROM alerts").fetchone()[0]
