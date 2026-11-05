"""Reading a shop's coupon text, and saying a coupon in a message. No I/O (docs/COUPONS.md).

Only what is plainly written becomes a number: a fixed amount, or a percentage with its cap and minimum order.
Everything else stays as the shop's words, and a coupon with no number never changes a price we show.
"""

import html
import re
import sqlite3

from deals.models import Coupon

_MONEY = r"(?:EGP|LE|L\.E\.?|AED|SAR|جنيه|ج\.م)"
_NUMBER = r"(\d[\d,]*(?:\.\d+)?)"
_AMOUNT_OFF = re.compile(rf"{_NUMBER}\s*{_MONEY}\s*(?:off|discount|خصم)", re.I)
_PERCENT = re.compile(rf"{_NUMBER}\s*%")
_CAP = re.compile(rf"(?:up to|max(?:imum)?(?: discount)?(?: is| of)?|upto|حتى)\s*{_NUMBER}\s*{_MONEY}", re.I)
_MIN_ORDER = re.compile(rf"(?:(?:orders?(?: value)?(?: of)?(?: over| above)?|spend|minimum(?: order)?(?: of)?)\s*{_NUMBER}\s*{_MONEY}"
                        rf"|buy for\s*{_NUMBER})", re.I)
_NOT_FOR_ALL = re.compile(r"first order|first purchase|new (?:customer|user)s?|أول طلب|عملاء جدد", re.I)
_MULTIBUY = re.compile(r"\bbuy\s*\d|\b\d\s*\+\s*\d\b|اشتر[ِي]?\s*\d", re.I)
_BUY_GET = re.compile(r"buy\s*(\d+)\W+get\s*(\d+)\s*free", re.I)
_BANK = re.compile(r"credit card|debit card|mastercard|\bvisa\b|\bbank\b|بطاقة|بنك|ائتمان", re.I)


def _minor(text: str) -> int:
    return round(float(text.replace(",", "")) * 100)


def parse(*, title: str | None, code: str | None = None, detail: str | None = None, terms=(), url: str | None = None,
          group_url: str | None = None, kind: str | None = None) -> Coupon | None:
    """A shop's coupon as a Coupon. `kind` is for a shop whose data says what it is; else it is read from the text."""
    title = " ".join((title or "").split())
    code = (code or "").strip() or None
    if not title and not code:
        return None
    detail = " ".join((detail or "").split()) or None
    if detail and (detail == title or detail == f"{title} {title}"):
        detail = None  # noon often just says the title twice
    terms = " · ".join(t for t in terms if isinstance(t, str))
    text = " · ".join(x for x in (title, detail, terms) if x)

    if kind is None:
        if _BANK.search(text) or "bank-offer" in (url or ""):
            kind = "bank"
        elif _MULTIBUY.search(title) and not re.search(r"buy for", title, re.I):
            kind = "multibuy"
        else:
            kind = "code"
    amount = percent = cap = min_order = None
    if kind in ("code", "badge", "bank") and not _NOT_FOR_ALL.search(text):
        off = _AMOUNT_OFF.search(title) or _AMOUNT_OFF.search(text)
        pct = _PERCENT.search(title) or _PERCENT.search(text)
        if pct and (not off or pct.start() < off.start()):
            percent = float(pct.group(1)) / 100
            found = _CAP.search(text)
            cap = _minor(found.group(1)) if found else None
        elif off:
            amount = _minor(off.group(1))
    found = _MIN_ORDER.search(text)
    if found:
        min_order = _minor(found.group(1) or found.group(2))
    return Coupon(kind=kind, title=title or code, code=code, detail=detail, amount=amount, percent=percent, cap=cap,
                  min_order=min_order, group_url=group_url)


def multibuy_share(title: str) -> float | None:
    """How much of the order a "buy N get..." offer takes off, when the words say: buy 2 get 1 free is a third."""
    if m := _BUY_GET.search(title):
        buy, free = int(m.group(1)), int(m.group(2))
        return free / (buy + free)
    if m := _PERCENT.search(title):
        return float(m.group(1)) / 100
    return None


def from_row(p: sqlite3.Row | dict) -> Coupon | None:
    """The coupon stored on a products row."""
    if not p["coupon_kind"]:
        return None
    return Coupon(kind=p["coupon_kind"], title=p["coupon_title"], code=p["coupon_code"], detail=p["coupon_detail"],
                  amount=p["coupon_amount"], percent=p["coupon_percent"], cap=p["coupon_cap"],
                  min_order=p["coupon_min_order"], group_url=p["coupon_url"])


def after(p: sqlite3.Row | dict) -> int | None:
    """The product's price after its coupon, when a coupon takes a known amount off it; else None."""
    coupon = from_row(p)
    off = coupon.saving(p["price"]) if coupon else None
    return p["price"] - off if off else None


def _money(minor: int, unit: str) -> str:
    return f"{minor / 100:,.0f} {unit}"


def line(coupon: Coupon, price: int | None) -> str:
    """The Arabic line for a product alert or post (HTML)."""
    title = html.escape(coupon.title, quote=False)
    code = f" <code>{html.escape(coupon.code, quote=False)}</code>" if coupon.code else ""
    if coupon.kind == "auto":
        off = f" {_money(coupon.amount, 'جنيه')}" if coupon.amount else ""
        return f"🎟 كوبون{off} محسوب بالفعل في السعر"
    if coupon.kind == "bank":
        text = html.escape(coupon.detail or coupon.title, quote=False)
        return f"🏦 كود بنكي{code}: {text} (بكارت البنك فقط)"
    if coupon.kind == "multibuy":
        return f"🎟 كوبون{code}: {title} (لازم تشتري أكتر من قطعة)"
    off = coupon.saving(price)
    if off:
        return f"🎟 كوبون{code}: {title} · السعر بعده <b>{_money(price - off, 'جنيه')}</b>"
    if coupon.min_order and price and price < coupon.min_order:
        return f"🎟 كوبون{code}: {title} (لطلب بـ {_money(coupon.min_order, 'جنيه')} على الأقل)"
    return f"🎟 كوبون{code}: {title}"


def card_line(coupon: Coupon, price: int | None) -> str:
    """The English line on a review card (HTML)."""
    label = f"<code>{html.escape(coupon.code, quote=False)}</code> " if coupon.code else ""
    text = f"🎟 {coupon.kind}: {label}{html.escape(coupon.title, quote=False)}"
    off = coupon.saving(price)
    return f"{text} → {_money(price - off, 'EGP')} after it" if off else text
