import threading
from dataclasses import replace

import pytest

from deals import bot, db
from deals.alerts import AlertSender, TelegramError, system_alert
from deals.db import iso, utcnow

ADMIN, FRIEND, HELPER, NEWBIE = 100, 200, 300, 400


class FakeTelegram:
    def __init__(self):
        self.sent, self.calls, self.queue, self.forbidden = [], [], [], set()

    def send(self, chat_id, text, buttons=None):
        if int(chat_id) in self.forbidden:
            raise TelegramError(403, "Forbidden: bot was blocked by the user")
        self.sent.append((int(chat_id), text, buttons))
        return {"message_id": len(self.sent)}

    def call(self, method, **params):
        self.calls.append((method, params))
        return True

    def updates(self, offset=None, wait=0):
        out = [u for u in self.queue if offset is None or u["update_id"] >= offset]
        return out

    def to(self, chat_id):
        return [text for c, text, _ in self.sent if c == chat_id]


@pytest.fixture
def env(cfg):
    cfg = replace(cfg, telegram_token="t", telegram_chat_id=str(ADMIN))
    conn = db.connect(cfg.db_path)
    bot.ensure_admin(conn, cfg, utcnow())
    return cfg, conn, FakeTelegram()


def message(update_id, chat_id, text, first_name="Ahmed"):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id, "type": "private"},
                                                "from": {"id": chat_id, "first_name": first_name}, "text": text}}


def button(update_id, from_id, data, chat=ADMIN, message_id=7):
    return {"update_id": update_id, "callback_query": {
        "id": "cb", "from": {"id": from_id}, "data": data,
        "message": {"chat": {"id": chat}, "message_id": message_id, "text": "طلب اشتراك جديد"}}}


def status(conn, chat_id):
    return conn.execute("SELECT status FROM subscribers WHERE chat_id = ?", (chat_id,)).fetchone()["status"]


def queue_alert(conn, kind="price_drop", text="deal!"):
    with conn:
        if kind == "system":
            system_alert(conn, f"k{text}", text, utcnow())
        else:
            conn.execute("INSERT INTO alerts (kind, message, created_at, next_attempt_at) VALUES (?, ?, ?, ?)",
                         (kind, text, iso(utcnow()), iso(utcnow())))


def send_all(cfg, conn, tg):
    sender = AlertSender(cfg, threading.Event(), telegram=tg)
    sender.fan_out(conn, utcnow())
    sender.deliver(conn)


def test_friend_needs_admin_approval(env, monkeypatch):
    cfg, conn, tg = env
    monkeypatch.setattr("time.sleep", lambda _: None)
    bot.handle_update(conn, cfg, tg, message(1, FRIEND, "/start"))
    assert status(conn, FRIEND) == "pending"
    assert "تم إرسال طلب اشتراكك" in tg.to(FRIEND)[0]
    request = [b for c, _, b in tg.sent if c == ADMIN][0]
    assert request[0][0]["callback_data"] == f"approve:{FRIEND}"

    # deals don't reach people waiting for approval
    queue_alert(conn)
    send_all(cfg, conn, tg)
    assert "deal!" not in tg.to(FRIEND) and "deal!" in tg.to(ADMIN)

    bot.handle_update(conn, cfg, tg, button(2, FRIEND, f"approve:{FRIEND}"))  # not the admin
    assert status(conn, FRIEND) == "pending"
    bot.handle_update(conn, cfg, tg, button(3, ADMIN, f"approve:{FRIEND}"))
    assert status(conn, FRIEND) == "active"
    assert "تمت الموافقة" in tg.to(FRIEND)[-1]
    assert any(m == "editMessageText" for m, _ in tg.calls)


def test_deals_go_to_everyone_system_alerts_to_admin_only(env, monkeypatch):
    cfg, conn, tg = env
    monkeypatch.setattr("time.sleep", lambda _: None)
    bot.handle_update(conn, cfg, tg, message(1, FRIEND, "/start"))
    bot.handle_update(conn, cfg, tg, button(2, ADMIN, f"approve:{FRIEND}"))
    queue_alert(conn, text="tv deal")
    queue_alert(conn, kind="system", text="noon stalled")
    send_all(cfg, conn, tg)
    assert "tv deal" in tg.to(FRIEND) and "tv deal" in tg.to(ADMIN)
    assert not any("noon stalled" in t for t in tg.to(FRIEND))
    assert any("noon stalled" in t for t in tg.to(ADMIN))


def test_stop_and_come_back_without_new_approval(env):
    cfg, conn, tg = env
    bot.handle_update(conn, cfg, tg, message(1, FRIEND, "/start"))
    bot.handle_update(conn, cfg, tg, button(2, ADMIN, f"approve:{FRIEND}"))
    bot.handle_update(conn, cfg, tg, message(3, FRIEND, "/stop"))
    assert status(conn, FRIEND) == "stopped"
    admin_messages = len(tg.to(ADMIN))
    bot.handle_update(conn, cfg, tg, message(4, FRIEND, "/start"))
    assert status(conn, FRIEND) == "active" and "أهلًا بعودتك" in tg.to(FRIEND)[-1]
    assert len(tg.to(ADMIN)) == admin_messages  # no new approval request


def test_blocking_the_bot_unsubscribes(env, monkeypatch):
    cfg, conn, tg = env
    monkeypatch.setattr("time.sleep", lambda _: None)
    bot.handle_update(conn, cfg, tg, message(1, FRIEND, "/start"))
    bot.handle_update(conn, cfg, tg, button(2, ADMIN, f"approve:{FRIEND}"))
    tg.forbidden.add(FRIEND)
    queue_alert(conn)
    send_all(cfg, conn, tg)
    assert status(conn, FRIEND) == "blocked"
    assert "deal!" in tg.to(ADMIN)


def test_listener_confirms_updates(env):
    cfg, conn, tg = env
    listener = bot.BotListener(cfg, threading.Event(), telegram=tg)
    tg.queue = [message(41, FRIEND, "/start"), message(42, FRIEND, "hello")]
    assert listener.poll_once(conn) == 2
    assert conn.execute("SELECT value FROM kv WHERE key = 'telegram_offset'").fetchone()["value"] == "43"
    assert listener.poll_once(conn) == 0  # already handled updates are not processed again


def subscribe(conn, cfg, tg, chat_id, update_id):
    bot.handle_update(conn, cfg, tg, message(update_id, chat_id, "/start"))
    bot.handle_update(conn, cfg, tg, button(update_id + 1, ADMIN, f"approve:{chat_id}"))


def answers(tg):
    return [p["text"] for m, p in tg.calls if m == "answerCallbackQuery"]


def test_owner_removes_someone_after_confirming(env, monkeypatch):
    cfg, conn, tg = env
    monkeypatch.setattr("time.sleep", lambda _: None)
    subscribe(conn, cfg, tg, FRIEND, 1)
    bot.handle_update(conn, cfg, tg, message(3, ADMIN, "/subscribers"))
    listing = [b for c, t, b in tg.sent if c == ADMIN and "المشتركون" in t][-1]
    assert listing[0][0]["callback_data"] == f"remove:{FRIEND}"

    bot.handle_update(conn, cfg, tg, button(4, FRIEND, f"remove_yes:{FRIEND}"))  # only the owner may
    assert status(conn, FRIEND) == "active"
    bot.handle_update(conn, cfg, tg, button(5, ADMIN, f"remove:{FRIEND}"))
    assert status(conn, FRIEND) == "active"  # first tap only asks
    confirm = [p for m, p in tg.calls if m == "editMessageText"][-1]
    assert confirm["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"remove_yes:{FRIEND}"
    bot.handle_update(conn, cfg, tg, button(6, ADMIN, f"remove_yes:{FRIEND}"))
    assert status(conn, FRIEND) == "removed" and "إلغاء اشتراكك" in tg.to(FRIEND)[-1]

    queue_alert(conn, text="after removal")
    send_all(cfg, conn, tg)
    assert "after removal" not in tg.to(FRIEND)
    # coming back needs a new approval
    bot.handle_update(conn, cfg, tg, message(7, FRIEND, "/start"))
    assert status(conn, FRIEND) == "pending"
    assert [b for c, _, b in tg.sent if c == ADMIN][-1][0][0]["callback_data"] == f"approve:{FRIEND}"


def test_helper_admin_gets_requests_warnings_and_report(env, monkeypatch):
    cfg, conn, tg = env
    monkeypatch.setattr("time.sleep", lambda _: None)
    subscribe(conn, cfg, tg, FRIEND, 1)
    subscribe(conn, cfg, tg, HELPER, 3)
    bot.handle_update(conn, cfg, tg, button(5, FRIEND, f"promote:{HELPER}"))  # only the owner may
    assert not bot.is_manager(conn, HELPER)
    bot.handle_update(conn, cfg, tg, button(6, ADMIN, f"promote:{HELPER}"))
    assert bot.is_manager(conn, HELPER) and "مساعدًا" in tg.to(HELPER)[-1]

    queue_alert(conn, kind="system", text="noon stalled")
    queue_alert(conn, text="tv deal")
    send_all(cfg, conn, tg)
    assert any("noon stalled" in t for t in tg.to(HELPER)) and any("noon stalled" in t for t in tg.to(ADMIN))
    assert not any("noon stalled" in t for t in tg.to(FRIEND))
    assert "tv deal" in tg.to(HELPER) and "tv deal" in tg.to(FRIEND)

    # a join request reaches both admins; the helper answers, both copies show who decided
    bot.handle_update(conn, cfg, tg, message(7, NEWBIE, "/start", first_name="Sara"))
    owner_copy = [i + 1 for i, (c, _, b) in enumerate(tg.sent) if c == ADMIN and b][-1]
    helper_copy = [i + 1 for i, (c, _, b) in enumerate(tg.sent) if c == HELPER and b][-1]
    bot.handle_update(conn, cfg, tg, button(8, HELPER, f"approve:{NEWBIE}", chat=HELPER, message_id=helper_copy))
    assert status(conn, NEWBIE) == "active"
    edits = {(p["chat_id"], p["message_id"]): p["text"] for m, p in tg.calls if m == "editMessageText"}
    assert "تمت الموافقة" in edits[(ADMIN, owner_copy)] and "تمت الموافقة" in edits[(HELPER, helper_copy)]
    # the owner pressing the stale copy changes nothing and says it was handled
    bot.handle_update(conn, cfg, tg, button(9, ADMIN, f"reject:{NEWBIE}", message_id=owner_copy))
    assert status(conn, NEWBIE) == "active" and "بالفعل" in answers(tg)[-1]
    # helpers can't remove people or name other helpers
    bot.handle_update(conn, cfg, tg, button(10, HELPER, f"remove_yes:{FRIEND}", chat=HELPER))
    bot.handle_update(conn, cfg, tg, button(11, HELPER, f"promote:{FRIEND}", chat=HELPER))
    assert status(conn, FRIEND) == "active" and not bot.is_manager(conn, FRIEND)

    bot.handle_update(conn, cfg, tg, button(12, ADMIN, f"demote:{HELPER}"))
    assert not bot.is_manager(conn, HELPER)
    queue_alert(conn, kind="system", text="disk low")
    send_all(cfg, conn, tg)
    assert not any("disk low" in t for t in tg.to(HELPER))
