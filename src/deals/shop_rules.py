"""Shop rules: the odd things each shop does with its data, and what the tracker does about them.

Every quirk we have found is one entry in RULES, so they are written down in one place: the web
UI's Rules page lists them with how often each one fired, and docs/SHOP_RULES.md explains them.
Adding a rule for any shop is one new entry here (and a test).

What each kind does:

- `placeholder`: a price the shop shows when a product has no real price (Jumia's 100,000 and
  50,000 EGP). The coordinator records such a listing as "no price, not for sale", and drops a
  crossed-out price at that value, so the value never enters the history, the typical price or an
  alert. Applied to every listing from every worker (old workers too), and once at start-up to the
  history already stored (`clean_history`), so adding a placeholder also cleans the past.
- `doubtful_reference`: a price that is a placeholder on some products and real on others (Jumia's
  10,000 EGP: kettles listed at 10,000 before their real 2,500, but also phones that really cost
  10,000). It stays in the history, but no alert is measured against it: an alert whose reference
  (the typical price, or the shop's crossed-out price) is that value is not sent.
- `no_claim_alerts`: the shop's crossed-out prices on new listings are mostly made up, so no
  new-listing alert is sent from them (noon). Its products alert only from our own history: an early
  drop after a day, a real drop after three.
- `low_stock_noise`: the shop shows "only N left" on so many listings that it proves nothing, so
  it never counts as evidence for a new listing's discount (pricing.py).
- `note`: handled inside the shop's adapter (`where`); listed so every quirk is in one place.

Prices are in piastres, like everywhere else.
"""

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime

from deals.models import Listing

KINDS = ("placeholder", "doubtful_reference", "no_claim_alerts", "low_stock_noise", "note")


@dataclass(frozen=True)
class ShopRule:
    id: str
    site: str
    kind: str  # one of KINDS
    title: str  # what the shop does, in a line
    action: str  # what the tracker does about it
    why: str  # the evidence: real products and numbers
    found: str  # when it was found, e.g. "4 Nov 2026"
    prices: frozenset[int] = frozenset()  # exact values (piastres) for placeholder / doubtful_reference
    below: int | None = None  # placeholder: any price under this (piastres)
    where: str = "shop_rules.py"  # the code that enforces it

    def matches(self, price: int | None) -> bool:
        return price is not None and (price in self.prices or (self.below is not None and price < self.below))


def _egp(*values: int) -> frozenset[int]:
    return frozenset(v * 100 for v in values)


RULES: tuple[ShopRule, ...] = (
    ShopRule(
        "jumia-placeholder-prices", "jumia_eg", "placeholder",
        "Products without a real price are listed at exactly 100,000 or 50,000 EGP, as a normal buyable "
        "product (no flag in Jumia's data), and the crossed-out price can be the same placeholder",
        "Recorded as no price (not for sale); a crossed-out price of 100,000 or 50,000 is dropped",
        "4 Nov 2026: 130 products had been at one of these two prices. A new listing goes up at the "
        "placeholder and gets its real price hours later (Honor X7e 100,000 then 12,620); 39 air "
        "conditioners were parked at 100,000 together on 29 Oct (Sharp 1.5 HP 30,397 then 100,000) "
        "and others blink to it for 5 to 20 minutes. 22 false alerts went out before this rule, "
        "e.g. 'Fresh mini bar -92%' (8,280 against 100,000). Crossed-out: Beko mixer 7,449 'was 50,000'. "
        "99,999 is a real price (iPhone 17 Pro Max) and is not touched",
        "4 Nov 2026", prices=_egp(100_000, 50_000)),
    ShopRule(
        "jumia-doubtful-10000", "jumia_eg", "doubtful_reference",
        "Some products are listed at exactly 10,000 EGP before their real price, but many phones and TVs "
        "really cost 10,000",
        "Kept in the history, but no alert is measured against a typical or crossed-out price of 10,000",
        "4 Nov 2026: 60 products had been at 10,000. Placeholders: Braun kettle 10,000 then 2,499, "
        "Ariete toaster, an ironing table; two false alerts (Fresh microwave 5,941 'down 41%' from 10,000). "
        "Real: Infinix Smart 20 at 9,100, 10,000, 8,830",
        "4 Nov 2026", prices=_egp(10_000)),
    ShopRule(
        "noon-no-new-listing-alerts", "noon_eg", "no_claim_alerts",
        "Sellers' crossed-out prices on new listings are mostly made up: '40% off' a price nobody sells at",
        "No new-listing alerts from the crossed-out price; noon products alert only from our own history "
        "(early drop after 1 day, real drop after 3 days)",
        "4 Nov 2026: 428 new-listing alerts were sent from noon (a third of all alerts, 314 of noon's 563 "
        "in the last week), median claim 44% off. For 250 (58%) nothing showed the claim was real: no noon "
        "seller was ever seen at the 'was' price, the price never went back up, and other shops were not "
        "dearer; for 36, other shops sold the same model at the same price or less (Unionaire 440L fridge "
        "18,999 'was 30,000'; other shops 21,249). Only 31 (7%) were 15%+ cheaper than other shops",
        "4 Nov 2026", where="alerts.py (check_product)"),
    ShopRule(
        "dubaiphone-placeholder-1egp", "dubaiphone_eg", "placeholder",
        "Sold-out products sit at 1 EGP until they get a real price",
        "Any price under 10 EGP is recorded as no price (not for sale)",
        "30 Oct 2026: Samsung Galaxy Z Fold 8 and Z Fold 8 Ultra listed at 1 EGP",
        "30 Oct 2026", below=1000, where="sites/dubaiphone_eg.py (PLACEHOLDER_PRICE) and shop_rules.py"),
    ShopRule(
        "dubaiphone-preorders", "dubaiphone_eg", "note",
        "Pre-orders are priced at the deposit (500 EGP) or at a price not on sale yet",
        "Pre-order products are skipped",
        "30 Oct 2026: pre-order phones at 500 EGP", "30 Oct 2026", where="sites/dubaiphone_eg.py"),
    ShopRule(
        "jumia-empty-range-redirect", "jumia_eg", "note",
        "A price range with no products, or a page past the last one, redirects to the parent category",
        "The served URL is checked on every page; such a page counts as an empty slice, not as products",
        "26 Oct 2026: /televisions/?price=999000-1000000 served the 590 products of the parent category",
        "26 Oct 2026", where="sites/jumia_eg.py (parse_catalog_page)"),
    ShopRule(
        "jumia-seller-crossed-out", "jumia_eg", "note",
        "The crossed-out price is set by the seller (88% of cards show one)",
        "Treated as a list price claim (RRP-like), never as proof of an earlier selling price",
        "26 Oct 2026", "26 Oct 2026", where="sites/jumia_eg.py (_parse_product)"),
    ShopRule(
        "noon-sellers", "noon_eg", "note",
        "One product has several sellers at very different prices, and the shown seller changes",
        "Each seller's offer is its own product (SKU@seller); no alert while another seller is cheaper",
        "26 Oct 2026: mixed sellers made fake ups and downs in one history", "26 Oct 2026",
        where="sites/noon_eg.py, alerts.py (best_other_offer)"),
    ShopRule(
        "amazon-decoy-pages", "amazon_eg", "note",
        "Sessions Amazon distrusts get pages of random products with no result count",
        "Such a page is treated as a block (fresh cookies, then the IP is paused), not as products",
        "26 Oct 2026", "26 Oct 2026", where="sites/amazon_eg.py, worker.py"),
    ShopRule(
        "raya-no-new-listing-alerts", "raya_eg", "no_claim_alerts",
        "Nearly every product shows a crossed-out price, most often exactly the price x 1.25",
        "No new-listing alerts from the crossed-out price; Raya's products alert only from our own history",
        "5 Nov 2026: a crossed-out price on 295 of 295 TVs and 290 of 300 phones, median 20% off; 37 of 200 TVs at "
        "exactly 20% (Rivacase backpack 1,000 'was' 1,250; iPhone 18 Pro Max 129,999 'was' 162,498.75, a price "
        "nobody would set)",
        "5 Nov 2026"),
    ShopRule(
        "etisal-no-new-listing-alerts", "etisal_eg", "no_claim_alerts",
        "Nearly every product shows a crossed-out price, with a discount that 'ends' on 31 Dec 2027",
        "No new-listing alerts from the crossed-out price; Etisal's products alert only from our own history",
        "5 Nov 2026: a discount price on 452 of its 478 products (95%), mean 21% off, each discount_end_date "
        "2027-12-31 or 2026-12-30",
        "5 Nov 2026"),
    ShopRule(
        "lcwaikiki-no-new-listing-alerts", "lcwaikiki_eg", "no_claim_alerts",
        "Half the catalogue is 'Sale Items', discounts of 15% to 65% that change with the season",
        "No new-listing alerts from the crossed-out price; its products alert only from our own history",
        "5 Nov 2026: a crossed-out price on 215 of 486 dresses and 119 of 300 men's clothes, median 31% to 36%",
        "5 Nov 2026"),
    ShopRule(
        "defacto-no-new-listing-alerts", "defacto_eg", "no_claim_alerts",
        "Most of the catalogue is discounted, by campaigns named after the month",
        "No new-listing alerts from the crossed-out price; its products alert only from our own history",
        "5 Nov 2026: a crossed-out price on 293 of 373 dresses (median 41%) and 96 of 96 from the men's sale page "
        "(median 20%); 'November - Net %20 Discount' runs to 31 Dec. woman-discount alone holds 5,328 products",
        "5 Nov 2026"),
    ShopRule(
        "lcwaikiki-empty-is-not-empty", "lcwaikiki_eg", "note",
        "An empty category or price range fills the page with 52 unrelated products, and asking past the last page "
        "answers page 1",
        "The adapter reads ProductFound and stops on PageCount",
        "5 Nov 2026: MinPrice=99999 lists 52 products from 249 EGP; PageIndex=67 of 66 answers page 1",
        "5 Nov 2026", where="sites/lcwaikiki_eg.py"),
    ShopRule(
        "lcwaikiki-maxprice-ignored", "lcwaikiki_eg", "note",
        "MaxPrice doesn't hold: asking for 300 to 500 EGP returns products at 1,299",
        "The adapter sends MinPrice only and the planner never splits this shop",
        "5 Nov 2026: 860 products 'between 300 and 500', priced 349 to 1,699",
        "5 Nov 2026", where="sites/lcwaikiki_eg.py"),
    ShopRule(
        "defacto-promo-cards", "defacto_eg", "note",
        "A few product cards are promotions: 'kargobedava' (free shipping), with no price",
        "The adapter skips cards with no price or link name",
        "5 Nov 2026: the first card of woman-discount is index 9000213, 'kargobedava'",
        "5 Nov 2026", where="sites/defacto_eg.py"),
    ShopRule(
        "elaraby-zero-price", "elaraby_eg", "note",
        "42 sold-out products show a price of 0",
        "The adapter reads 0 as no price",
        "5 Nov 2026: e.g. SEIKO Alarm Clock QHL097L at 0, all out of stock, hidden from any price filter",
        "5 Nov 2026", where="sites/elaraby_eg.py"),
    ShopRule(
        "elaraby-manufacturer-is-country", "elaraby_eg", "note",
        "The API's `manufacturer` holds the country of origin ('Egypt', 'China'), not the brand",
        "The adapter finds the brand in the title",
        "5 Nov 2026: option 18 is Egypt on 599 products, 19 China on 898",
        "5 Nov 2026", where="sites/elaraby_eg.py"),
    ShopRule(
        "raya-prebooking-deposits", "raya_eg", "note",
        "'Pre Booking' products are priced at the deposit: 10,000 EGP for a Galaxy Z Fold8 Ultra",
        "The adapter skips them: a deposit is not a price, and the real listing replacing it would look like a change",
        "5 Nov 2026: 10 'Pre Booking - Samsung Galaxy Z Fold8' SKUs at exactly 10,000 EGP among the phones",
        "5 Nov 2026", where="sites/raya_eg.py"),
    ShopRule(
        "vodafone-only-in-stock-read", "vodafone_eg", "note",
        "Without hideOutOfStock the store lists 4,085 products with old prices and no stock flag; 921 are in stock",
        "The adapter asks for what is in stock only, as the site does: a product that sells out disappears",
        "5 Nov 2026: 52 of 395 TVs and 136 of 429 phones are in stock",
        "5 Nov 2026", where="sites/vodafone_eg.py"),
    ShopRule(
        "orange-sizes-hide-behind-one-price", "orange_eg", "note",
        "A product with several storage sizes lists only its cheapest price, while its page opens on any size",
        "Orange's phones, tablets, laptops and consoles aren't watched until each size is read on its own",
        "5 Nov 2026: Redmi Note 15 5G listed at 17,999 (8/256 GB), its page opens at 23,999 (12/512 GB). Sizes at "
        "different prices on 53 of 98 phones, 9 of 17 tablets, 1 of 12 laptops; 0 of 28 TVs",
        "5 Nov 2026", where="config.example.toml"),
    ShopRule(
        "dokkantech-wrong-counts", "dokkantech_eg", "note",
        "A collection's products_count is not what its products.json lists",
        "The adapter pages until a short page and never relies on the count; expect last_count != expected_total",
        "5 Nov 2026: 'all' counts 590 and lists 1,612; 'audio' counts 268 and lists 205; 'mouse' counts 124 and lists 124",
        "5 Nov 2026", where="sites/dokkantech_eg.py"),
    *(ShopRule(f"{site.replace('_eg', '').replace('_', '-')}-low-stock", site, "low_stock_noise",
               "'Only N left' is shown on so many listings that it proves nothing",
               "Never counts as evidence for a new listing's discount", why, found, where="pricing.py")
      for site, why, found in (
          ("amazon_eg", "25 Oct 2026: 45% of listings show 3 or fewer left", "25 Oct 2026"),
          ("amazon_now_eg", "3 Nov 2026: Amazon's own stock counter, like amazon.eg", "3 Nov 2026"),
          ("jumia_eg", "26 Oct 2026: 45% of cards say 3 or fewer", "26 Oct 2026"),
          ("2b_eg", "27 Oct 2026: 'only N left' on 57% of its TVs", "27 Oct 2026"),
          ("carrefour_eg", "28 Oct 2026: low stock of 1 to 3 on 42% of its TVs", "28 Oct 2026"),
          ("raya_eg", "5 Nov 2026: 'only 3 or fewer left' on 64% of its TVs, 29% of its phones", "5 Nov 2026"),
          ("etisal_eg", "5 Nov 2026: 3 or fewer left on 57% of its phones, 37% of its accessories", "5 Nov 2026"),
          ("lcwaikiki_eg", "5 Nov 2026: 3 or fewer left on 44% of its dresses", "5 Nov 2026"),
          ("defacto_eg", "5 Nov 2026: 3 or fewer left on 28% of its dresses, and a count on every card", "5 Nov 2026"))),
)

BY_ID = {r.id: r for r in RULES}
assert len(BY_ID) == len(RULES), "rule ids must be unique"
assert all(r.kind in KINDS for r in RULES)


def rules_for(site: str, kind: str | None = None) -> list[ShopRule]:
    return [r for r in RULES if r.site == site and (kind is None or r.kind == kind)]


def placeholder(site: str, price: int | None) -> ShopRule | None:
    """The placeholder rule this price matches on this shop, if any."""
    return next((r for r in rules_for(site, "placeholder") if r.matches(price)), None)


def doubtful(site: str, price: int | None) -> ShopRule | None:
    """The rule saying no alert may be measured against this price on this shop, if any."""
    return next((r for r in RULES if r.site == site and r.kind in ("placeholder", "doubtful_reference")
                 and r.matches(price)), None)


def stops(site: str, deal) -> ShopRule | None:
    """The rule that says this deal (a pricing.Deal) must not be sent on this shop, if any: a reference
    the shop also uses as a placeholder, or a new-listing claim from a shop whose claims are ignored."""
    if deal.kind == "new_product" and (rule := next(iter(rules_for(site, "no_claim_alerts")), None)):
        return rule
    return doubtful(site, deal.reference)


def low_stock_is_noise(site: str | None) -> bool:
    return bool(site) and bool(rules_for(site, "low_stock_noise"))


def clean(listing: Listing) -> tuple[Listing, ShopRule | None, int | None]:
    """The listing with placeholder prices removed, the rule that fired and the value it caught."""
    rule = placeholder(listing.site, listing.price)
    if rule:
        return (replace(listing, price=None, in_stock=False, list_price=None, list_price_kind=None),
                rule, listing.price)
    rule = placeholder(listing.site, listing.list_price)
    if rule:
        return replace(listing, list_price=None, list_price_kind=None), rule, listing.list_price
    return listing, None, None


def hit(conn: sqlite3.Connection, rule: ShopRule, site: str, sku: str, price: int | None,
        now: datetime, detail: str | None = None) -> None:
    """Count one firing of a rule on a product (for the web UI: what each rule caught)."""
    at = now.isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO shop_rule_hits (rule, site, sku, price, hits, first_at, last_at, detail)
           VALUES (?, ?, ?, ?, 1, ?, ?, ?)
           ON CONFLICT (rule, site, sku) DO UPDATE SET hits = hits + 1, price = excluded.price,
               last_at = excluded.last_at, detail = COALESCE(excluded.detail, shop_rule_hits.detail)""",
        (rule.id, site, sku, price, at, at, detail),
    )


def clean_history(conn: sqlite3.Connection) -> int:
    """Apply the placeholder rules to what is already stored: such prices become "no price, not for
    sale" in the history and on the product, and crossed-out prices at those values are dropped.
    Each product caught is counted in shop_rule_hits. Idempotent (nothing matches a second time), so
    it runs at every start; returns the number of history rows changed."""
    changed = 0
    for rule in (r for r in RULES if r.kind == "placeholder"):
        match, args = _match_sql(rule)
        ids = "SELECT id FROM products WHERE site = ?"
        with conn:
            conn.execute(
                f"""INSERT INTO shop_rule_hits (rule, site, sku, price, hits, first_at, last_at, detail)
                    SELECT ?, p.site, p.sku, MAX(h.price), COUNT(*), MIN(h.observed_at), MAX(h.observed_at),
                           'in the history before the rule'
                    FROM price_history h JOIN products p ON p.id = h.product_id
                    WHERE p.site = ? AND {match.format(col='h.price')}
                    GROUP BY p.id
                    ON CONFLICT (rule, site, sku) DO UPDATE SET hits = hits + excluded.hits""",
                (rule.id, rule.site, *args))
            changed += conn.execute(
                f"""UPDATE price_history SET price = NULL, list_price = NULL, in_stock = 0
                    WHERE product_id IN ({ids}) AND {match.format(col='price')}""",
                (rule.site, *args)).rowcount
            changed += conn.execute(
                f"""UPDATE price_history SET list_price = NULL
                    WHERE product_id IN ({ids}) AND {match.format(col='list_price')}""",
                (rule.site, *args)).rowcount
            conn.execute(f"UPDATE products SET price = NULL, list_price = NULL, list_price_kind = NULL, in_stock = 0 "
                         f"WHERE site = ? AND {match.format(col='price')}", (rule.site, *args))
            conn.execute(f"UPDATE products SET list_price = NULL, list_price_kind = NULL "
                         f"WHERE site = ? AND {match.format(col='list_price')}", (rule.site, *args))
    return changed


def _match_sql(rule: ShopRule) -> tuple[str, list[int]]:
    """A WHERE fragment ('{col}' to fill in) matching the rule's prices, and its arguments."""
    parts, args = [], []
    if rule.prices:
        parts.append(f"{{col}} IN ({', '.join('?' * len(rule.prices))})")
        args += sorted(rule.prices)
    if rule.below is not None:
        parts.append("{col} < ?")
        args.append(rule.below)
    return "(" + " OR ".join(parts) + ")", args


def summary(conn: sqlite3.Connection, sample: int = 20) -> list[dict]:
    """Every rule with what it caught: products, firings, the last one, and the latest products."""
    counts = {r["rule"]: r for r in conn.execute(
        "SELECT rule, COUNT(*) AS products, SUM(hits) AS hits, MAX(last_at) AS last_at FROM shop_rule_hits GROUP BY rule")}
    out = []
    for rule in RULES:
        c = counts.get(rule.id)
        latest = [dict(r) for r in conn.execute(
            """SELECT h.site, h.sku, h.price, h.hits, h.first_at, h.last_at, h.detail, p.id AS product_id, p.title
               FROM shop_rule_hits h LEFT JOIN products p ON p.site = h.site AND p.sku = h.sku
               WHERE h.rule = ? ORDER BY h.last_at DESC LIMIT ?""", (rule.id, sample))] if c else []
        out.append({
            "id": rule.id, "site": rule.site, "kind": rule.kind, "title": rule.title, "action": rule.action,
            "why": rule.why, "found": rule.found, "where": rule.where,
            "prices": sorted(rule.prices), "below": rule.below,
            "products": c["products"] if c else 0, "hits": c["hits"] if c else 0,
            "last_at": c["last_at"] if c else None, "latest": latest,
        })
    return out
