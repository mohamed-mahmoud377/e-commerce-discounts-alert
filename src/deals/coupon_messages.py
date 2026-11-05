"""One post per coupon that covers many products: a screenshot of the shop's page of them, one line and a link.

Decided here, every few minutes in the Watchdog; delivered like any deal (the review group, or the subscribers). With
`alerts.coupon_messages` off, the decision is only written down: `coupons.state` then shows what would have gone out,
which is how the floor gets tuned, and codes active on the day it is switched on are already decided, so there is no
flood. See docs/COUPONS.md.
"""

import html
import logging
import sqlite3
from collections import Counter
from datetime import datetime, timedelta

from zoneinfo import ZoneInfo

from deals import alerts, coupons, publish, shop_rules, shop_settings, shotq
from deals.config import Config
from deals.db import iso, parse_iso
from deals.models import Coupon
from deals.shops import SHOPS

log = logging.getLogger(__name__)
CAIRO = ZoneInfo("Africa/Cairo")

BATCH = 50  # codes looked at per round, so a burst of new codes can't hold the watchdog
CANDIDATES = 20  # products per code whose history is read
SHOWN = 5
GIVE_UP = timedelta(hours=48)  # a code that hasn't qualified by now is decided: not worth a message
READS_PER_HOUR = 20  # promotion pages a shop's workers are asked to read
READ_AGAIN = timedelta(hours=6)  # one asked for and not answered (refused, or the worker went away)
BRAND_SHARE = 0.6  # of a promotion's products must start with the same word, to name it in the post


def next_promotion(conn: sqlite3.Connection, site: str, now: datetime) -> dict | None:
    """The next promotion whose page a worker should read, if the hour's reads aren't used up. Amazon's search cards
    show which promotion a product is in, but only its page says what the promotion is."""
    used = conn.execute("SELECT COUNT(*) FROM coupons WHERE site = ? AND read_tried_at >= ?",
                        (site, iso(now - timedelta(hours=1)))).fetchone()[0]
    if used >= READS_PER_HOUR:
        return None
    row = conn.execute(
        """SELECT id, code, group_url FROM coupons WHERE site = ? AND kind = 'promo' AND read_at IS NULL
           AND (read_tried_at IS NULL OR read_tried_at < ?) ORDER BY first_seen_at LIMIT 1""",
        (site, iso(now - READ_AGAIN))).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE coupons SET read_tried_at = ? WHERE id = ?", (iso(now), row["id"]))
    return {"site": site, "code": row["code"], "url": row["group_url"]}


def record_promotion(conn: sqlite3.Connection, r: dict, now: datetime) -> None:
    """What a worker read from a promotion's page. Each product takes it on when next crawled (db._read_promotion),
    which is also when a coupon that makes it a real discount is judged."""
    if r["status"] == "failed":
        log.info("promotion %s %s not read: %s", r["site"], r["code"], r.get("reason"))
        return
    if r["status"] != "ok":  # no such page any more
        conn.execute("UPDATE coupons SET read_at = ?, state = 'ended' WHERE site = ? AND code = ? AND state = 'new'",
                     (iso(now), r["site"], r["code"]))
        return

    def minor(value: float) -> int | None:
        return round(value * 100) if value else None

    title, quantity = r["title"], r["min_quantity"] if r["min_quantity"] >= 2 else None
    percent = r["benefit_percent"] / 100 or None
    kind = "bank" if coupons.is_bank(title) else "multibuy" if quantity else "code"
    start, end = coupons.schedule_dates(r["schedule"])
    conn.execute(
        """UPDATE coupons SET kind = ?, title = ?, amount = ?, percent = ?, cap = ?, min_order = ?, min_quantity = ?, starts_at = ?,
                              ends_at = ?, read_at = ?, state = CASE WHEN ? = 'bank' AND state = 'new' THEN 'bank' ELSE state END
           WHERE site = ? AND code = ?""",
        (kind, title, None if percent else minor(r["benefit_amount"]), percent, minor(r["benefit_cap"]) if percent else None,
         minor(r["min_amount"]), quantity, start, end, iso(now), kind, r["site"], r["code"]))


def _offers(conn: sqlite3.Connection, cfg: Config, c: sqlite3.Row, now: datetime) -> tuple[int, list[dict], str | None]:
    """How many in-stock watched products show the code, its best ones, and the brand when they share one. Each is measured
    at what it costs with the coupon against its own usual price (its shelf price while we have too little history to
    say), so a raised price never passes. The coupon is taken as used as written: 50 off 200 is a quarter off a 30 EGP
    sweet too, in an order of them."""
    rows = conn.execute(
        """SELECT * FROM products WHERE site = ? AND coupon_code = ? AND in_stock = 1 AND price IS NOT NULL
           AND grp IS NOT NULL""", (c["site"], c["code"])).fetchall()
    priced = []
    for p in rows:
        coupon = coupons.from_row(p)
        share = coupons.order_share(coupon, p["price"]) if coupon else None
        if share:
            priced.append((p, round(p["price"] * (1 - share)), share))
    priced.sort(key=lambda x: -x[2])  # biggest cut first; history is read for these only
    offers = []
    for p, price, _ in priced[:CANDIDATES]:
        stats = alerts.product_stats(conn, p["id"], now, at_price=price)
        enough = stats.typical_90d and stats.history_days >= cfg.alerts.min_history_days
        usual = stats.typical_90d if enough else p["price"]
        if shop_rules.doubtful(c["site"], usual):
            continue
        if usual > price:
            offers.append({"p": p, "price": price, "usual": usual, "discount": 1 - price / usual})
    offers.sort(key=lambda o: -o["discount"])
    return len(rows), offers, _brand([p["title"] for p in rows])


def _brand(titles: list[str]) -> str | None:
    """The word most of a promotion's titles start with: Amazon's cards give no brand, and a post reads better with one."""
    first = [t.split()[0] for t in titles if t.split()]
    if not first:
        return None
    counts = Counter(w.casefold() for w in first)
    word, n = counts.most_common(1)[0]
    if n / len(titles) < BRAND_SHARE or len(word) < 3 or not any(ch.isalpha() for ch in word):
        return None
    return Counter(w for w in first if w.casefold() == word).most_common(1)[0][0]


def _recently_alerted(conn: sqlite3.Connection, product_id: int, now: datetime) -> bool:
    return conn.execute(f"SELECT 1 FROM alerts WHERE product_id = ? AND created_at >= ? AND {alerts.WENT_OUT} LIMIT 1",
                        (product_id, iso(now - timedelta(days=7)))).fetchone() is not None


def _message(c: sqlite3.Row, coupon: Coupon, brand: str | None, offers: list[dict]) -> str:
    """The post's text: the line it is about, the shop, the code if there is one to type, its end if the page gives one.
    The products are in its screenshot and behind its link; a shop with no such page lists a few."""
    def e(text: str) -> str:
        return html.escape(text, quote=False)

    shop = SHOPS[c["site"]].arabic.split("(")[0].strip() if c["site"] in SHOPS else c["site"]
    lines = [f"🎟 <b>{coupons.headline(coupon, brand)}</b>", f"🏪 {e(shop)}"]
    if coupon.code and coupon.typed:
        lines.append(f"الكود: <code>{e(coupon.code)}</code>")
    if c["ends_at"]:
        lines.append(coupons.until(c["ends_at"]))
    if not c["group_url"]:
        lines.append("")
        for o in offers:
            p = o["p"]
            link = f"<a href=\"{html.escape(p['url'])}\">{e(publish.short_title(p['title']))}</a>"
            after = f"<s>{p['price'] / 100:,.0f}</s> ← <b>{o['price'] / 100:,.0f} جنيه</b>" if coupon.saving(p["price"]) else f"{p['price'] / 100:,.0f} جنيه"
            lines.append(f"• {link}: {after}")
    text = "\n".join(lines)
    if c["group_url"]:
        text += f"{publish.COUPON_LINK}<a href=\"{html.escape(c['group_url'])}\">كل المنتجات</a>"
    return text


def _card(alert_id: int, c: sqlite3.Row, count: int, best: float, message: str) -> str:
    shop = SHOPS[c["site"]].label if c["site"] in SHOPS else c["site"]
    return (f"🎟 <b>REVIEW #{alert_id}</b> · coupon · {html.escape(shop, quote=False)}\n"
            f"{count:,} products in stock · best {best:.0%} below its usual price\n\n{message}")


def _queue(conn: sqlite3.Connection, cfg: Config, c: sqlite3.Row, count: int, offers: list[dict], shown: list[dict],
           brand: str | None, best: float, now: datetime) -> None:
    message = _message(c, coupons.from_row(offers[0]["p"]), brand, shown)
    alert_id = conn.execute(
        """INSERT INTO alerts (kind, coupon_id, drop_pct, score, message, created_at, next_attempt_at)
           VALUES ('coupon', ?, ?, ?, ?, ?, ?)""", (c["id"], best, round(best * 100), message, iso(now), iso(now))).lastrowid
    if cfg.review_enabled:
        conn.execute(
            """INSERT INTO reviews (alert_id, status, card, tier, basis, created_at, expires_at, next_attempt_at)
               VALUES (?, 'pending', ?, 'good', 'coupon', ?, ?, ?)""",
            (alert_id, _card(alert_id, c, count, best, message), iso(now),
             iso(now + timedelta(hours=cfg.review.expire_hours)), iso(now)))
    if c["group_url"]:  # the page itself is the picture; no worker to take it, and the post goes as text
        shotq.add(conn, cfg, alert_id, c["site"], c["group_url"], now)


def _decide(conn: sqlite3.Connection, c: sqlite3.Row, state: str, best: float, count: int, now: datetime) -> None:
    conn.execute("UPDATE coupons SET state = ?, decided_at = ?, best_discount = ?, products = ? WHERE id = ?",
                 (state, iso(now), best, count, c["id"]))


def step(conn: sqlite3.Connection, cfg: Config, now: datetime) -> None:
    """Decide about the codes that have waited long enough. Best first, up to each shop's limit for the day."""
    rules = cfg.alerts
    today = now.astimezone(CAIRO).date().isoformat()
    due = conn.execute("SELECT * FROM coupons WHERE state = 'new' AND first_seen_at <= ? ORDER BY first_seen_at LIMIT ?",
                       (iso(now - timedelta(minutes=rules.coupon_message_wait_minutes)), BATCH)).fetchall()
    looked = []
    with conn:
        for c in due:
            old = now - parse_iso(c["first_seen_at"]) >= GIVE_UP
            if c["kind"] == "promo" and not c["read_at"]:  # not known yet what it is
                if old:
                    _decide(conn, c, "unread", 0.0, 0, now)
                continue
            if c["ends_at"] and c["ends_at"] < today:
                _decide(conn, c, "ended", 0.0, 0, now)
                continue
            count, offers, brand = _offers(conn, cfg, c, now)
            looked.append((c, count, offers, brand, offers[0]["discount"] if offers else 0.0, old))
        looked.sort(key=lambda x: -x[4])
        for c, count, offers, brand, best, old in looked:
            shown = [o for o in offers if not _recently_alerted(conn, o["p"]["id"], now)][:SHOWN]
            if (count < rules.coupon_message_min_products or best < rules.coupon_message_min_discount
                    or not (shown or c["group_url"])):
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
            _queue(conn, cfg, c, count, offers, shown, brand, best, now)
            _decide(conn, c, "sent", best, count, now)
            log.info("coupon post: %s %s, %d products, best %.0f%%", c["site"], c["code"], count, best * 100)
