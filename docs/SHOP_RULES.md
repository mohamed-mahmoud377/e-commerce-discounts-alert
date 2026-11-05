# Shop rules: what each shop gets wrong, and what we do about it

Every shop has habits that would corrupt a product's history or fake a discount if its data were taken at
face value: a placeholder price on a product that has no real price yet, a "only 2 left" shown on half the
catalogue, a page that silently becomes a different page. Each habit we have found is **one entry in
`src/deals/shop_rules.py`** (`RULES`). The same list is shown on the web UI's **Rules & watches** page (click
a rule for its evidence and the products it caught), and this document explains it.

Started 4 November 2026 with Jumia's placeholder prices, which had already caused 25 false alerts (22 against 100,000 or 50,000, 3 against 10,000).

## The kinds of rule

| Kind | What the tracker does | Where |
| --- | --- | --- |
| `placeholder` | The price is not a price. A listing at that value is recorded as **no price, not for sale**, and a crossed-out price at that value is dropped. The value never enters the history, the typical price or an alert. Applied by the coordinator to every listing from every worker (old worker code too), and once at every start to the history already stored (`clean_history()`), so a new placeholder also cleans the past | `shop_rules.clean()` in the coordinator's page handler |
| `doubtful_reference` | The value is a placeholder on some products and real on others. It stays in the history, but **no alert is measured against it**: a deal whose reference (the typical price, or the crossed-out price of a new listing) is that value is not sent, and is counted on the Rules page | `shop_rules.doubtful()` in `alerts.check_product()` |
| `no_claim_alerts` | The shop's crossed-out prices on new listings are mostly made up, so **no new-listing alert** is sent from them. Its products alert only from our own history (an early drop after a day, a real drop after three). Each stopped alert is counted on the Rules page | `shop_rules.stops()` in `alerts.check_product()` |
| `low_stock_noise` | "Only N left" is shown so widely on this shop that it proves nothing, so it never counts as evidence for a new listing's discount, and the alert text says so | `pricing.LOW_STOCK_EVERYWHERE` |
| `note` | Handled inside the shop's adapter (`sites/<shop>.py`); listed so every quirk is in one place | the adapter |

`placeholder` rules also count as doubtful references, so an alert measured against a placeholder is stopped
even if one were somehow stored.

## The rules

| Shop | Rule id | Kind | What the shop does | What we do | Found |
| --- | --- | --- | --- | --- | --- |
| Jumia | `jumia-placeholder-prices` | placeholder | Lists products without a real price at exactly **100,000 or 50,000 EGP**, as normal buyable products; the crossed-out price can be the same placeholder | No price, not for sale; such a crossed-out price is dropped | 4 Nov 2026 |
| Jumia | `jumia-doubtful-10000` | doubtful_reference | **10,000 EGP** is a placeholder for kettles and toasters, but a real price for many phones and TVs | No alert measured against a typical or crossed-out 10,000 | 4 Nov 2026 |
| noon | `noon-no-new-listing-alerts` | no_claim_alerts | Sellers' crossed-out prices on new listings are mostly made up ("40% off" a price nobody sells at) | No new-listing alerts; only alerts from our own history | 4 Nov 2026 |
| Dubai Phone | `dubaiphone-placeholder-1egp` | placeholder | Sold-out products sit at **1 EGP** until they get a real price | Anything under 10 EGP is no price (in the adapter and the coordinator) | 30 Oct 2026 |
| Dubai Phone | `dubaiphone-preorders` | note | Pre-orders are priced at the 500 EGP deposit | Skipped | 30 Oct 2026 |
| Jumia | `jumia-empty-range-redirect` | note | An empty price range or a page past the end redirects to the parent category | The served URL is checked on every page | 26 Oct 2026 |
| Jumia | `jumia-seller-crossed-out` | note | The crossed-out price is set by the seller (88% of cards) | Treated as an RRP-like claim | 26 Oct 2026 |
| noon | `noon-sellers` | note | One product, several sellers at very different prices | Each seller's offer is its own product (`SKU@seller`); no alert while another seller is cheaper | 26 Oct 2026 |
| amazon.eg | `amazon-decoy-pages` | note | Distrusted sessions get pages of random products with no result count | Treated as a block | 26 Oct 2026 |
| amazon.eg, Amazon Now, Jumia, 2B, Carrefour, Raya, Etisal, LC Waikiki, DeFacto | `*-low-stock` | low_stock_noise | "Only N left" on 28% to 64% of listings | Not evidence | 25 to 28 Oct, 3 Nov 2026 |
| Raya | `raya-no-new-listing-alerts` | no_claim_alerts | A crossed-out price on 97% to 100%, often exactly the price x 1.25 | No new-listing alert from it | 5 Nov 2026 |
| Etisal | `etisal-no-new-listing-alerts` | no_claim_alerts | A "discount" on 95% of products that ends in 2027 | No new-listing alert from it | 5 Nov 2026 |
| LC Waikiki | `lcwaikiki-no-new-listing-alerts` | no_claim_alerts | Half the catalogue is "Sale Items", 15% to 65% off | No new-listing alert from it | 5 Nov 2026 |
| DeFacto | `defacto-no-new-listing-alerts` | no_claim_alerts | Most of the catalogue is in a monthly campaign | No new-listing alert from it | 5 Nov 2026 |
| LC Waikiki | `lcwaikiki-empty-is-not-empty` | note | An empty result shows 52 unrelated products; past the last page is page 1 | Reads `ProductFound`, stops on `PageCount` | 5 Nov 2026 |
| LC Waikiki | `lcwaikiki-maxprice-ignored` | note | `MaxPrice` doesn't hold | `MinPrice` only; never split by price | 5 Nov 2026 |
| DeFacto | `defacto-promo-cards` | note | A few cards are promotions with no price | Skipped | 5 Nov 2026 |
| El Araby | `elaraby-zero-price` | note | 42 sold-out products show a price of 0 | Read as no price | 5 Nov 2026 |
| El Araby | `elaraby-manufacturer-is-country` | note | `manufacturer` holds the country of origin | Brand comes from the title | 5 Nov 2026 |
| Raya | `raya-prebooking-deposits` | note | "Pre Booking" phones at a 10,000 EGP deposit | Skipped in the adapter | 5 Nov 2026 |
| Vodafone | `vodafone-only-in-stock-read` | note | 4,085 listed, 921 in stock, no stock flag | Only what is in stock is asked for | 5 Nov 2026 |
| Orange | `orange-sizes-hide-behind-one-price` | note | A product with several sizes lists its cheapest price; the page opens on any size (53 of 98 phones) | Its phones, tablets, laptops and consoles aren't watched | 5 Nov 2026 |
| Dokkan Tech | `dokkantech-wrong-counts` | note | `products_count` wrong in both directions | Pages until a short page | 5 Nov 2026 |

The evidence for each (real products and numbers) is in the `why` field of the rule and on the Rules page.

## Jumia's placeholders in detail

Measured on the live database on 4 November 2026 (full write-up in [JUMIA.md](JUMIA.md), "Placeholder
prices"):

- 130 products had been at exactly 100,000 or 50,000; 121 products had one of them as the crossed-out price.
- Three patterns: a **new listing** goes up at the placeholder and gets its real price hours later (Honor
  X7e 100,000, then 12,620); products are **parked** together (39 air conditioners moved to 100,000 at 12:00
  UTC on 29 Oct and stayed there); a real price **blinks** to it for 5 to 20 minutes.
- Nothing in Jumia's data marks them: `isBuyable` is true, there is no stock text. Only the exact value
  tells. 99,999 is a real price (iPhone 17 Pro Max with a real crossed-out 125,000) and is not touched.
- Damage before the rule: 22 alerts sent, measured against 100,000 or 50,000 (17 early drops and 5 "possible
  price errors"), plus 3 against 10,000. Replaying all 1,302 alerts through the new rules stops exactly
  those 25 Jumia alerts (one of the 10,000 ones was a new-listing alert against a 10,000 crossed-out price)
  and no other.
- The parked air conditioners would have caused the next batch: coming back from weeks at 100,000 to their
  real 30,000 looks like a 70% drop when the placeholder is in the history. Cleaning the history at start-up
  (426 rows on 4 Nov, 0.17 s) prevents that.

**The cost.** A real Jumia product priced at exactly 100,000 or 50,000 shows as not for sale while it is at
that price. It only loses a non-deal price point: we never alert on a price going up, and a later drop is
measured against its other prices. A real Jumia phone whose typical price is exactly 10,000 can't be alerted
from that reference; the Rules page lists every alert the rule stopped, so this can be reviewed.

## noon's new-listing alerts in detail

428 noon new-listing alerts were sent up to 4 November 2026, a third of all 1,302 alerts (314 of noon's 563 in the last week), with a median claim of 44% off. Checked afterwards: for 250 (58%) nothing showed the claim was real (no noon seller was ever seen at the "was" price, the price never went back up, other shops were not dearer); for 36 of them other shops sold the same model at the same price or less (Unionaire 440L fridge 18,999 "was 30,000", other shops 21,249). Only 31 (7%) were 15% or more cheaper than other shops.

How each alert was judged, on a `make db` snapshot of 4 Nov: the alert's "was" price against the highest
in-stock price any noon seller of the same item was ever seen at (90% of the claim counts as seen); the
offer's own price after the alert (15% or more higher counts as "went back up"); and the median price of the
same model on other shops (matched by model code, see [MATCHING.md](MATCHING.md)). An alert with none of
these signs after two days counts as unproven. This is a lower bound on how many were wrong: the "real"
group includes claims backed only by one other noon seller's high price.

**The cost.** A product launched on noon at a real discount is no longer alerted on its first day. If its
price drops after that, the early-drop path (a day of history, 30% below everything seen) and the normal
path (three days, 20% below typical) still catch it. The 31 alerts that were clearly cheaper than other shops
point to a better reference for new listings: the same model's price on other shops (ROADMAP item 6).

## Candidates seen on other shops (not rules yet)

Found while checking Jumia on 4 Nov 2026; each needs its own look before it becomes a rule:

- **amazon.eg**: 100,000 EGP on a few listings that are clearly not that price (a 55" Samsung TV 35,999 then
  100,000, a Beko fridge, a tablet), and 50,000 on others (alert 1393, Poco X8 Pro 35,000 "30% below
  50,000", is worth checking). These come from third-party sellers rather than from Amazon, and 99,999 is
  a real laptop price there. A rule here would probably need the seller, not only the value.
- **noon**: 100,000 on a 55" Neo QLED, 50,000 on a gaming PC; noon also has 10,000 on 25 products.
- **B.TECH**: 99,999 and 50,000 look real (MacBooks, iPhones), except a Sokany sandwich maker at 50,000.

## How to add a rule

1. Find the evidence on the live data (`make sql`): the products, their history, the alerts it caused.
2. Add a `ShopRule` to `RULES` in `src/deals/shop_rules.py`: a unique `id` (`<shop>-<what>`), the `kind`, a
   one-line `title` (what the shop does) and `action` (what we do), the evidence in `why` with real products
   and numbers, `found` (the date), and `prices` (exact values in piastres, `_egp(...)` converts) or `below`.
3. Add a test in `tests/test_shop_rules.py` built from the real case.
4. Replay it: run `clean_history()` and `doubtful()` on a `make db` snapshot and count the history rows it
   changes and the past alerts it would have stopped (see the section above for the Jumia numbers).
5. Deploy. The coordinator applies it to every listing at once and cleans the stored history at start-up.
   Check the Rules page (or `SELECT * FROM shop_rule_hits WHERE rule = '<id>'` in `make sql`).
6. Write it up here and in the shop's own doc.

A rule that needs code beyond these kinds (a shop-specific parsing trick) stays in the shop's adapter; add a
`note` entry pointing to it so it is listed with the rest.
