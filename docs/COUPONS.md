# Coupons and discount codes

What each shop shows, how we fetch it, and when a coupon reaches people. Built 5 November 2026. Code: `coupons.py` (reading
the text, the lines in messages), `db._record_coupon`, `alerts._coupon_deal`, `coupon_messages.py`. Tests: `tests/test_coupons.py`.

## Where each shop shows coupons

| Shop | Where | What we get | Read today? |
|---|---|---|---|
| noon | Listing JSON: `discount_tag_code`, `_title`, `_detail`, `_terms`, `discount_canonical_url` | Code, words, and the page of every product it covers | Yes |
| Raneen | Listing card `.rn-card__coupon code` ("USE Code WF500") | A code, with no value | Yes |
| Carrefour | Card badge `DiscountCoupon` ("Extra 500EGP Off") | An amount, with no code | Yes |
| B.TECH | `price.discounts[name=item_coupon]` | An amount **already in `final_price`** | Yes |
| Amazon, Amazon Now | Search cards: the promotion's id, and its words under the price. `/-/ar/promotion/psp/<id>`: everything else | A promotion's numbers, dates and a page of all its products | Yes (see below) |
| e& | Category name ("Mobile Deals (Use code: MOBILE25)") | A code per category | Not yet (`eand-promo-codes-not-read`) |
| Dubai Phone | `couponBadge` on a product | Unknown: null in our saved pages | Not yet |

The other shops' saved pages only had checkout or review-reward text (Aghezty, Sigma, Ravin, LC Waikiki and others). That was
a keyword sweep of saved pages on 5 November 2026, so a shop could still show one on a page we did not save.

## What live noon Egypt showed (5 November 2026)

A small sample: the first page of 6 categories, about 300 products.

- About 6 in 50 products on the mobiles, beauty, fashion and home pages carried a code; 1 in 50 on the wider electronics
  and computers pages; the grocery page returned no products.
- `MOBILE200` covers 25 products and `MOBILE300` 52, at a median of 2.2% and 2.0% off, 2.9% at most.
- Of 12 distinct codes, 7 are multi-buy ("Buy 2 get 20% off") and 2 need a minimum spend ("Buy For 350 Get 50 EGP off").
- `discount_canonical_url` works both ways: the API at that path returns a normal catalogue (`/eg-nov26-mobile-200`: 25 hits),
  and `https://www.noon.com/egypt-en/eg-nov26-mobile-300/` is a page people can open (HTTP 200, the code on the page).

So on phones a coupon alone rarely reaches the 20% alert rule.

## Amazon's promotions (measured 5-6 November 2026)

These are what deal channels post ("3 بسعر 2 على منتجات Braes"). There is no code to type: Amazon applies them to the cart.

- **Every search card names its promotion:** `data-csa-c-item-id="amzn1.asin.X:amzn1.promotion.A18KBWDDL262Z4"`, on 57 of 60 cards of a normal
  `shampoo` search. Finding them adds no crawl pages. A card names one promotion even when several apply.
- **The card's words can be another offer's.** "Buy 5 products and get 5% off", "Buy 350 get 50" and "Buy 400 get 75" are right, but Elvive's
  (`A2XJ3FFU2U5GTP`, buy 2 save 15%) shows only the NBE line. So a card gives the id, and only the page says what it is.
- **Most are banks':** 37 of those 60 cards had NBE's `A10EJ9XHRVH2JC`. The id changes between months.
- **The page's HTML holds the numbers:** `#promotionTitle`, `#promotionSchedule`, and in its script `minQuantity`, `minAmount`, `benefitAmount`, `benefitPercent`,
  `benefitAmountCap`. "Buy 5, 5% off" has a cap of 50 EGP the card never mentions. The title and dates come in the session's language, whatever the URL says
  ("From 05 Nov 2026 to 12 Nov 2026", "من 05 نوفمبر 2026 حتى 12 نوفمبر 2026", "Expires: Monday, 30 November 2026"); some have none. The products load by script
  (`/promotion/psp/productInfoList`), so only a real browser's screenshot shows them.
- **The sort menu is the page's script:** no link opens the page cheapest first, as far as we found.
- **Screenshots:** checked on two live promotions and noon's `/eg-nov26-mobile-300`, 6 November 2026. noon's images need about 10 s after its products appear; at 4 s most tiles were still its logo.

**How it flows.** A card stores a coupon of kind `promo`: its id in `code`, `typed = 0` (nobody types it, so it is never shown as a code), and the card's words, which never lower a price.
A reply to a page upload can name one unread promotion (`coupon_messages.next_promotion`, at most 20 an hour per shop, to workers that say `reads_promotions`). The worker reads the page and posts
what it found to `POST /api/coupons/facts`, which fills in the `coupons` row; a title naming a bank makes it `bank`, a missing page `ended`, and one not answered is asked for again after 6 hours.
Each product takes on those facts when next crawled (`db._read_promotion`), which is also when a coupon that makes it a real discount gets judged.

## What we store

- `products.coupon_*`: the coupon a product shows now. A crawl that sees the product with none clears it. A worker too old to
  read coupons does not send the field, which leaves the stored one alone.
- `coupons`: one row per code per shop (kinds `code` and `multibuy`), with `state`: `new`, then `sent`, `would_send`,
  `below_floor` or `over_limit`.
- **Never `price_history`.** The usual price stays the shelf price. A coupon appearing does not touch `last_change_at` either.

`coupons.parse` turns only what is plainly written into numbers: a fixed amount, or a percentage with its cap and minimum
order. Everything else stays as the shop's words, and a coupon with no number never changes a price we show. An offer for
first orders or new customers gets no number.

## How it reaches people

| Kind | Example | A: line in product alert | B: can trigger a product alert | C: can get a coupon message |
|---|---|---|---|---|
| `code`, with a known value | MOBILE300 | Yes, with the price after it | Yes | Yes |
| `code`, no value | WF500 | Yes, words only | No | No: nothing to measure |
| `code`, minimum spend above the price | Buy For 350 Get 50 | Yes, says the minimum | No, on that product | Yes |
| `multibuy` | Buy 2 get 20% off | Yes, says several are needed | No | Yes, says several are needed |
| `bank` | NBEOCT500 | Yes, says it needs the card | No | No |
| `promo`, not read yet | Amazon's card | The card's words only | No | No, until read |
| `badge`, an amount with no code | Extra 500 EGP Off | Yes, with the price after it | Yes | No |
| `auto`, already in the price | B.TECH | "Already in the price" | Already a normal price drop | No |

**A is always on. B and C are off until `[alerts] coupon_alerts` and `coupon_messages` are set.** The plan is to read a few days
of real data first (below), then set the floors and switch them on. Deploy the code before adding these keys to the config.

### A. A line in every product alert

`coupons.line` in the Telegram alert and the public post, `coupons.card_line` on the English review card.

### B. A coupon on an unchanged price

A new or bigger coupon with a known value runs `pricing.evaluate` again, on the price after the coupon, from the coordinator as a page
is uploaded. So a drop and a coupon can add up: 17% down plus 4% off passes a 20% rule. A pass becomes kind `coupon_drop`,
with `alerts.price` being the price after the coupon.

- **Only against the product's own history.** The `new_product` and `early_drop` paths don't count: a shop's claim or a day of
  prices is too little to call a coupon a real discount.
- **`compute_stats(at_price=...)`** asks "how often was it this cheap" at the price after the coupon, and counts today's shelf price as
  history. Without that a product that never changed price has no history at all, and that is the case B exists for.
- **A seller who raises the price and then adds a coupon gets nowhere,** because the usual price comes from what it sold at.
- It shares the existing limits (`_recently_alerted`, shop rules, the shop's own floors).
- At approval the post is checked against the price after the coupon. If the coupon has ended the card says so.

### C. One post per coupon

Modelled on the channels' posts: a screenshot of the page of products, one line, its end date when known, a link.

```
[screenshot: the banner and the first row of products]

🎟 وفر 75 جنيه لما تشتري بـ 400 جنيه أو أكتر من TRESemmé
🏪 أمازون مصر
⏳ لحد 12 نوفمبر
[🛒 كل المنتجات]
```

The line comes from the coupon's numbers (`coupons.headline`), and only falls back to the shop's own words. The brand is added when at least 60% of its products' titles start with one word.
A coupon with a code to type adds it. A shop with no page of products (Raneen) lists up to 5 instead, and has no screenshot. A failed screenshot costs only the picture.
**Value:** the coupon as used, against each product's own usual price: 50 off 200 is 25% on a 30 EGP sweet too, since the order fills up with others, and a cap counts ("Buy 5, 5% off, at most 50" is 1% on 1,000 EGP goods).

`coupon_messages.step`, every 5 minutes in the Watchdog. A code is looked at `coupon_message_wait_minutes` (60) after first
sighting, because a crawl finds a code's products gradually. It goes out when it covers `coupon_message_min_products` (5) in-stock watched
products and its best one is `coupon_message_min_discount` (5%) below that product's usual price (its shelf price while the history is under
3 days). The message shows up to 5 products, best discount first, skips any alerted in the last week, and links to the shop's page of all
products where there is one. It goes through the review group like any deal.

- **Limit:** `coupon_messages_per_day` (5) per shop, best value first; the rest wait for the next day, up to 48 hours.
- **Repeats:** a code is news again only if it was not seen for 7 days (`db.COUPON_GONE_DAYS`) or is now worth more.
- **No flood:** with the switch off, due codes are marked `would_send` or `below_floor`, so those active on the day it is switched on are
  already decided.
- A shop with its alerts off or silent (web UI) gets `would_send`, never a message.
- **Not read after 48 hours:** `unread`. **Past its end date, or its page gone:** `ended`, and Approve refuses it too. **A bank's:** `bank`.

## Looking coupons up

**Web UI → Coupons.** Every code seen in the last 30 days, best discount first, with what the rule decided in plain words. Bank offers and ended ones show only when picked in the Decision filter.
A row opens the post as it would look (from the same code that sends it), why it got that decision, and its products: on the shelf, with the coupon, usual price.

**Send by hand:** an admin can queue one coupon's post from that page, with its screenshot. It goes to the review group when that is set up, else straight to the subscribers, and the button says which. It works with `coupon_messages` off and passes over the minimums, since a person chose it, and counts towards the shop's limit for the day. Refused for a bank offer, an ended coupon, one already posted, an Amazon promotion not read yet, or one no product in stock shows.

From the command line:

```bash
make coupons
```

```bash
make coupons CODE=MOBILE200
```

```bash
make coupons FIND=UA55CU7000
```

```bash
make coupon-group URL=/eg-nov26-mobile-200
```

The last one asks noon itself, from this Mac, for the code's whole catalogue, including products outside our watched categories.
It is by hand only: crawls never ask for these pages. `GET /api/coupons?site=&code=` gives the same data, and the web UI's product page shows
the coupon and other products sharing the code.

## Tuning from real data

The Coupons tab's cards and Decision filter show most of this. For more, run in `make sql` once the code has been live for a few days:

```sql
-- Codes per day and what the rule decided
SELECT substr(first_seen_at, 1, 10) day, site, state, COUNT(*) n, ROUND(AVG(best_discount), 3) avg_best
FROM coupons GROUP BY 1, 2, 3 ORDER BY 1 DESC, 2;
-- Products with a coupon, by shop and kind
SELECT site, coupon_kind, COUNT(*) n, SUM(coupon_amount IS NOT NULL OR coupon_percent IS NOT NULL) with_value
FROM products WHERE coupon_kind IS NOT NULL GROUP BY 1, 2;
```

## Not known yet

- Whether a Carrefour coupon needs anything we can't see (`api/v1/pdp/vouchers` is not explored). It is read as an amount anyone gets, so
  check some before switching B on.
- What Raneen's `WF500` takes off: the card doesn't say.
- How many codes noon has across all categories, and how many promotions Amazon has, which decides whether 5% and 5 a day are right.
- What a "3 for 2" promotion's numbers look like: none was in our sample. It is read from its title ("احصل على 3 بسعر 2") or from a percentage that leaves a whole number of items.
- Whether a read works from the droplet, where Amazon often refuses: a refusal only means it is asked again 6 hours later, possibly of the Mac.
