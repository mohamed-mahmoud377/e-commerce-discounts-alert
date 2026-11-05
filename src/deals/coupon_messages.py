"""One message per coupon that covers many products: the code, its value, a few products and a link to all of them.

Decided here, every few minutes in the Watchdog; delivered like any deal (the review group, or the subscribers). With
`alerts.coupon_messages` off, the decision is only written down: `coupons.state` then shows what would have gone out,
which is how the floor gets tuned, and codes active on the day it is switched on are already decided, so there is no
flood. See docs/COUPONS.md.
"""

import html
import logging
import sqlite3
from datetime import datetime, timedelta

from deals import alerts, coupons, publish, shop_rules, shop_settings
from deals.config import Config
from deals.db import iso, parse_iso
from deals.shops import SHOPS

log = logging.getLogger(__name__)

BATCH = 50  # codes looked at per round, so a burst of new codes can't hold the watchdog
CANDIDATES = 20  # products per code whose history is read
SHOWN = 5
GIVE_UP = timedelta(hours=48)  # a code that hasn't qualified by now is decided: not worth a message


def _offers(conn: sqlite3.Connection, cfg: Config, c: sqlite3.Row, now: datetime) -> tuple[int, list[dict]]:
    """How many in-stock watched products show the code, and its best ones: what each costs with the coupon
    against its own usual price (its shelf price while we have too little history to say)."""
    rows = conn.execute(
        """SELECT * FROM products WHERE site = ? AND coupon_code = ? AND in_stock = 1 AND price IS NOT NULL
           AND grp IS NOT NULL""", (c["site"], c["code"])).fetchall()
    share = coupons.multibuy_share(c["title"]) if c["kind"] == "multibuy" else None

    def after(p: sqlite3.Row) -> int | None:
        if c["kind"] == "multibuy":
            return round(p["price"] * (1 - share)) if share else None
        return coupons.after(p)

    priced = [(p, a) for p in rows if (a := after(p)) is not None]
    priced.sort(key=lambda x: x[1] / x[0]["price"])  # biggest cut first; history is read for these only
    offers = []
    for p, price in priced[:CANDIDATES]:
        stats = alerts.product_stats(conn, p["id"], now, at_price=price)
        enough = stats.typical_90d and stats.history_days >= cfg.alerts.min_history_days
        usual = stats.typical_90d if enough else p["price"]
        if shop_rules.doubtful(c["site"], usual):
            continue
        if usual > price:
            offers.append({"p": p, "price": price, "usual": usual, "discount": 1 - price / usual})
    offers.sort(key=lambda o: -o["discount"])
    return len(rows), offers


def _recently_alerted(conn: sqlite3.Connection, product_id: int, now: datetime) -> bool:
    return conn.execute(f"SELECT 1 FROM alerts WHERE product_id = ? AND created_at >= ? AND {alerts.WENT_OUT} LIMIT 1",
                        (product_id, iso(now - timedelta(days=7)))).fetchone() is not None


def _message(c: sqlite3.Row, count: int, offers: list[dict]) -> str:
    def e(text: str) -> str:
        return html.escape(text, quote=False)

    shop = SHOPS[c["site"]].arabic.split("(")[0].strip() if c["site"] in SHOPS else c["site"]
    lines = [f"🎟 <b>كوبون جديد على {e(shop)}: <code>{e(c['code'])}</code></b>",
             f"{e(c['title'])} · يغطي {count:,} منتج على الأقل"]
    if c["kind"] == "multibuy":
        lines.append("لازم تشتري أكتر من قطعة عشان الخصم يتطبق")
    lines.append("")
    for o in offers:
        p = o["p"]
        link = f"<a href=\"{html.escape(p['url'])}\">{e(publish.short_title(p['title']))}</a>"
        if c["kind"] == "multibuy":
            lines.append(f"• {link}: {p['price'] / 100:,.0f} جنيه للقطعة")
        else:
            lines.append(f"• {link}: <s>{p['price'] / 100:,.0f}</s> ← <b>{o['price'] / 100:,.0f} جنيه</b>")
    text = "\n".join(lines)
    if c["group_url"]:
        text += f"{publish.COUPON_LINK}<a href=\"{html.escape(c['group_url'])}\">كل المنتجات</a>"
    return text


def _card(alert_id: int, c: sqlite3.Row, count: int, best: float, message: str) -> str:
    shop = SHOPS[c["site"]].label if c["site"] in SHOPS else c["site"]
    return (f"🎟 <b>REVIEW #{alert_id}</b> · coupon · {html.escape(shop, quote=False)}\n"
            f"{count:,} products in stock · best {best:.0%} below its usual price\n\n{message}")


def _queue(conn: sqlite3.Connection, cfg: Config, c: sqlite3.Row, count: int, offers: list[dict], best: float, now: datetime) -> None:
    message = _message(c, count, offers)
    alert_id = conn.execute(
        """INSERT INTO alerts (kind, coupon_id, drop_pct, score, message, created_at, next_attempt_at)
           VALUES ('coupon', ?, ?, ?, ?, ?, ?)""", (c["id"], best, round(best * 100), message, iso(now), iso(now))).lastrowid
    if cfg.review_enabled:
        conn.execute(
            """INSERT INTO reviews (alert_id, status, card, tier, basis, created_at, expires_at, next_attempt_at)
               VALUES (?, 'pending', ?, 'good', 'coupon', ?, ?, ?)""",
            (alert_id, _card(alert_id, c, count, best, message), iso(now),
             iso(now + timedelta(hours=cfg.review.expire_hours)), iso(now)))


def _decide(conn: sqlite3.Connection, c: sqlite3.Row, state: str, best: float, count: int, now: datetime) -> None:
    conn.execute("UPDATE coupons SET state = ?, decided_at = ?, best_discount = ?, products = ? WHERE id = ?",
                 (state, iso(now), best, count, c["id"]))


def step(conn: sqlite3.Connection, cfg: Config, now: datetime) -> None:
    """Decide about the codes that have waited long enough. Best first, up to each shop's limit for the day."""
    rules = cfg.alerts
    due = conn.execute("SELECT * FROM coupons WHERE state = 'new' AND first_seen_at <= ? ORDER BY first_seen_at LIMIT ?",
                       (iso(now - timedelta(minutes=rules.coupon_message_wait_minutes)), BATCH)).fetchall()
    looked = []
    for c in due:
        count, offers = _offers(conn, cfg, c, now)
        best = offers[0]["discount"] if offers else 0.0
        looked.append((c, count, offers, best))
    looked.sort(key=lambda x: -x[3])
    with conn:
        for c, count, offers, best in looked:
            old = now - parse_iso(c["first_seen_at"]) >= GIVE_UP
            shown = [o for o in offers if not _recently_alerted(conn, o["p"]["id"], now)][:SHOWN]
            if count < rules.coupon_message_min_products or best < rules.coupon_message_min_discount or not shown:
                if old:
                    _decide(conn, c, "below_floor", best, count, now)
                continue
            if not rules.coupon_messages or shop_settings.get(conn, c["site"]).alerts != "on":
                _decide(conn, c, "would_send", best, count, now)
                continue
            sent = conn.execute("SELECT COUNT(*) FROM coupons WHERE site = ? AND state = 'sent' AND decided_at >= ?",
                                (c["site"], iso(now - timedelta(days=1)))).fetchone()[0]
            if sent >= rules.coupon_messages_per_day:
                if old:
                    _decide(conn, c, "over_limit", best, count, now)
                continue
            _queue(conn, cfg, c, count, shown, best, now)
            _decide(conn, c, "sent", best, count, now)
            log.info("coupon message: %s %s, %d products, best %.0f%%", c["site"], c["code"], count, best * 100)
