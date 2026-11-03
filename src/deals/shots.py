"""A screenshot of a deal's product page, with red boxes around the price and the seller, for the alert.

The worker that found the deal takes it (it can reach the shop: most shops refuse the droplet), in a
thread of its own so crawling never waits, and uploads it once to the coordinator, which sends it with
the alert and keeps it only in memory (shotq.py). Nothing is written to disk on either side.

Each shop has a spec: which page to open (the Arabic one, as friends read it), the elements to box
(price block first: without it there is no screenshot) and the elements the crop should also show
(title, main image). The boxes are drawn into the page itself, then the union of everything is cut
out at phone-friendly size. See docs/GUIDE.md, "Alert screenshots".
"""

import logging
import queue
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Spec:
    # Each entry is one box: alternative selectors, the first visible one is used. The first box is required.
    boxes: tuple[tuple[str, ...], ...]
    context: tuple[str, ...] = ()  # also in the crop if they fit, never boxed
    hide: tuple[str, ...] = ()  # in-page banners to remove (fixed and sticky ones are always hidden)
    ready: str = "body"  # the page has rendered the product once this is visible
    locale: str = "ar-EG"

    def page_url(self, url: str) -> str:
        return url


@dataclass(frozen=True)
class AmazonSpec(Spec):
    def page_url(self, url: str) -> str:
        return url.replace("/-/en/", "/-/ar/")  # same ids and layout, mirrored (right to left)


_AMAZON_PRICE = ("#corePriceDisplay_desktop_feature_div", "#corePrice_desktop", "#corePrice_feature_div")
SPECS: dict[str, Spec] = {
    # Price, discount and list price in the centre column; "Shipper / Seller" rows in the buy box.
    "amazon_eg": AmazonSpec(boxes=(_AMAZON_PRICE, ("#offerDisplayFeatures_desktop", "#merchantInfoFeature_feature_div")),
                            context=("#productTitle", "#imgTagWrapperId"), hide=("#sp-cc",), ready="#productTitle"),
    # Amazon Now's buy box has no seller rows: "Ships from and sold by Amazon Now." is under the price.
    "amazon_now_eg": AmazonSpec(boxes=(_AMAZON_PRICE, ("#fresh-merchant-info", "#offerDisplayFeatures_desktop")),
                                context=("#productTitle", "#imgTagWrapperId"), hide=("#sp-cc",), ready="#productTitle"),
}

VIEWPORT = {"width": 1280, "height": 1000}  # the desktop layout, like the shop's own page on a computer
SCALE = 1.5  # device pixels per CSS pixel: sharp text after Telegram recompresses the photo
MARGIN = 14
MAX_HEIGHT = 820  # CSS pixels: a crop taller than this reads badly on a phone
PAD = 5  # between an element and its red box
QUALITY = 80
PAGE_TIMEOUT = 20  # seconds; the coordinator holds the alert for screenshot_wait_seconds at most
IDLE_CLOSE = 300  # seconds without a screenshot before the browser is closed (memory)

Rect = tuple[float, float, float, float]  # x, y, width, height in CSS pixels of the page


def union(rects: list[Rect]) -> Rect:
    x0, y0 = min(r[0] for r in rects), min(r[1] for r in rects)
    x1, y1 = max(r[0] + r[2] for r in rects), max(r[1] + r[3] for r in rects)
    return x0, y0, x1 - x0, y1 - y0


def crop(boxes: list[Rect], context: list[Rect], page_width: float, max_height: float = MAX_HEIGHT,
         margin: float = MARGIN) -> Rect:
    """The region to cut out: every box, plus each context element that still fits in max_height, with a
    margin, inside the page. Boxes taller together than max_height: from the top of the first (the price)."""
    area = union(boxes)
    if area[3] + 2 * margin > max_height:
        area = union(boxes[:1])
    for r in context:
        wider = union([area, r])
        if wider[3] + 2 * margin <= max_height:
            area = wider
    x0, y0 = max(0.0, area[0] - margin), max(0.0, area[1] - margin)
    x1 = min(page_width, area[0] + area[2] + margin)
    y1 = min(y0 + max_height, area[1] + area[3] + margin)
    return round(x0), round(y0), round(x1 - x0), round(y1 - y0)


# Hides what floats over the product (a cookie bar, a chat button, a sticky header): fixed or sticky elements
# that hold none of the product's own (a page may wrap everything in one). Nothing is clicked, so no choice
# is made for a consent prompt.
_UNCOVER = """([extra, keep]) => {
  const ours = keep.flatMap(s => [...document.querySelectorAll(s)]);
  for (const e of document.querySelectorAll('body *')) {
    const p = getComputedStyle(e).position;
    if ((p === 'fixed' || p === 'sticky') && !ours.some(o => e.contains(o)))
      e.style.setProperty('visibility', 'hidden', 'important');
  }
  for (const s of extra) for (const e of document.querySelectorAll(s)) e.style.setProperty('display', 'none', 'important');
}"""

# Draws a red frame over each rectangle (page coordinates), above everything and ignoring clicks.
_DRAW = """(rects) => {
  for (const [x, y, w, h] of rects) {
    const d = document.createElement('div');
    d.className = 'deals-shot-box';
    d.style.cssText = `position:absolute;left:${x}px;top:${y}px;width:${w}px;height:${h}px;` +
      'border:3px solid #e3001b;border-radius:6px;box-sizing:border-box;z-index:2147483647;pointer-events:none';
    document.body.appendChild(d);
  }
}"""


def _rect(page, selectors: tuple[str, ...]) -> Rect | None:
    for s in selectors:
        el = page.query_selector(s)
        box = el.bounding_box() if el else None
        if box and box["width"] > 0 and box["height"] > 0:
            return box["x"], box["y"], box["width"], box["height"]
    return None


def annotate(page, spec: Spec) -> bytes | None:
    """Box and cut out the product on a loaded page (scrolled to the top). JPEG bytes, or None when the
    price block is not on the page."""
    page.evaluate("window.scrollTo(0, 0)")
    page.evaluate(_UNCOVER, [list(spec.hide), [s for alts in spec.boxes for s in alts] + list(spec.context)])
    found =[_rect(page, alternatives) for alternatives in spec.boxes]
    if found[0] is None:
        return None
    boxes = [r for r in found if r is not None]
    framed = [(x - PAD, y - PAD, w + 2 * PAD, h + 2 * PAD) for x, y, w, h in boxes]
    page.evaluate(_DRAW, [list(r) for r in framed])
    context = [r for r in (_rect(page, (s,)) for s in spec.context) if r is not None]
    width = page.evaluate("document.documentElement.scrollWidth")
    x, y, w, h = crop(framed, context, width)
    return page.screenshot(type="jpeg", quality=QUALITY, full_page=True,
                           clip={"x": x, "y": y, "width": w, "height": h})


class Shooter(threading.Thread):
    """Takes the screenshots one at a time with one headless browser (Playwright objects stay in this
    thread), and hands each result to `report(alert_id, jpeg or None, reason)`."""

    def __init__(self, report, stop: threading.Event):
        super().__init__(name="screenshots", daemon=True)
        self.report, self.stop_event = report, stop
        self.jobs: queue.Queue = queue.Queue(maxsize=10)
        self._queued: set[int] = set()
        self._pw = self._browser = None

    def submit(self, shot: dict) -> None:
        """Queue a screenshot (from a coordinator reply); never blocks the crawl loop that got it."""
        if shot["alert_id"] in self._queued:
            return
        try:
            self.jobs.put_nowait(shot)
            self._queued.add(shot["alert_id"])
        except queue.Full:
            log.info("screenshot queue full; alert %s goes out without one", shot["alert_id"])

    def run(self) -> None:
        idle_since = time.monotonic()
        try:
            while not self.stop_event.is_set():
                try:
                    shot = self.jobs.get(timeout=5)
                except queue.Empty:
                    if self._browser and time.monotonic() - idle_since > IDLE_CLOSE:
                        self._close()
                    continue
                self._queued.discard(shot["alert_id"])
                self._take(shot)
                idle_since = time.monotonic()
        finally:
            self._close()

    def _take(self, shot: dict) -> None:
        spec = SPECS.get(shot["site"])
        if spec is None:
            self.report(shot["alert_id"], None, f"no screenshot spec for {shot['site']}")
            return
        started = time.monotonic()
        try:
            image = self.shoot(spec, shot["url"])
        except Exception as exc:
            log.info("screenshot for alert %s failed: %s", shot["alert_id"], f"{exc.__class__.__name__}: {exc}"[:200])
            self._close()  # a broken browser is started afresh next time
            self.report(shot["alert_id"], None, f"{exc.__class__.__name__}: {exc}"[:300])
            return
        if image is None:
            log.info("screenshot for alert %s: price block not found on %s", shot["alert_id"], shot["url"])
            self.report(shot["alert_id"], None, "price block not found")
            return
        log.info("screenshot for alert %s: %d KB in %.1fs", shot["alert_id"], len(image) // 1024,
                 time.monotonic() - started)
        self.report(shot["alert_id"], image, None)

    def shoot(self, spec: Spec, url: str) -> bytes | None:
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
        ctx = self._browser.new_context(locale=spec.locale, viewport=VIEWPORT, device_scale_factor=SCALE)
        try:
            page = ctx.new_page()
            page.goto(spec.page_url(url), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT * 1000)
            # A challenge page (Amazon's WAF) reloads itself into the product once solved.
            page.wait_for_selector(spec.ready, state="visible", timeout=PAGE_TIMEOUT * 1000)
            try:
                page.wait_for_load_state("load", timeout=5000)  # images and fonts, briefly
            except Exception:
                pass
            return annotate(page, spec)
        finally:
            ctx.close()

    def _close(self) -> None:
        for thing, stop in ((self._browser, "close"), (self._pw, "stop")):
            try:
                if thing is not None:
                    getattr(thing, stop)()
            except Exception:
                pass
        self._pw = self._browser = None
