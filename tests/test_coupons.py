"""Coupons: read from the shops' real pages, kept apart from price history, and able to cause a message only when honest."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

from conftest import fixture_json, fixture_text
import pytest

from deals import cli, coupon_messages, coupons, db, health, webui
from deals.alerts import AlertSender, check_product
from deals.config import ReviewRules
from deals.db import iso, utcnow
from deals.models import Coupon, Listing
from deals.sites.btech_eg import parse_products
from deals.sites.carrefour_eg import parse_listing_page
from deals.sites.noon_eg import parse_catalog
from deals.sites.raneen_eg import parse_listing
from test_coordinator import W1, lease, listing, plan_amazon
from test_publish import ADMIN, CHANNEL, REVIEW, FakeTelegram, press, review, sender_round


# --- reading the shops


def test_noon_egypt_gives_the_code_its_value_and_the_page_of_its_products():
    found = {x.coupon.code: x.coupon for x in parse_catalog(fixture_json("noon_category_eg.json.gz"), 1).listings if x.coupon}
    assert found["APP200"].kind == "code" and found["APP200"].amount == 20_000
    assert found["APP200"].group_url == "https://www.noon.com/egypt-en/eg-nov26-app-200/"
    assert found["APP100"].min_order == 100_000
    assert found["CLEAN20"].kind == "multibuy" and found["CLEAN20"].saving(100_000) is None
    assert found["FINE50"].min_order == 35_000 and found["FINE50"].saving(30_000) is None and found["FINE50"].saving(40_000) == 5_000


def test_noon_bank_codes_are_marked_and_never_lower_a_price():
    found = {x.coupon.code: x.coupon for x in parse_catalog(fixture_json("noon_category.json.gz"), 1).listings if x.coupon}
    assert found["FAB10"].kind == "bank" and found["FAB10"].saving(1_000_000) is None
    assert found["CAMERAS20"].percent == 0.2 and found["CAMERAS20"].cap == 15_000
    assert found["CAMERAS20"].saving(1_000_000) == 15_000 and found["CAMERAS20"].saving(10_000) == 2_000
    assert found["B3GET10"].kind == "multibuy"


def test_raneen_gives_a_code_with_no_value():
    listings = parse_listing(fixture_text("raneen_dishwashers.html.gz"), 1).listings
    c = next(x.coupon for x in listings if x.coupon)
    assert c.code == "WF500" and c.kind == "code" and c.saving(1_000_000) is None


def test_carrefour_gives_an_amount_with_no_code():
    listings = parse_listing_page(fixture_text("carrefour_category.html.gz"), 1).listings
    found = [x.coupon for x in listings if x.coupon]
    assert found and all(c.kind == "badge" and c.code is None and c.amount for c in found)
    assert any(c.amount == 50_000 for c in found)


def test_btech_coupon_is_already_in_the_price():
    listings = parse_products(fixture_json("btech_products.json.gz"), 1).listings
    found = [x for x in listings if x.coupon]
    assert len(found) == 13 and all(x.coupon.kind == "auto" and x.coupon.amount for x in found)
    assert all(x.coupon.saving(x.price) is None for x in found)


@pytest.mark.parametrize("title,detail,expected", [
    ("300 EGP OFF", "300 EGP OFF 300 EGP OFF", {"amount": 30_000, "detail": None}),
    ("Extra 500EGP Off", None, {"amount": 50_000}),
    ("100 EGP off", "100 EGP off on orders over 1000 EGP", {"amount": 10_000, "min_order": 100_000}),
    ("Extra 10% OFF", "Extra 10% OFF up to 50 EGP", {"percent": 0.1, "cap": 5_000}),
    ("Buy 2 get 200 EGP off", None, {"kind": "multibuy", "amount": None}),
    ("Save big", None, {"amount": None, "percent": None}),
    ("100 EGP off", "On your first order", {"amount": None}),
    ("10% off", "With your CIB Credit Cards", {"kind": "bank"}),
])
def test_only_what_is_plainly_written_becomes_a_number(title, detail, expected):
    c = coupons.parse(title=title, detail=detail, code="X")
    assert {k: getattr(c, k) for k in expected} == expected


def test_how_much_of_the_order_a_multibuy_takes():
    assert coupons.multibuy_share("Buy 2 get 1 free") == pytest.approx(1 / 3)
    assert coupons.multibuy_share("Buy 2, Get 20% off") == 0.2
    assert coupons.multibuy_share("Buy 2 get 200 EGP off") is None


# --- recording


CODE = Coupon(kind="code", title="300 EGP OFF", code="MOBILE300", amount=30_000, group_url="https://www.noon.com/egypt-en/x/")


def item(price=1_000_000, coupon=None, unknown=False, sku="A"):
    return Listing(site="noon_eg", sku=sku, title=f"Phone {sku}", url=f"https://www.noon.com/{sku}", price=price, list_price=None,
                   currency="EGP", in_stock=True, coupon=coupon, coupon_unknown=unknown)


@pytest.fixture
def conn(cfg):
    return db.connect(cfg.db_path)


def test_a_coupon_is_kept_and_cleared_and_never_touches_price_history(conn):
    now = utcnow()
    pid = db.record(conn, item(), grp="g", now=now).product_id
    before = len(db.history(conn, pid))
    assert db.record(conn, item(coupon=CODE), grp="g", now=now).coupon_new
    p = db.product(conn, pid)
    assert p["coupon_code"] == "MOBILE300" and coupons.after(p) == 970_000 and p["price"] == 1_000_000
    assert p["last_change_at"] == iso(now) and len(db.history(conn, pid)) == before
    assert not db.record(conn, item(coupon=CODE), grp="g", now=now).coupon_new  # the same coupon is no news
    db.record(conn, item(), grp="g", now=now)
    assert db.product(conn, pid)["coupon_kind"] is None


def test_a_worker_too_old_to_read_coupons_leaves_the_stored_one(conn):
    pid = db.record(conn, item(coupon=CODE), grp="g").product_id
    db.record(conn, item(unknown=True), grp="g")
    assert db.product(conn, pid)["coupon_code"] == "MOBILE300"


def test_a_bigger_coupon_is_news_and_a_bank_code_is_not(conn):
    db.record(conn, item(coupon=CODE), grp="g")
    assert db.record(conn, item(coupon=replace(CODE, amount=50_000)), grp="g").coupon_new
    assert not db.record(conn, item(coupon=replace(CODE, amount=20_000)), grp="g").coupon_new
    assert not db.record(conn, item(sku="B", coupon=Coupon(kind="bank", title="10%", code="NBE", percent=0.1)), grp="g").coupon_new


def test_the_upload_says_when_the_worker_could_not_know():
    from deals.coordinator import ListingIn
    base = {"site": "noon_eg", "sku": "A", "title": "t", "url": "u", "price": 100, "list_price": None, "currency": "EGP", "in_stock": True}
    assert ListingIn(**base).to_listing().coupon_unknown
    assert not ListingIn(**base, coupon=None).to_listing().coupon_unknown
    got = ListingIn(**base, coupon={"kind": "code", "title": "x", "code": "C", "amount": 5}).to_listing()
    assert got.coupon == Coupon(kind="code", title="x", code="C", amount=5) and not got.coupon_unknown


def test_the_worker_always_sends_the_field():
    from deals.worker import _listing_json
    assert _listing_json(item())["coupon"] is None and "coupon_unknown" not in _listing_json(item())
    assert _listing_json(item(coupon=CODE))["coupon"]["code"] == "MOBILE300"


# --- a coupon on a product alert, and a coupon as the reason for one


def history(conn, price=1_000_000, days=20, coupon=None, sku="A", grp="g"):
    """A product that has sat at one price for a while."""
    now = utcnow()
    return db.record(conn, item(price=price, sku=sku), grp=grp, now=now - timedelta(days=days)).product_id, now


@pytest.fixture
def on(cfg):
    return replace(cfg, alerts=replace(cfg.alerts, coupon_alerts=True))


def test_a_product_alert_shows_the_coupon(conn, cfg):
    pid, now = history(conn)
    db.record(conn, item(price=700_000, coupon=CODE), grp="g", now=now)
    assert check_product(conn, cfg, pid, bootstrap=False, now=now).kind == "price_drop"
    message = conn.execute("SELECT message FROM alerts").fetchone()["message"]
    assert "<code>MOBILE300</code>" in message and "6,700 جنيه" in message


def test_a_bank_code_on_an_alert_names_the_card_and_auto_says_it_is_already_in(conn, cfg):
    bank = coupons.line(Coupon(kind="bank", title="10% off", code="NBE", detail="10% off NBE Visa"), 100_000)
    assert "بكارت البنك" in bank and "NBE Visa" in bank
    auto = coupons.line(Coupon(kind="auto", title="x", amount=42_400), 447_500)
    assert "محسوب بالفعل" in auto and "424" in auto
    multi = coupons.line(Coupon(kind="multibuy", title="Buy 2 get 20% off", code="B"), 100_000)
    assert "أكتر من قطعة" in multi
    short = coupons.line(Coupon(kind="code", title="100 off", code="C", amount=10_000, min_order=100_000), 50_000)
    assert "على الأقل" in short


def test_a_small_coupon_on_a_steady_price_is_no_alert(conn, on):
    pid, now = history(conn)
    db.record(conn, item(coupon=CODE), grp="g", now=now)  # 3% off
    assert check_product(conn, on, pid, bootstrap=False, now=now) is None


def test_a_big_coupon_on_a_steady_price_is_a_coupon_alert(conn, on):
    pid, now = history(conn)
    big = replace(CODE, amount=300_000)
    db.record(conn, item(coupon=big), grp="g", now=now)
    deal = check_product(conn, on, pid, bootstrap=False, now=now)
    assert deal.kind == "coupon_drop" and deal.price == 700_000 and deal.reference == 1_000_000
    row = conn.execute("SELECT * FROM alerts").fetchone()
    assert row["price"] == 700_000 and "السعر بالكوبون" in row["message"] and "السعر قبل الكوبون" in row["message"]
    assert db.product(conn, pid)["price"] == 1_000_000 and len(db.history(conn, pid)) == 1


def test_a_drop_plus_a_coupon_together_can_pass_the_rule(conn, cfg, on):
    pid, now = history(conn, days=10)  # under the 14 days a smaller all-time low would alert on
    db.record(conn, item(price=830_000, coupon=replace(CODE, amount=40_000)), grp="g", now=now)  # 17% + 4%
    assert check_product(conn, cfg, pid, bootstrap=False, now=now) is None  # switch off: the shelf price alone
    assert check_product(conn, on, pid, bootstrap=False, now=now).kind == "coupon_drop"


def test_raising_the_price_and_adding_a_coupon_gets_nowhere(conn, on):
    pid, now = history(conn)
    db.record(conn, item(price=1_300_000, coupon=replace(CODE, amount=300_000)), grp="g", now=now)
    assert check_product(conn, on, pid, bootstrap=False, now=now) is None


@pytest.mark.parametrize("coupon", [
    Coupon(kind="bank", title="50% off", code="NBE", percent=0.5),
    Coupon(kind="multibuy", title="Buy 2 get 50% off", code="B2"),
    Coupon(kind="auto", title="in the price", amount=500_000),
    Coupon(kind="code", title="big", code="C", amount=500_000, min_order=2_000_000),
    Coupon(kind="code", title="no value", code="WF500"),
])
def test_coupons_not_everyone_gets_at_this_price_never_cause_an_alert(conn, on, coupon):
    pid, now = history(conn)
    db.record(conn, item(coupon=coupon), grp="g", now=now)
    assert check_product(conn, on, pid, bootstrap=False, now=now) is None


def test_a_coupon_on_a_new_listing_rests_on_nothing_of_ours(conn, on):
    now = utcnow()
    pid = db.record(conn, item(coupon=replace(CODE, amount=500_000)), grp="g", now=now).product_id
    assert check_product(conn, on, pid, bootstrap=False, now=now) is None


def test_a_coupon_price_the_product_often_sits_at_is_no_alert(conn, on):
    """Cheap half the time: "how often it was this cheap" must be asked at the price after the coupon."""
    now = utcnow()
    pid = db.record(conn, item(price=700_000), grp="g", now=now - timedelta(days=20)).product_id
    db.record(conn, item(price=1_000_000), grp="g", now=now - timedelta(days=10))
    db.record(conn, item(price=1_000_000, coupon=replace(CODE, amount=300_000)), grp="g", now=now)
    assert check_product(conn, on, pid, bootstrap=False, now=now) is None


def test_the_alert_limit_holds_for_coupon_alerts(conn, on):
    pid, now = history(conn)
    db.record(conn, item(coupon=replace(CODE, amount=300_000)), grp="g", now=now)
    assert check_product(conn, on, pid, bootstrap=False, now=now)
    assert check_product(conn, on, pid, bootstrap=False, now=now + timedelta(hours=1)) is None
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1


# --- the review group


@pytest.fixture
def review_env(cfg):
    cfg = replace(cfg, telegram_token="t", telegram_chat_id=str(ADMIN), telegram_review_chat_id=str(REVIEW),
                  telegram_public_chat_id=CHANNEL, review=ReviewRules(),
                  alerts=replace(cfg.alerts, coupon_alerts=True, coupon_messages=True, screenshots=False))
    conn = db.connect(cfg.db_path)
    conn.execute("INSERT INTO subscribers (chat_id, status, is_admin, created_at, updated_at) VALUES (?, 'active', 1, ?, ?)",
                 (ADMIN, iso(utcnow()), iso(utcnow())))
    return cfg, conn, FakeTelegram()


def coupon_alert(env):
    cfg, conn, tg = env
    pid, now = history(conn)
    db.record(conn, item(coupon=replace(CODE, amount=300_000)), grp="g", now=now)
    check_product(conn, cfg, pid, bootstrap=False, now=now)
    sender_round(cfg, conn, tg)
    alert_id = conn.execute("SELECT id FROM alerts").fetchone()["id"]
    return pid, alert_id, now


def test_a_coupon_alert_is_reviewed_and_posted_at_the_price_after_the_coupon(review_env):
    cfg, conn, tg = review_env
    pid, alert_id, now = coupon_alert(review_env)
    card = tg.to(REVIEW)[0]["text"]
    assert "coupon_drop" in card and "7,000 EGP" in card and "MOBILE300" in card
    press(conn, cfg, tg, f"rv_ok:{alert_id}")
    sender_round(cfg, conn, tg)
    post = tg.to(CHANNEL)[0]["text"]
    assert "7,000 جنيه" in post and "<code>MOBILE300</code>" in post
    assert review(conn, alert_id)["status"] == "published" and review(conn, alert_id)["price"] == 700_000


def test_approving_after_the_coupon_ended_says_so(review_env):
    cfg, conn, tg = review_env
    pid, alert_id, now = coupon_alert(review_env)
    db.record(conn, item(), grp="g", now=now)  # same price, no coupon
    press(conn, cfg, tg, f"rv_ok:{alert_id}")
    assert "coupon is gone" in tg.edits()[-1]["text"] and review(conn, alert_id)["status"] == "pending"


# --- one message per coupon


def covered(conn, n=6, price=1_000_000, coupon=None, site_prefix="P", days=20):
    """n products with a history, then showing a coupon an hour and more ago."""
    now = utcnow()
    coupon = coupon or replace(CODE, amount=100_000)
    for i in range(n):
        db.record(conn, item(price=price + i * 1000, sku=f"{site_prefix}{i}"), grp="g", now=now - timedelta(days=days))
    then = now - timedelta(hours=2)
    for i in range(n):
        db.record(conn, item(price=price + i * 1000, sku=f"{site_prefix}{i}", coupon=coupon), grp="g", now=then)
    return now


def state(conn, code="MOBILE300"):
    return conn.execute("SELECT * FROM coupons WHERE code = ?", (code,)).fetchone()


def test_a_coupon_message_names_the_code_some_products_and_the_page_of_all(review_env):
    cfg, conn, tg = review_env
    now = covered(conn)
    coupon_messages.step(conn, cfg, now)
    assert state(conn)["state"] == "sent" and state(conn)["products"] == 6
    row = conn.execute("SELECT * FROM alerts WHERE kind = 'coupon'").fetchone()
    assert "MOBILE300" in row["message"] and row["message"].count("• ") == coupon_messages.SHOWN
    sender_round(cfg, conn, tg)
    assert "coupon" in tg.to(REVIEW)[0]["text"] and tg.to(ADMIN) == []
    press(conn, cfg, tg, f"rv_ok:{row['id']}")
    sender_round(cfg, conn, tg)
    post = tg.to(CHANNEL)[0]
    assert post["buttons"] == [[{"text": "🛒 كل المنتجات", "url": CODE.group_url}]]
    assert "كل المنتجات" not in post["text"] and "9,000" in post["text"]
    assert review(conn, row["id"])["status"] == "published"


def test_without_the_review_group_subscribers_get_it_with_the_link_in_the_text(review_env):
    cfg, conn, tg = review_env
    cfg = replace(cfg, telegram_review_chat_id="", telegram_public_chat_id="")
    now = covered(conn)
    coupon_messages.step(conn, cfg, now)
    sender = AlertSender(cfg, None, telegram=tg)
    sender.fan_out(conn, now)
    sender.deliver(conn, now)
    text = tg.to(ADMIN)[0]["text"]
    assert "MOBILE300" in text and "كل المنتجات" in text and CODE.group_url in text


def test_with_the_switch_off_the_decision_is_only_written_down(review_env):
    cfg, conn, tg = review_env
    cfg = replace(cfg, alerts=replace(cfg.alerts, coupon_messages=False))
    now = covered(conn)
    coupon_messages.step(conn, cfg, now)
    assert state(conn)["state"] == "would_send" and state(conn)["best_discount"] == pytest.approx(0.1)
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0
    coupon_messages.step(conn, replace(cfg, alerts=replace(cfg.alerts, coupon_messages=True)), now)
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0  # switching on later floods nobody


def test_a_new_code_waits_for_the_crawl_to_find_its_products(review_env):
    cfg, conn, tg = review_env
    now = covered(conn)
    coupon_messages.step(conn, cfg, now - timedelta(hours=1, minutes=30))
    assert state(conn)["state"] == "new"


@pytest.mark.parametrize("kw,amount", [({"n": 4}, 100_000), ({"n": 6}, 30_000)])
def test_too_few_products_or_too_small_stays_quiet_and_is_decided_after_two_days(review_env, kw, amount):
    cfg, conn, tg = review_env
    now = covered(conn, coupon=replace(CODE, amount=amount), **kw)
    coupon_messages.step(conn, cfg, now)
    assert state(conn)["state"] == "new"
    coupon_messages.step(conn, cfg, now + timedelta(hours=49))
    assert state(conn)["state"] == "below_floor"
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0


def test_the_discount_is_measured_against_the_products_own_history(review_env):
    """It was 8,000 until today: 1,000 off the raised 10,000 is no discount at all."""
    cfg, conn, tg = review_env
    now = utcnow()
    for i in range(6):
        db.record(conn, item(price=800_000, sku=f"P{i}"), grp="g", now=now - timedelta(days=20))
        db.record(conn, item(price=1_000_000, sku=f"P{i}", coupon=replace(CODE, amount=100_000)), grp="g", now=now - timedelta(hours=2))
    coupon_messages.step(conn, cfg, now)
    assert state(conn)["state"] == "new" and conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0


def test_a_limit_per_shop_per_day_best_value_first(review_env):
    cfg, conn, tg = review_env
    cfg = replace(cfg, alerts=replace(cfg.alerts, coupon_messages_per_day=1))
    now = covered(conn, coupon=replace(CODE, code="SMALL", amount=70_000), site_prefix="S")
    covered(conn, coupon=replace(CODE, code="BIG", amount=200_000), site_prefix="B")
    coupon_messages.step(conn, cfg, now)
    assert state(conn, "BIG")["state"] == "sent" and state(conn, "SMALL")["state"] == "new"
    coupon_messages.step(conn, cfg, now + timedelta(hours=25))
    assert state(conn, "SMALL")["state"] == "sent"


def test_a_code_comes_back_as_news_only_after_a_week_or_when_it_grows(review_env):
    cfg, conn, tg = review_env
    now = covered(conn)
    coupon_messages.step(conn, cfg, now)
    db.record(conn, item(sku="P0", coupon=replace(CODE, amount=100_000)), grp="g", now=now + timedelta(days=3))
    assert state(conn)["state"] == "sent"
    db.record(conn, item(sku="P0", coupon=replace(CODE, amount=100_000)), grp="g", now=now + timedelta(days=11))
    assert state(conn)["state"] == "new"
    coupon_messages.step(conn, cfg, now + timedelta(days=11, hours=2))
    db.record(conn, item(sku="P0", coupon=replace(CODE, amount=200_000)), grp="g", now=now + timedelta(days=12))
    assert state(conn)["state"] == "new" and state(conn)["amount"] == 200_000


def test_products_alerted_this_week_are_left_out_of_the_message(review_env):
    cfg, conn, tg = review_env
    now = covered(conn)
    alerted = db.find_product(conn, "noon_eg", "P0")["id"]
    conn.execute("INSERT INTO alerts (product_id, kind, price, message, created_at, next_attempt_at) VALUES (?, 'price_drop', 1, 'x', ?, ?)",
                 (alerted, iso(now), iso(now)))
    coupon_messages.step(conn, cfg, now)
    message = conn.execute("SELECT message FROM alerts WHERE kind = 'coupon'").fetchone()["message"]
    assert "Phone P0" not in message and "Phone P1" in message


def test_a_multibuy_message_says_you_need_several(review_env):
    cfg, conn, tg = review_env
    now = covered(conn, coupon=Coupon(kind="multibuy", title="Buy 2 get 20% off", code="BUY2"))
    coupon_messages.step(conn, cfg, now)
    assert state(conn, "BUY2")["state"] == "sent"
    assert "أكتر من قطعة" in conn.execute("SELECT message FROM alerts WHERE kind = 'coupon'").fetchone()["message"]


def test_bank_codes_and_codeless_coupons_get_no_message(conn, cfg):
    db.record(conn, item(coupon=Coupon(kind="bank", title="10%", code="NBE", percent=0.1)), grp="g")
    db.record(conn, item(sku="B", coupon=Coupon(kind="badge", title="Extra 500 off", amount=50_000)), grp="g")
    assert conn.execute("SELECT COUNT(*) FROM coupons").fetchone()[0] == 0


def test_an_approved_coupon_message_with_no_products_left_is_stopped(review_env):
    cfg, conn, tg = review_env
    now = covered(conn)
    coupon_messages.step(conn, cfg, now)
    alert_id = conn.execute("SELECT id FROM alerts").fetchone()["id"]
    sender_round(cfg, conn, tg)
    conn.execute("UPDATE products SET coupon_code = NULL, coupon_kind = NULL")
    press(conn, cfg, tg, f"rv_ok:{alert_id}")
    assert "No product shows this coupon" in tg.edits()[-1]["text"] and tg.to(CHANNEL) == []


# --- looking them up


def test_reports_and_lists_cope_with_an_alert_about_no_one_product(review_env, capsys, monkeypatch):
    cfg, conn, tg = review_env
    monkeypatch.setattr(cli, "_config", lambda args: cfg)
    now = covered(conn)
    coupon_messages.step(conn, cfg, now)
    conn.commit()
    assert "رسالة كوبون 1" in health.daily_report(conn, cfg, now)
    assert webui.alerts(conn, now, 1, None, 10)[0]["kind"] == "coupon"
    cli.cmd_alerts(SimpleNamespace(config=None, days=1, system=False, limit=10, verbose=False))
    assert "coupon" in capsys.readouterr().out


def test_the_coupons_command_lists_codes_products_and_finds_one(review_env, capsys, monkeypatch):
    cfg, conn, tg = review_env
    monkeypatch.setattr(cli, "_config", lambda args: cfg)
    covered(conn)
    conn.commit()
    args = dict(config=None, site=None, code=None, find=None, limit=100)
    cli.cmd_coupons(SimpleNamespace(**args))
    out = capsys.readouterr().out
    assert "MOBILE300" in out and "noon_eg" in out
    cli.cmd_coupons(SimpleNamespace(**{**args, "code": "MOBILE300"}))
    out = capsys.readouterr().out
    assert CODE.group_url in out and "Phone P3" in out
    cli.cmd_coupons(SimpleNamespace(**{**args, "find": "Phone P1"}))
    assert "9,010" in capsys.readouterr().out


def test_codes_and_their_products_can_be_looked_up(conn):
    now = covered(conn)
    db.record(conn, item(price=900_000, sku="OUT", coupon=replace(CODE, amount=100_000)), grp="g", now=now)
    codes = webui.coupon_codes(conn, "noon_eg", "MOBILE300")
    assert codes[0]["in_stock"] == 7 and codes[0]["group_url"] == CODE.group_url
    assert [p["title"] for p in webui.coupon_products(conn, "noon_eg", "MOBILE300", 2)] == ["Phone OUT", "Phone P0"]
    pid = db.find_product(conn, "noon_eg", "P0")["id"]
    page = webui.product(conn, pid, now)
    assert page["coupon_after"] == 900_000 and page["product"]["coupon_code"] == "MOBILE300"
    assert pid not in [o["id"] for o in page["coupon_products"]] and len(page["coupon_products"]) == 6


def test_a_page_upload_stores_the_coupon_and_can_trigger_an_alert(cfg):
    from fastapi.testclient import TestClient
    from deals.coordinator import create_app
    cfg = replace(cfg, alerts=replace(cfg.alerts, coupon_alerts=True))
    with TestClient(create_app(cfg, background=False)) as c:
        c.conn = c.app.state.conn
        plan_amazon(c)
        job = lease(c)
        then = utcnow() - timedelta(days=20)
        pid = db.record(c.conn, Listing("amazon_eg", "A1", "TV", "https://x/A1", 500_000, None, "EGP", True), grp="TVs", now=then).product_id
        c.conn.commit()
        big = {"kind": "code", "title": "1500 EGP off", "code": "BIG", "amount": 150_000}
        r = c.post(f"/api/jobs/{job['id']}/page", headers=W1, json={
            "lease_token": job["lease_token"], "page": 1, "done": True, "listings": [listing("A1", coupon=big)]})
        assert r.json()["deals"] == 1
        assert c.conn.execute("SELECT kind, price FROM alerts WHERE product_id = ?", (pid,)).fetchone()["kind"] == "coupon_drop"
        assert db.product(c.conn, pid)["coupon_code"] == "BIG"
