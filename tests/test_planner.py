from fakesite import FakeSite, make_catalog

from deals.models import Slice
from deals.planner import plan


def test_plan_covers_every_product_within_the_cap():
    catalog = make_catalog()
    site = FakeSite(catalog)
    planned = plan(site, Slice("category", "root"), delay=(0, 0), report=lambda _: None)

    assert all(p.total is not None and p.total <= site.max_slice_total for p in planned)
    covered = set()
    for p in planned:
        covered |= {sku for sku, _, _ in site.matches(p.slice)}
    assert covered == {sku for sku, _, _ in catalog}


def test_plan_respects_price_floor():
    catalog = make_catalog()
    site = FakeSite(catalog)
    planned = plan(site, Slice("category", "root", price_min=1000), delay=(0, 0), report=lambda _: None)

    assert all(p.slice.price_min is not None and p.slice.price_min >= 1000 for p in planned)
    covered = set().union(*({sku for sku, _, _ in site.matches(p.slice)} for p in planned))
    assert covered == {sku for sku, _, price in catalog if price >= 1000}


def test_every_probe_page_is_reported():
    batches = []
    site = FakeSite(make_catalog(200))
    plan(site, Slice("category", "root"), on_listings=batches.append, delay=(0, 0), report=lambda _: None)
    assert len(batches) == site.requests and any(batches)  # empty price bands report empty pages


def test_products_that_sit_on_a_parent_category_are_not_lost():
    # Every shop lists some products on a category itself and in none of its children, so
    # a sub-category split would lose them; the planner splits by price only.
    catalog = make_catalog() + [(f"PARENT{i}", "root", 500 + i) for i in range(30)]
    site = FakeSite(catalog)
    planned = plan(site, Slice("category", "root"), delay=(0, 0), report=lambda _: None)

    assert all(p.slice.value == "root" for p in planned)
    covered = set().union(*({sku for sku, _, _ in site.matches(p.slice)} for p in planned))
    assert covered == {sku for sku, _, _ in catalog}


def test_an_empty_deal_feed_is_kept_but_an_empty_category_is_not():
    # A deal feed with no deals right now must still be polled: deals start any minute and the
    # plan is redone only weekly (31 Oct 2026: nine Amazon feeds sat unpolled for a week).
    site = FakeSite(make_catalog(50))
    feed = Slice("deals", "node", price_min=10**9)
    assert [(p.slice, p.total) for p in plan(site, feed, delay=(0, 0), report=lambda _: None)] == [(feed, 0)]
    empty = Slice("category", "root", price_min=10**9)
    assert plan(site, empty, delay=(0, 0), report=lambda _: None) == []
