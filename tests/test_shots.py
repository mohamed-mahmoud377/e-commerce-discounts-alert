import threading
from dataclasses import replace
from datetime import timedelta

import pytest
from selectolax.lexbor import LexborHTMLParser

from conftest import fixture_text
from deals import bot, db, shotq, shots
from deals.alerts import AlertSender, TelegramError, shot_caption
from deals.db import iso, utcnow
from deals.models import Listing
from test_coordinator import W1, client, lease, listing, plan_amazon  # noqa: F401 (client is a fixture)

JPEG = b"\xff\xd8\xff\xe0" + b"x" * 2000
ADMIN, FRIEND = 100, 200


@pytest.fixture(autouse=True)
def fresh_state():
    shotq._shooters.clear()
    shotq.IMAGES.clear()
    yield
    shotq._shooters.clear()  # other test files must not see a screenshot worker
    shotq.IMAGES.clear()


# Each shop's product page as the screenshot browser saw it (Playwright's page.content()), with what its price
# box and its seller box (if the shop shows one) must contain.
SAVED = [
    ("amazon_eg", "amazon_product_ar.html.gz", ("%", "جنيه"), "Electro.Zone"),
    ("amazon_now_eg", "amazon_now_product.html.gz", ("%", "جنيه"), "Amazon Now"),
    ("jumia_eg", "jumia_product_rendered.html.gz", ("2,799.00 جنيه", "3,499.00", "20%"), "Miracle Trading"),
    ("noon_eg", "noon_product_rendered.html.gz", ("3529.00", "4750.00", "خصم 25%"), "جون بي سي"),
    ("btech_eg", "btech_product_rendered.html.gz", ("113,335", "124,335"), "الحريري جروب"),
    ("2b_eg", "2b_product_rendered.html.gz", ("70,999", "96,899", "27%"), None),
    ("cairosales_eg", "cairosales_product_rendered.html.gz", ("10,270", "11,670", "-12%"), None),
    ("dubaiphone_eg", "dubaiphone_product_rendered.html.gz", ("5,599",), None),  # no crossed-out price on the page
    ("hardwaremarket_eg", "hardwaremarket_product_rendered.html.gz", ("1.999,00", "2.999,00"), None),
    ("dream2000_eg", "dream2000_product_rendered.html.gz", ("25,499.00", "28,900.00"), None),
    ("carrefour_eg", "carrefour_product_rendered.html.gz", ("6,849", "16,999.00", "خصم 60%"), "Electro Zone"),
    ("raya_eg", "raya_product_rendered.html.gz", ("4,555", "5,694", "1,139"), "Raya Shop"),
    ("vodafone_eg", "vodafone_product_rendered.html.gz", ("50,999", "55,799"), None),
    ("dokkantech_eg", "dokkantech_product_rendered.html.gz", ("1,349",), None),
    ("etisal_eg", "etisal_product_rendered.html.gz", ("20,799", "27,579", "6,780"), None),
    ("orange_eg", "orange_product_rendered.html.gz", ("25,655",), None),
    ("lcwaikiki_eg", "lcwaikiki_product_rendered.html.gz", ("1,499.00", "1,999.00"), None),
    ("defacto_eg", "defacto_product_rendered.html.gz", ("799", "999"), None),
    ("elaraby_eg", "elaraby_product_rendered.html.gz", ("58,340", "60,770"), None),
    ("miami_eg", "miami_product_rendered.html.gz", ("53,000",), None),
    ("sigma_eg", "sigma_product_rendered.html.gz", ("45999", "47000"), None),
    ("elnekhely_eg", "elnekhely_product_rendered.html.gz", ("48,999", "52,999"), None),
    ("wayup_eg", "wayup_product_rendered.html.gz", ("1,600.00", "3,199.00"), None),
    ("gcshop_eg", "gcshop_product_rendered.html.gz", ("6,360", "6,710"), None),
    ("azzam_eg", "azzam_product_rendered.html.gz", ("27,995.00",), None),
]


@pytest.mark.parametrize("site,fixture,price,seller", SAVED, ids=[s[0] for s in SAVED])
def test_every_box_and_context_element_is_on_a_saved_page(site, fixture, price, seller):
    tree = LexborHTMLParser(fixture_text(fixture))
    spec = shots.SPECS[site]
    found = [next((tree.css_first(s) for s in alternatives if tree.css_first(s) is not None), None)
             for alternatives in spec.boxes]
    assert all(found), spec.boxes
    for s in (*spec.context, spec.ready):
        assert tree.css_first(s) is not None, s
    assert all(p in found[0].text() for p in price), found[0].text()[:300]
    assert (len(spec.boxes) == 2) == (seller is not None)  # a seller box where the shop shows a seller
    if seller:
        assert seller in found[1].text()


def test_every_shop_with_alerts_has_a_spec():
    assert set(shots.SPECS) == {s[0] for s in SAVED}


@pytest.mark.parametrize("site,url,arabic", [
    ("amazon_eg", "https://www.amazon.eg/-/en/dp/B0976DNC9B", "https://www.amazon.eg/-/ar/dp/B0976DNC9B"),
    ("jumia_eg", "https://www.jumia.com.eg/beko-iron-131373891.html", "https://www.jumia.com.eg/ar/beko-iron-131373891.html"),
    ("noon_eg", "https://www.noon.com/egypt-en/hot-60i/N70204493V/p/", "https://www.noon.com/egypt-ar/hot-60i/N70204493V/p/"),
    ("btech_eg", "https://btech.com/en/p/e2bc?offering_id=f87a", "https://btech.com/ar/p/e2bc?offering_id=f87a"),
    ("2b_eg", "https://2b.com.eg/en/dell-latitude-5450.html", "https://2b.com.eg/ar/dell-latitude-5450.html"),
    ("cairosales_eg", "https://cairosales.com/en/monitors/dell-e2216hv.html", "https://cairosales.com/ar/monitors/dell-e2216hv.html"),
    ("dubaiphone_eg", "https://www.dubaiphone.net/en/shop/anker-maggo", "https://www.dubaiphone.net/shop/anker-maggo"),
    ("hardwaremarket_eg", "https://hardwaremarket.net/product/h858/", "https://hardwaremarket.net/product/h858/"),  # English only
    ("dream2000_eg", "https://dream2000.com/en/products/haier?variant=1", "https://dream2000.com/products/haier?variant=1"),
    ("carrefour_eg", "https://www.carrefouregypt.com/mafegy/en/hob/beko/p/513309",
     "https://www.carrefouregypt.com/mafegy/ar/hob/beko/p/513309"),
])
def test_the_arabic_page(site, url, arabic):
    assert shots.SPECS[site].page_url(url) == arabic


def test_amazon_now_seller_line_and_arabic_page():
    tree = LexborHTMLParser(fixture_text("amazon_now_product.html.gz"))
    assert "Amazon Now" in tree.css_first("#fresh-merchant-info").text()
    spec = shots.SPECS["amazon_now_eg"]
    assert (spec.page_url("https://www.amazon.eg/-/en/dp/B0CP2LJBY1?almBrandId=sAuWWBROaG&fpw=alm")
            == "https://www.amazon.eg/-/ar/dp/B0CP2LJBY1?almBrandId=sAuWWBROaG&fpw=alm")
    assert spec.locale == "ar-EG"


def test_crop_takes_the_boxes_and_what_fits_of_the_context():
    price, seller = (288, 329, 559, 52), (37, 510, 206, 78)
    title, image = (309, 208, 537, 59), (865, 206, 344, 410)
    assert shots.crop([price, seller], [title, image], 1280) == (23, 192, 1200, 438)
    # a tall image does not fit under the height cap: left out, the boxes stay
    assert shots.crop([price, seller], [(865, 206, 344, 2000)], 1280) == (23, 315, 838, 287)
    # boxes too far apart together: from the price box down
    x, y, w, h = shots.crop([price, (37, 1900, 206, 78)], [], 1280)
    assert (x, y, h) == (274, 315, 80)
    # never outside the page
    assert shots.crop([(0, 0, 1300, 40)], [], 1280) == (0, 0, 1280, 54)


# The layout of an Amazon product page in miniature (the saved real pages need their stylesheets to render).
PAGE = """<html dir="rtl"><body style="margin:0;font:16px sans-serif">
<div style="position:fixed;bottom:0;left:0;right:0;height:300px;background:#123" id="sp-cc">cookies</div>
<div style="display:flex;gap:20px;padding:200px 18px">
  <div id="rightCol" style="width:244px"><div id="offerDisplayFeatures_desktop" style="height:78px;margin-top:300px">
    Amazon.eg</div></div>
  <div id="centerCol" style="width:559px"><h1 id="productTitle">طقم طهي</h1>
    <div id="corePriceDisplay_desktop_feature_div" style="height:52px">-33% 5,364.77 جنيه</div></div>
  <div id="imgTagWrapperId" style="width:344px;height:410px;background:#ccc"></div>
</div></body></html>"""


def test_annotate_draws_the_boxes_hides_overlays_and_returns_a_jpeg():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(f"no Chromium here: {exc}")
        try:
            page = browser.new_page(viewport=shots.VIEWPORT)
            page.set_content(PAGE)
            image = shots.annotate(page, shots.SPECS["amazon_eg"])
            assert image[:2] == b"\xff\xd8" and len(image) > 3000
            boxes = page.evaluate("[...document.querySelectorAll('.deals-shot-box')].map(e => e.getBoundingClientRect().height)")
            assert boxes == [52 + 2 * shots.PAD, 78 + 2 * shots.PAD]  # price block and seller, a little larger
            assert page.evaluate("getComputedStyle(document.querySelector('#sp-cc')).display") == "none"
            page.evaluate("document.querySelector('#corePriceDisplay_desktop_feature_div').remove()")
            assert shots.annotate(page, shots.SPECS["amazon_eg"]) is None  # no price block: no screenshot
        finally:
            browser.close()


# noon's product page while it hydrates: the gallery has its placeholder and no image yet, the labels are still
# English, and the "lowest price / few left" ticker rolls next to the price.
NOON_EARLY = """<html dir="rtl"><body style="margin:0;font:16px sans-serif">
<h1 data-qa="pdp-product-name">مقلاة</h1>
<div class="_priceAndNudgesContainer_1cnog_67"><div class="_priceOfferCtr_1fgtw_1" style="height:40px">3529.00 25% Off</div>
  <div class="_nudgesContainer_1cnog_75" style="height:16px;overflow:hidden">few left</div></div>
<a class="_partnerRatingsCtr_x" href="/egypt-ar/seller/p-1/" style="display:block;height:80px">Sold by JP</a>
<div data-pdp-gallery=""><div class="_imageContainer_135hb_63"><div class="_mediaContainer_1lnxf_39"></div></div>
  <div class="_placeholderContainer_1lnxf_32"><img alt="placeholder" src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></div></div>
</body></html>"""
PIXELS = ("data:image/gif;base64,R0lGODlhAgACAIAAAAAAAP///yH5BAEAAAEALAAAAAACAAIAAAICjF8AOw==")


def test_noon_waits_for_the_gallery_image_and_drops_the_rolling_ticker():
    """Hydration brings the Arabic labels and the image together: a shot before it has English and no image."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    spec = shots.SPECS["noon_eg"]
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(f"no Chromium here: {exc}")
        try:
            page = browser.new_page(viewport=shots.VIEWPORT)
            page.set_content(NOON_EARLY)
            assert not page.evaluate(shots._LOADED, list(spec.context))  # not yet: the price alone is not enough
            page.evaluate("""(src) => { const i = document.createElement('img'); i.src = src;
                document.querySelector('[class*="_mediaContainer_"]').appendChild(i); }""", PIXELS)
            page.wait_for_function(shots._LOADED, arg=list(spec.context), timeout=2000)
            assert shots.annotate(page, spec)[:2] == b"\xff\xd8"
            assert page.evaluate("""getComputedStyle(document.querySelector('[class*="_nudgesContainer_"]')).display""") == "none"
        finally:
            browser.close()


class FakeTelegram:
    def __init__(self):
        self.out, self.fail_photo = [], False

    def send(self, chat_id, text, buttons=None):
        self.out.append((chat_id, "text", text))
        return {"message_id": len(self.out)}

    def send_photo(self, chat_id, photo, caption):
        if self.fail_photo:
            raise TelegramError(self.fail_photo, "Bad Request: IMAGE_PROCESS_FAILED")
        self.out.append((chat_id, "photo", photo))
        return {"message_id": len(self.out), "photo": [{"file_id": "small"}, {"file_id": "FILE-1"}]}


def deal_on_amazon(client, shoot=True):
    """A crawl page that reveals a 40% drop; returns the page reply."""
    plan_amazon(client)
    conn = client.conn
    with conn:
        db.record(conn, Listing("amazon_eg", "TV1", "Big TV", "https://www.amazon.eg/-/en/dp/TV1", 1_000_000, None,
                                "EGP", True), grp="TVs", now=utcnow() - timedelta(days=60))
        conn.execute("UPDATE jobs SET last_done_at = ? WHERE kind = 'crawl'", (iso(utcnow()),))
    job = lease(client)
    return client.post(f"/api/jobs/{job['id']}/page", headers=W1, json={
        "lease_token": job["lease_token"], "page": 1, "done": True, "shoot": shoot,
        "listings": [listing("TV1", 6000, url="https://www.amazon.eg/-/en/dp/TV1")]}).json()


def sender_for(cfg, conn):
    cfg = replace(cfg, telegram_token="t", telegram_chat_id=str(ADMIN))
    bot.ensure_admin(conn, cfg, utcnow())
    with conn:
        conn.execute("INSERT INTO subscribers (chat_id, status, created_at, updated_at) VALUES (?, 'active', ?, ?)",
                     (FRIEND, iso(utcnow()), iso(utcnow())))
    tg = FakeTelegram()
    return AlertSender(cfg, threading.Event(), telegram=tg), tg


def test_the_worker_that_found_the_deal_gets_its_screenshot_and_the_alert_waits_for_it(client, cfg, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    reply = deal_on_amazon(client)
    assert reply["deals"] == 1
    alert_id = client.conn.execute("SELECT id FROM alerts WHERE kind = 'price_drop'").fetchone()["id"]
    assert reply["shot"] == {"alert_id": alert_id, "site": "amazon_eg", "url": "https://www.amazon.eg/-/en/dp/TV1"}

    sender, tg = sender_for(cfg, client.conn)
    sender.fan_out(client.conn, utcnow())
    assert sender.deliver(client.conn) == 0 and tg.out == []  # waiting for the screenshot

    r = client.post(f"/api/shots/{alert_id}", params={"status": "ok"}, content=JPEG,
                    headers={**W1, "Content-Type": "image/jpeg"})
    assert r.json()["status"] == "ok"
    assert sender.deliver(client.conn) == 2
    assert [(c, kind) for c, kind, _ in tg.out] == [(ADMIN, "photo"), (ADMIN, "text"), (FRIEND, "photo"), (FRIEND, "text")]
    assert tg.out[0][2] == JPEG and tg.out[2][2] == "FILE-1"  # uploaded once, then by Telegram's id
    assert shotq.IMAGES.get(alert_id) is None  # nothing kept once Telegram has it
    shot = client.conn.execute("SELECT * FROM shots WHERE alert_id = ?", (alert_id,)).fetchone()
    assert shot["status"] == "ok" and shot["worker"] == "w1" and shot["file_id"] == "FILE-1" and shot["bytes"] == len(JPEG)


def test_without_a_screenshot_worker_alerts_are_not_held(client, cfg, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    reply = deal_on_amazon(client, shoot=False)
    assert reply["deals"] == 1 and "shot" not in reply
    assert client.conn.execute("SELECT COUNT(*) FROM shots").fetchone()[0] == 0
    sender, tg = sender_for(cfg, client.conn)
    sender.fan_out(client.conn, utcnow())
    assert sender.deliver(client.conn) == 2 and {k for _, k, _ in tg.out} == {"text"}


def test_no_screenshot_in_time_or_none_found_means_text_only(client, cfg, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    deal_on_amazon(client)
    alert_id = client.conn.execute("SELECT id FROM alerts WHERE kind = 'price_drop'").fetchone()["id"]
    sender, tg = sender_for(cfg, client.conn)
    sender.fan_out(client.conn, utcnow())
    later = utcnow() + timedelta(seconds=cfg.alerts.screenshot_wait_seconds + 1)
    assert sender.deliver(client.conn, later) == 2 and {k for _, k, _ in tg.out} == {"text"}
    # the worker's screenshot after the alert went out is not kept
    r = client.post(f"/api/shots/{alert_id}", content=JPEG, headers={**W1, "Content-Type": "image/jpeg"})
    assert r.json()["status"] == "late" and shotq.IMAGES.get(alert_id) is None


def test_a_page_without_the_price_block_releases_the_alert_at_once(client, cfg, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    deal_on_amazon(client)
    alert_id = client.conn.execute("SELECT id FROM alerts WHERE kind = 'price_drop'").fetchone()["id"]
    client.post(f"/api/shots/{alert_id}", params={"status": "none", "reason": "price block not found"}, headers=W1)
    sender, tg = sender_for(cfg, client.conn)
    sender.fan_out(client.conn, utcnow())
    assert sender.deliver(client.conn) == 2 and {k for _, k, _ in tg.out} == {"text"}


def test_a_failed_photo_never_costs_the_text(client, cfg, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    deal_on_amazon(client)
    alert_id = client.conn.execute("SELECT id FROM alerts WHERE kind = 'price_drop'").fetchone()["id"]
    client.post(f"/api/shots/{alert_id}", content=JPEG, headers={**W1, "Content-Type": "image/jpeg"})
    sender, tg = sender_for(cfg, client.conn)
    tg.fail_photo = 400
    sender.fan_out(client.conn, utcnow())
    assert sender.deliver(client.conn) == 2 and [k for _, k, _ in tg.out] == ["text", "text"]
    assert sender.deliver(client.conn) == 0  # and it is not retried
    marked = client.conn.execute("SELECT COUNT(*) FROM deliveries WHERE photo_sent_at IS NOT NULL").fetchone()[0]
    assert marked == 2  # a refused photo is not tried again


def test_a_passing_photo_failure_is_tried_again_only_with_the_text(client, cfg, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    deal_on_amazon(client)
    alert_id = client.conn.execute("SELECT id FROM alerts WHERE kind = 'price_drop'").fetchone()["id"]
    client.post(f"/api/shots/{alert_id}", content=JPEG, headers={**W1, "Content-Type": "image/jpeg"})
    sender, tg = sender_for(cfg, client.conn)
    tg.fail_photo = 502
    sender.fan_out(client.conn, utcnow())
    assert sender.deliver(client.conn) == 2 and [k for _, k, _ in tg.out] == ["text", "text"]
    assert shotq.IMAGES.get(alert_id) == JPEG  # still nobody has it: kept until it is dropped (KEEP_SECONDS)


def test_screenshot_jobs_go_to_screenshot_workers_once_and_not_too_late(client, cfg):
    plan_amazon(client)
    now = utcnow()
    with client.conn:
        cur = client.conn.execute("INSERT INTO alerts (kind, message, created_at, next_attempt_at) VALUES "
                                  "('price_drop', 'x', ?, ?)", (iso(now), iso(now)))
        assert not shotq.add(client.conn, cfg, cur.lastrowid, "amazon_eg", "https://www.amazon.eg/-/en/dp/X", now)
        shotq.seen_shooter(["amazon_eg"], now)
        assert not shotq.add(client.conn, cfg, cur.lastrowid, "noon_eg", "https://noon/x", now)  # nobody shoots noon
        shotq.seen_shooter(["other_eg"], now)
        assert not shotq.add(client.conn, cfg, cur.lastrowid, "other_eg", "https://other/x", now)  # no spec
        assert shotq.add(client.conn, cfg, cur.lastrowid, "amazon_eg", "https://www.amazon.eg/-/en/dp/X", now)
    r = client.post("/api/lease", json={"sites": ["amazon_eg"]}, headers=W1).json()
    assert "shot" not in r  # this worker doesn't take screenshots
    r = client.post("/api/lease", json={"sites": ["amazon_eg"], "shoot": True}, headers=W1).json()
    assert r["shot"]["alert_id"] == cur.lastrowid
    assert shotq.lease(client.conn, ["amazon_eg"], "w2", now) is None  # leased
    assert shotq.lease(client.conn, ["amazon_eg"], "w2", now + timedelta(seconds=50)) is None  # past the wait
    assert shotq.lease(client.conn, ["amazon_eg"], "w2", now + timedelta(seconds=20)) is None  # lease still runs


def test_uploads_must_be_jpegs(client):
    r = client.post("/api/shots/1", content=b"<html>", headers={**W1, "Content-Type": "image/jpeg"})
    assert r.status_code == 422


def test_caption_names_the_shop_and_cairo_time():
    text = shot_caption("amazon_now_eg", "2026-11-03T17:31:00+00:00")
    assert "أمازون ناو" in text and "19:31" in text
