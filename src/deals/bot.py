"""The Telegram bot's conversation: who may receive alerts.

- /start from someone new sends the admin (config [telegram] chat_id) a request
  with Approve / Reject buttons; approved people get deal alerts.
- /stop unsubscribes. People who were approved before can /start again without
  a new approval.
- System alerts (stalls, blocks, broken parsers) only ever go to the admin.

Updates are read by long polling, so nothing has to listen on a public port.
"""

import html
import logging
import sqlite3
import threading
from datetime import datetime

from deals import db
from deals.alerts import Telegram
from deals.config import Config
from deals.db import iso, utcnow

log = logging.getLogger(__name__)

REQUEST_SENT = ("👋 أهلًا {name}!\n"
                "تم إرسال طلب اشتراكك في تنبيهات العروض إلى المسؤول. ستصلك رسالة هنا فور الموافقة.")
PENDING = "⏳ طلبك قيد المراجعة، ستصلك رسالة فور الموافقة."
APPROVED = ("✅ تمت الموافقة على اشتراكك!\n"
            "ستصلك من الآن تنبيهات العروض الحقيقية من أمازون مصر ونون مصر: تلفزيونات، ثلاجات، تكييفات، "
            "موبايلات، لابتوبات، شاشات، سماعات وأجهزة مطبخ.\n"
            "لإيقاف التنبيهات أرسل /stop")
WELCOME_BACK = "✅ أهلًا بعودتك! تم تفعيل التنبيهات مرة أخرى. لإيقافها أرسل /stop"
REJECTED = "عذرًا، لم تتم الموافقة على طلب الاشتراك."
ALREADY_ACTIVE = "أنت مشترك بالفعل ✅ ستصلك العروض هنا. لإيقافها أرسل /stop"
STOPPED = "🔕 تم إيقاف التنبيهات. للاشتراك مرة أخرى أرسل /start"
NOT_SUBSCRIBED = "أنت غير مشترك حاليًا. للاشتراك أرسل /start"
HELP = ("🤖 بوت تنبيهات العروض الحقيقية من أمازون مصر ونون مصر.\n"
        "نقارن السعر بتاريخ أسعار المنتج نفسه، وليس بالسعر المشطوب على الموقع.\n\n"
        "/start للاشتراك\n/stop لإيقاف التنبيهات")
ADMIN_START = ("أنت المسؤول عن هذا البوت ✅\n"
               "تصلك كل تنبيهات العروض وتنبيهات النظام. عندما يضغط شخص /start ستصلك رسالة للموافقة أو الرفض.\n"
               "/subscribers لعرض المشتركين")
NOT_ALLOWED = "هذا الإجراء للمسؤول فقط"


def ensure_admin(conn: sqlite3.Connection, cfg: Config, now: datetime) -> None:
    """The configured chat is the admin and receives everything."""
    if not cfg.telegram_chat_id:
        return
    admin = int(cfg.telegram_chat_id)
    with conn:
        conn.execute("UPDATE subscribers SET is_admin = 0 WHERE chat_id != ?", (admin,))
        conn.execute(
            """INSERT INTO subscribers (chat_id, status, is_admin, approved_at, created_at, updated_at)
               VALUES (?, 'active', 1, ?, ?, ?)
               ON CONFLICT (chat_id) DO UPDATE SET is_admin = 1""",
            (admin, iso(now), iso(now), iso(now)),
        )


def handle_update(conn: sqlite3.Connection, cfg: Config, tg: Telegram, update: dict,
                  now: datetime | None = None) -> None:
    now = now or utcnow()
    admin = int(cfg.telegram_chat_id)
    if "callback_query" in update:
        _on_button(conn, tg, update["callback_query"], admin, now)
        return
    msg = update.get("message")
    if not msg or msg.get("chat", {}).get("type") != "private":
        return  # ignore groups and edits
    chat_id = msg["chat"]["id"]
    user = msg.get("from", {})
    name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or "صديقي"
    text = (msg.get("text") or "").strip()
    command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    sub = conn.execute("SELECT * FROM subscribers WHERE chat_id = ?", (chat_id,)).fetchone()

    if command == "/start":
        if chat_id == admin:
            tg.send(chat_id, ADMIN_START)
        elif sub and sub["status"] == "active":
            tg.send(chat_id, ALREADY_ACTIVE)
        elif sub and sub["status"] == "pending":
            tg.send(chat_id, PENDING)
        elif sub and sub["approved_at"] and sub["status"] in ("stopped", "blocked"):
            _set_status(conn, chat_id, "active", now)
            tg.send(chat_id, WELCOME_BACK)
        else:
            with conn:
                conn.execute(
                    """INSERT INTO subscribers (chat_id, name, username, status, created_at, updated_at)
                       VALUES (?, ?, ?, 'pending', ?, ?)
                       ON CONFLICT (chat_id) DO UPDATE SET name = excluded.name, username = excluded.username,
                           status = 'pending', updated_at = excluded.updated_at""",
                    (chat_id, name, user.get("username"), iso(now), iso(now)),
                )
            tg.send(chat_id, REQUEST_SENT.format(name=html.escape(name, quote=False)))
            handle = f" (@{user['username']})" if user.get("username") else ""
            tg.send(admin, f"🔔 <b>طلب اشتراك جديد</b>\n{html.escape(name, quote=False)}{handle}\nchat id: {chat_id}",
                    buttons=[[{"text": "✅ موافقة", "callback_data": f"approve:{chat_id}"},
                              {"text": "❌ رفض", "callback_data": f"reject:{chat_id}"}]])
            log.info("subscription request from %s (%s)", chat_id, name)
    elif command == "/stop":
        if sub and sub["status"] in ("active", "pending"):
            _set_status(conn, chat_id, "stopped", now)
            tg.send(chat_id, STOPPED)
        else:
            tg.send(chat_id, NOT_SUBSCRIBED)
    elif command == "/subscribers" and chat_id == admin:
        tg.send(chat_id, _subscriber_list(conn))
    else:
        tg.send(chat_id, HELP)


def _on_button(conn, tg: Telegram, cq: dict, admin: int, now: datetime) -> None:
    if cq.get("from", {}).get("id") != admin:
        tg.call("answerCallbackQuery", callback_query_id=cq["id"], text=NOT_ALLOWED)
        return
    action, _, target = (cq.get("data") or "").partition(":")
    sub = conn.execute("SELECT * FROM subscribers WHERE chat_id = ?", (int(target or 0),)).fetchone()
    if sub is None or action not in ("approve", "reject"):
        tg.call("answerCallbackQuery", callback_query_id=cq["id"], text="طلب غير معروف")
        return
    if action == "approve":
        with conn:
            conn.execute("UPDATE subscribers SET status = 'active', approved_at = ?, updated_at = ? WHERE chat_id = ?",
                         (iso(now), iso(now), sub["chat_id"]))
        tg.send(sub["chat_id"], APPROVED)
        result = "✅ تمت الموافقة"
    else:
        _set_status(conn, sub["chat_id"], "rejected", now)
        tg.send(sub["chat_id"], REJECTED)
        result = "❌ تم الرفض"
    message = cq.get("message") or {}
    if message:  # replace the buttons with the decision
        tg.call("editMessageText", chat_id=message["chat"]["id"], message_id=message["message_id"],
                text=f"{html.escape(message.get('text', ''), quote=False)}\n\n{result}", parse_mode="HTML")
    tg.call("answerCallbackQuery", callback_query_id=cq["id"], text=result)
    log.info("subscriber %s: %s", sub["chat_id"], action)


def _set_status(conn, chat_id: int, status: str, now: datetime) -> None:
    with conn:
        conn.execute("UPDATE subscribers SET status = ?, updated_at = ? WHERE chat_id = ?", (status, iso(now), chat_id))


def _subscriber_list(conn) -> str:
    labels = {"active": "✅ مشترك", "pending": "⏳ ينتظر الموافقة", "stopped": "🔕 أوقف التنبيهات",
              "rejected": "❌ مرفوض", "blocked": "🚫 حظر البوت"}
    rows = conn.execute("SELECT * FROM subscribers ORDER BY is_admin DESC, status, created_at").fetchall()
    lines = ["<b>المشتركون</b>"]
    for r in rows:
        who = html.escape(r["name"] or str(r["chat_id"]), quote=False)
        if r["username"]:
            who += f" (@{r['username']})"
        lines.append(f"{'👑 ' if r['is_admin'] else ''}{who}: {labels.get(r['status'], r['status'])}")
    return "\n".join(lines)


class BotListener(threading.Thread):
    """Long-polls Telegram for messages and button presses."""

    def __init__(self, cfg: Config, stop: threading.Event, telegram: Telegram | None = None):
        super().__init__(name="telegram-bot", daemon=True)
        self.cfg, self.stop_event = cfg, stop
        self.tg = telegram or Telegram(cfg.telegram_token)

    def run(self) -> None:
        conn = db.connect(self.cfg.db_path)
        backoff = 5.0
        while not self.stop_event.is_set():
            try:
                self.poll_once(conn, wait=50)
                backoff = 5.0
            except Exception as exc:
                log.warning("telegram polling failed (%s); retrying in %.0fs", exc, backoff)
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, 300)

    def poll_once(self, conn: sqlite3.Connection, wait: int = 0) -> int:
        row = conn.execute("SELECT value FROM kv WHERE key = 'telegram_offset'").fetchone()
        updates = self.tg.updates(offset=int(row["value"]) if row else None, wait=wait)
        for update in updates:
            try:
                handle_update(conn, self.cfg, self.tg, update)
            except Exception:
                log.exception("could not handle telegram update %s", update.get("update_id"))
            with conn:  # confirm it, even if handling failed, so one bad message can't jam the bot
                conn.execute("INSERT INTO kv (key, value) VALUES ('telegram_offset', ?) "
                             "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (str(update["update_id"] + 1),))
        return len(updates)
