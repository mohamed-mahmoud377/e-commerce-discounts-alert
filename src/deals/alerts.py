"""Turning price changes into alerts, and delivering them.

Alerts go into the `alerts` table first (an outbox) and a background loop
delivers them to Telegram with retries, so a Telegram outage or a coordinator
restart never loses an alert.
"""

import html
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from curl_cffi import requests

from deals import db
from deals.config import Config
from deals.db import iso, parse_iso, utcnow
from deals.pricing import Deal, Point, PriceStats, ProductView, compute_stats, evaluate

log = logging.getLogger(__name__)

# Alert messages are in Arabic (Telegram renders them right-to-left).
KIND_TITLE = {
    "price_error": "🚨 خطأ محتمل في السعر",
    "price_drop": "🔥 انخفاض حقيقي في السعر",
    "all_time_low": "📉 أقل سعر على الإطلاق",
    "new_product": "🆕 منتج جديد بخصم كبير",
}
SITE_NAME = {"amazon_eg": "أمازون مصر", "noon_eg": "نون مصر"}
CONFIDENCE = {"high": "عالية", "medium": "متوسطة", "low": "منخفضة"}
SIGNAL_TEXT = {
    "only_left": "متبقٍ {n} فقط",
    "mega_deal": "عرض ضخم (Mega Deal)",
    "limited_deal": "عرض لفترة محدودة",
    "lowest_365d": "الموقع: أقل سعر خلال سنة",
    "lowest_30d": "الموقع: أقل سعر خلال 30 يومًا",
    "selling_fast": "ينفد بسرعة",
    "sold_recently": "بيع منه أكثر من {n} مؤخرًا",
}


def product_stats(conn: sqlite3.Connection, product_id: int, now: datetime) -> PriceStats:
    points = [Point(parse_iso(r["observed_at"]), r["price"], bool(r["in_stock"]))
              for r in db.history(conn, product_id)]
    return compute_stats(points, now)


def check_product(conn: sqlite3.Connection, cfg: Config, product_id: int, *, bootstrap: bool,
                  now: datetime) -> Deal | None:
    """Evaluate a product whose price just changed (or that just appeared); queue an alert if it's a deal."""
    p = db.product(conn, product_id)
    stats = product_stats(conn, product_id, now)
    watch = cfg.watch(p["grp"]) if p["grp"] else None
    view = ProductView(
        price=p["price"], in_stock=bool(p["in_stock"]), list_price=p["list_price"],
        list_price_kind=p["list_price_kind"], stock_left=p["stock_left"], recent_sales=p["recent_sales"],
        rating=p["rating"], reviews=p["reviews"], badges=tuple(b for b in p["badges"].split(",") if b),
    )
    deal = evaluate(view, stats, cfg.alerts, bootstrap=bootstrap,
                    min_price=watch.min_price if watch else None,
                    min_drop=watch.min_drop if watch else None)
    if deal is None or _recently_alerted(conn, cfg, product_id, deal.price, now):
        return None
    group = (watch.label or watch.name) if watch else (p["grp"] or "")
    conn.execute(
        """INSERT INTO alerts (product_id, kind, price, reference_price, drop_pct, score, message,
                               created_at, next_attempt_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (product_id, deal.kind, deal.price, deal.reference, deal.drop, deal.score,
         format_deal(p, stats, deal, group), iso(now), iso(now)),
    )
    return deal


def _recently_alerted(conn, cfg: Config, product_id: int, price: int, now: datetime) -> bool:
    """Don't repeat an alert unless the price fell noticeably further or a week passed."""
    since = iso(now - timedelta(days=cfg.alerts.realert_after_days))
    last = conn.execute(
        "SELECT price FROM alerts WHERE product_id = ? AND created_at >= ? ORDER BY created_at DESC LIMIT 1",
        (product_id, since),
    ).fetchone()
    return last is not None and price > last["price"] * (1 - cfg.alerts.realert_extra_drop)


def system_alert(conn: sqlite3.Connection, key: str, message: str, now: datetime,
                 cooldown: timedelta = timedelta(hours=6)) -> bool:
    """Operational alert (worker down, site blocked, parser broken), at most once per cooldown."""
    recent = conn.execute("SELECT 1 FROM alerts WHERE dedupe_key = ? AND created_at >= ?",
                          (key, iso(now - cooldown))).fetchone()
    if recent:
        return False
    conn.execute(
        "INSERT INTO alerts (kind, dedupe_key, message, created_at, next_attempt_at) VALUES ('system', ?, ?, ?, ?)",
        (key, f"⚙️ {html.escape(message, quote=False)}", iso(now), iso(now)),
    )
    return True


def format_deal(p: sqlite3.Row, stats: PriceStats, deal: Deal, group: str = "") -> str:
    def e(text: str) -> str:
        return html.escape(text, quote=False)  # quotes only matter inside attributes

    lines = [
        f"<b>{KIND_TITLE[deal.kind]} · خصم {deal.drop:.0%}</b>",
        f"<a href=\"{html.escape(p['url'])}\">{e(p['title'][:150])}</a>",
        "",
    ]
    if deal.kind == "new_product":
        label = "سعره الأخير على الموقع" if deal.list_price_kind == "was" else "السعر المشطوب على الموقع"
        lines.append(f"💰 السعر الآن: <b>{_egp(deal.price)}</b> ({label} {_egp(deal.reference)})")
    else:
        lines.append(f"💰 السعر الآن: <b>{_egp(deal.price)}</b> (سعره المعتاد {_egp(deal.reference)})")
    if stats.low is not None:
        lines.append(f"📊 الأسعار التي رصدناها: من {stats.low / 100:,.0f} إلى {_egp(stats.high)} · "
                     f"عدد التغييرات خلال 90 يومًا: {stats.changes_90d} · نتابعه منذ {_days(stats.tracked_days)}")

    if deal.kind == "new_product":
        lines.append("• منتج جديد علينا: الخصم معلن من الموقع ولم نتحقق منه بعد من سجل أسعارنا")
    else:
        lines.append(f"• أقل من سعره المعتاد بنسبة {deal.drop:.0%} (حسب آخر {_days(deal.reference_days)})")
    if deal.all_time_low:
        lines.append(f"• أقل سعر رأيناه على الإطلاق (أقل سعر سابق {_egp(deal.previous_low)})")
    if deal.previous:
        lines.append(f"• كان {_egp(deal.previous)} قبل التغيير مباشرة")
    if deal.inflated_list_price:
        lines.append(f"• تجاهل السعر المشطوب على الموقع ({_egp(deal.inflated_list_price)}): "
                     f"أعلى من أي سعر رأيناه ({_egp(stats.high)})")

    extras = [SIGNAL_TEXT[code].format(n=f"{n:,}" if n is not None else "") for code, n in deal.signals]
    if p["rating"] and p["reviews"]:
        extras.append(f"★ {p['rating']:.1f} ({p['reviews']:,} تقييم)")
    if extras:
        lines.append("⚡ " + e(" · ".join(extras)))

    where = [e(group), SITE_NAME.get(p["site"], p["site"])]
    if p["seller"]:
        where.append(f"البائع: {e(p['seller'])}")
    where += [f"الثقة: {CONFIDENCE[deal.confidence]}", f"النقاط: {deal.score}/100"]
    lines.append("🏷️ " + " · ".join(w for w in where if w))
    return "\n".join(lines)


def _egp(minor: int | None) -> str:
    return "-" if minor is None else f"{minor / 100:,.0f} جنيه"


def _days(days: float) -> str:
    """Arabic number agreement: يوم واحد، يومين، 3 أيام، 15 يومًا."""
    n = round(days)
    if n < 1:
        return "أقل من يوم"
    if n == 1:
        return "يوم واحد"
    if n == 2:
        return "يومين"
    return f"{n} أيام" if n <= 10 else f"{n} يومًا"


class TelegramError(RuntimeError):
    def __init__(self, status: int, description: str, retry_after: int | None = None):
        super().__init__(f"telegram HTTP {status}: {description}")
        self.status, self.description, self.retry_after = status, description, retry_after

    @property
    def forbidden(self) -> bool:
        """The user blocked the bot (or deleted their account): stop sending to them."""
        return self.status == 403


class Telegram:
    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, http_timeout: float = 20, **params) -> dict | list | bool:
        r = requests.post(f"https://api.telegram.org/bot{self.token}/{method}",
                          json={k: v for k, v in params.items() if v is not None}, timeout=http_timeout)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code != 200 or not data.get("ok"):
            raise TelegramError(r.status_code, data.get("description") or r.text[:200],
                                (data.get("parameters") or {}).get("retry_after"))
        return data["result"]

    def send(self, chat_id: int | str, text: str, buttons: list[list[dict]] | None = None) -> dict:
        return self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                         link_preview_options={"is_disabled": False, "prefer_small_media": True},
                         reply_markup={"inline_keyboard": buttons} if buttons else None)

    def updates(self, offset: int | None = None, wait: int = 0) -> list[dict]:
        """Incoming messages; with wait > 0 this long-polls (Telegram holds the request open)."""
        return self.call("getUpdates", http_timeout=wait + 15, offset=offset, timeout=wait,
                         allowed_updates=["message", "callback_query"])


class AlertSender(threading.Thread):
    """Hands each alert to its recipients and delivers them, retrying per person.

    Deal alerts go to every active subscriber; system alerts only to the admin.
    """

    def __init__(self, cfg: Config, stop: threading.Event, interval: float = 3.0, telegram: Telegram | None = None):
        super().__init__(name="alert-sender", daemon=True)
        self.cfg, self.stop_event, self.interval = cfg, stop, interval
        self.configured = bool(cfg.telegram_token and cfg.telegram_chat_id)
        self.telegram = telegram or Telegram(cfg.telegram_token)

    def run(self) -> None:
        conn = db.connect(self.cfg.db_path)
        if not self.configured:
            log.warning("telegram not configured: alerts are only stored (see 'deals alerts')")
        while not self.stop_event.wait(self.interval):
            try:
                now = utcnow()
                self.fan_out(conn, now)
                self.deliver(conn, now)
            except Exception:
                log.exception("alert delivery loop failed")

    def fan_out(self, conn: sqlite3.Connection, now: datetime) -> None:
        new = conn.execute("SELECT id, kind, message FROM alerts WHERE sent_at IS NULL ORDER BY created_at LIMIT 100").fetchall()
        for a in new:
            with conn:
                if not self.configured:
                    log.info("ALERT %s", a["message"].replace("\n", " | "))
                    conn.execute("UPDATE alerts SET sent_at = ?, error = 'telegram not configured' WHERE id = ?",
                                 (iso(now), a["id"]))
                    continue
                who = "is_admin = 1" if a["kind"] == "system" else "status = 'active'"
                recipients = [r["chat_id"] for r in conn.execute(f"SELECT chat_id FROM subscribers WHERE {who}")]
                for chat_id in recipients:
                    conn.execute("INSERT OR IGNORE INTO deliveries (alert_id, chat_id, next_attempt_at) VALUES (?, ?, ?)",
                                 (a["id"], chat_id, iso(now)))
                conn.execute("UPDATE alerts SET sent_at = ?, error = ? WHERE id = ?",
                             (iso(now), None if recipients else "no subscribers", a["id"]))

    def deliver(self, conn: sqlite3.Connection, now: datetime | None = None) -> int:
        now = now or utcnow()
        pending = conn.execute(
            """SELECT d.id, d.chat_id, d.attempts, a.message FROM deliveries d
               JOIN alerts a ON a.id = d.alert_id JOIN subscribers s ON s.chat_id = d.chat_id
               WHERE d.sent_at IS NULL AND d.attempts < 10 AND d.next_attempt_at <= ?
                 AND (s.status = 'active' OR s.is_admin = 1)
               ORDER BY d.id LIMIT 30""", (iso(now),)).fetchall()
        sent = 0
        for d in pending:
            try:
                self.telegram.send(d["chat_id"], d["message"])
            except TelegramError as exc:
                with conn:
                    if exc.forbidden:
                        log.info("chat %s blocked the bot; unsubscribing", d["chat_id"])
                        conn.execute("UPDATE subscribers SET status = 'blocked', updated_at = ? WHERE chat_id = ? "
                                     "AND is_admin = 0", (iso(now), d["chat_id"]))
                        conn.execute("UPDATE deliveries SET attempts = 10, error = ? WHERE id = ?",
                                     (exc.description[:300], d["id"]))
                    else:
                        self._retry_later(conn, d, exc, now, exc.retry_after)
            except Exception as exc:  # network trouble
                with conn:
                    self._retry_later(conn, d, exc, now)
            else:
                sent += 1
                with conn:
                    conn.execute("UPDATE deliveries SET sent_at = ?, attempts = attempts + 1, error = NULL WHERE id = ?",
                                 (iso(now), d["id"]))
                time.sleep(1)  # stay far below Telegram's per-chat limit
        return sent

    @staticmethod
    def _retry_later(conn, d, exc, now: datetime, retry_after: int | None = None) -> None:
        delay = timedelta(seconds=retry_after or min(3600, 30 * 2 ** d["attempts"]))
        log.warning("alert delivery %s to %s failed (%s); retrying in %s", d["id"], d["chat_id"], exc, delay)
        conn.execute("UPDATE deliveries SET attempts = attempts + 1, next_attempt_at = ?, error = ? WHERE id = ?",
                     (iso(now + delay), str(exc)[:300], d["id"]))
