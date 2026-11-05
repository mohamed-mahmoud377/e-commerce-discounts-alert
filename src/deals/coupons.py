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
_BUY_N = re.compile(r"buy\s*(\d+)\b", re.I)
_N_FOR_M = re.compile(r"(\d+)\s*(?:بسعر|for the price of)\s*(\d+)", re.I)  # "احصل على 3 بسعر 2"
_SPEND = re.compile(r"buy\s+(\d{2,})\s+get\s+(\d+)\b(?!\s*(?:%|free))", re.I)  # Amazon's "Buy 350 get 50"
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

    spend = _SPEND.search(title)
    if kind is None:
        if _BANK.search(text) or "bank-offer" in (url or ""):
            kind = "bank"
        elif spend:
            kind = "code"
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
    min_quantity = None
    if kind == "multibuy" and (m := _BUY_N.search(title)):  # "Buy 2, Get 20% off", "Buy 2 get 200 EGP off"
        min_quantity = int(m.group(1))
        off = _AMOUNT_OFF.search(title)
        pct = _PERCENT.search(title)
        percent, amount = float(pct.group(1)) / 100 if pct else None, _minor(off.group(1)) if off and not pct else None
    found = _MIN_ORDER.search(text)
    if found:
        min_order = _minor(found.group(1) or found.group(2))
    if spend and not _NOT_FOR_ALL.search(text):
        min_order, amount = _minor(spend.group(1)), _minor(spend.group(2))
    return Coupon(kind=kind, title=title or code, code=code, detail=detail, amount=amount, percent=percent, cap=cap,
                  min_order=min_order, group_url=group_url, min_quantity=min_quantity)


def order_share(coupon: Coupon, price: int | None) -> float | None:
    """How much of the order it takes off at this item price; for a "buy N get..." offer also from its words:
    buy 2 get 1 free is a third."""
    if (share := coupon.share(price)) is not None:
        return share
    if coupon.kind != "multibuy":
        return None
    if m := _BUY_GET.search(coupon.title):
        buy, free = int(m.group(1)), int(m.group(2))
        return free / (buy + free)
    if (m := _N_FOR_M.search(coupon.title)) and int(m.group(1)) > int(m.group(2)):
        return 1 - int(m.group(2)) / int(m.group(1))
    if (m := _PERCENT.search(coupon.title)) and not coupon.cap:
        return float(m.group(1)) / 100
    return None


def from_row(p: sqlite3.Row | dict) -> Coupon | None:
    """The coupon stored on a products row."""
    if not p["coupon_kind"]:
        return None
    return Coupon(kind=p["coupon_kind"], title=p["coupon_title"], code=p["coupon_code"], detail=p["coupon_detail"],
                  amount=p["coupon_amount"], percent=p["coupon_percent"], cap=p["coupon_cap"],
                  min_order=p["coupon_min_order"], group_url=p["coupon_url"], min_quantity=p["coupon_min_quantity"],
                  typed=bool(p["coupon_typed"]))


def after(p: sqlite3.Row | dict) -> int | None:
    """The product's price after its coupon, when a coupon takes a known amount off it; else None."""
    coupon = from_row(p)
    off = coupon.saving(p["price"]) if coupon else None
    return p["price"] - off if off else None


def _money(minor: int, unit: str) -> str:
    return f"{minor / 100:,.0f} {unit}"


def line(coupon: Coupon, price: int | None) -> str:
    """The Arabic line for a product alert or post (HTML); empty when there is nothing to say yet."""
    title = html.escape(coupon.title, quote=False)
    code = f" <code>{html.escape(coupon.code, quote=False)}</code>" if coupon.code and coupon.typed else ""
    if coupon.kind == "promo":  # not read yet: only the card's words, which never lower a price
        if not title:
            return ""
        return f"🏦 {title} (بكارت البنك فقط)" if _BANK.search(coupon.title) else f"🎟 عرض: {title}"
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
    label = f"<code>{html.escape(coupon.code, quote=False)}</code> " if coupon.code and coupon.typed else ""
    text = f"🎟 {coupon.kind}: {label}{html.escape(coupon.title, quote=False)}"
    off = coupon.saving(price)
    return f"{text} → {_money(price - off, 'EGP')} after it" if off else text


def is_bank(title: str) -> bool:
    return bool(_BANK.search(title))


def _egp(minor: int) -> str:
    return f"{minor / 100:,.0f}"


def headline(c: Coupon, brand: str | None = None) -> str:
    """The one line a coupon post is about, from the coupon's numbers (HTML). Its own words when they say more."""
    def of(text: str, on_products: bool = True) -> str:
        if not brand:
            return text
        return f"{text} {'على منتجات' if on_products else 'من'} {html.escape(brand, quote=False)}"

    if c.kind == "multibuy" and (m := _BUY_GET.search(c.title)):
        buy, free = int(m.group(1)), int(m.group(2))
        return of(f"{buy + free} بسعر {buy}")
    if c.kind == "multibuy" and (m := _N_FOR_M.search(c.title)):
        return of(f"{m.group(1)} بسعر {m.group(2)}")
    if c.kind == "multibuy" and c.amount and c.min_quantity:
        return of(f"اشتري {c.min_quantity} ووفر {_egp(c.amount)} جنيه")
    if c.amount and c.min_order:
        return of(f"وفر {_egp(c.amount)} جنيه لما تشتري بـ {_egp(c.min_order)} جنيه أو أكتر", on_products=False)
    if c.kind == "multibuy" and c.percent and c.min_quantity:
        paid = c.min_quantity * (1 - c.percent)
        if c.min_quantity >= 2 and not c.cap and paid >= 1 and abs(paid - round(paid)) < 0.02:
            return of(f"{c.min_quantity} بسعر {round(paid)}")
        limit = f"، بحد أقصى {_egp(c.cap)} جنيه" if c.cap else ""
        return of(f"اشتري {c.min_quantity} ووفر {c.percent * 100:g}%{limit}")
    if c.amount and c.kind != "multibuy":
        return of(f"خصم {_egp(c.amount)} جنيه")
    if c.percent and c.kind != "multibuy":
        limit = f" بحد أقصى {_egp(c.cap)} جنيه" if c.cap else ""
        spend = f" لما تشتري بـ {_egp(c.min_order)} جنيه أو أكتر" if c.min_order else ""
        return of(f"خصم {c.percent * 100:g}%{limit}{spend}")
    return of(html.escape(c.title, quote=False))


_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
           "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "مايو": 5, "يونيو": 6, "يوليو": 7, "أغسطس": 8,
           "اغسطس": 8, "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12}
_MONTH_NAME = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
_DATE = re.compile(r"(\d{1,2})\s+([^\W\d_]+)\s+(20\d\d)")


def schedule_dates(text: str) -> tuple[str | None, str | None]:
    """(start, end) as YYYY-MM-DD from "From 05 Nov 2026 to 12 Nov 2026", its Arabic twin, or "Expires: Monday, 30 November 2026"."""
    found = []
    for day, month, year in _DATE.findall(text or ""):
        number = _MONTHS.get(month) or _MONTHS.get(month[:3].lower())
        if number:
            found.append(f"{year}-{number:02d}-{int(day):02d}")
    if not found:
        return None, None
    return (found[0] if len(found) > 1 else None), found[-1]


def until(end: str) -> str:
    """'2026-11-12' as the line a post closes with."""
    _, month, day = end.split("-")
    return f"⏳ لحد {int(day)} {_MONTH_NAME[int(month) - 1]}"
