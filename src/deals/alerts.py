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

KIND_TITLE = {
    "price_error": "🚨 Possible price error",
    "price_drop": "🔥 Real price drop",
    "all_time_low": "📉 All-time low",
    "new_product": "🆕 New listing, big claimed discount",
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
    conn.execute(
        """INSERT INTO alerts (product_id, kind, price, reference_price, drop_pct, score, message,
                               created_at, next_attempt_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (product_id, deal.kind, deal.price, deal.reference, deal.drop, deal.score,
         format_deal(p, stats, deal), iso(now), iso(now)),
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


def format_deal(p: sqlite3.Row, stats: PriceStats, deal: Deal) -> str:
    def e(text: str) -> str:
        return html.escape(text, quote=False)  # quotes only matter inside attributes

    lines = [
        f"<b>{KIND_TITLE[deal.kind]} · -{deal.drop:.0%}</b>",
        f"<a href=\"{html.escape(p['url'])}\">{e(p['title'][:150])}</a>",
        "",
        f"💰 <b>{_egp(deal.price)}</b>  (typical {_egp(deal.reference)})" if deal.kind != "new_product"
        else f"💰 <b>{_egp(deal.price)}</b>  (site says {_egp(deal.reference)})",
    ]
    if stats.low is not None:
        lines.append(f"📊 Range we've seen: {_egp(stats.low)} – {_egp(stats.high)} · "
                     f"{stats.changes_90d} changes in 90d · tracked {stats.tracked_days:.0f} days")
    for reason in deal.reasons:
        lines.append(f"• {e(reason)}")
    extras = list(deal.signals)
    if p["rating"] and p["reviews"]:
        extras.append(f"★ {p['rating']:.1f} ({p['reviews']:,} reviews)")
    if extras:
        lines.append("⚡ " + e(" · ".join(extras)))
    lines.append(f"🏷️ {e(p['grp'] or '')} · {e(p['site'])} · confidence {deal.confidence} · score {deal.score}")
    return "\n".join(lines)


def _egp(minor: int | None) -> str:
    return "-" if minor is None else f"EGP {minor / 100:,.0f}"


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, chat_id

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> None:
        r = requests.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                  "link_preview_options": {"is_disabled": False, "prefer_small_media": True}},
            timeout=20,
        )
        if r.status_code == 429:
            retry = r.json().get("parameters", {}).get("retry_after", 30)
            raise RuntimeError(f"telegram rate limit, retry after {retry}s")
        if r.status_code != 200:
            raise RuntimeError(f"telegram HTTP {r.status_code}: {r.text[:200]}")

    def updates(self) -> list[dict]:
        r = requests.get(f"https://api.telegram.org/bot{self.token}/getUpdates", timeout=20)
        r.raise_for_status()
        return r.json().get("result", [])


class AlertSender(threading.Thread):
    """Delivers queued alerts. Retries with backoff; never drops an alert silently."""

    def __init__(self, cfg: Config, stop: threading.Event, interval: float = 3.0):
        super().__init__(name="alert-sender", daemon=True)
        self.cfg, self.stop_event, self.interval = cfg, stop, interval
        self.telegram = Telegram(cfg.telegram_token, cfg.telegram_chat_id)

    def run(self) -> None:
        conn = db.connect(self.cfg.db_path)
        if not self.telegram.configured:
            log.warning("telegram not configured: alerts are only stored (see 'deals alerts')")
        while not self.stop_event.wait(self.interval):
            try:
                self.deliver(conn)
            except Exception:
                log.exception("alert delivery loop failed")

    def deliver(self, conn: sqlite3.Connection, now: datetime | None = None) -> int:
        now = now or utcnow()
        pending = conn.execute(
            """SELECT id, message, attempts FROM alerts WHERE sent_at IS NULL AND attempts < 10
               AND next_attempt_at <= ? ORDER BY created_at LIMIT 20""", (iso(now),)).fetchall()
        sent = 0
        for a in pending:
            if not self.telegram.configured:
                log.info("ALERT %s", a["message"].replace("\n", " | "))
                with conn:
                    conn.execute("UPDATE alerts SET sent_at = ?, error = 'telegram not configured' WHERE id = ?",
                                 (iso(now), a["id"]))
                continue
            try:
                self.telegram.send(a["message"])
            except Exception as exc:
                delay = timedelta(seconds=min(3600, 30 * 2 ** a["attempts"]))
                log.warning("alert %s not sent (%s); retrying in %s", a["id"], exc, delay)
                with conn:
                    conn.execute("UPDATE alerts SET attempts = attempts + 1, next_attempt_at = ?, error = ? "
                                 "WHERE id = ?", (iso(now + delay), str(exc)[:300], a["id"]))
            else:
                sent += 1
                with conn:
                    conn.execute("UPDATE alerts SET sent_at = ?, attempts = attempts + 1, error = NULL "
                                 "WHERE id = ?", (iso(now), a["id"]))
                time.sleep(1)  # stay far below Telegram's per-chat limit
        return sent
