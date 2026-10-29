"""Product pages (details.py, detailq.py): parsers on saved real pages, and every rule that keeps
reading them safe: only when idle, paced, capped, paused after a refusal, never touching crawling."""

import gzip
import threading
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from deals import db, detailq, details, matching
from deals.config import DetailRules
from deals.db import iso, utcnow
from deals.models import Blocked, Listing
from deals.worker import Worker

FIXTURES = Path(__file__).parent / "fixtures"
NOW = utcnow()


def page(name: str) -> str:
    return gzip.decompress((FIXTURES / name).read_bytes()).decode(errors="ignore")


@pytest.mark.parametrize("site, fixture, gtins, mpns", [
    ("jumia_eg", "jumia_product.html.gz", ["6932169394079"], ["CPH2831"]),  # OPPO A6: barcode + model
    ("jumia_eg", "jumia_product_model.html.gz", [], ["JMK8001"]),
    ("2b_eg", "2b_product.html.gz", ["6932169394079"], ["CPH2831AG"]),  # the same OPPO A6: the same barcode
    ("amazon_eg", "amazon_product.html.gz", ["0050036377676"], []),  # UPC; its "model number" has no digit
    ("btech_eg", "btech_product.html.gz", [], ["L43HYNDA630"]),  # from the description meta
    ("cairosales_eg", "cairosales_product.html.gz", [], ["OLED65CS6VA-S30A"]),
])
def test_parsers_read_the_product_block_only(site, fixture, gtins, mpns):
    d = details.PARSERS[site](page(fixture))
    assert d.gtins == gtins and d.mpns[:len(mpns)] == mpns and len(d.specs) <= details.MAX_SPECS


class FakeResponse:
    def __init__(self, status, text):
        self.status_code, self.text = status, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSite:
    def __init__(self, name, status=200, text=None):
        self.name = name
        self._session = self
        self.status, self.text, self.calls = status, text, 0

    def get(self, url, **kw):
        self.calls += 1
        return FakeResponse(self.status, self.text if self.text is not None else page("jumia_product.html.gz"))


def test_fetch_details_never_fights_a_challenge():
    assert details.fetch_details(FakeSite("jumia_eg"), "u").gtins == ["6932169394079"]
    assert details.fetch_details(FakeSite("jumia_eg", 404, ""), "u") is None  # gone
    for site in (FakeSite("jumia_eg", 403), FakeSite("jumia_eg", 429), FakeSite("jumia_eg", 200, "<title>Robot Check</title>" + "x" * 30000),
                 FakeSite("jumia_eg", 200, "<html>Just a moment...</html>" + " " * 30000),
                 FakeSite("jumia_eg", 200, "<html>short</html>")):
        with pytest.raises(Blocked):
            details.fetch_details(site, "u")
        assert site.calls == 1  # one request, no retry
    with pytest.raises(ValueError):
        details.fetch_details(FakeSite("noon_eg"), "u")


@pytest.fixture
def conn(cfg):
    return db.connect(cfg.db_path)


def add(conn, site, sku, title, price=100_000, in_stock=True, seen=NOW):
    with conn:
        return db.record(conn, Listing(site, sku, title, f"https://x/{sku}", price, None, "EGP", in_stock), now=seen).product_id


def test_offer_only_when_idle_paced_capped_and_not_refused(cfg, conn):
    cfg = replace(cfg, details=DetailRules(every_seconds=30, daily_cap=2))
    a, b, c = (add(conn, "jumia_eg", f"J{i}", f"Kettle {i}") for i in range(3))
    matching.rebuild(conn, NOW)  # refills the queue
    assert conn.execute("SELECT COUNT(*) FROM detail_queue WHERE site = 'jumia_eg'").fetchone()[0] == 3
    ip = "1.2.3.4"
    assert detailq.offer(conn, cfg, ip, "jumia_eg", NOW, idle_seconds=5) is None  # a crawl job is about due
    assert detailq.offer(conn, replace(cfg, details=DetailRules(enabled=False)), ip, "jumia_eg", NOW, 60) is None
    assert detailq.offer(conn, cfg, ip, "noon_eg", NOW, 60) is None  # not a page-reading shop
    first = detailq.offer(conn, cfg, ip, "jumia_eg", NOW, 60)
    assert first["product_id"] == a and first["url"] == "https://x/J0"
    assert detailq.offer(conn, cfg, ip, "jumia_eg", NOW + timedelta(seconds=10), 60) is None  # pace
    assert detailq.offer(conn, cfg, "5.6.7.8", "jumia_eg", NOW + timedelta(seconds=10), 60) is not None  # pace is per IP
    assert detailq.offer(conn, cfg, ip, "jumia_eg", NOW + timedelta(seconds=31), 60) is not None
    assert detailq.offer(conn, cfg, ip, "jumia_eg", NOW + timedelta(seconds=62), 60) is None  # daily cap (2)
    with conn:
        conn.execute("INSERT INTO blocks VALUES ('jumia_eg', '9.9.9.9', 1, ?, 'captcha', ?)",
                     (iso(NOW + timedelta(hours=1)), iso(NOW)))
    assert detailq.offer(conn, cfg, "9.9.9.9", "jumia_eg", NOW, 60) is None  # the shop refuses this IP's crawling
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0  # nothing touched the crawl jobs


def test_a_refusal_pauses_that_shop_for_that_ip(cfg, conn):
    add(conn, "jumia_eg", "J1", "Kettle")
    add(conn, "jumia_eg", "J2", "Toaster")
    matching.rebuild(conn, NOW)
    first = detailq.offer(conn, cfg, "1.2.3.4", "jumia_eg", NOW, 60)
    detailq.record(conn, cfg, first["product_id"], "jumia_eg", "1.2.3.4", {"status": "blocked", "reason": "HTTP 403"}, NOW)
    later = NOW + timedelta(hours=23)
    assert detailq.offer(conn, cfg, "1.2.3.4", "jumia_eg", later, 60) is None
    assert detailq.offer(conn, cfg, "1.2.3.4", "jumia_eg", NOW + timedelta(hours=25), 60) is not None
    assert "HTTP 403" in detailq.stats(conn, later)[0]["paused"][0]


def test_what_a_page_gives_is_kept_and_matches(cfg, conn):
    tagged = add(conn, "jumia_eg", "J1", "OPPO A6 5G Dual SIM Aurora Gold 8GB RAM 256GB", 2_000_000)
    coded = add(conn, "btech_eg", "B1", "Oppo A6 5G CPH2831 , 256 GB , 8 GB - Aurora Gold", 2_050_000)
    barcoded = add(conn, "2b_eg", "T1", "Oppo A6 5G - 8GB RAM - 256GB - Aurora Gold", 2_100_000)
    detailq.record(conn, cfg, tagged, "jumia_eg", "ip", {"status": "ok", "gtins": ["06932169394079"],
                                                         "mpns": ["CPH2831"], "specs": {"color": "Aurora Gold"}}, NOW)
    detailq.record(conn, cfg, barcoded, "2b_eg", "ip", {"status": "ok", "gtins": ["6932169394079"]}, NOW)
    row = conn.execute("SELECT status, gtins, codes FROM product_details WHERE product_id = ?", (tagged,)).fetchone()
    assert tuple(row) == ("ok", "6932169394079", "CPH2831")
    matching.rebuild(conn, NOW)
    models = {r[0]: r[1] for r in conn.execute("SELECT product_id, model FROM product_models")}
    # the page's model number joins the B.TECH title code; the shared barcode brings 2B along
    assert models[tagged] == models[coded] == models[barcoded] and "CPH2831" in models[coded]
    # read once: neither is queued again, unless its title changes
    queued = {r[0] for r in conn.execute("SELECT product_id FROM detail_queue")}
    assert tagged not in queued and barcoded not in queued and coded in queued
    with conn:
        conn.execute("UPDATE products SET title = 'OPPO A6 5G 8GB 256GB Gold' WHERE id = ?", (tagged,))
    detailq.refill(conn, NOW)
    assert tagged in {r[0] for r in conn.execute("SELECT product_id FROM detail_queue")}


def test_used_accessories_and_stale_listings_are_not_queued(conn):
    add(conn, "jumia_eg", "J1", "Renewed - iPhone 14 128GB")
    add(conn, "jumia_eg", "J2", "Ear Pads Cushions for Sony WH-1000XM5")
    add(conn, "jumia_eg", "J3", "Kettle sold out", in_stock=False)
    add(conn, "jumia_eg", "J4", "Kettle not seen lately", seen=NOW - timedelta(days=10))
    add(conn, "noon_eg", "N1@s", "Kettle on noon")  # no page parser for noon
    matching.rebuild(conn, NOW)
    assert conn.execute("SELECT COUNT(*) FROM detail_queue").fetchone()[0] == 0


class FakeApi:
    def __init__(self):
        self.calls = []

    def call(self, path, payload):
        self.calls.append((path, payload))
        return {"ok": True}


def test_worker_reads_one_page_and_reports_a_refusal_without_retrying(tmp_path):
    good, refused = FakeSite("jumia_eg"), FakeSite("jumia_eg", 403)
    sites = {"jumia_eg": good}
    w = Worker("http://x", "t", ["jumia_eg"], tmp_path, site_factory=lambda name: sites[name])
    w.api = FakeApi()
    w.read_detail({"product_id": 7, "site": "jumia_eg", "url": "https://jumia/p"})
    path, report = w.api.calls[-1]
    assert path == "/api/details/7" and report["status"] == "ok" and report["gtins"] == ["6932169394079"]
    w._site_objects["jumia_eg"] = refused
    w.read_detail({"product_id": 8, "site": "jumia_eg", "url": "https://jumia/q"})
    assert w.api.calls[-1][1]["status"] == "blocked" and refused.calls == 1


def test_a_model_number_written_with_spaces_is_one_code():
    assert details.from_pairs([("Model", "UI6 F1T S")]).mpns == ["UI6F1TS"]
    assert details.from_pairs([("Model", "CPH2831 Gold")]).mpns == ["CPH2831"]
    assert details.from_pairs([("Model Number", "a long description of the product")]).mpns == []
