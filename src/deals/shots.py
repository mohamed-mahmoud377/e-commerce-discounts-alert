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
    block: tuple[str, ...] = ()  # URL patterns not to load (a heavy icon font the screenshot would wait for)
    ready: str = "body"  # the page has rendered the product once this is visible
    settle: float = 0  # seconds more for a page that redraws itself after that (Etisal's Angular hydration)
    locale: str = "ar-EG"
    arabic: tuple[str, str] | None = None  # (English part, Arabic part) of the URL, replaced once

    def page_url(self, url: str) -> str:
        return url.replace(*self.arabic, 1) if self.arabic else url


_AMAZON_PRICE = ("#corePriceDisplay_desktop_feature_div", "#corePrice_desktop", "#corePrice_feature_div")
_AMAZON_AR = ("/-/en/", "/-/ar/")  # same ids and layout, mirrored (right to left)
SPECS: dict[str, Spec] = {
    # Price, discount and list price in the centre column; "Shipper / Seller" rows in the buy box.
    "amazon_eg": Spec(boxes=(_AMAZON_PRICE, ("#offerDisplayFeatures_desktop", "#merchantInfoFeature_feature_div")),
                      context=("#productTitle", "#imgTagWrapperId"), hide=("#sp-cc",), ready="#productTitle",
                      arabic=_AMAZON_AR),
    # Amazon Now's buy box has no seller rows: "Ships from and sold by Amazon Now." is under the price.
    "amazon_now_eg": Spec(boxes=(_AMAZON_PRICE, ("#fresh-merchant-info", "#offerDisplayFeatures_desktop")),
                          context=("#productTitle", "#imgTagWrapperId"), hide=("#sp-cc",), ready="#productTitle",
                          arabic=_AMAZON_AR),
    # Server-rendered with utility classes (no ids): the price row (price, crossed-out price, % badge) under
    # the title; the seller card ("بيانات البائع", a link to the seller's store) in the right column.
    "jumia_eg": Spec(boxes=((".col10 .-phm > .-hr.-pvm",), ("section.card:has(> a > h2) > .-hr.-pam",)),
                     context=(".col10 h1", "#imgs"), ready=".col10 h1",
                     arabic=("jumia.com.eg/", "jumia.com.eg/ar/")),
    # CSS-module classes (`_priceOfferCtr_1fgtw_1`): matched on the name, the hash changes with each build.
    # The page shows the offer that wins the buy box, whose seller is a link to /seller/. The price is drawn
    # 2 s before hydration, which brings the Arabic labels and the main image: waiting for that image is what
    # keeps out "Sold by" and "25% Off" (docs/CATEGORIES.md). The ticker beside the price rolls: gone.
    "noon_eg": Spec(boxes=(('div[class*="_priceOfferCtr_"]', '[data-qa="div-price-now"]'),
                           ('a[class*="_partnerRatingsCtr_"]', 'a[href*="/seller/p-"]')),
                    context=('h1[data-qa="pdp-product-name"]',
                             '[data-pdp-gallery] [class*="_imageContainer_"]:first-child [class*="_mediaContainer_"] img'),
                    hide=('div[class*="_nudgesContainer_"]',),
                    ready='div[class*="_priceOfferCtr_"]', arabic=("/egypt-en/", "/egypt-ar/")),
    # Next.js with Tailwind classes; test ids on the price. The seller is the "يباع من" row of the details
    # card, known by its icon; the saving ("هتوفر") is a badge on the image.
    "btech_eg": Spec(boxes=(('[data-testid="pdp-price"]',), ('div:has(> div > img[src*="pdp-seller-store"])',)),
                     context=("h1", 'div:has(> [data-testid="pdp-gallery-zoom"])'), ready='[data-testid="pdp-price"]',
                     arabic=("btech.com/en/", "btech.com/ar/")),
    # Magento: price, crossed-out price and the "% تخفيض" label share a wrapper. 2B sells its own stock: no
    # seller line.
    "2b_eg": Spec(boxes=((".product-info-main div.price-wrapper", ".product-info-main .price-box"),),
                  context=("h1.page-title", '[data-gallery-role="gallery-placeholder"]'),
                  ready=".product-info-main .price-box", arabic=("2b.com.eg/en/", "2b.com.eg/ar/")),
    # PrestaShop 1.6 ids. The price sits under the short description, which can push it far below the title.
    # Cairo Sales sells its own stock.
    "cairosales_eg": Spec(boxes=((".content_prices", "#our_price_display"),), context=("h1", "#image-block"),
                          ready="#our_price_display", arabic=("cairosales.com/en/", "cairosales.com/ar/")),
    # Next.js with Tailwind classes. The product page shows the price alone (never the crossed-out one, even
    # when the listing has it), next to "or N a month"; Dubai Phone sells its own stock. Arabic has no prefix.
    "dubaiphone_eg": Spec(boxes=(("div.self-center > p:has(span.tabular-nums)",),),
                          context=("h1.dp-product-title", ".embla__viewport"), ready="h1.dp-product-title",
                          arabic=("dubaiphone.net/en/", "dubaiphone.net/")),
    # WooCommerce (WoodMart theme), English only: <del> and <ins> prices in one line, the "-8%" label on the
    # first gallery image. Hardware Market sells its own stock.
    "hardwaremarket_eg": Spec(boxes=((".summary p.price", "p.price"),),
                              context=("h1.product_title", ".woocommerce-product-gallery__image"),
                              ready="h1.product_title"),
    # Shopify. The page also holds a hidden quick-view copy of the product (same classes): the main section's
    # ids end in "__main". Dream 2000 sells its own stock. Arabic is the default locale (no prefix). The whole
    # Tabler icon font (jsDelivr, @latest) can take 10 s or more, and a screenshot waits for fonts.
    "dream2000_eg": Spec(boxes=(('[id^="Price__"][id$="__main"]',),),
                         context=("h1.product-title",
                                  '[id^="MediaGallery__"][id$="__main"] .media-gallery__media-item.is-active'),
                         ready='[id^="Price__"][id$="__main"]', arabic=("dream2000.com/en/", "dream2000.com/"),
                         block=("**/@tabler/icons-webfont*/**",)),
    # MAF's Next.js storefront, Tailwind classes only: the price row is the one around the left-to-right
    # amount (price, crossed-out price, "خصم 19%"); the seller card has the seller's name in bold.
    "carrefour_eg": Spec(boxes=(("main div:has(> div.force-ltr)",),
                                ("main div.flex-col:has(> div.flex-col > span > strong)",)),
                         context=("main h1", '[data-testid="productimage-container"]'),
                         ready="main div:has(> div.force-ltr)", arabic=("/mafegy/en/", "/mafegy/ar/")),
    # Nuxt, Tailwind classes only. The price row holds the price, the crossed-out price and "توفر"; the seller
    # is the list holding the /seller/ link (a marketplace). The gallery's first picture is MagicZoom's.
    "raya_eg": Spec(boxes=(("div.ProductInfoColumn div.justify-between:has(span.currency)",),
                           ('ul:has(a[href*="/seller/"])',)),
                    context=("h1.text-primary-700", "a.MagicZoom img.object-contain"),
                    ready="div.ProductInfoColumn span.currency", arabic=("/en/", "/ar/")),
    # Angular. A sticky header holds a second copy of the price and appears over the product: go through the main
    # info block and hide it.
    "vodafone_eg": Spec(boxes=(("vf-product-main-info .price-wrapper",),),
                        context=("h1.product-name", "img.main-image"), hide=("#product-header",),
                        ready="vf-product-main-info .price-wrapper", arabic=("/en/prod/", "/ar/prod/")),
    # Shopify, English by default and Arabic under /ar/. Sells its own stock.
    "dokkantech_eg": Spec(boxes=((".product-info__price",),),
                          context=("h1.product-title", "img.product-image"),
                          ready=".product-info__price", arabic=("dokkantech.com/", "dokkantech.com/ar/")),
    # Angular (SuperCommerce). The price box has the price, the crossed-out price and "وفر". The server's page
    # is redrawn while Angular takes over: a shot taken then is a squashed mix of the top and the bottom of the page.
    "etisal_eg": Spec(boxes=(("div.product-detail-price",),),
                      context=("h1.product-name", ".img-zoom-container img"),
                      ready="div.product-detail-price span.price-value", arabic=("/en/", "/ar/"), settle=3),
    # Angular, Tailwind. Its h1 says "غير متوفر" when it is sold out. The price header holds the title too.
    "orange_eg": Spec(boxes=(("div:has(> div > span.product-price-current)", "span.product-price-current"),),
                      context=("h1", ".gallery-slide img"),
                      ready="span.product-price-current", arabic=("/en/", "/ar/")),
    # React. Arabic has no prefix, and the English slug redirects to the Arabic one. The price block is the crossed-out
    # price, "-25%" and the sale price, which the shop words as "price in cart".
    "lcwaikiki_eg": Spec(boxes=(("div.product-detail__price",),),
                         context=("h1.product-detail__title", "img.main-image"),
                         ready="div.product-detail__price", arabic=("lcwaikiki.eg/en/", "lcwaikiki.eg/")),
    # Server-rendered. Arabic has no prefix. Product cards further down use other classes for their prices.
    "defacto_eg": Spec(boxes=(("div.product-detail__price",),),
                       context=("h1.product-detail__info-header-title", "div.product-first-media img"),
                       ready="div.product-detail__price", arabic=("defacto.com.eg/en-eg/", "defacto.com.eg/")),
    # Magento with its usual layout; sells its own stock. The gallery's image is the active Fotorama frame.
    "elaraby_eg": Spec(boxes=((".product-info-main .product-info-price",),),
                       context=("h1.page-title", ".fotorama__stage__frame.fotorama__active img"),
                       ready=".product-info-main .product-info-price", arabic=("/en/", "/ar/")),
    # WooCommerce with Elementor; its own price widget. The theme's .price is the mini cart's "0 EGP".
    "miami_eg": Spec(boxes=((".miamicenters-price-container",),),
                     context=("h1.product_title", "img.wp-post-image"),
                     ready=".miamicenters-price-container", arabic=("miamicenters.com/", "miamicenters.com/ar/")),
    # Next.js with Tailwind classes: the price under the title, whose big digits are its only text-3xl; the sidebar repeats it.
    "sigma_eg": Spec(boxes=(("article section div.flex.items-center:has(span.text-3xl)", "article aside div.flex.items-center"),),
                     context=("article h1", "article img.object-contain"),
                     ready="article h1", arabic=("/en/", "/ar/")),
    # OpenCart's Journal theme; English only.
    "elnekhely_eg": Spec(boxes=(("div.product-price-group div.price-wrapper",),),
                         context=("h1.page-title", ".swiper-slide-visible img"), hide=(".notification-wrapper",),
                         ready="div.product-price-group"),
    "wayup_eg": Spec(boxes=((".product-info__price",),),
                     context=("h1.product-info__title", ".product-gallery img"),
                     ready=".product-info__price", arabic=("wayupsports.com/", "wayupsports.com/ar/")),
    # Shopify's Dawn-like theme; English only (/ar/ is a 404). Related products have prices too: the first one is the product's.
    "gcshop_eg": Spec(boxes=((".price",),),
                      context=("h1", ".carousel-cell.is-selected img.product__image"), ready=".price"),
    # English: on its Arabic page the gallery's carousel makes the page 11,280 px wide, and the crop comes out blank.
    "azzam_eg": Spec(boxes=((".price-list--product",),),
                     context=("h1.product-title", ".product-gallery__media.is-initial img"),
                     ready=".price-list--product"),
    # Nuxt with Tailwind classes: the price's digits are .currency, in the block marked text-primary-800.
    "mazaya_eg": Spec(boxes=((".text-primary-800:has(.currency)",),),
                      context=("h1.product-name", ".zoom-container img"),
                      ready="h1.product-name", settle=1, arabic=("mazaya.eg/en/", "mazaya.eg/ar/")),
    # Salesforce Commerce Cloud: the selected size's price. A "from" product opens on its biggest size (docs/FACES.md).
    "faces_eg": Spec(boxes=((".js-price-container",),),
                     context=(".product-brand", ".js-name-container", ".product-carousel"),
                     ready=".js-name-container", arabic=("/en/p/", "/ar/p/")),
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

# True once every context element is on the page and its first image (the main product image) has loaded:
# lazy loaders put a 1-pixel placeholder in `src` until the image is in view, and noon renders its gallery
# after the price (about 2 s).
_LOADED = """(context) => context.every(s => {
  const e = document.querySelector(s), i = e && (e.tagName === 'IMG' ? e : e.querySelector('img'));
  return e && (!i || (i.complete && i.naturalWidth > 1));
})"""

# Draws a red frame over each rectangle (page coordinates), above everything and ignoring clicks. A page zoomed
# with CSS (Vodafone's <html> has zoom: 0.82) shrinks the frame's numbers too, so they are divided by its zoom.
_DRAW = """(rects) => {
  const z = parseFloat(getComputedStyle(document.documentElement).zoom) * parseFloat(getComputedStyle(document.body).zoom) || 1;
  for (const [x, y, w, h] of rects) {
    const d = document.createElement('div');
    d.className = 'deals-shot-box';
    d.style.cssText = `position:absolute;left:${x / z}px;top:${y / z}px;width:${w / z}px;height:${h / z}px;` +
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
        self._agent = ""

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
            # The full browser in its new headless mode, under a normal Chrome's user agent: noon's bot wall
            # resets the connection of the headless shell, and of any "HeadlessChrome" agent.
            self._browser = self._pw.chromium.launch(headless=True, channel="chromium")
            probe = self._browser.new_page()
            self._agent = probe.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
            probe.close()
        ctx = self._browser.new_context(locale=spec.locale, viewport=VIEWPORT, device_scale_factor=SCALE,
                                        user_agent=self._agent)
        try:
            for pattern in spec.block:
                ctx.route(pattern, lambda route: route.abort())
            page = ctx.new_page()
            # Not until the whole HTML is in: noon streams its page for 30 s, the price shows after 14.
            page.goto(spec.page_url(url), wait_until="commit", timeout=PAGE_TIMEOUT * 1000)
            # A challenge page (Amazon's WAF) reloads itself into the product once solved.
            page.wait_for_selector(spec.ready, state="visible", timeout=PAGE_TIMEOUT * 1000)
            for wait in (lambda: page.wait_for_function(_LOADED, arg=list(spec.context), timeout=5000),
                         lambda: page.wait_for_load_state("load", timeout=2000)):  # the rest, briefly
                try:
                    wait()
                except Exception:
                    pass
            if spec.settle:
                page.wait_for_timeout(spec.settle * 1000)
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
