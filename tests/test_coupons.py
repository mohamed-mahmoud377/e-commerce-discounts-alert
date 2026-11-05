"""Coupons: read from the shops' real pages, kept apart from price history, and able to cause a message only when honest."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

from conftest import fixture_json, fixture_text
import pytest

from deals import cli, coupon_messages, coupons, db, health, shots, webui
from deals.sites.amazon_eg import parse_promotion_page, parse_search_page
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
    ("Buy 2 get 200 EGP off", None, {"kind": "multibuy", "amount": 20_000, "min_quantity": 2}),
    ("Buy 2, Get 10% off", None, {"kind": "multibuy", "percent": 0.1, "min_quantity": 2}),
    ("Save big", None, {"amount": None, "percent": None}),
    ("100 EGP off", "On your first order", {"amount": None}),
    ("10% off", "With your CIB Credit Cards", {"kind": "bank"}),
])
def test_only_what_is_plainly_written_becomes_a_number(title, detail, expected):
    c = coupons.parse(title=title, detail=detail, code="X")
    assert {k: getattr(c, k) for k in expected} == expected


def test_how_much_of_the_order_a_multibuy_takes():
    def share(title, price=10_000):
        return coupons.order_share(coupons.parse(title=title, code="X"), price)
    assert share("Buy 2 get 1 free") == pytest.approx(1 / 3)
    assert share("Buy 2, Get 20% off") == 0.2
    assert share("Buy 2 get 200 EGP off", 50_000) == 0.2  # 200 off an order of two at 500
    assert coupons.order_share(Coupon(kind="multibuy", title="احصل على 3 بسعر 2", code="X"), 10_000) == pytest.approx(1 / 3)


def test_a_minimum_spend_is_valued_on_the_order_not_the_cheap_item():
    c = coupons.parse(title="Buy 200 get 50", code="X")
    assert (c.amount, c.min_order, c.kind) == (5_000, 20_000, "code")
    assert c.saving(3_000) is None and c.share(3_000) == 0.25  # one sweet at 30 EGP: a quarter off an order of them
    assert c.saving(30_000) == 5_000 and c.share(30_000) == pytest.approx(5 / 30)


def test_a_cap_lowers_what_a_quantity_offer_is_worth():
    c = Coupon(kind="multibuy", title="Buy 5, 5% off", percent=0.05, min_quantity=5, cap=5_000)
    assert c.share(10_000) == 0.05  # 5 at 100: 25 EGP off, under the cap
    assert c.share(100_000) == 0.01  # 5 at 1,000: 50 EGP of 5,000


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
    assert "خصم 1,000 جنيه" in row["message"] and "<code>MOBILE300</code>" in row["message"] and "• " not in row["message"]
    sender_round(cfg, conn, tg)
    assert "coupon" in tg.to(REVIEW)[0]["text"] and tg.to(ADMIN) == []
    press(conn, cfg, tg, f"rv_ok:{row['id']}")
    sender_round(cfg, conn, tg)
    post = tg.to(CHANNEL)[0]
    assert post["buttons"] == [[{"text": "🛒 كل المنتجات", "url": CODE.group_url}]]
    assert "كل المنتجات" not in post["text"]
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
    now = covered(conn, coupon=replace(CODE, amount=100_000, group_url=None))
    alerted = db.find_product(conn, "noon_eg", "P0")["id"]
    conn.execute("INSERT INTO alerts (product_id, kind, price, message, created_at, next_attempt_at) VALUES (?, 'price_drop', 1, 'x', ?, ?)",
                 (alerted, iso(now), iso(now)))
    coupon_messages.step(conn, cfg, now)
    message = conn.execute("SELECT message FROM alerts WHERE kind = 'coupon'").fetchone()["message"]
    assert "Phone P0" not in message and "Phone P1" in message


def test_a_multibuy_message_says_you_need_several(review_env):
    cfg, conn, tg = review_env
    now = covered(conn, coupon=coupons.parse(title="Buy 2 get 20% off", code="BUY2"))
    coupon_messages.step(conn, cfg, now)
    assert state(conn, "BUY2")["state"] == "sent"
    assert "اشتري 2 ووفر 20%" in conn.execute("SELECT message FROM alerts WHERE kind = 'coupon'").fetchone()["message"]


def test_a_shop_with_no_page_lists_a_few_products(review_env):
    cfg, conn, tg = review_env
    now = covered(conn, coupon=replace(CODE, amount=100_000, group_url=None))
    coupon_messages.step(conn, cfg, now)
    message = conn.execute("SELECT message FROM alerts WHERE kind = 'coupon'").fetchone()["message"]
    assert message.count("• ") == coupon_messages.SHOWN and "كل المنتجات" not in message


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
    assert "no product shows it" in tg.edits()[-1]["text"] and tg.to(CHANNEL) == []


# --- Amazon's promotions


PROMO = Coupon(kind="promo", title="Buy 400 get 75", code="A1GDSWPM8PBPCO", typed=False,
               group_url="https://www.amazon.eg/-/ar/promotion/psp/A1GDSWPM8PBPCO")


def amazon(price=30_000, sku="T0", coupon=PROMO, title=None):
    return Listing(site="amazon_eg", sku=sku, title=title or f"TRESemmé Shampoo {sku}", url=f"https://www.amazon.eg/-/en/dp/{sku}",
                   price=price, list_price=None, currency="EGP", in_stock=True, coupon=coupon)


def facts(code=PROMO.code, page="amazon_promo_spend.html.gz"):
    return {"site": "amazon_eg", "code": code, "status": "ok", **parse_promotion_page(fixture_text(page))}


def test_amazon_cards_give_the_promotion_id_and_never_a_code_to_type():
    found = {x.sku: x.coupon for x in parse_search_page(fixture_text("amazon_promos_search.html.gz")).listings if x.coupon}
    assert len(found) >= 50 and all(c.kind == "promo" and not c.typed and c.saving(1_000_000) is None for c in found.values())
    by_id = {c.code: c for c in found.values()}
    assert by_id["A18KBWDDL262Z4"].title == "Buy 5 products and get 5% off"
    assert by_id["A1GDSWPM8PBPCO"].group_url == PROMO.group_url
    assert coupons.is_bank(by_id["A10EJ9XHRVH2JC"].title)
    assert coupons.is_bank(by_id["A2XJ3FFU2U5GTP"].title)  # Elvive's card shows only the bank's line, see the next test


def test_a_card_showing_a_bank_line_is_what_its_page_says(conn):
    """Elvive's promotion is buy 2, save 15%, whatever its card says."""
    now = utcnow()
    elvive = replace(PROMO, code="A2XJ3FFU2U5GTP", title="20% off NBE Visa Signature Credit Cards")
    pid = db.record(conn, amazon(coupon=elvive), grp="g", now=now).product_id
    coupon_messages.record_promotion(conn, facts(elvive.code, "amazon_promo_dates.html.gz"), now)
    db.record(conn, amazon(coupon=elvive), grp="g", now=now)
    p = db.product(conn, pid)
    assert (p["coupon_kind"], p["coupon_percent"], p["coupon_min_quantity"]) == ("multibuy", 0.15, 2)
    assert coupons.headline(coupons.from_row(p)) == "اشتري 2 ووفر 15%"


def test_promotion_pages_give_their_numbers_and_dates():
    q = parse_promotion_page(fixture_text("amazon_promo_quantity.html.gz"))
    assert (q["min_quantity"], q["benefit_percent"], q["benefit_cap"]) == (5, 5.0, 50.0) and q["schedule"] == ""
    s = parse_promotion_page(fixture_text("amazon_promo_spend.html.gz"))
    assert (s["min_amount"], s["benefit_amount"]) == (400.0, 75.0)
    assert coupons.schedule_dates(s["schedule"]) == ("2026-11-05", "2026-11-12")
    ar = parse_promotion_page(fixture_text("amazon_promo_dates_ar.html.gz"))
    assert coupons.schedule_dates(ar["schedule"]) == ("2026-11-05", "2026-11-12") and ar["min_quantity"] == 2
    bank = parse_promotion_page(fixture_text("amazon_promo_bank.html.gz"))
    assert coupons.schedule_dates(bank["schedule"]) == (None, "2026-11-30")
    assert parse_promotion_page("<html><title>Page not found</title></html>") is None


def test_promotions_are_asked_for_one_at_a_time_up_to_the_hours_limit(conn):
    now = utcnow()
    for i in range(coupon_messages.READS_PER_HOUR + 3):
        db.record(conn, amazon(sku=f"S{i}", coupon=replace(PROMO, code=f"PROMO{i}")), grp="g", now=now)
    asked = [coupon_messages.next_promotion(conn, "amazon_eg", now) for _ in range(coupon_messages.READS_PER_HOUR + 1)]
    assert asked[0] == {"site": "amazon_eg", "code": "PROMO0", "url": PROMO.group_url}
    assert asked[-1] is None and len({a["code"] for a in asked[:-1]}) == coupon_messages.READS_PER_HOUR
    assert coupon_messages.next_promotion(conn, "noon_eg", now) is None
    # Unanswered (refused, or the worker went away): asked again hours later.
    later = now + coupon_messages.READ_AGAIN + timedelta(minutes=1)
    assert coupon_messages.next_promotion(conn, "amazon_eg", later)["code"] == "PROMO0"
    coupon_messages.record_promotion(conn, {"site": "amazon_eg", "code": "PROMO0", "status": "failed", "reason": "x"}, later)
    assert state(conn, "PROMO0")["read_at"] is None


def test_read_facts_stay_on_the_product_through_later_crawls_and_can_alert(conn, on):
    now = utcnow()
    pid = db.record(conn, amazon(price=50_000), grp="g", now=now - timedelta(days=20)).product_id
    assert coupons.after(db.product(conn, pid)) is None
    assert check_product(conn, on, pid, bootstrap=False, now=now) is None  # not read: words only
    coupon_messages.record_promotion(conn, facts(), now)
    assert db.record(conn, amazon(price=50_000), grp="g", now=now).coupon_new  # now it takes 75 off 500
    p = db.product(conn, pid)
    assert (p["coupon_kind"], p["coupon_amount"], p["coupon_min_order"], p["coupon_typed"]) == ("code", 7_500, 40_000, 0)
    assert coupons.after(p) == 42_500
    assert not db.record(conn, amazon(price=50_000), grp="g", now=now).coupon_new  # and does not flip back
    assert check_product(conn, on, pid, bootstrap=False, now=now) is None  # 15% is under the 20% rule


def test_a_promotion_with_a_bank_title_stays_a_bank_offer(conn, cfg):
    now = utcnow()
    bank = replace(PROMO, code="A10EJ9XHRVH2JC", title="20% off NBE Visa Signature Credit Cards")
    pid = db.record(conn, amazon(coupon=bank), grp="g", now=now).product_id
    coupon_messages.record_promotion(conn, facts(bank.code, "amazon_promo_bank.html.gz"), now)
    assert state(conn, bank.code)["state"] == "bank"
    assert not db.record(conn, amazon(coupon=bank), grp="g", now=now).coupon_new
    p = db.product(conn, pid)
    assert p["coupon_kind"] == "bank" and coupons.after(p) is None
    assert "بكارت البنك" in coupons.line(coupons.from_row(p), p["price"])


def test_an_ended_page_ends_the_promotion(conn):
    now = utcnow()
    db.record(conn, amazon(), grp="g", now=now)
    coupon_messages.record_promotion(conn, {"site": "amazon_eg", "code": PROMO.code, "status": "gone"}, now)
    assert state(conn, PROMO.code)["state"] == "ended"


def promoted(conn, page="amazon_promo_spend.html.gz", price=15_000, n=6, code=PROMO.code):
    """Products at a steady price, in a promotion that has been read."""
    now = utcnow()
    start = now - timedelta(hours=2)
    coupon = replace(PROMO, code=code, title="")
    for i in range(n):
        db.record(conn, amazon(price=price, sku=f"T{i}", coupon=None), grp="g", now=now - timedelta(days=20))
        db.record(conn, amazon(price=price, sku=f"T{i}", coupon=coupon), grp="g", now=start)
    coupon_messages.record_promotion(conn, facts(code, page), start)
    conn.execute("UPDATE coupons SET ends_at = '2099-11-12' WHERE ends_at IS NOT NULL")  # the saved page's date, in any year
    for i in range(n):
        db.record(conn, amazon(price=price, sku=f"T{i}", coupon=coupon), grp="g", now=start)
    return now


def test_a_promotion_post_is_one_line_a_date_and_a_screenshot_on_cheap_products_too(review_env, monkeypatch):
    """Like the posts this was built from: 75 off 400 on products that each cost 150."""
    cfg, conn, tg = review_env
    now = promoted(conn)
    monkeypatch.setattr(coupon_messages, "shotq", shotq_recorder := SimpleNamespace(calls=[], add=lambda *a, **k: shotq_recorder.calls.append(a)))
    coupon_messages.step(conn, cfg, now)
    row = conn.execute("SELECT * FROM alerts WHERE kind = 'coupon'").fetchone()
    assert "وفر 75 جنيه لما تشتري بـ 400 جنيه أو أكتر من TRESemmé" in row["message"]
    assert "⏳ لحد 12 نوفمبر" in row["message"] and PROMO.code not in row["message"].split("href")[0]
    assert "• " not in row["message"]
    assert shotq_recorder.calls[0][3:5] == ("amazon_eg", PROMO.group_url)


def test_an_unread_promotion_is_not_posted_and_is_given_up_on(review_env):
    cfg, conn, tg = review_env
    now = covered(conn, coupon=replace(PROMO, title="Buy 400 get 75"))
    coupon_messages.step(conn, cfg, now)
    assert state(conn, PROMO.code)["state"] == "new"
    coupon_messages.step(conn, cfg, now + timedelta(hours=49))
    assert state(conn, PROMO.code)["state"] == "unread"
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0


def test_an_ended_promotion_is_never_posted(review_env):
    cfg, conn, tg = review_env
    now = promoted(conn)
    conn.execute("UPDATE coupons SET ends_at = '2020-01-01'")
    coupon_messages.step(conn, cfg, now)
    assert state(conn, PROMO.code)["state"] == "ended" and conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0


def test_the_promotions_cap_can_put_it_under_the_floor(review_env):
    """Buy 5, 5% off, at most 50 EGP: on 2,000 EGP products that is half a percent."""
    cfg, conn, tg = review_env
    now = promoted(conn, "amazon_promo_quantity.html.gz", price=200_000)
    coupon_messages.step(conn, cfg, now)
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0


@pytest.mark.parametrize("coupon,brand,expected", [
    (Coupon(kind="code", title="x", amount=5_000, min_order=20_000), "Chew Chew", "وفر 50 جنيه لما تشتري بـ 200 جنيه أو أكتر من Chew Chew"),
    (Coupon(kind="multibuy", title="Buy 2 get 1 free"), "Braes", "3 بسعر 2 على منتجات Braes"),
    (Coupon(kind="multibuy", title="احصل على 3 بسعر 2"), "Braes", "3 بسعر 2 على منتجات Braes"),
    (Coupon(kind="multibuy", title="x", percent=0.05, cap=5_000, min_quantity=5), None, "اشتري 5 ووفر 5%، بحد أقصى 50 جنيه"),
    (Coupon(kind="multibuy", title="x", percent=0.15, min_quantity=2), None, "اشتري 2 ووفر 15%"),
    (Coupon(kind="multibuy", title="x", percent=0.5, min_quantity=2), None, "2 بسعر 1"),
    (Coupon(kind="code", title="x", amount=30_000), None, "خصم 300 جنيه"),
    (Coupon(kind="code", title="x", percent=0.1, cap=5_000), None, "خصم 10% بحد أقصى 50 جنيه"),
    (Coupon(kind="code", title="Use code <WF500>", code="WF500"), None, "Use code &lt;WF500&gt;"),
])
def test_headlines(coupon, brand, expected):
    assert coupons.headline(coupon, brand) == expected


def test_the_brand_is_named_only_when_most_products_share_it():
    assert coupon_messages._brand(["TRESemmé A", "tresemmé B", "TRESemmé C", "Other D"]) == "TRESemmé"
    assert coupon_messages._brand(["Dove A", "Lux B", "Pert C"]) is None


def test_a_promotion_id_is_never_shown_as_a_code():
    line = coupons.line(Coupon(kind="code", title="Buy 400 get 75", code="A1GDS", amount=7_500, min_order=40_000, typed=False), 50_000)
    assert "A1GDS" not in line and "425" in line


def test_the_post_uses_its_screenshot_and_goes_as_text_without_one(review_env):
    cfg, conn, tg = review_env
    now = promoted(conn)
    coupon_messages.step(conn, cfg, now)
    alert_id = conn.execute("SELECT id FROM alerts WHERE kind = 'coupon'").fetchone()["id"]
    sender_round(cfg, conn, tg)
    press(conn, cfg, tg, f"rv_ok:{alert_id}")
    sender_round(cfg, conn, tg)  # no worker can take one here
    assert tg.to(CHANNEL)[0]["buttons"][0][0]["url"] == PROMO.group_url and not tg.photos
    conn.execute("UPDATE reviews SET status = 'approved', attempts = 0 WHERE alert_id = ?", (alert_id,))
    conn.execute("INSERT OR REPLACE INTO shots (alert_id, site, url, status, created_at, wait_until, file_id) VALUES (?, 'amazon_eg', 'u', 'ok', ?, ?, 'FILE')",
                 (alert_id, iso(now), iso(now)))
    sender_round(cfg, conn, tg)
    assert tg.photos[0]["photo"] == "FILE" and "وفر 75" in tg.photos[0]["caption"]


def test_a_worker_is_asked_to_read_only_if_it_says_it_can(cfg):
    from fastapi.testclient import TestClient
    from deals.coordinator import create_app
    with TestClient(create_app(cfg, background=False)) as c:
        c.conn = c.app.state.conn
        plan_amazon(c)
        promo = {"kind": "promo", "title": "", "code": "PROMO1", "typed": False, "group_url": "https://www.amazon.eg/-/ar/promotion/psp/PROMO1"}
        job = lease(c)
        old = c.post(f"/api/jobs/{job['id']}/page", headers=W1, json={
            "lease_token": job["lease_token"], "page": 1, "listings": [listing("A1", coupon=promo)]}).json()
        assert "promotion" not in old
        new = c.post(f"/api/jobs/{job['id']}/page", headers=W1, json={
            "lease_token": job["lease_token"], "page": 2, "listings": [], "reads_promotions": True}).json()
        assert new["promotion"]["code"] == "PROMO1"
        r = c.post("/api/coupons/facts", headers=W1, json={
            "site": "amazon_eg", "code": "PROMO1", "status": "ok", "title": "Buy 400 get 75", "min_amount": 400, "benefit_amount": 75, "benefit_cap": 75})
        assert r.json()["ok"]
        row = c.conn.execute("SELECT * FROM coupons WHERE code = 'PROMO1'").fetchone()
        assert (row["amount"], row["min_order"], row["cap"], row["kind"]) == (7_500, 40_000, None, "code") and row["read_at"]


def test_the_worker_reads_a_promotion_and_reports_what_it_found():
    from deals.worker import Worker
    calls = []
    fake = SimpleNamespace(api=SimpleNamespace(call=lambda path, body: calls.append((path, body))),
                           site=lambda name: SimpleNamespace(fetch_promotion=lambda url: {"title": "t", "schedule": ""}))
    Worker.read_promotion(fake, {"site": "amazon_eg", "code": "P", "url": "u"})
    assert calls == [("/api/coupons/facts", {"site": "amazon_eg", "code": "P", "status": "ok", "title": "t", "schedule": ""})]
    fake.site = lambda name: SimpleNamespace(fetch_promotion=lambda url: None)
    Worker.read_promotion(fake, {"site": "amazon_eg", "code": "P", "url": "u"})
    assert calls[-1][1]["status"] == "gone"


def test_screenshots_pick_the_right_page_spec():
    assert shots.spec_for("amazon_eg", PROMO.group_url) is shots.AMAZON_PROMOTION
    assert shots.spec_for("amazon_eg", "https://www.amazon.eg/-/en/dp/B0") is shots.SPECS["amazon_eg"]
    assert shots.spec_for("noon_eg", "https://www.noon.com/egypt-en/eg-nov26-mobile-300/") is shots.NOON_COUPON_PAGE
    assert shots.spec_for("noon_eg", "https://www.noon.com/egypt-en/x/N1/p/") is shots.SPECS["noon_eg"]


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
