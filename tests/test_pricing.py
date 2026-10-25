from datetime import timedelta

from conftest import T0

from deals.config import AlertRules
from deals.models import LIMITED_DEAL, LOWEST_365D, SELLING_FAST
from deals.pricing import Point, ProductView, compute_stats, evaluate, weighted_median

RULES = AlertRules()
EGP = 100


def days(n: float):
    return T0 + timedelta(days=n)


def series(*steps):
    """steps: (day, price_egp) or (day, None) for out of stock."""
    return [Point(days(d), None if p is None else p * EGP, p is not None) for d, p in steps]


def view(price_egp, **kw):
    return ProductView(price=price_egp * EGP, in_stock=True, **kw)


def test_weighted_median_weights_by_duration():
    assert weighted_median([(100, 60.0), (200, 1.0)]) == 100
    assert weighted_median([(100, 1.0), (200, 60.0)]) == 200
    assert weighted_median([]) is None


def test_stats_basic():
    s = compute_stats(series((0, 1000), (30, 900), (40, 700)), now=days(41))
    assert s.current == 700 * EGP and s.previous == 900 * EGP
    assert s.low == 900 * EGP and s.high == 1000 * EGP  # the current price is not part of its own reference
    assert s.typical_90d == 1000 * EGP  # 30 days at 1000 vs 10 at 900
    assert round(s.history_days) == 40 and s.changes_90d == 2


def test_real_drop_alerts():
    s = compute_stats(series((0, 10000), (60, 6500)), now=days(60.01))
    deal = evaluate(view(6500), s, RULES)
    assert deal.kind == "price_drop" and deal.drop == 0.35 and deal.confidence == "high"
    assert deal.reference == 10000 * EGP


def test_fake_discount_inflate_then_cut_is_ignored():
    # 60 days at 1000, "raised" to 2000 for 3 days, then "50% off" back to 1000
    s = compute_stats(series((0, 1000), (60, 2000), (63, 1000)), now=days(63.01))
    assert s.previous == 2000 * EGP  # a naive "vs previous price" check would see -50%
    assert evaluate(view(1000), s, RULES) is None


def test_small_drop_is_ignored_but_all_time_low_counts():
    s = compute_stats(series((0, 1000), (50, 950), (60, 780)), now=days(60.01))
    deal = evaluate(view(780), s, RULES)  # -22% vs typical 1000, below every earlier price
    assert deal.kind == "all_time_low" and deal.all_time_low and deal.previous_low == 950 * EGP
    s = compute_stats(series((0, 1000), (50, 700), (52, 1000), (60, 780)), now=days(60.01))
    assert evaluate(view(780), s, RULES) is None  # -22% but it was 700 before: not an all-time low


def test_price_error_flagged():
    s = compute_stats(series((0, 20000), (40, 5000)), now=days(40.01))
    assert evaluate(view(5000), s, RULES).kind == "price_error"


def test_out_of_stock_time_does_not_count():
    s = compute_stats(series((0, 1000), (2, None), (80, 600)), now=days(80.01))
    assert round(s.history_days) == 2  # 78 days out of stock don't count as history
    assert evaluate(view(600), s, RULES) is None  # only 2 days of history: < min_history_days


def test_min_price_and_group_threshold():
    # dipped to 550 for two days earlier, so 600 is not an all-time low
    s = compute_stats(series((0, 1000), (10, 550), (12, 1000), (40, 600)), now=days(40.01))
    assert evaluate(view(600), s, RULES, min_price=1000) is None
    assert evaluate(view(600), s, RULES, min_drop=0.5) is None
    assert evaluate(view(600), s, RULES, min_drop=0.4).kind == "price_drop"


def test_new_product_needs_backing_evidence():
    s = compute_stats(series((0, 700)), now=days(0.01))
    bare = view(700, list_price=1000 * EGP, list_price_kind="list")
    assert evaluate(bare, s, RULES) is None  # claimed -30% alone is not trusted
    scarce = view(700, list_price=1000 * EGP, list_price_kind="list", stock_left=3, badges=(LIMITED_DEAL,))
    deal = evaluate(scarce, s, RULES)
    assert deal.kind == "new_product" and deal.confidence == "low"
    assert ("only_left", 3) in deal.signals and ("limited_deal", None) in deal.signals


def test_new_product_on_first_crawl_needs_two_signals():
    s = compute_stats(series((0, 700)), now=days(0.01))
    one = view(700, list_price=1000 * EGP, list_price_kind="list", stock_left=3)
    assert evaluate(one, s, RULES, bootstrap=True) is None
    two = view(700, list_price=1000 * EGP, list_price_kind="list", stock_left=3, badges=(LOWEST_365D,))
    assert evaluate(two, s, RULES, bootstrap=True).kind == "new_product"


def test_marketing_nudges_are_not_evidence():
    s = compute_stats(series((0, 700)), now=days(0.01))
    hype = view(700, list_price=1000 * EGP, list_price_kind="list", stock_left=8, recent_sales=500,
                badges=(SELLING_FAST,))
    assert evaluate(hype, s, RULES) is None  # "selling fast", "500 sold", "8 left": no proof of a real discount
    assert evaluate(view(700, list_price=1000 * EGP, list_price_kind="list", stock_left=2), s, RULES).kind == "new_product"


def test_absurd_rrp_discount_rejected():
    s = compute_stats(series((0, 100)), now=days(0.01))
    fake = view(100, list_price=1000 * EGP, list_price_kind="list", stock_left=2, badges=(LIMITED_DEAL,))
    assert evaluate(fake, s, RULES) is None  # "90% off" an RRP


def test_only_n_left_is_not_evidence_on_amazon():
    s = compute_stats(series((0, 700)), now=days(0.01))
    noon = ProductView(700 * EGP, True, list_price=1000 * EGP, list_price_kind="list", stock_left=2, site="noon_eg")
    amazon = ProductView(700 * EGP, True, list_price=1000 * EGP, list_price_kind="list", stock_left=2, site="amazon_eg")
    assert evaluate(noon, s, RULES).kind == "new_product"
    assert evaluate(amazon, s, RULES) is None  # 45% of amazon.eg listings say "only N left"
