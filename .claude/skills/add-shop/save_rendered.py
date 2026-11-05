#!/usr/bin/env python3
"""Save a product page as the screenshot browser sees it, for tests/test_shots.py (its Spec must exist).

    .venv/bin/python .claude/skills/add-shop/save_rendered.py <shop> <product url> tests/fixtures/<short>_product_rendered.html.gz
"""
import gzip
import sys

from playwright.sync_api import sync_playwright

from deals import shots


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    site, url, path = sys.argv[1:]
    spec = shots.SPECS[site]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel="chromium")  # as shots.Shooter does
        probe = browser.new_page()  # also its user agent: Cloudflare and noon's wall refuse "HeadlessChrome"
        agent = probe.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
        probe.close()
        page = browser.new_context(locale=spec.locale, viewport=shots.VIEWPORT, user_agent=agent).new_page()
        page.goto(spec.page_url(url), wait_until="commit", timeout=30000)
        page.wait_for_selector(spec.ready, state="visible", timeout=30000)
        page.wait_for_timeout(3000)  # hydration brings the Arabic labels and the images
        with gzip.open(path, "wt") as out:
            out.write(page.content())
        browser.close()


if __name__ == "__main__":
    main()
