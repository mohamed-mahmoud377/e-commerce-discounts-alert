"""The public release: every deal goes to the admins' review group, an approved one to the public channel."""
import threading
from dataclasses import replace
from datetime import timedelta

import pytest

from deals import alerts, bot, db, health, matching, publish, shotq
from deals.alerts import AlertSender, TelegramError, manual_alert, system_alert
from deals.config import ReviewRules, load
from deals.db import iso, utcnow
from deals.models import Listing
from test_coordinator import W1, client, lease, listing, plan_amazon  # noqa: F401 (client is a fixture)

ADMIN, FRIEND, HELPER = 100, 200, 300
REVIEW, CHANNEL = -1001, "@deals_eg"
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 2000
TITLE = "Samsung 55 Inch Crystal UHD TV, 4K, Smart, Black"


class FakeTelegram:
    def __init__(self):
        self.sent, self.photos, self.calls = [], [], []
        self.fail = None  # raised for anything sent to this chat: (chat, exception)
        self.fail_photo = None

    def _check(self, chat_id):
        if self.fail and self.fail[0] == chat_id:
            raise self.fail[1]

    def send(self, chat_id, text, buttons=None, reply_to=None):
        self._check(chat_id)
        self.sent.append({"chat": chat_id, "text": text, "buttons": buttons, "reply_to": reply_to})
        return {"message_id": 1000 + len(self.sent), "chat": {"username": "deals_eg"}}

    def send_photo(self, chat_id, photo, caption, buttons=None, reply_to=None):
        self._check(chat_id)
        if self.fail_photo:
            raise self.fail_photo
        self.photos.append({"chat": chat_id, "photo": photo, "caption": caption, "buttons": buttons, "reply_to": reply_to})
        return {"message_id": 2000 + len(self.photos), "chat": {"username": "deals_eg"},
                "photo": [{"file_id": "small"}, {"file_id": "FILE-1"}]}

    def call(self, method, **params):
        self.calls.append((method, params))
        return True

    def to(self, chat):
        return [m for m in self.sent if m["chat"] == chat]

    def answers(self):
        return [p["text"] for m, p in self.calls if m == "answerCallbackQuery"]

    def edits(self):
        return [p for m, p in self.calls if m == "editMessageText"]


@pytest.fixture
def env(cfg):
    cfg = replace(cfg, telegram_token="t", telegram_chat_id=str(ADMIN), telegram_review_chat_id=str(REVIEW),
                  telegram_public_chat_id=CHANNEL)
    conn = db.connect(cfg.db_path)
    now = utcnow()
    bot.ensure_admin(conn, cfg, now)
    with conn:
        for chat, manager in ((FRIEND, 0), (HELPER, 1)):
            conn.execute("INSERT INTO subscribers (chat_id, status, is_manager, name, created_at, updated_at) "
                         "VALUES (?, 'active', ?, 'x', ?, ?)", (chat, manager, iso(now), iso(now)))
    return cfg, conn, FakeTelegram()


def product(conn, sku="TV1", title=TITLE, was=1_000_000, site="amazon_eg", seller=None):
    """A product at `was` piastres for 60 days."""
    return db.record(conn, Listing(site, sku, title, f"https://shop.example/{sku}", was, None, "EGP", True, seller=seller),
                     grp="TVs", now=utcnow() - timedelta(days=60)).product_id


def drop_to(conn, cfg, pid, price, now=None):
    """The crawl that finds the product at `price`; returns the alert's id."""
    now = now or utcnow()
    p = db.product(conn, pid)
    db.record(conn, Listing(p["site"], p["sku"], p["title"], p["url"], price, None, "EGP", True, seller=p["seller"]), grp="TVs", now=now)
    deal = alerts.check_product(conn, cfg, pid, bootstrap=False, now=now)
    assert deal is not None
    return conn.execute("SELECT id FROM alerts WHERE product_id = ? ORDER BY id DESC", (pid,)).fetchone()["id"]


def sender_round(cfg, conn, tg, now=None):
    AlertSender(cfg, threading.Event(), telegram=tg).fan_out(conn, now or utcnow())
    publish.step(conn, cfg, tg, now or utcnow())


def press(conn, cfg, tg, data, who=ADMIN, message_id=1001, now=None):
    bot.handle_update(conn, cfg, tg, {"update_id": 1, "callback_query": {
        "id": "cb", "from": {"id": who, "first_name": "Mohamed"}, "data": data,
        "message": {"chat": {"id": REVIEW}, "message_id": message_id, "text": "card"}}}, now)


def review(conn, alert_id):
    return conn.execute("SELECT * FROM reviews WHERE alert_id = ?", (alert_id,)).fetchone()


def carded(env, price=600_000, **kw):
    """A deal whose review card is in the group."""
    cfg, conn, tg = env
    alert_id = drop_to(conn, cfg, product(conn, **kw), price)
    sender_round(cfg, conn, tg)
    return alert_id


# --- where alerts go


def test_a_deal_goes_to_the_review_group_only_never_to_subscribers(env, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    cfg, conn, tg = env
    alert_id = carded(env)
    AlertSender(cfg, threading.Event(), telegram=tg).deliver(conn)
    assert [m["chat"] for m in tg.sent] == [REVIEW]
    assert tg.photos == []  # no screenshot on the card
    assert conn.execute("SELECT COUNT(*) FROM deliveries WHERE alert_id = ?", (alert_id,)).fetchone()[0] == 0
    r = review(conn, alert_id)
    assert r["status"] == "pending" and r["message_id"] == 1001


def test_the_card_is_english_and_has_the_details_and_the_buttons(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    card = tg.sent[0]
    for words in (f"REVIEW #{alert_id}", "price_drop", "score", "6,000 EGP", "10,000 EGP", "−40%", "4,000 EGP",
                  "Tracked: 60 days", "Amazon", "https://shop.example/TV1"):
        assert words in card["text"], words
    assert not any("؀" <= c <= "ۿ" for c in card["text"].replace("تلفزيونات", ""))
    assert [b["callback_data"] for b in card["buttons"][0]] == [f"rv_ok:{alert_id}", f"rv_no:{alert_id}", f"rv_view:{alert_id}"]


def test_the_card_gives_every_price_its_own_line_and_separates_its_sections(env):
    carded(env)
    text = env[2].sent[0]["text"]
    lines = text.split("\n")
    for line in ("💰 Price: <b>6,000 EGP</b>", "Usual: <b>10,000 EGP</b> (over 60 days)", "Drop: <b>−40%</b>", "Saves: <b>4,000 EGP</b>"):
        assert line in lines, line
    assert lines.index("💰 Price: <b>6,000 EGP</b>") + 1 == lines.index("Usual: <b>10,000 EGP</b> (over 60 days)")
    assert len(text.split("\n\n")) >= 4  # header, title, prices, history


CODED = "Sharp AC AH-XP12BME"


def seen_now(conn, pid, price=None):
    p = db.product(conn, pid)
    db.record(conn, Listing(p["site"], p["sku"], p["title"], p["url"], price or p["price"], None, "EGP", True, seller=p["seller"]),
              grp="TVs", now=utcnow())


def matched_deal(env, confidence=None):
    """A deal on Amazon for a product noon (whose price moved) and Jumia (whose price did not) sell as well."""
    cfg, conn, tg = env
    pid = product(conn, "A1", CODED)
    noon = product(conn, "N1@s", CODED, was=1_100_000, site="noon_eg", seller="Shop")
    jumia = product(conn, "J1", CODED, was=950_000, site="jumia_eg")
    seen_now(conn, noon, 900_000)
    seen_now(conn, jumia)
    matching.rebuild(conn, utcnow())
    if confidence is not None:
        with conn:
            conn.execute("UPDATE models SET confidence = ?", (confidence,))
    alert_id = drop_to(conn, cfg, pid, 600_000)
    sender_round(cfg, conn, tg)
    return alert_id


def test_the_card_gives_each_other_shop_a_link_and_its_price_history(env):
    matched_deal(env)
    lines = env[2].sent[0]["text"].split("\n")
    at = lines.index("🏪 Elsewhere")
    assert lines[at + 1:at + 3] == [
        '<a href="https://shop.example/N1@s">noon</a>: 9,000 EGP (usual 11,000, lowest 9,000)',
        '<a href="https://shop.example/J1">Jumia</a>: 9,500 EGP (same price for 60 days)']


def test_the_card_labels_a_less_sure_match_and_the_old_alert_text_stays_without_it(env):
    alert_id = matched_deal(env, confidence=60)
    text = env[2].sent[0]["text"]
    assert "🏪 Elsewhere (unsure match, confidence 60)" in text and "9,000 EGP" in text
    assert "🏪" not in env[1].execute("SELECT message FROM alerts WHERE id = ?", (alert_id,)).fetchone()[0]


def test_the_card_leaves_out_a_match_below_the_floor(env):
    matched_deal(env, confidence=49)
    assert "🏪 Elsewhere: no usable match found" in env[2].sent[0]["text"]


def test_the_card_says_when_no_other_shop_has_it(env):
    carded(env)
    assert "🏪 Elsewhere: no usable match found" in env[2].sent[0]["text"]


def test_the_card_lists_another_seller_and_says_no_other_shop_has_it(env):
    cfg, conn, tg = env
    pid, deal = product(conn), alerts.Deal(kind="price_drop", price=600_000, reference=1_000_000, drop=0.4, previous=900_000,
                                           confidence="medium", score=40, reference_days=60)
    with conn:
        conn.execute("UPDATE products SET list_price = 1200000 WHERE id = ?", (pid,))
    lines = publish.format_card(1, db.product(conn, pid), alerts.product_stats(conn, pid, utcnow()), deal, "good", other_offer=620_000,
                                others=[], past=2, note="", now=utcnow()).split("\n")
    for line in ("Just before: 9,000 EGP", "Crossed out: 12,000 EGP (claims −50%)", "🏪 Elsewhere", "Other seller: 6,200 EGP",
                 "Other shops: no usable match found"):
        assert line in lines, line


def test_a_failed_lookup_costs_neither_the_card_nor_claims_there_is_no_match(env, monkeypatch):
    def boom(*a):
        raise RuntimeError("matching tables gone")
    monkeypatch.setattr(matching, "other_shops", boom)
    carded(env)
    text = env[2].sent[0]["text"]
    assert "💰 Price" in text and "no usable match" not in text and "Elsewhere" not in text


def test_the_card_warns_about_renewed_goods_and_marketplace_sellers(env):
    carded(env, title="Renewed - ProBook 640 G3 With 14-Inch Display", site="noon_eg", seller="Laptech")
    text = env[2].sent[0]["text"]
    assert "Renewed" in text and "marketplace seller" in text and "Laptech" in text


def test_the_card_warns_about_a_round_usual_price(env):
    carded(env, was=10_000_000, price=2_000_000)
    assert "round usual price 100,000" in env[2].sent[0]["text"]


def test_system_messages_still_go_to_the_admins_privately(env, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    cfg, conn, tg = env
    with conn:
        system_alert(conn, "k", "worker down", utcnow())
    sender = AlertSender(cfg, threading.Event(), telegram=tg)
    sender.fan_out(conn, utcnow())
    sender.deliver(conn)
    assert sorted(m["chat"] for m in tg.sent) == [ADMIN, HELPER]


def test_without_both_chats_nothing_changes(env, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    cfg, conn, tg = env
    cfg = replace(cfg, telegram_public_chat_id="")  # half set up: the old way
    alert_id = drop_to(conn, cfg, product(conn), 600_000)
    assert review(conn, alert_id) is None
    sender = AlertSender(cfg, threading.Event(), telegram=tg)
    sender.fan_out(conn, utcnow())
    sender.deliver(conn)
    assert sorted(m["chat"] for m in tg.sent) == [ADMIN, FRIEND, HELPER]


def test_a_burst_goes_out_at_one_card_a_round_best_deals_first(env):
    cfg, conn, tg = env
    weak = drop_to(conn, cfg, product(conn, "A"), 700_000)
    strong = drop_to(conn, cfg, product(conn, "B"), 450_000)
    sender_round(cfg, conn, tg)
    assert len(tg.sent) == 1 and f"#{strong}" in tg.sent[0]["text"]
    publish.step(conn, cfg, tg, utcnow())
    assert len(tg.sent) == 2 and f"#{weak}" in tg.sent[1]["text"]


def test_telegram_refusing_a_card_tries_again_later(env):
    cfg, conn, tg = env
    alert_id = drop_to(conn, cfg, product(conn), 600_000)
    tg.fail = (REVIEW, TelegramError(429, "Too Many Requests", 40))
    now = utcnow()
    sender_round(cfg, conn, tg, now)
    r = review(conn, alert_id)
    assert r["message_id"] is None and r["attempts"] == 1 and r["next_attempt_at"] == iso(now + timedelta(seconds=40))
    tg.fail = None
    publish.step(conn, cfg, tg, now)
    assert tg.sent == []  # not yet
    publish.step(conn, cfg, tg, now + timedelta(seconds=41))
    assert review(conn, alert_id)["message_id"] == 1001


def test_a_score_floor_skips_the_card(env):
    cfg, conn, tg = env
    cfg = replace(cfg, review=ReviewRules(min_score=99))
    alert_id = drop_to(conn, cfg, product(conn), 700_000)
    sender_round(cfg, conn, tg)
    assert tg.sent == [] and review(conn, alert_id)["status"] == "skipped"


# --- approving


def test_approve_posts_the_arabic_deal_with_its_screenshot_and_a_buy_button(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    with conn:
        conn.execute("INSERT INTO shots (alert_id, site, url, status, created_at, wait_until) "
                     "VALUES (?, 'amazon_eg', 'u', 'ok', ?, ?)", (alert_id, iso(utcnow()), iso(utcnow())))
    cfg.shots_dir.mkdir()
    (cfg.shots_dir / f"{alert_id}.jpg").write_bytes(JPEG)
    press(conn, cfg, tg, f"rv_ok:{alert_id}")
    r = review(conn, alert_id)
    assert r["status"] == "approved" and r["decided_by"] == ADMIN and r["decided_by_name"] == "Mohamed"
    publish.step(conn, cfg, tg, utcnow())

    post = tg.photos[0]
    assert post["chat"] == CHANNEL and post["photo"] == JPEG
    assert "6,000" in post["caption"] and "<s>10,000</s>" in post["caption"] and "40%" in post["caption"]
    assert "Samsung 55 Inch Crystal UHD TV" in post["caption"] and len(post["caption"]) <= 1024
    assert "#أمازون_مصر" in post["caption"]
    assert post["buttons"][0][0]["url"] == "https://shop.example/TV1"
    r = review(conn, alert_id)
    assert r["status"] == "published" and r["public_message_id"] == 2001 and r["price"] == 600_000
    assert conn.execute("SELECT file_id FROM shots WHERE alert_id = ?", (alert_id,)).fetchone()["file_id"] == "FILE-1"
    # the card shows who decided and links the post, and has no buttons left
    last = tg.edits()[-1]
    assert "Approved by Mohamed" in last["text"] and "https://t.me/deals_eg/2001" in last["text"]
    assert "reply_markup" not in last


def test_no_screenshot_means_a_text_post(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    press(conn, cfg, tg, f"rv_ok:{alert_id}")
    publish.step(conn, cfg, tg, utcnow())
    posts = tg.to(CHANNEL)
    assert len(posts) == 1 and "6,000" in posts[0]["text"] and posts[0]["buttons"][0][0]["url"]


def test_a_screenshot_being_taken_holds_the_post_a_minute_at_most(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    now = utcnow()
    with conn:
        conn.execute("INSERT INTO shots (alert_id, site, url, status, created_at, wait_until) "
                     "VALUES (?, 'amazon_eg', 'u', 'taking', ?, ?)", (alert_id, iso(now), iso(now + timedelta(minutes=30))))
    press(conn, cfg, tg, f"rv_ok:{alert_id}", now=now)
    publish.step(conn, cfg, tg, now + timedelta(seconds=30))
    assert tg.to(CHANNEL) == []
    publish.step(conn, cfg, tg, now + timedelta(seconds=61))
    assert len(tg.to(CHANNEL)) == 1


def test_a_photo_telegram_rejects_never_costs_the_post(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    with conn:
        conn.execute("INSERT INTO shots (alert_id, site, url, status, created_at, wait_until) "
                     "VALUES (?, 'amazon_eg', 'u', 'ok', ?, ?)", (alert_id, iso(utcnow()), iso(utcnow())))
    cfg.shots_dir.mkdir()
    (cfg.shots_dir / f"{alert_id}.jpg").write_bytes(JPEG)
    tg.fail_photo = TelegramError(400, "Bad Request: IMAGE_PROCESS_FAILED")
    press(conn, cfg, tg, f"rv_ok:{alert_id}")
    publish.step(conn, cfg, tg, utcnow())
    assert len(tg.to(CHANNEL)) == 1 and review(conn, alert_id)["status"] == "published"


def test_helpers_may_decide_other_people_may_not(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    press(conn, cfg, tg, f"rv_ok:{alert_id}", who=FRIEND)
    assert review(conn, alert_id)["status"] == "pending" and tg.answers()[-1]
    press(conn, cfg, tg, f"rv_ok:{alert_id}", who=HELPER)
    assert review(conn, alert_id)["status"] == "approved" and review(conn, alert_id)["decided_by"] == HELPER


def test_two_admins_cannot_both_decide(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    press(conn, cfg, tg, f"rv_ok:{alert_id}")
    press(conn, cfg, tg, f"rv_why:{alert_id}:boring", who=HELPER)
    assert review(conn, alert_id)["status"] == "approved"
    assert "already" in tg.answers()[-1]
    press(conn, cfg, tg, f"rv_ok:{alert_id}", who=HELPER)
    publish.step(conn, cfg, tg, utcnow())
    publish.step(conn, cfg, tg, utcnow())
    assert len(tg.to(CHANNEL)) == 1


def test_a_failed_post_retries_then_says_so_and_can_be_pressed_again(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    press(conn, cfg, tg, f"rv_ok:{alert_id}")
    tg.fail = (CHANNEL, TelegramError(403, "Forbidden: bot is not a member of the channel chat"))
    now = utcnow()
    for i in range(publish.MAX_TRIES):
        publish.step(conn, cfg, tg, now + timedelta(days=i))
    r = review(conn, alert_id)
    assert r["status"] == "failed" and "not a member" in r["error"]
    last = tg.edits()[-1]
    assert "failed" in last["text"] and last["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"rv_ok:{alert_id}"
    tg.fail = None
    press(conn, cfg, tg, f"rv_ok:{alert_id}", now=now + timedelta(days=9))
    publish.step(conn, cfg, tg, now + timedelta(days=9))
    assert review(conn, alert_id)["status"] == "published"


# --- the price moved since the card went out


def price_now(conn, alert_id, price, in_stock=True):
    pid = conn.execute("SELECT product_id FROM alerts WHERE id = ?", (alert_id,)).fetchone()[0]
    p = db.product(conn, pid)
    db.record(conn, Listing(p["site"], p["sku"], p["title"], p["url"], price, None, "EGP", in_stock), grp="TVs", now=utcnow())


def test_sold_out_cannot_be_published(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    price_now(conn, alert_id, None, in_stock=False)
    press(conn, cfg, tg, f"rv_ok:{alert_id}")
    assert review(conn, alert_id)["status"] == "pending"
    edit = tg.edits()[-1]
    assert "sold out" in edit["text"].lower()
    actions = [b["callback_data"] for row in edit["reply_markup"]["inline_keyboard"] for b in row]
    assert f"rv_why:{alert_id}:gone" in actions and f"rv_force:{alert_id}" not in actions


def test_a_higher_price_asks_first_and_publishes_the_new_price_without_the_old_screenshot(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    with conn:
        conn.execute("INSERT INTO shots (alert_id, site, url, status, created_at, wait_until) "
                     "VALUES (?, 'amazon_eg', 'u', 'ok', ?, ?)", (alert_id, iso(utcnow()), iso(utcnow())))
    cfg.shots_dir.mkdir()
    (cfg.shots_dir / f"{alert_id}.jpg").write_bytes(JPEG)
    price_now(conn, alert_id, 800_000)
    press(conn, cfg, tg, f"rv_ok:{alert_id}")
    assert review(conn, alert_id)["status"] == "pending"
    edit = tg.edits()[-1]
    assert "8,000 EGP" in edit["text"]
    assert any(b["callback_data"] == f"rv_force:{alert_id}" for row in edit["reply_markup"]["inline_keyboard"] for b in row)

    press(conn, cfg, tg, f"rv_force:{alert_id}")
    publish.step(conn, cfg, tg, utcnow())
    assert tg.photos == []  # the screenshot shows the old price
    post = tg.to(CHANNEL)[0]["text"]
    assert "8,000" in post and "20%" in post and "6,000" not in post
    assert review(conn, alert_id)["price"] == 800_000


def test_a_price_back_at_its_usual_level_cannot_be_published(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    price_now(conn, alert_id, 1_000_000)
    press(conn, cfg, tg, f"rv_ok:{alert_id}")
    actions = [b["callback_data"] for row in tg.edits()[-1]["reply_markup"]["inline_keyboard"] for b in row]
    assert f"rv_force:{alert_id}" not in actions
    press(conn, cfg, tg, f"rv_force:{alert_id}")  # an old button can't do it either
    assert review(conn, alert_id)["status"] == "pending"


def test_a_lower_price_publishes_at_once_at_the_lower_price(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    price_now(conn, alert_id, 500_000)
    press(conn, cfg, tg, f"rv_ok:{alert_id}")
    publish.step(conn, cfg, tg, utcnow())
    assert "5,000" in tg.to(CHANNEL)[0]["text"] and review(conn, alert_id)["price"] == 500_000


# --- rejecting, preview, expiry


def test_reject_asks_why_and_keeps_the_reason(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    press(conn, cfg, tg, f"rv_no:{alert_id}")
    assert review(conn, alert_id)["status"] == "pending"
    keyboard = tg.calls[-2][1]["reply_markup"]["inline_keyboard"]
    actions = [b["callback_data"] for row in keyboard for b in row]
    assert {f"rv_why:{alert_id}:{r}" for r in publish.REASONS} <= set(actions) and f"rv_back:{alert_id}" in actions

    press(conn, cfg, tg, f"rv_back:{alert_id}")
    assert [b["callback_data"] for b in tg.calls[-2][1]["reply_markup"]["inline_keyboard"][0]][0] == f"rv_ok:{alert_id}"

    press(conn, cfg, tg, f"rv_why:{alert_id}:used")
    r = review(conn, alert_id)
    assert r["status"] == "rejected" and r["reason"] == "used" and r["decided_by"] == ADMIN
    assert "Rejected by Mohamed" in tg.edits()[-1]["text"] and publish.REASONS["used"] in tg.edits()[-1]["text"]
    publish.step(conn, cfg, tg, utcnow())
    assert tg.to(CHANNEL) == []


def test_preview_shows_the_exact_post_in_the_group_and_publishes_nothing(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    with conn:
        conn.execute("INSERT INTO shots (alert_id, site, url, status, created_at, wait_until) "
                     "VALUES (?, 'amazon_eg', 'u', 'ok', ?, ?)", (alert_id, iso(utcnow()), iso(utcnow())))
    cfg.shots_dir.mkdir()
    (cfg.shots_dir / f"{alert_id}.jpg").write_bytes(JPEG)
    press(conn, cfg, tg, f"rv_view:{alert_id}")
    shown = tg.photos[0]
    assert shown["chat"] == REVIEW and shown["reply_to"] == 1001 and shown["buttons"][0][0]["url"]
    assert review(conn, alert_id)["status"] == "pending"
    assert conn.execute("SELECT file_id FROM shots WHERE alert_id = ?", (alert_id,)).fetchone()["file_id"] == "FILE-1"
    # the post itself reuses Telegram's copy
    press(conn, cfg, tg, f"rv_ok:{alert_id}")
    publish.step(conn, cfg, tg, utcnow())
    assert tg.photos[1]["chat"] == CHANNEL and tg.photos[1]["photo"] == "FILE-1"
    assert tg.photos[1]["caption"] == shown["caption"]  # the same post, the preview did not use up its hook


def test_a_deal_nobody_decided_expires_after_six_hours(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    assert cfg.review.expire_hours == 6
    publish.step(conn, cfg, tg, utcnow() + timedelta(hours=5))
    assert review(conn, alert_id)["status"] == "pending"
    later = utcnow() + timedelta(hours=6, minutes=1)
    publish.step(conn, cfg, tg, later)
    assert review(conn, alert_id)["status"] == "expired"
    assert "Expired" in tg.edits()[-1]["text"] and "reply_markup" not in tg.edits()[-1]


def test_a_late_press_after_expiry_time_does_nothing(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    press(conn, cfg, tg, f"rv_ok:{alert_id}", now=utcnow() + timedelta(hours=7))
    assert review(conn, alert_id)["status"] != "approved"
    assert "expired" in tg.answers()[-1].lower()


def test_a_card_never_posted_expires_quietly(env):
    cfg, conn, tg = env
    alert_id = drop_to(conn, cfg, product(conn), 600_000)
    AlertSender(cfg, threading.Event(), telegram=tg).fan_out(conn, utcnow())
    publish.step(conn, cfg, tg, utcnow() + timedelta(hours=7))
    assert tg.sent == [] and tg.edits() == [] and review(conn, alert_id)["status"] == "expired"


# --- the public text


def test_hooks_rotate_and_never_repeat_in_a_row(env):
    cfg, conn, tg = env
    hooks = []
    for i in range(6):
        alert_id = carded(env, sku=f"R{i}")
        press(conn, cfg, tg, f"rv_ok:{alert_id}", message_id=1)
        publish.step(conn, cfg, tg, utcnow())
        hooks.append(review(conn, alert_id)["hook"])
        with conn:  # as if the next one came later
            conn.execute("UPDATE reviews SET published_at = ? WHERE alert_id = ?", (iso(utcnow() + timedelta(seconds=i)), alert_id))
    assert all(a != b for a, b in zip(hooks, hooks[1:]))
    assert len(set(hooks)) >= 3


@pytest.mark.parametrize("pool", list(publish.HOOKS))
def test_every_hook_is_short_and_arabic_and_fills_in(pool):
    for hook in publish.HOOKS[pool]:
        text = hook.format(saving="4,000", pct=40)
        assert len(text) <= 60 and any("؀" <= c <= "ۿ" for c in text)


def test_tone_follows_the_quality_of_the_deal():
    rules = ReviewRules()
    deal = lambda **kw: alerts.Deal(**{"kind": "price_drop", "price": 1, "reference": 2, "drop": 0.25, "confidence": "medium", "score": 25, **kw})  # noqa: E731
    assert publish.tier(deal(), rules) == "good"
    assert publish.tier(deal(score=55), rules) == "strong"
    assert publish.tier(deal(drop=0.36), rules) == "strong"
    assert publish.tier(deal(score=75), rules) == "crazy"
    assert publish.tier(deal(kind="price_error", drop=0.7), rules) == "crazy"
    assert publish.tier(deal(all_time_low=True, drop=0.45, reference_days=30), rules) == "crazy"
    assert publish.tier(deal(all_time_low=True, drop=0.45, reference_days=5), rules) == "strong"  # hardly any history


def test_a_lowest_price_on_a_few_days_of_history_is_not_called_the_lowest(env):
    cfg, conn, tg = env
    pid = db.record(conn, Listing("amazon_eg", "NEW", TITLE, "https://shop.example/NEW", 1_000_000, None, "EGP", True),
                    grp="TVs", now=utcnow() - timedelta(days=5)).product_id
    alert_id = drop_to(conn, cfg, pid, 700_000)
    assert review(conn, alert_id)["all_time_low"] == 0
    sender_round(cfg, conn, tg)
    press(conn, cfg, tg, f"rv_ok:{alert_id}")
    publish.step(conn, cfg, tg, utcnow())
    assert not review(conn, alert_id)["hook"].startswith("low:") and "Lowest we saw" in tg.sent[0]["text"]


def test_a_new_listing_never_claims_our_own_history(env):
    cfg, conn, tg = env
    text = publish.render_post(kind="new_product", tier="strong", basis="claim", all_time_low=False, title=TITLE,
                               site="noon_eg", group="موبايلات", note=None, price=600_000, reference=1_000_000, index=0)[0]
    assert "حسب الموقع" in text
    assert "متابعتنا" not in text and "أقل سعر" not in text


def test_an_all_time_low_says_so_and_a_price_error_warns(env):
    args = dict(tier="crazy", title=TITLE, site="noon_eg", group="", note=None, price=300_000, reference=1_000_000, index=0)
    low = publish.render_post(kind="price_drop", basis="history", all_time_low=True, **args)[1]
    assert low.startswith("low:")
    text, hook = publish.render_post(kind="price_error", basis="history", all_time_low=True, **args)
    assert hook.startswith("error:") and "يلغي" in text


def test_a_renewed_product_says_so_on_the_public_post():
    text = publish.render_post(kind="price_drop", tier="good", basis="history", all_time_low=False,
                               title="Renewed - ProBook 640 G3 With 14-Inch Display", site="noon_eg", group="لابتوبات",
                               note=None, price=600_000, reference=1_000_000, index=0)[0]
    assert "متجدد" in text


def test_a_hand_picked_deal_with_no_discount_shows_no_crossed_out_price():
    text = publish.render_post(kind="manual", tier="good", basis="none", all_time_low=False, title=TITLE, site="amazon_eg",
                               group="", note="كوبون <SAVE10>", price=600_000, reference=600_000, index=0)[0]
    assert "<s>" not in text and "بدل" not in text and "كوبون &lt;SAVE10&gt;" in text


@pytest.mark.parametrize("title,short", [
    ("HP EliteBook 840 G7 Laptop, Intel Core i5-10310U, 256GB SSD, 8GB RAM, Intel UHD Graphics, 14 Inch FHD Display, Windows 10 Pro - Silver (Renewed), Grade A",
     "HP EliteBook 840 G7 Laptop, Intel Core i5-10310U, 256GB SSD, 8GB RAM"),
    ("Tecno Spark 50 - 6GB RAM - 128GB - Halo Blue", "Tecno Spark 50 - 6GB RAM - 128GB - Halo Blue"),
    ("Ugreen X524 45W USB-C GaN Power Adapter - White white", "Ugreen X524 45W USB-C GaN Power Adapter - White white"),
    ("Magnetic Wireless Power Bank 10000mAh - 2026 Upgraded 22.5W PD Fast Charging Portable Charger with 15W Wireless Charging, 11N Strong",
     "Magnetic Wireless Power Bank 10000mAh"),
    ("Pluto Hand Blender 1200W with 500ml Chopper Whisk Measuring Cup Stainless Steel Potato Masher Multi-Attachment Immersion Blender",
     "Pluto Hand Blender 1200W with 500ml Chopper Whisk Measuring Cup…"),
])
def test_short_titles(title, short):
    assert publish.short_title(title) == short
    assert len(publish.short_title(title)) <= 71


def test_the_caption_always_fits_telegrams_limit():
    text = publish.render_post(kind="price_error", tier="crazy", basis="history", all_time_low=True, title="x" * 400, site="btech_eg",
                               group="أجهزة ألعاب (بلايستيشن، إكس بوكس، سويتش)", note="ن" * 300, price=1, reference=2, index=0)[0]
    assert len(text) <= 1024 and "#أجهزة_ألعاب " in text + " "


# --- hand-sent alerts, screenshots on disk, the bot


def test_a_hand_sent_alert_goes_through_review_with_its_note(env):
    cfg, conn, tg = env
    pid = product(conn)
    d = manual_alert(conn, cfg, pid, utcnow(), note="كوبون SAVE10", sender="mo", send=True)
    sender_round(cfg, conn, tg)
    assert [m["chat"] for m in tg.sent] == [REVIEW] and "كوبون SAVE10" in tg.sent[0]["text"]
    press(conn, cfg, tg, f"rv_ok:{d['alert_id']}")
    publish.step(conn, cfg, tg, utcnow())
    assert "كوبون SAVE10" in tg.to(CHANNEL)[0]["text"]


def test_the_manual_preview_says_it_goes_to_review(env):
    cfg, conn, tg = env
    assert manual_alert(conn, cfg, product(conn), utcnow())["review"] is True


def test_a_review_screenshot_is_kept_on_disk_and_waits_longer(env):
    cfg, conn, tg = env
    now = utcnow()
    shotq.seen_shooter(["amazon_eg"], now)
    alert_id = drop_to(conn, cfg, product(conn), 600_000, now)
    shot = conn.execute("SELECT * FROM shots WHERE alert_id = ?", (alert_id,)).fetchone()
    assert shot["wait_until"] == iso(now + timedelta(seconds=shotq.REVIEW_WAIT_SECONDS))
    # the card does not wait for it
    sender_round(cfg, conn, tg, now)
    assert len(tg.to(REVIEW)) == 1
    assert shotq.record(conn, alert_id, "w1", JPEG, None, now + timedelta(minutes=5), store=cfg.shots_dir) == "ok"
    assert (cfg.shots_dir / f"{alert_id}.jpg").read_bytes() == JPEG
    assert shotq.IMAGES.get(alert_id) is None


def test_old_screenshots_are_swept(env):
    cfg, conn, tg = env
    cfg.shots_dir.mkdir()
    old, new = cfg.shots_dir / "1.jpg", cfg.shots_dir / "2.jpg"
    old.write_bytes(JPEG), new.write_bytes(JPEG)
    import os
    stale = (utcnow() - timedelta(hours=49)).timestamp()
    os.utime(old, (stale, stale))
    publish.sweep_images(cfg, utcnow())
    assert not old.exists() and new.exists()


def test_start_sends_people_to_the_channel_without_a_request(env):
    cfg, conn, tg = env
    update = lambda chat: {"update_id": 1, "message": {"chat": {"id": chat, "type": "private"}, "from": {"id": chat, "first_name": "A"}, "text": "/start"}}  # noqa: E731
    bot.handle_update(conn, cfg, tg, update(400))
    assert "https://t.me/deals_eg" in tg.to(400)[0]["text"]
    assert conn.execute("SELECT 1 FROM subscribers WHERE chat_id = 400").fetchone() is None
    assert tg.to(ADMIN) == []
    bot.handle_update(conn, cfg, tg, update(FRIEND))  # an old subscriber too
    assert "https://t.me/deals_eg" in tg.to(FRIEND)[0]["text"]


def test_being_added_to_a_chat_tells_the_owner_its_id(env):
    cfg, conn, tg = env
    added = lambda who: {"update_id": 1, "my_chat_member": {  # noqa: E731
        "chat": {"id": -100555, "type": "channel", "title": "Deals"}, "from": {"id": who},
        "new_chat_member": {"status": "administrator"}}}
    bot.handle_update(conn, cfg, tg, added(ADMIN))
    assert "-100555" in tg.to(ADMIN)[0]["text"]
    bot.handle_update(conn, cfg, tg, added(999))  # a stranger adding the bot somewhere tells nobody
    assert len(tg.to(ADMIN)) == 1


def test_joining_a_channel_without_the_right_to_post_is_called_out(env):
    cfg, conn, tg = env
    joined = lambda member: {"update_id": 1, "my_chat_member": {  # noqa: E731
        "chat": {"id": -100555, "type": "channel", "title": "Deals"}, "from": {"id": ADMIN}, "new_chat_member": member}}
    bot.handle_update(conn, cfg, tg, joined({"status": "member"}))
    assert "as member" in tg.to(ADMIN)[-1]["text"] and "can't post" in tg.to(ADMIN)[-1]["text"]
    bot.handle_update(conn, cfg, tg, joined({"status": "administrator", "can_post_messages": False}))
    assert "can't post" in tg.to(ADMIN)[-1]["text"]
    bot.handle_update(conn, cfg, tg, joined({"status": "administrator", "can_post_messages": True}))
    assert "as administrator" in tg.to(ADMIN)[-1]["text"] and "can't post" not in tg.to(ADMIN)[-1]["text"]


def test_a_group_upgrade_and_the_id_command_give_the_chat_id(env):
    cfg, conn, tg = env
    group = {"id": -5, "type": "group"}
    bot.handle_update(conn, cfg, tg, {"update_id": 1, "message": {"chat": group, "from": {"id": ADMIN}, "migrate_to_chat_id": -1009}})
    assert "-1009" in tg.to(ADMIN)[0]["text"]
    bot.handle_update(conn, cfg, tg, {"update_id": 2, "message": {"chat": group, "from": {"id": HELPER}, "text": "/id@Jerrys_deals_bot"}})
    assert "-5" in tg.to(-5)[0]["text"]
    bot.handle_update(conn, cfg, tg, {"update_id": 3, "message": {"chat": group, "from": {"id": FRIEND}, "text": "/id"}})
    assert len(tg.to(-5)) == 1


def test_telegram_client_sends_buttons_and_replies_with_a_photo(monkeypatch):
    seen = {}

    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "result": {"message_id": 5}}

    def post(url, **kw):
        seen.update(kw)
        return R()

    monkeypatch.setattr(alerts.requests, "post", post)
    alerts.Telegram("t").send_photo("@c", b"\xff\xd8", "cap", buttons=[[{"text": "b", "url": "https://x"}]], reply_to=9)
    assert '"https://x"' in seen["data"]["reply_markup"] and '"message_id": 9' in seen["data"]["reply_parameters"]


# --- config, health, report, web UI


def test_config_reads_the_chats_and_the_review_rules(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[telegram]\nbot_token = "t"\nchat_id = "1"\nreview_chat_id = -100\npublic_chat_id = "@x"\n[review]\nexpire_hours = 3\n')
    c = load(path)
    assert c.telegram_review_chat_id == "-100" and c.telegram_public_chat_id == "@x" and c.review.expire_hours == 3
    assert c.review_enabled and c.shots_dir == c.data_dir / "shots"
    path.write_text('[telegram]\nbot_token = "t"\nchat_id = "1"\n')
    assert not load(path).review_enabled


def test_health_warns_about_failed_posts_and_a_stuck_queue(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    check = lambda now=None: next(c for c in health.run_checks(conn, cfg, now or utcnow()) if c.name == "review")  # noqa: E731
    assert check().level == health.OK and "1 waiting" in check().detail
    with conn:
        conn.execute("UPDATE reviews SET status = 'failed', decided_at = ? WHERE alert_id = ?", (iso(utcnow()), alert_id))
    assert check().level == health.FAIL
    with conn:
        conn.execute("UPDATE reviews SET status = 'pending', message_id = NULL WHERE alert_id = ?", (alert_id,))
    assert check(utcnow() + timedelta(minutes=45)).level == health.WARN


def test_the_daily_report_counts_the_decisions(env):
    cfg, conn, tg = env
    alert_id = carded(env)
    press(conn, cfg, tg, f"rv_why:{alert_id}:boring")
    assert "المراجعة" in health.daily_report(conn, cfg, utcnow())


def test_the_web_ui_shows_the_review_state(env):
    from deals import webui
    cfg, conn, tg = env
    alert_id = carded(env)
    row = next(a for a in webui.alerts(conn, utcnow(), 1, None, 10) if a["id"] == alert_id)
    assert row["review"] == "pending"


def test_a_screenshot_upload_from_a_worker_lands_on_disk(cfg):
    from fastapi.testclient import TestClient

    from deals.coordinator import create_app
    cfg = replace(cfg, telegram_review_chat_id="-1", telegram_public_chat_id="@x")
    app = create_app(cfg, background=False)
    with TestClient(app) as c:
        conn = app.state.conn
        with conn:
            conn.execute("INSERT INTO alerts (id, kind, message, created_at, next_attempt_at) VALUES (7, 'price_drop', 'm', ?, ?)",
                         (iso(utcnow()), iso(utcnow())))
            conn.execute("INSERT INTO shots (alert_id, site, url, status, created_at, wait_until) VALUES (7, 'amazon_eg', 'u', 'taking', ?, ?)",
                         (iso(utcnow()), iso(utcnow() + timedelta(minutes=5))))
        r = c.post("/api/shots/7", params={"status": "ok"}, content=JPEG, headers={**W1, "Content-Type": "image/jpeg"})
    assert r.json()["status"] == "ok" and (cfg.shots_dir / "7.jpg").read_bytes() == JPEG
