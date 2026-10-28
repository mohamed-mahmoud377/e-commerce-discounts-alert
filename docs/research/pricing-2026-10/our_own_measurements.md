# Our own measurements (live database, 28 October 2026)

Source: `/var/lib/deals/deals.db` on the droplet, queried read-only on 28 Oct 2026 ~16:30 UTC.
Caveat: only 1 to 3 days of history per shop (Amazon and noon since 25 Oct, Jumia since 26 Oct, the small
shops since 26 to 28 Oct). Everything below is preliminary and should be re-measured after 30+ days.
History is a step function: a row is written only when price, crossed-out price or stock changes.

## Data volume

| Shop | Products | History rows | First row |
| --- | --- | --- | --- |
| noon_eg | 36,302 | 43,907 | 2026-10-25 20:47 |
| amazon_eg | 17,727 | 23,253 | 2026-10-25 20:08 |
| jumia_eg | 15,002 | 18,973 | 2026-10-26 18:08 |
| btech_eg | 6,163 | 6,763 | 2026-10-27 06:42 |
| carrefour_eg | 1,734 | 1,865 | 2026-10-28 07:43 |
| cairosales_eg | 1,609 | 1,731 | 2026-10-26 22:21 |
| 2b_eg | 1,600 | 1,818 | 2026-10-26 22:21 |
| dream2000_eg | 1,265 | 1,620 | 2026-10-27 06:41 |

Alerts so far: 174 new_product, 45 early_drop (no Path A alerts yet: 3 days of history are needed).

## Price changes while in stock: rises vs drops

| Shop | Changes | Rises | Drops | Drops >=10% | >=20% | >=40% | Changes under 2% | Avg rise | Avg drop |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| noon_eg | 7,430 | 5,998 | 1,432 | 392 | 155 | 53 | 15.1% | +10.8% | -8.3% |
| amazon_eg | 3,743 | 1,915 | 1,828 | 229 | 57 | 14 | 41.6% | +6.0% | -4.5% |
| jumia_eg | 3,005 | 2,010 | 995 | 240 | 102 | 22 | 12.1% | +11.3% | -8.2% |
| btech_eg | 591 | 454 | 137 | 7 | 1 | 0 | 16.9% | +5.9% | -4.2% |
| 2b_eg | 196 | 171 | 25 | 4 | 0 | 0 | 12.8% | +5.7% | -5.0% |
| carrefour_eg | 131 | 124 | 7 | 2 | 2 | 0 | 3.1% | +5.4% | -10.1% |
| dream2000_eg | 141 | 91 | 50 | 6 | 0 | 0 | 17.0% | +6.0% | -6.2% |
| cairosales_eg | 78 | 31 | 47 | 6 | 1 | 0 | 26.9% | +2.8% | -5.1% |

Reading: rises outnumber drops 4:1 on noon, 3:1 on B.TECH, 2:1 on Jumia, 7:1 on 2B, 18:1 on Carrefour.
This coincides with the EGP slide in October 2026 (about 49.3 to 53.5 per USD, see noon_and_egypt_market.md)
and the 5 to 7% phone price rises every 10 to 20 days. Amazon is the exception: rises and drops are balanced
and 42% of its changes are under 2% (algorithmic repricing jitter, consistent with Chen, Mislove & Wilson
2016). A price history is drifting up, so an old median under-states today's regular price.

## How long big drops (>=15%, in stock before and after) last

Outcome = what the next history row of the product shows.

| Shop | Drops >=15% | Still low now | Reverted (back to >=95% of before) | Went out of stock | Fell further | Partly back up | Reverted within 15 min | within 1 h | within 6 h | Avg age of still-low drops |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| noon_eg | 279 | 227 | 7 | 0 | 0 | 45 | 0 | 0 | 1 | 30.7 h |
| jumia_eg | 151 | 106 | 39 | 0 | 3 | 3 | 22 | 35 | 35 | 26.6 h |
| amazon_eg | 120 | 53 | 51 | 4 | 4 | 8 | 7 | 32 | 46 | 33.8 h |
| others | 10 | 8 | 2 | 0 | 0 | 0 | 0 | 0 | 2 | |

Of the reversals, the price came back to *exactly* the earlier price in 33 of 51 (Amazon), 35 of 39 (Jumia),
4 of 7 (noon). Only 3 Amazon and 3 Jumia products reverted twice or more.

Reading:
- noon: big drops stick (81% still low after a day or more, 2.5% reverted). A noon drop is almost always a
  real repricing or campaign, not noise. The 45 "partly back up" are worth checking (seller nudging up).
- Amazon: 43% of big drops reverted, 27% within an hour, mostly to the exact earlier price. This is the
  featured-offer (Buy Box) switching between sellers or short promotions. One confirming observation
  (a second crawl at the same price) would remove most of these from alerts, but costs one revisit interval.
- Jumia: the quick exact reversals are the shop's alternating cache already documented in JUMIA.md.

## Hour of day (Cairo time, UTC+3 until 30 Oct 2026), 26 to 28 Oct

Drops >=10% per shop, all rises >=5%, and all history rows (a proxy for crawl coverage):

| Cairo hour | Amazon drops | noon drops | Jumia drops | Rises >=5% (all shops) | All rows |
| --- | --- | --- | --- | --- | --- |
| 00 | 3 | 2 | 1 | 126 | 383 |
| 01 | 4 | 1 | 4 | 106 | 283 |
| 02 | 3 | 2 | 7 | 20 | 146 |
| 03 | 9 | 3 | 14 | 74 | 538 |
| 04 | 6 | 10 | 21 | 477 | 1233 |
| 05 | 5 | 1 | 15 | 78 | 307 |
| 06 | 11 | 0 | 6 | 69 | 233 |
| 07 | 18 | 1 | 1 | 47 | 341 |
| 08 | 14 | 8 | 7 | 79 | 279 |
| 09 | 5 | 17 | 0 | 191 | 455 |
| 10 | 15 | 77 | 9 | 592 | 990 |
| 11 | 8 | 60 | 6 | 342 | 861 |
| 12 | 11 | 30 | 15 | 347 | 836 |
| 13 | 7 | 34 | 19 | 513 | 1117 |
| 14 | 18 | 26 | 29 | 601 | 1252 |
| 15 | 12 | 30 | 28 | 594 | 1279 |
| 16 | 7 | 18 | 10 | 330 | 722 |
| 17 | 3 | 2 | 10 | 292 | 607 |
| 18 | 7 | 38 | 8 | 445 | 1357 |
| 19 | 17 | 24 | 24 | 602 | 1378 |
| 20 | 5 | 3 | 6 | 85 | 438 |
| 21 | 8 | 2 | 2 | 55 | 259 |
| 22 | 24 | 0 | 4 | 55 | 223 |
| 23 | 6 | 2 | 2 | 32 | 180 |

Reading: noon's drops are concentrated in Cairo office hours, 09:00 to 16:59 (292 of 391 = 75%) plus an
18:00 to 19:00 bump; almost none 20:00 to 08:00. That looks like sellers and noon's category teams acting
by hand or by campaigns starting at office hours. Amazon's drops are spread round the clock (algorithmic
repricers), with no clear peak. Jumia peaks 13:00 to 15:00 and 19:00 and has a small night bump 03:00 to 05:00.
Caveat: two and a half days only; the crawl itself runs round the clock (the Mac is kept awake), but the rows
column shows the volume of changes, not of crawls, so quiet hours may partly reflect fewer changes.

## Implications noted for the report

1. noon: shift crawl budget towards 09:00 to 20:00 Cairo, and especially the first hour of the working
   day (10:00) when campaigns go live; nights can run at the cap.
2. Amazon: needs a confirmation or a "flip-flop" memory rather than a time-of-day schedule; drops that come
   back to the exact earlier price within an hour are the featured offer switching.
3. Under upward drift (rises 2 to 18 times more common than drops), a 90-day median lags the current regular
   price: the tracker will miss deals (false negatives) rather than invent them, but a drop that just
   undoes a recent rise is not a deal.
4. Re-run the queries in docs/PRICING_RESEARCH.md ("How to re-measure") monthly to replace these preliminary numbers.
