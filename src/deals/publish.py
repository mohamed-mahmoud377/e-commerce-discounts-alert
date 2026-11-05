"""The way to the public channel: the admins see every deal first, and only what they approve goes out.

A deal alert gets a `reviews` row with a detailed English card. The card goes to the private review group
with Approve, Reject and Preview buttons; a deal nobody decided on closes after `review.expire_hours`,
because a flash sale goes stale. On approval the post goes to the public channel: short Arabic with a hook
line that rotates and gets louder the better the deal is, the screenshot, and a button to the shop.

The bot only writes the decision and the loop here (`step`, every 3 seconds in AlertSender) does the sending,
so a Telegram outage or a restart loses nothing. See docs/PUBLISHING.md.
"""

import html
import logging
import re
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from deals import coupons
from deals.config import Config, ReviewRules
from deals.db import PRICE_NOISE, iso, parse_iso
from deals.pricing import LOW_STOCK_EVERYWHERE, Deal, PriceStats
from deals.shops import SHOPS

log = logging.getLogger(__name__)
CAIRO = ZoneInfo("Africa/Cairo")

MAX_TRIES = 5  # posting to the channel: 30 s, 1, 2, 4 minutes apart, then the card says it failed
SHOT_HOLD = timedelta(seconds=60)  # an approved deal waits this long for a screenshot still being taken
IMAGE_DAYS = 2  # screenshots on disk are removed after this long; by then the deal is decided or expired
BACKLOG = timedelta(minutes=30)  # the health check warns when a card has waited this long to go out

REASONS = {
    "fake": "Inflated reference",
    "used": "Used or renewed",
    "seller": "Bad seller",
    "small": "Discount too small",
    "boring": "Not interesting",
    "data": "Wrong data",
    "gone": "Price already gone",
    "other": "Other",
}
COUPON_LINK = "\n\n🛒 "  # where a coupon message's link to all its products starts; a post has a button instead
TIER_ICON = {"crazy": "🔴", "strong": "🟠", "good": "🟡"}

# The first line of a public post. Each pool's hooks must be true of every deal that can reach the pool:
# only "low" may say it is the lowest we saw, and "new" rests on the shop's claim, not on our history.
HOOKS = {
    "crazy": ["😱 السعر ده مش طبيعي!", "🤯 الحق قبل ما يخلص!", "🔥🔥🔥 عرض مايتفوتش!", "💣 وفّر {saving} جنيه مرة واحدة!"],
    "strong": ["🔥 عرض جامد!", "💥 نزل {pct}% بجد مش كلام!", "⚡ فرصة حلوة أوي!", "🤑 وفّر {saving} جنيه!"],
    "good": ["👌 خصم حقيقي مش وهمي", "💡 فرصة حلوة لو بتدور عليه", "🛍 سعر حلو النهاردة", "✨ لقطة لو محتاجه"],
    "low": ["📉 أقل سعر شفناه عليه!", "🏆 عمره ما نزل للسعر ده!", "🔻 أرخص سعر من ساعة ما بنتابعه!"],
    "error": ["🚨 شكلها غلطة في السعر! الحق قبل ما تتصلّح", "😳 السعر ده شكله غلطة، الحق!"],
    "new": ["🆕 نزل جديد وعليه خصم {pct}%!", "🆕 لسه نازل بخصم كبير"],
    "manual": ["📣 عرض اخترناه لك", "⭐ اختيار فريقنا"],
}
# What the discount rests on, in the post. Rotates with the hook.
WHY = {
    "history": ["✅ مقارنة بسعره الحقيقي من متابعتنا، مش السعر المشطوب",
                "✅ حسبناها من تاريخ سعره عندنا، مش السعر المشطوب",
                "✅ قارناه بسعره المعتاد، مش باللي الموقع كاتبه"],
    "early": ["ℹ️ لسه بنتابعه من قريب، بس ده أقل من كل اللي شفناه"],
    "claim": ["ℹ️ الخصم حسب الموقع، ولسه بنتابع سعره بنفسنا"],
}
RENEWED = re.compile(r"renewed|refurb|open[- ]box|pre[- ]?owned|second[- ]hand|مجدد|متجدد|مستعمل", re.I)
_TITLE_BREAK = re.compile(r"[,،]| [-–|] ")
TITLE_MAX = 70


def tier(deal: Deal, rules: ReviewRules) -> str:
    """How good the deal is, for the tone of its post."""
    if (deal.kind == "price_error" or deal.score >= rules.crazy_score
            or (_solid_low(deal, rules) and deal.drop >= rules.crazy_low_drop)):
        return "crazy"
    return "strong" if deal.score >= rules.strong_score or deal.drop >= rules.strong_drop else "good"


def _solid_low(deal: Deal, rules: ReviewRules) -> bool:
    """On a few days of history nearly every drop is "the lowest we saw" (54% of a week's alerts, 4 Nov
    2026), which says nothing about how good it is."""
    return deal.all_time_low and deal.reference_days >= rules.solid_low_days


def _basis(deal: Deal) -> str:
    if deal.kind == "new_product" or (deal.kind == "manual" and deal.list_price_kind):
        return "claim"
    if deal.kind == "manual" and not deal.reference_days:
        return "none"
    return "early" if deal.kind == "early_drop" else "history"


def prepare(conn: sqlite3.Connection, cfg: Config, alert_id: int, p: sqlite3.Row, stats: PriceStats, deal: Deal, group: str,
            *, other_offer: int | None, others: list | None, past: int, note: str, now: datetime) -> None:
    """Write the deal's review row: the card as it is now, and the facts its public post needs later."""
    level = tier(deal, cfg.review)
    skipped = deal.kind != "manual" and deal.score < cfg.review.min_score
    conn.execute(
        """INSERT INTO reviews (alert_id, status, card, tier, basis, all_time_low, grp, note, created_at, expires_at,
                                next_attempt_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (alert_id, "skipped" if skipped else "pending",
         format_card(alert_id, p, stats, deal, level, other_offer=other_offer, others=others, past=past, note=note, now=now),
         level, _basis(deal), int(_solid_low(deal, cfg.review)), group, note or None, iso(now),
         iso(now + timedelta(hours=cfg.review.expire_hours)), iso(now)),
    )


def format_card(alert_id: int, p: sqlite3.Row, stats: PriceStats, deal: Deal, level: str, *, other_offer: int | None,
                others: list | None, past: int, note: str, now: datetime) -> str:
    """Everything an admin needs to decide, in English. `others` is None when the other shops could not be looked up."""
    def e(text: str) -> str:
        return html.escape(text, quote=False)

    def egp(minor: int) -> str:
        return f"{minor / 100:,.0f} EGP"

    def days(n: float) -> str:
        return f"{n:.0f} day{'' if f'{n:.0f}' == '1' else 's'}"

    def label(site: str) -> str:
        return SHOPS[site].label if site in SHOPS else site

    def shop_line(o: dict) -> str:
        line = f"<a href=\"{html.escape(o['url'])}\">{e(label(o['site']))}</a>: {egp(o['price'])}"
        if o["usual"] is not None:
            low = min(o["price"], o["low"]) if o["low"] is not None else o["price"]
            return f"{line} (usual {o['usual'] / 100:,.0f}, lowest {low / 100:,.0f})"
        return f"{line} (same price for {days(o['steady_days'])})" if o["steady_days"] >= 1 else line

    shop = label(p["site"])
    where = [shop, p["grp"] or "", f"seller: {e(p['seller'])}" if p["seller"] else ""]
    lines = [
        f"{TIER_ICON[level]} <b>REVIEW #{alert_id}</b> · {deal.kind} · score {deal.score} · {deal.confidence} confidence",
        "",
        f"<a href=\"{html.escape(p['url'])}\">{e(p['title'][:200])}</a>",
        " · ".join(w for w in where if w),
    ]

    flags = []
    if RENEWED.search(p["title"]):
        flags.append("Renewed or used")
    if p["site"] == "noon_eg" and p["seller"] and "noon" not in p["seller"].lower():
        flags.append("marketplace seller")
    if deal.kind == "price_error":
        flags.append("possible pricing mistake: the shop may cancel")
    if deal.confidence == "low":
        flags.append("short history or the shop's own claim")
    if deal.inflated_list_price:
        flags.append("crossed-out price above anything we saw")
    if deal.reference >= 1_000_000 and deal.reference % 1_000_000 == 0:  # 10,000 EGP, 100,000 EGP: docs/SHOP_RULES.md
        flags.append(f"round usual price {deal.reference / 100:,.0f}: maybe the shop's placeholder")
    if p["rating"] and p["rating"] < 3.8:
        flags.append(f"low rating {p['rating']:.1f}")
    if p["reviews"] is not None and p["reviews"] < 5:
        flags.append("few reviews")
    if flags:
        lines += ["", "⚠️ " + " · ".join(flags)]

    # One number a line: a card is read fast, on a phone, and the decision rests on these.
    lines += ["", f"💰 Price: <b>{egp(deal.price)}</b>"]
    basis = _basis(deal)
    if basis == "none":
        lines.append("Usual: no history and no crossed-out price to compare with")
    elif basis == "claim":
        lines.append(f"Shop's claim: <b>{egp(deal.reference)}</b> (not from our history)")
    elif deal.drop >= 0.005:
        lines.append(f"Usual: <b>{egp(deal.reference)}</b> (over {days(deal.reference_days)})")
    else:
        lines.append(f"Usual: <b>{egp(deal.reference)}</b> (over {days(deal.reference_days)}), not below it")
    if basis == "claim" or (basis != "none" and deal.drop >= 0.005):
        lines += [f"Drop: <b>−{deal.drop:.0%}</b>", f"Saves: <b>{egp(deal.reference - deal.price)}</b>"]
    if deal.previous:
        lines.append(f"Just before: {egp(deal.previous)}")
    if p["list_price"] and basis != "claim":
        lines.append(f"Crossed out: {egp(p['list_price'])}" + (f" (claims −{1 - deal.price / p['list_price']:.0%})" if p["list_price"] > deal.price else ""))

    lines += ["", f"📊 Tracked: {days(stats.tracked_days)}", f"Changes: {stats.changes_90d} in 90 days"]
    if stats.low is not None:
        lines.append(f"Range: {stats.low / 100:,.0f} – {egp(stats.high)}")
    if deal.share_at_or_below is not None:
        lines.append(f"This cheap: {deal.share_at_or_below:.0%} of the time")
    if deal.all_time_low:
        lines.append("Lowest we saw")

    nothing = "no usable match found"
    seller = [f"Other seller: {egp(other_offer)}"] if other_offer is not None else []
    if others:
        unsure = f" (unsure match, confidence {others[0]['confidence']})" if others[0]["unsure"] else ""
        lines += ["", f"🏪 Elsewhere{unsure}", *seller, *map(shop_line, others)]
    elif others is None and seller:
        lines += ["", "🏪 Elsewhere", *seller]
    elif others is not None and seller:
        lines += ["", "🏪 Elsewhere", *seller, f"Other shops: {nothing}"]
    elif others is not None:
        lines += ["", f"🏪 Elsewhere: {nothing}"]

    product = [f"Rating: ★ {p['rating']:.1f} ({p['reviews'] or 0:,})" if p["rating"] else "",
               f"Stock: {p['stock_left']} left" + (" (shown on half the shop)" if p["site"] in LOW_STOCK_EVERYWHERE else "")
               if p["stock_left"] is not None and p["stock_left"] <= 5 else "",
               "Signals: " + ", ".join(code for code, _ in deal.signals) if deal.signals else ""]
    if any(product):
        lines += ["", *(x for x in product if x)]
    if coupon := coupons.from_row(p):
        lines += ["", coupons.card_line(coupon, p["price"])]
    if note:
        lines += ["", f"📝 {e(note)}"]
    lines += ["", f"Past alerts: {past} · found {now.astimezone(CAIRO):%H:%M} Cairo · public tone: {level}"]
    return "\n".join(lines)


# --- the public post


def short_title(title: str) -> str:
    """The shop's title, whole if it is short, else up to a natural break, else up to a word."""
    title = " ".join(title.split())
    if len(title) <= TITLE_MAX:
        return title
    breaks = [m.start() for m in _TITLE_BREAK.finditer(title) if 12 <= m.start() <= TITLE_MAX]
    if breaks:
        return title[:breaks[-1]]
    cut = title[:TITLE_MAX]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "…"


def _hashtag(name: str | None) -> str:
    word = re.sub(r"\W+", "_", (name or "").split("(")[0]).strip("_")
    return f"#{word}" if word else ""


def _pool(kind: str, level: str, all_time_low: bool) -> str:
    if kind in ("manual", "new_product"):
        return "manual" if kind == "manual" else "new"
    if kind == "price_error":
        return "error"
    return "low" if all_time_low else level


def render_post(*, kind: str, tier: str, basis: str, all_time_low: bool, title: str, site: str, group: str | None,
                note: str | None, price: int, reference: int, index: int, coupon: str | None = None) -> tuple[str, str]:
    """The Arabic post and which hook it used. Fits a photo's caption (1,024 characters)."""
    pool = _pool(kind, tier, all_time_low)
    hook_at = index % len(HOOKS[pool])
    saving = reference - price
    pct = round(saving / reference * 100) if reference else 0
    hook = HOOKS[pool][hook_at].format(saving=f"{saving / 100:,.0f}", pct=pct)
    shop = SHOPS[site].arabic if site in SHOPS else site
    lines = [f"<b>{hook}</b>", "", f"<b>{html.escape(short_title(title), quote=False)}</b>", ""]
    if basis != "none" and pct >= 1:
        lines.append(f"💰 <b>{price / 100:,.0f} جنيه</b> بدل <s>{reference / 100:,.0f}</s> · خصم {pct}%")
        lines.append(f"💸 هتوفّر {saving / 100:,.0f} جنيه")
        why = WHY.get(basis)
        if why:
            lines.append(why[index % len(why)])
    else:
        lines.append(f"💰 <b>{price / 100:,.0f} جنيه</b>")
    if coupon:
        lines.append(coupon)
    if RENEWED.search(title):
        lines.append("♻️ المنتج متجدد أو مستعمل، مش جديد")
    if kind == "price_error":
        lines.append("⚠️ لو غلطة ممكن الموقع يلغي الطلب")
    if note:
        lines.append(f"📝 {html.escape(note, quote=False)}")
    lines.append(f"🏪 {html.escape(shop.split('(')[0].strip(), quote=False)}")
    tags = [t for t in (_hashtag(group), _hashtag(shop)) if t]
    if tags:
        lines += ["", " ".join(tags)]
    return "\n".join(lines), f"{pool}:{hook_at}"


def channel_url(cfg: Config) -> str | None:
    """The public channel's link, when it is set by its @username."""
    chat = cfg.telegram_public_chat_id
    return f"https://t.me/{chat[1:]}" if chat.startswith("@") else None


def _chat(value: str) -> int | str:
    return int(value) if value.lstrip("-").isdigit() else value


def _facts(conn, alert_id: int) -> dict | None:
    row = conn.execute(
        """SELECT r.*, a.kind, a.price AS alert_price, a.reference_price AS reference, a.message,
                  p.title, p.url, p.site, p.price AS now_price, p.in_stock, p.coupon_kind, p.coupon_code, p.coupon_title,
                  p.coupon_detail, p.coupon_amount, p.coupon_percent, p.coupon_cap, p.coupon_min_order, p.coupon_url,
                  c.site AS code_site, c.code AS code, c.group_url AS code_url
           FROM reviews r JOIN alerts a ON a.id = r.alert_id LEFT JOIN products p ON p.id = a.product_id
           LEFT JOIN coupons c ON c.id = a.coupon_id
           WHERE r.alert_id = ?""", (alert_id,)).fetchone()
    if row is None:
        return None
    f = dict(row)
    f["coupon_gone"] = False
    if f["kind"] == "coupon_drop" and f["now_price"] is not None:
        # What counts is the price after the coupon. With the coupon gone that is the shelf price: "it rose".
        coupon = coupons.from_row(f)
        off = coupon.saving(f["now_price"]) if coupon else None
        f["coupon_gone"] = not off
        f["now_price"] -= off or 0
    elif f["kind"] == "coupon":
        # A coupon message has no one product: it is still good while products show the code.
        left = conn.execute("SELECT COUNT(*) FROM products WHERE site = ? AND coupon_code = ? AND in_stock = 1",
                            (f["code_site"], f["code"])).fetchone()[0]
        f.update(in_stock=left > 0, now_price=0, alert_price=0, reference=0, coupon_gone=not left)
    return f


def _post(conn, f: sqlite3.Row) -> tuple[str, str, list[list[dict]]]:
    """The post for this deal at the product's price right now: (text, hook, buy button). A price that rose
    since the alert has lost what made it loud. The hook is the next unused one of its pool, so a preview and
    the post that follows it are the same."""
    if f["kind"] == "coupon":  # written when it was queued (coupon_messages.py)
        url = f["code_url"]
        text = f["message"].rsplit(COUPON_LINK, 1)[0] if url else f["message"]
        return text, "coupon:0", [[{"text": "🛒 كل المنتجات", "url": url}]] if url else []
    price = f["now_price"]
    rose = price > f["alert_price"] * (1 + PRICE_NOISE)
    kind = "price_drop" if rose and f["kind"] == "price_error" else f["kind"]
    level, low = ("good", False) if rose else (f["tier"], bool(f["all_time_low"]))
    pool = _pool(kind, level, low)
    last = conn.execute("SELECT hook FROM reviews WHERE status = 'published' AND hook LIKE ? "
                        "ORDER BY published_at DESC, alert_id DESC LIMIT 1", (f"{pool}:%",)).fetchone()
    index = int(last["hook"].split(":")[1]) + 1 if last else 0
    text, hook = render_post(kind=kind, tier=level, basis=f["basis"], all_time_low=low, title=f["title"], site=f["site"],
                             group=f["grp"], note=f["note"], price=price, reference=f["reference"], index=index,
                             coupon=None if f["coupon_gone"] or not (c := coupons.from_row(f))
                             else coupons.line(c, f["now_price"] if f["kind"] != "coupon_drop" else None))
    shop = SHOPS[f["site"]].arabic.split("(")[0].strip() if f["site"] in SHOPS else f["site"]
    return text, hook, [[{"text": f"🛒 شوف العرض على {shop}", "url": f["url"]}]]


def _photo(conn, cfg: Config, f: sqlite3.Row) -> bytes | str | None:
    """The deal's screenshot, unless the price has moved away from what it shows."""
    if f["kind"] == "coupon":
        return None
    if abs(f["now_price"] - f["alert_price"]) > f["alert_price"] * PRICE_NOISE:
        return None
    shot = conn.execute("SELECT status, file_id FROM shots WHERE alert_id = ?", (f["alert_id"],)).fetchone()
    if shot is None or shot["status"] != "ok":
        return None
    if shot["file_id"]:
        return shot["file_id"]
    path = cfg.shots_dir / f"{f['alert_id']}.jpg"
    return path.read_bytes() if path.exists() else None


def _send_post(conn, cfg: Config, tg, chat: int | str, f: sqlite3.Row, text: str, buttons: list[list[dict]],
               reply_to: int | None = None) -> dict:
    """Photo with the post as its caption, or the post alone. A photo Telegram refuses (not trouble that
    passes) never costs the post; passing trouble is raised, to try the whole post again."""
    photo = _photo(conn, cfg, f)
    if photo is not None:
        try:
            result = tg.send_photo(chat, photo, text, buttons=buttons, reply_to=reply_to)
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status is None or status >= 500 or status in (403, 429):
                raise
            log.warning("screenshot of alert %s refused (%s); posting the text alone", f["alert_id"], exc)
        else:
            sizes = result.get("photo") or []
            if sizes and not isinstance(photo, str):
                with conn:
                    conn.execute("UPDATE shots SET file_id = ? WHERE alert_id = ?", (sizes[-1]["file_id"], f["alert_id"]))
            return result
    return tg.send(chat, text, buttons=buttons, reply_to=reply_to)


# --- the loop


_swept_at: datetime | None = None


def step(conn: sqlite3.Connection, cfg: Config, tg, now: datetime) -> None:
    """One round. At most one card or closed card per round keeps the group under Telegram's limit of 20
    messages a minute; a burst just takes longer, best deals first."""
    global _swept_at
    with conn:  # cards that never went out close without a message
        conn.execute("""UPDATE reviews SET status = 'expired' WHERE status = 'pending' AND message_id IS NULL AND expires_at <= ?""",
                     (iso(now),))
    _publish_approved(conn, cfg, tg, now)
    if not _expire_one(conn, cfg, tg, now):
        _post_card(conn, cfg, tg, now)
    if _swept_at is None or now - _swept_at >= timedelta(hours=1):
        _swept_at = now
        sweep_images(cfg, now)


def _post_card(conn, cfg: Config, tg, now: datetime) -> None:
    r = conn.execute(
        """SELECT r.alert_id, r.card, r.attempts FROM reviews r JOIN alerts a ON a.id = r.alert_id
           WHERE r.status = 'pending' AND r.message_id IS NULL AND r.next_attempt_at <= ?
             AND a.sent_at IS NOT NULL AND a.error IS NULL
           ORDER BY a.score DESC, r.alert_id LIMIT 1""", (iso(now),)).fetchone()
    if r is None:
        return
    try:
        sent = tg.send(_chat(cfg.telegram_review_chat_id), r["card"], buttons=_decision_buttons(r["alert_id"]))
    except Exception as exc:
        log.warning("review card of alert %s failed (%s)", r["alert_id"], exc)
        with conn:
            _try_later(conn, r["alert_id"], r["attempts"], exc, now)
        return
    with conn:
        conn.execute("UPDATE reviews SET message_id = ?, attempts = 0, error = NULL WHERE alert_id = ?",
                     (sent["message_id"], r["alert_id"]))


def _expire_one(conn, cfg: Config, tg, now: datetime) -> bool:
    r = conn.execute("SELECT alert_id, card, message_id FROM reviews WHERE status = 'pending' AND expires_at <= ? "
                     "ORDER BY expires_at LIMIT 1", (iso(now),)).fetchone()
    if r is None:
        return False
    with conn:  # an approval in this same moment wins
        closed = conn.execute("UPDATE reviews SET status = 'expired' WHERE alert_id = ? AND status = 'pending'",
                              (r["alert_id"],)).rowcount
    if not closed:
        return False
    _edit(tg, _chat(cfg.telegram_review_chat_id), r["message_id"], f"{r['card']}\n\n⌛ <b>Expired</b>: nobody decided in time")
    return True


def _publish_approved(conn, cfg: Config, tg, now: datetime) -> None:
    rows = conn.execute("SELECT alert_id FROM reviews WHERE status = 'approved' AND next_attempt_at <= ? "
                        "ORDER BY decided_at LIMIT 10", (iso(now),)).fetchall()
    for row in rows:
        f = _facts(conn, row["alert_id"])
        shot = conn.execute("SELECT status FROM shots WHERE alert_id = ?", (f["alert_id"],)).fetchone()
        if (shot and shot["status"] in ("pending", "taking")
                and now < parse_iso(f["decided_at"]) + SHOT_HOLD):
            continue  # the screenshot is on its way
        _publish(conn, cfg, tg, f, now)
        return  # one a round


def _publish(conn, cfg: Config, tg, f: sqlite3.Row, now: datetime) -> None:
    card_chat = _chat(cfg.telegram_review_chat_id)
    who = html.escape(f["decided_by_name"] or str(f["decided_by"]), quote=False)
    if f["now_price"] is None or not f["in_stock"]:
        _failed(conn, tg, card_chat, f, "the coupon's products are gone" if f["kind"] == "coupon" else "sold out since it was approved")
        return
    text, hook, buttons = _post(conn, f)
    try:
        sent = _send_post(conn, cfg, tg, _chat(cfg.telegram_public_chat_id), f, text, buttons)
    except Exception as exc:
        log.warning("could not post alert %s to the channel (%s)", f["alert_id"], exc)
        if f["attempts"] + 1 >= MAX_TRIES:
            _failed(conn, tg, card_chat, f, str(exc))
        else:
            with conn:
                _try_later(conn, f["alert_id"], f["attempts"], exc, now)
        return
    with conn:
        conn.execute("UPDATE reviews SET status = 'published', price = ?, hook = ?, public_message_id = ?, published_at = ?, "
                     "error = NULL WHERE alert_id = ?", (f["now_price"] or None, hook, sent["message_id"], iso(now), f["alert_id"]))
    username = (sent.get("chat") or {}).get("username")
    link = f" → <a href=\"https://t.me/{username}/{sent['message_id']}\">the post</a>" if username else ""
    _edit(tg, card_chat, f["message_id"], f"{f['card']}\n\n✅ <b>Approved by {who}</b>{link}")


def _failed(conn, tg, chat, f: sqlite3.Row, error: str) -> None:
    with conn:
        conn.execute("UPDATE reviews SET status = 'failed', error = ? WHERE alert_id = ?", (error[:300], f["alert_id"]))
    _edit(tg, chat, f["message_id"], f"{f['card']}\n\n⚠️ <b>Posting failed</b>: {html.escape(error[:300], quote=False)}",
          [[{"text": "🔁 Try again", "callback_data": f"rv_ok:{f['alert_id']}"},
            {"text": "❌ Reject", "callback_data": f"rv_no:{f['alert_id']}"}]])


def _try_later(conn, alert_id: int, attempts: int, exc: Exception, now: datetime) -> None:
    delay = getattr(exc, "retry_after", None) or min(3600, 30 * 2 ** attempts)
    conn.execute("UPDATE reviews SET attempts = attempts + 1, next_attempt_at = ?, error = ? WHERE alert_id = ?",
                 (iso(now + timedelta(seconds=delay)), str(exc)[:300], alert_id))


def sweep_images(cfg: Config, now: datetime) -> None:
    """Screenshots last until their deal is long decided or expired."""
    if not cfg.shots_dir.exists():
        return
    oldest = (now - timedelta(days=IMAGE_DAYS)).timestamp()
    for path in cfg.shots_dir.glob("*.jpg"):
        try:
            if path.stat().st_mtime < oldest:
                path.unlink()
        except OSError as exc:
            log.warning("could not remove %s (%s)", path, exc)


# --- the buttons


def _decision_buttons(alert_id: int) -> list[list[dict]]:
    return [[{"text": "✅ Approve", "callback_data": f"rv_ok:{alert_id}"},
             {"text": "❌ Reject", "callback_data": f"rv_no:{alert_id}"},
             {"text": "👁 Preview", "callback_data": f"rv_view:{alert_id}"}]]


def _reason_buttons(alert_id: int) -> list[list[dict]]:
    keys = [{"text": label, "callback_data": f"rv_why:{alert_id}:{key}"} for key, label in REASONS.items()]
    return [keys[i:i + 2] for i in range(0, len(keys), 2)] + [[{"text": "↩️ Back", "callback_data": f"rv_back:{alert_id}"}]]


def _edit(tg, chat, message_id: int | None, text: str, buttons: list[list[dict]] | None = None) -> None:
    """Change a card; without `buttons` its buttons go. The decision stands even if this fails."""
    if message_id is None:
        return
    params = {"chat_id": chat, "message_id": message_id, "text": text, "parse_mode": "HTML",
              "link_preview_options": {"is_disabled": False, "prefer_small_media": True}}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": buttons}
    try:
        tg.call("editMessageText", **params)
    except Exception as exc:
        log.info("could not edit review card %s (%s)", message_id, exc)


def on_button(conn: sqlite3.Connection, cfg: Config, tg, cq: dict, allowed: bool, now: datetime) -> None:
    """An admin pressed something on a review card."""
    def answer(text: str) -> None:
        tg.call("answerCallbackQuery", callback_query_id=cq["id"], text=text)

    if not allowed:
        answer("Only the admins can decide")
        return
    action, _, rest = (cq.get("data") or "").partition(":")
    target, _, reason = rest.partition(":")
    f = _facts(conn, int(target)) if target.isdigit() else None
    if f is None:
        answer("Unknown deal")
        return
    if action == "rv_view":
        _preview(conn, cfg, tg, f, answer)
        return

    if f["status"] not in ("pending", "failed"):
        answer(f"This deal is already {f['status']}")
        return
    if f["status"] == "pending" and iso(now) >= f["expires_at"]:
        answer("This deal has expired")
        return
    message = cq.get("message") or {}
    chat, message_id = message.get("chat", {}).get("id"), message.get("message_id")
    name = (cq.get("from") or {}).get("first_name") or str((cq.get("from") or {}).get("id"))
    who = html.escape(name, quote=False)

    if action == "rv_no":
        _edit(tg, chat, message_id, f"{f['card']}\n\n<b>Why reject it?</b>", _reason_buttons(f["alert_id"]))
        answer("")
    elif action == "rv_back":
        _edit(tg, chat, message_id, f["card"], _decision_buttons(f["alert_id"]))
        answer("")
    elif action == "rv_why" and reason in REASONS:
        if _decide(conn, f["alert_id"], "rejected", cq, name, now, reason=reason):
            _edit(tg, chat, message_id, f"{f['card']}\n\n❌ <b>Rejected by {who}</b>: {REASONS[reason]}")
            answer("Rejected")
        else:
            answer("Someone else already decided")
    elif action in ("rv_ok", "rv_force"):
        problem = _moved(f)
        if problem and action == "rv_ok":
            _edit(tg, chat, message_id, f"{f['card']}\n\n⚠️ <b>{problem}</b>", _moved_buttons(f))
            answer("The price moved")
        elif problem and not _can_force(f):
            answer("The price is no longer a discount")
        elif _decide(conn, f["alert_id"], "approved", cq, name, now):
            _edit(tg, chat, message_id, f"{f['card']}\n\n⏳ <b>Approved by {who}</b>, posting…")
            answer("Posting…")
        else:
            answer("Someone else already decided")
    else:
        answer("Unknown action")


def _moved(f: sqlite3.Row) -> str | None:
    """Why the deal is not what the card says any more; a price that fell further is still fine."""
    if f["kind"] == "coupon":
        return "No product shows this coupon any more." if f["coupon_gone"] else None
    if not f["in_stock"] or f["now_price"] is None:
        return "It is sold out now."
    if f["coupon_gone"]:
        return f"The coupon is gone: the price is {f['now_price'] / 100:,.0f} EGP (it was {f['alert_price'] / 100:,.0f} EGP with it)."
    if f["now_price"] > f["alert_price"] * (1 + PRICE_NOISE):
        return f"The price rose to {f['now_price'] / 100:,.0f} EGP (it was {f['alert_price'] / 100:,.0f} EGP)."
    return None


def _can_force(f: sqlite3.Row) -> bool:
    return f["kind"] != "coupon" and bool(f["in_stock"]) and f["now_price"] is not None and f["now_price"] < f["reference"]


def _moved_buttons(f: sqlite3.Row) -> list[list[dict]]:
    rows = []
    if _can_force(f):
        rows.append([{"text": f"📣 Post at {f['now_price'] / 100:,.0f} EGP", "callback_data": f"rv_force:{f['alert_id']}"}])
    rows.append([{"text": "❌ Reject: price gone", "callback_data": f"rv_why:{f['alert_id']}:gone"}])
    rows.append([{"text": "↩️ Back", "callback_data": f"rv_back:{f['alert_id']}"}])
    return rows


def _decide(conn, alert_id: int, status: str, cq: dict, name: str, now: datetime, reason: str | None = None) -> bool:
    """Only the first decision counts, whoever presses second. Posting starts afresh after a failure."""
    with conn:
        cur = conn.execute(
            """UPDATE reviews SET status = ?, decided_by = ?, decided_by_name = ?, decided_at = ?, reason = ?,
                                  attempts = 0, next_attempt_at = ?, error = NULL
               WHERE alert_id = ? AND status IN ('pending', 'failed')""",
            (status, (cq.get("from") or {}).get("id"), name, iso(now), reason, iso(now), alert_id))
    return cur.rowcount == 1


def _preview(conn, cfg: Config, tg, f: sqlite3.Row, answer) -> None:
    if f["now_price"] is None or not f["in_stock"]:
        answer("It is sold out now")
        return
    text, _, buttons = _post(conn, f)
    try:
        _send_post(conn, cfg, tg, _chat(cfg.telegram_review_chat_id), f, text, buttons, reply_to=f["message_id"])
    except Exception as exc:
        log.warning("preview of alert %s failed (%s)", f["alert_id"], exc)
        answer("Telegram refused the preview, try again")
        return
    answer("")
