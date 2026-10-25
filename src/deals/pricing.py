"""Price history statistics and deal detection.

History is a step function: each row's price holds until the next row. All
"typical price" figures are *time-weighted*: a price that held for 60 days
counts 60x more than one that lasted a day. That makes the reference price
robust against the classic fake discount, where a seller raises the price for
a few days and then "cuts" it back to normal.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from deals.config import AlertRules
from deals.models import LIMITED_DEAL, LOWEST_30D, LOWEST_365D, MEGA_DEAL, SELLING_FAST

DAY = 86400.0

# What can back up a shop's claimed discount on a product we have no history for.
# Popularity ("sold recently") and urgency ("selling out fast") are marketing and
# don't count; "only N left" only counts when N <= 3 (amazon.eg shows "only N left"
# on about half its listings).
STRONG_SIGNALS = {"mega_deal", "limited_deal", "lowest_365d", "lowest_30d"}
# Shops where "only N left" is shown so widely it proves nothing (amazon.eg: 45% of
# listings show 3 or fewer left, measured 2026-10-25).
LOW_STOCK_EVERYWHERE = {"amazon_eg"}


@dataclass(frozen=True)
class Point:
    at: datetime
    price: int | None
    in_stock: bool


@dataclass(frozen=True)
class PriceStats:
    current: int | None
    previous: int | None  # in-stock price before the latest change
    low: int | None  # lowest in-stock price before the current one (whole history)
    high: int | None
    typical_30d: int | None  # time-weighted median before the current price
    typical_90d: int | None
    low_90d: int | None
    high_90d: int | None
    tracked_days: float  # since first seen
    history_days: float  # in-stock days of data before the current price (90-day window)
    changes_90d: int
    current_since: datetime | None


def compute_stats(points: Sequence[Point], now: datetime) -> PriceStats:
    if not points:
        return PriceStats(None, None, None, None, None, None, None, None, 0, 0, 0, None)
    pts = sorted(points, key=lambda p: p.at)
    last = pts[-1]
    current = last.price if last.in_stock else None

    # (price, start, end) for every in-stock stretch before the current one
    prior = [(p.price, p.at, nxt.at) for p, nxt in zip(pts, pts[1:]) if p.in_stock and p.price is not None]
    prices = [p for p, _, _ in prior]

    w90 = _clip(prior, now - timedelta(days=90))
    w30 = _clip(prior, now - timedelta(days=30))
    return PriceStats(
        current=current,
        previous=prior[-1][0] if prior else None,
        low=min(prices) if prices else None,
        high=max(prices) if prices else None,
        typical_30d=weighted_median(w30),
        typical_90d=weighted_median(w90),
        low_90d=min((p for p, _ in w90), default=None),
        high_90d=max((p for p, _ in w90), default=None),
        tracked_days=(now - pts[0].at).total_seconds() / DAY,
        history_days=sum(w for _, w in w90) / DAY,
        changes_90d=sum(1 for p in pts[1:] if p.at >= now - timedelta(days=90)),
        current_since=last.at,
    )


def _clip(segments, since: datetime) -> list[tuple[int, float]]:
    """(price, seconds) of each segment inside [since, ...)."""
    out = []
    for price, start, end in segments:
        start = max(start, since)
        if end > start:
            out.append((price, (end - start).total_seconds()))
    return out


def weighted_median(samples: Sequence[tuple[int, float]]) -> int | None:
    total = sum(w for _, w in samples)
    if total <= 0:
        return None
    acc = 0.0
    for price, weight in sorted(samples):
        acc += weight
        if acc >= total / 2:
            return price
    return None  # unreachable


@dataclass(frozen=True)
class ProductView:
    """What deal rules need to know about the product right now."""

    price: int | None
    in_stock: bool
    list_price: int | None = None
    list_price_kind: str | None = None
    stock_left: int | None = None
    recent_sales: int | None = None
    rating: float | None = None
    reviews: int | None = None
    badges: tuple[str, ...] = ()
    site: str | None = None


@dataclass(frozen=True)
class Deal:
    """Why a price is a deal, as facts; alerts.py turns them into a message."""

    kind: str  # price_drop | all_time_low | price_error | new_product
    price: int
    reference: int  # what the drop is measured against
    drop: float  # 0.42 = 42% below reference
    confidence: str  # high | medium | low
    score: int  # 0-100, for ranking
    all_time_low: bool = False
    previous_low: int | None = None  # lowest earlier price, when this beats it
    previous: int | None = None  # the price just before, when it differs from the reference
    inflated_list_price: int | None = None  # site's crossed-out price above anything we saw
    reference_days: float = 0  # how much history the reference covers
    list_price_kind: str | None = None  # new_product: "was" or "list"
    signals: list[tuple[str, int | None]] = field(default_factory=list)  # e.g. ("only_left", 3)


def evaluate(product: ProductView, stats: PriceStats, rules: AlertRules, *,
             min_price: int | None = None, min_drop: float | None = None,
             bootstrap: bool = False) -> Deal | None:
    """Decide whether the product's current price is a real deal.

    min_price is in EGP. bootstrap=True while we are seeing a slice for the
    first time: every product looks "new" then, so new-product deals need
    stronger evidence to avoid an alert flood on the first crawl.
    """
    price = product.price
    if not product.in_stock or price is None or price <= 0:
        return None
    if min_price and price < min_price * 100:
        return None
    min_drop = rules.min_drop if min_drop is None else min_drop
    signals = _signals(product)
    facts: dict = {}

    if stats.history_days >= rules.min_history_days and stats.typical_90d:
        reference = stats.typical_90d
        drop = 1 - price / reference
        all_time_low = stats.low is not None and price < stats.low
        if drop >= rules.price_error_drop:
            kind = "price_error"
        elif drop >= min_drop:
            kind = "price_drop"
        elif (all_time_low and drop >= rules.atl_min_drop
              and stats.history_days >= rules.atl_min_history_days):
            kind = "all_time_low"
        else:
            return None

        confidence = "high" if stats.history_days >= 14 else "medium"
        facts["reference_days"] = min(stats.tracked_days, 90)
        if all_time_low:
            facts.update(all_time_low=True, previous_low=stats.low)
        if stats.previous and stats.previous != reference:
            facts["previous"] = stats.previous
        if (product.list_price and stats.high and stats.history_days >= 14
                and product.list_price > stats.high * 1.15):
            facts["inflated_list_price"] = product.list_price
        score = drop * 100 + (10 if all_time_low else 0)
    else:
        # Not enough history of our own: rely on the site's claimed discount,
        # but only when other evidence backs it up.
        if not product.list_price or product.list_price <= price:
            return None
        drop = 1 - price / product.list_price
        if drop < max(rules.new_product_min_drop, min_drop):
            return None
        if product.list_price_kind != "was" and drop >= 0.8:
            return None  # "80% off" an inflated RRP is almost always fake
        evidence = [code for code, n in signals if code in STRONG_SIGNALS
                    or (code == "only_left" and n <= 3 and product.site not in LOW_STOCK_EVERYWHERE)]
        if product.list_price_kind == "was":
            evidence.append("was_price")
        if len(evidence) < (2 if bootstrap else 1):
            return None
        kind, reference, confidence = "new_product", product.list_price, "low"
        facts["list_price_kind"] = product.list_price_kind
        score = drop * 100

    if product.stock_left is not None and product.stock_left <= 5:
        score += 5
    if {LIMITED_DEAL, MEGA_DEAL} & set(product.badges):
        score += 5
    if {LOWEST_30D, LOWEST_365D} & set(product.badges):
        score += 5
    score *= {"high": 1.0, "medium": 0.85, "low": 0.7}[confidence]
    return Deal(kind, price, reference, round(drop, 4), confidence, max(0, min(100, round(score))),
                signals=signals, **facts)


def _signals(p: ProductView) -> list[tuple[str, int | None]]:
    out: list[tuple[str, int | None]] = []
    if p.stock_left is not None and p.stock_left <= 10:
        out.append(("only_left", p.stock_left))
    if MEGA_DEAL in p.badges:
        out.append(("mega_deal", None))
    elif LIMITED_DEAL in p.badges:
        out.append(("limited_deal", None))
    if LOWEST_365D in p.badges:
        out.append(("lowest_365d", None))
    elif LOWEST_30D in p.badges:
        out.append(("lowest_30d", None))
    if SELLING_FAST in p.badges:
        out.append(("selling_fast", None))
    if p.recent_sales and p.recent_sales >= 50:
        out.append(("sold_recently", p.recent_sales))
    return out
