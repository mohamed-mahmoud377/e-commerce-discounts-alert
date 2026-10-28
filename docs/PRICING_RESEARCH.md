# Catching real price drops on amazon.eg and noon

Research report, 28 October 2026. Read it alongside [GUIDE.md](GUIDE.md) ("How fast you hear about a
price change", Algorithms 3, 4 and 5). The raw notes behind it, with every source, are in
[research/pricing-2026-10/](research/pricing-2026-10/) (one file per topic, plus our own measurements).

**Summary.** On both shops the price you see is the price of one *offer* chosen from several sellers. It is not a
fixed product price. On Amazon the chosen offer is the Featured Offer ("Buy Box"). It changes often, and algorithmic
sellers make it change more. On noon each seller's offer is separate, and the offer shown first is not always the
cheapest. So a "drop" can be a seller switch rather than a price cut. Our first three days of data match this: **43% of
big Amazon drops reverted, 27% within an hour and mostly to the exact earlier price**, while **81% of big noon drops
were still low a day later**.

The shops' crossed-out prices are weak evidence. Amazon's "Typical price" is a 90-day median of paid prices and is
the most honest of them. A noon deal price only has to be below the seller's own current price. Egypt's regulator
named the main trick on 27 Oct 2026: raise the price, then announce a cut. Its inspectors will compare November
prices with those of the previous one to two months.

Egyptian prices are also drifting up fast. Rises outnumber drops 4 to 1 on noon, and phones have risen about 55% in
2026. A trailing median therefore lags the real regular price. The tracker misses some real deals because of this,
but the lag does not create fake ones.

The current design is close to the published best practice: its own time-weighted history, a 90-day median, the
rarity rule and a 20% threshold. The largest gains now come from four changes:

- spend the crawl budget on drops, not on price rises and repricing noise;
- crawl the deal feeds faster, and on a calendar of November start times in Cairo time;
- add the legal "lowest price in the previous 30 days" test;
- on noon, judge a seller's drop against the cheapest price of the item across all sellers.

The ranked list is in the final section.

Source labels used below: **[Amazon]** / **[noon]** = the shop's own pages; **[official]** = a government, regulator,
platform or standards text; **[paper]** = peer-reviewed or working paper; **[news]**; **[blog]** = vendor, SEO or
coupon site (weakest). "Not loaded" means the page could not be fetched directly and the wording comes from a
search-engine extract. "Own derivation" marks formulas and rules worked out for this report that no published source
states in that form.

## Amazon prices move many times a day, and the shown price is a seller contest

**How often Amazon changes prices.** The often-quoted figure is **more than 2.5 million price changes per day** on
amazon.com. It comes from a vendor study of November 2013, so it is 13 years old and says nothing about amazon.eg
([Profitero](https://profitero.com/2013/12/profitero-reveals-that-amazon-com-makes-more-than-2-5-million-price-changes-every-day),
[Quartz](https://qz.com/157828/amazon-changes-its-prices-more-than-2-5-million-times-a-day)) [news]. Prices can change
"as often as every 15 minutes" ([RetailWire](https://retailwire.com/discussion/amazons-price-changing-machine/))
[news]. The popular "every 10 minutes" claim has no traceable source. Amazon says it tries to match other retailers'
low prices on products it sells itself, and that it does not set third-party sellers' prices
([The Register](https://www.theregister.com/2023/10/04/amazon_project_nessie_algorithm/)) [news].

**Project Nessie.** The US FTC alleges that Amazon ran a secret algorithm, "Project Nessie". It raised prices where
Amazon expected rivals to follow, and was switched on and off at least 8 times between 2015 and 2019
([PYMNTS](https://www.pymnts.com/legal/2023/ftc-alleges-amazon-deployed-secret-algorithm-to-raise-prices/),
[BCLP](https://www.bclplaw.com/en-US/insights/the-ftc-and-state-case-against-amazon-highlights-risks-and-impacts-from-using-pricing-algorithms.html))
[news]. Amazon disputes this, and it is an allegation, not a court finding. It still matters for a tracker: a price
that is raised and then falls back to its old level is not a discount.

**The Featured Offer (Buy Box).** The best evidence on how the shown price behaves is Chen, Mislove and Wilson
([WWW 2016](https://www.khoury.northeastern.edu/home/cbw/static/pdf/amazon-www16.pdf)) [paper]. They tracked every
seller of 1,641 best-selling products for four months:

| Finding (amazon.com, 2016) | Number |
| --- | --- |
| Products whose Featured Offer price never changed in 6 weeks | 13% |
| Products with more than 14 Featured Offer price changes in 6 weeks | 50% |
| Products whose Featured Offer *seller* stayed the same | 31% |
| How often the cheapest seller wins the Featured Offer | about 60% |
| Algorithmic (repricing-bot) sellers found | 543 |
| Bots that match the lowest price and sit within $1 of it | 70% |
| Ordinary seller-product pairs whose price never changed | 65% |
| Price changes within 1 minute of the previous one (once-a-minute crawl) | 40%, mostly "jitter" |

Two facts follow for a tracker. First, the shown price can drop with no seller cutting anything: the Featured Offer
simply moves to a different, cheaper seller. Second, Amazon's servers can briefly show an old price for up to about
15 minutes after a change. That stale price cannot be bought. The same paper says Amazon lets sellers update prices
about every 20 minutes. Amazon also removes the Featured Offer when a price is "significantly higher than recent
prices". It deactivates offers it flags as "potential pricing errors", whether far too low or far too high. Both
rules come from Amazon's Fair Pricing Policy as quoted in Seller Central forums
([forum](https://sellercentral.amazon.com/seller-forums/discussions/t/57267418-e5fc-4d42-89b8-47b4bf8c76f6),
[EU forum](https://sellercentral-europe.amazon.com/forums/t/potential-pricing-errors-offer-deactivation-notification-in-pricing-health/369903))
[Amazon, forum]. Amazon does not publish what "significantly higher" means.

**Amazon's crossed-out prices.** Amazon shows two kinds, and they are not equally honest. The **List Price** is a
manufacturer's or vendor's suggested price. It may only be shown if customers bought at or above it on Amazon, or
another retailer offered it, in the last 90 days (180 days for seasonal products). The **Typical Price** (formerly
"Was Price") is "the 90-day median price paid by customers", excluding limited-time promotions
([amazon.com help, not loaded: 503](https://www.amazon.com/gp/help/customer/display.html?nodeId=GQ6B6RH72AX8D2TD))
[Amazon]. Amazon's developer API defines `WasPrice` the same way
([SP-API changelog](https://developer-docs.amazon.com/sp-api/lang-ja_JP/changelog/update-product-pricing-api-v2022-05-01-expands-functionality-for-getcompetitivesummary-operation))
[Amazon].

An Amazon staff post on Seller Central US announced two changes in 2026
([Seller Central forum](https://sellercentral.amazon.com/seller-forums/discussions/t/f48a1fe5-aa8e-4806-b687-2d9aeec5c351),
covered by [ChannelX](https://channelx.world/2026/04/amazon-get-strict-on-reference-pricing/)) [Amazon]:

- **From 23 April 2026**, a List Price counts only if it was a Featured Offer sale on Amazon or a recent price at
  another retailer.
- **From 18 May 2026**, if a product was below its non-promotional median on more than half of the last 90 days, the
  Typical Price is computed from *all* sales. A product on "permanent promotion" can no longer show an inflated
  reference.

Amazon says reference prices are now aligned with the price-history graph on product pages, which shows the lowest
Featured Offer price per day. A blog quoting an Amazon FAQ adds that sales during peak events are always excluded
from the Typical Price ([InventoryHero](https://www.inventoryhero.ai/blog/amazon-was-price)) [blog, unverified].

**This is close to what the tracker already does.** Amazon's honest reference is a 90-day median of real prices that
leaves out promotions, which is the tracker's "typical" price. The 18 May rule plays the same role as the tracker's
rarity rule. Whether amazon.eg applies the 2026 rules is **not confirmed**: the amazon.eg help pages returned 503,
and the staff post does not name marketplaces.

**Deal types.** Amazon's own Deals Guide (AU marketplace, June 2024) gives these rules
([Amazon Deals Guide PDF](https://m.media-amazon.com/images/G/35/sp-marketing-toolkit/Sellerfacingguides/Deals/Deals_Guide.pdf))
[Amazon]:

- Lightning Deals run **4 to 12 hours**, and Amazon chooses the time.
- Best Deals run for a few days.
- A deal must be **at least 15% below the lowest price of the last 30 days**, and the product must have a rating of
  at least 3 stars.

Blogs give other thresholds for other years and marketplaces, such as 20% and 90 days
([Amalytix](https://www.amalytix.com/en/knowledge/marketing/amazon-lightning-deals/),
[Helium10](https://www.helium10.com/blog/amazon-lightning-deals/)) [blog]. A Lightning Deal shows a "% claimed" bar.
When the units are claimed it switches to a waitlist, and a buyer then has 15 minutes to check out
([Amazon help](https://digprjsurvey.amazon.com/csad/help/node/GW3L8JX7Q9FH8ALB)) [Amazon]. So a deal can end long
before its timer. Coupons ("clip coupon") and bank offers are applied at checkout and never appear in the listing
price.

**amazon.eg specifics.** amazon.eg replaced Souq.com Egypt on **1 September 2020**, and all of its sellers came from
Souq ([Marketplace Pulse](https://www.marketplacepulse.com/articles/amazon-launches-in-egypt)) [news]. The site is
therefore heavily third-party, and the Featured Offer effects above should be strong there. Prime arrived on 14 June
2022 and now costs EGP 29 a month or EGP 249 a year
([Daily News Egypt, Jun 2026](https://www.dailynewsegypt.com/2026/06/24/amazon-egypt-sees-prime-day-as-key-driver-of-value-digital-growth/))
[news]. valU instalments came in July 2022
([Finextra](https://www.finextra.com/newsarticle/40570/amazon-egypt-rolls-out-bnpl-options)) [news].

Nobody publishes how often amazon.eg reprices, its Featured Offer churn, or its deal thresholds. Whether amazon.eg
runs Lightning Deals at all is also unconfirmed. The tracker's own `deals:<node>` lane can answer that: check whether
it returns lightning-type deals. In the project's parser, amazon.eg shows both "Was:" and "List:" crossed-out prices
(`list_price_kind`).

**Price errors.** Amazon's Conditions of Use let it cancel orders placed at a mistaken price, and a Seattle court
upheld this in 2003 ([Seattle Times](https://archive.seattletimes.com/archive/20030129/amazon29/amazon-wins-small-claims-fight-over-price-mistakes))
[news]. In practice Amazon sometimes honours the price, sometimes gives a small credit, and sometimes cancels
([PetaPixel 2017](https://petapixel.com/2017/08/03/amazon-honoring-500-pricing-error-2200-sony-lens/),
[PetaPixel 2019](https://petapixel.com/2019/09/14/amazon-lists-550-sony-lens-for-250-but-gives-buyers-20-instead))
[news]. No statistics exist on how often errors happen or how fast they are fixed.

## noon sells one product through many offers, and its deal prices are self-declared

noon lists each product once. Sellers attach *offers*, and an offer is "the combination of the seller's price and
chosen processing time". One offer wins the Buy Box. The rest sit under "View All Offers"
([noon Seller Help, "Winning the buy box"](https://support.noon.partners/portal/en/kb/articles/winning-the-buy-box))
[noon]. noon names five factors for the Buy Box: offer price, seller ratings, seller score, customer-initiated
returns, and fast fulfilment. It publishes no weights. For **Fulfilled-by-noon (FBN)** offers only two factors count:
**price and customer-initiated returns**. So the offer shown first can be a slightly dearer FBN offer.

Sellers must keep a minimum and maximum price for each product, and noon sets its own range per catalogue item
([noon Seller Help, promotions errors](https://helpcenter.noon.partners/en/category/promotions-and-deals/how-can-i-fix-listing-errors-in-promotions))
[noon]. This guard rail probably makes extreme glitch prices rarer on noon than elsewhere. That is an inference, not
tested. No official noon repricing tool was found.

**noon's deal rules show that its crossed-out price is a claim, not a record.** A deal price is rejected if it is
"greater than current price": it must be below the seller's current price and inside the min/max range
([noon Seller Help](https://helpcenter.noon.partners/en/category/promotions-and-deals/how-can-i-fix-listing-errors-in-promotions))
[noon]. Mega deals must be scheduled within the next 30 days. Category deals must be set at least four days before
they start ([noon Seller Help, mega deals, not loaded](https://support.noon.partners/portal/en/kb/articles/how-can-i-participate-in-noon-mega-deals))
[noon]. Nothing found requires the "before" price to have been charged for any length of time. A seller could raise
the price on Monday and enrol a "deal" on Friday.

Deals are scheduled with start and end dates, so drops should cluster at deal start times. The catalog API's "starts
today" and "ends today" filters are a natural hook for that.

**noon's "lowest price" badges.** noon shows "Lowest price in 7 / 30 days / a year" badges
([noon UAE lowest-price page](https://www.noon.com/uae-en/lowest-price-uae/)) [noon]. noon publishes no definition
of them. In our data the 30-day badge sits on **19% of the catalogue** and the 365-day badge on 8% ([CATEGORIES.md](CATEGORIES.md)).
Under steady price rises, any small give-back hits a "30-day low", so this badge says little. The tracker is right
not to count it as evidence. The 7-day filter is still useful as a *feed*, because it lists where something just
moved down.

**How common multiple sellers are.** This is our own measurement on the 27 Oct snapshot, counting offers seen in the
previous 24 hours. Of 23,671 in-stock noon items, **8.0% had two or more in-stock sellers**. Among those, the dearest
offer was on average **25.4% above the cheapest**, and **492 items had a gap of 20% or more**. On these items a 20%
drop in one seller's offer can leave the price above what another seller has charged all along. The current
cheapest-offer rule catches that only if the cheaper offer is still in stock and was seen in the last 24 hours (see
the recommendations).

**Sale events.** noon's Yellow Friday 2025 ran **20 to 30 November** in the UAE, Saudi Arabia and Egypt, advertised
as "up to 80–90% off" ([Gulf Business](https://gulfbusiness.com/yellow-friday-noon-90-per-cent-off-meals-from-dh1/),
[Zawya](https://www.zawya.com/en/press-release/companies-news/yellow-friday-sale-launches-with-10-days-of-unbeatable-deals-wigzwq37))
[news]. No source documents whether noon Egypt releases deals at fixed hours during the event.

## Egypt adds fast inflation, a strict regulator and a November-heavy calendar

### Prices drift up in steps

| Indicator | Value | Source |
| --- | --- | --- |
| Urban inflation, Sep 2026 | 13.9% a year; +1.3% in the month | [EnterpriseAM](https://enterpriseam.com/egypt/2026/10/11/urban-inflation-eased-to-13-9-in-september-even-as-monthly-prices-continue-to-pick-up/) [news, CAPMAS data] |
| CBE forecast, FY2026/27 average | 16.6% | same |
| USD/EGP | 49.33 (29 Jun 2026), 51.66 (28 Sep), 53.00 (16 Oct), 53.42 (17 Oct), about 54.5 in banks per phone traders (late Oct) | [Al Khaleej](https://www.alkhaleej.ae/2026-06-29/اقتصاد/أسواق-عربية-وعالمية/سعر-الدولار-مقابل-الجنيه-المصري-الاثنين-29-يونيو-2026-العملة-الأمريكية-تواصل-التراجع), [Soutalomam](https://soutalomam.net/%d8%b3%d8%b9%d8%b1-%d8%a7%d9%84%d8%af%d9%88%d9%84%d8%a7%d8%b1-%d8%a7%d9%84%d9%8a%d9%88%d9%85-%d8%a7%d9%84%d8%b3%d8%a8%d8%aa-17-%d8%a3%d9%83%d8%aa%d9%88%d8%a8%d8%b1-2026-%d8%a3%d9%85%d8%a7%d9%85-%d8%a7/), [Akher News](https://akher.news/80122/) [news] |
| Locally made phones | +55% since January 2026, raised "every 10 to 20 days by roughly 5% to 7%" | [Banker, 3 Oct 2026](https://www.banker.news/154827) [news] |
| Imported phones after the October slide | expected +5–7%; Samsung issued new price lists on 22 Oct 2026 | [Masrawy](https://www.masrawy.com/news/news_economy/details/2026/10/22/3066897/%D8%B4%D8%B9%D8%A8%D8%A9-%D8%A7%D9%84%D9%85%D8%AD%D9%85%D9%88%D9%84-%D8%B2%D9%8A%D8%A7%D8%AF%D8%A9-%D9%85%D8%B1%D8%AA%D9%82%D8%A8%D8%A9-%D9%81%D9%8A-%D8%A3%D8%B3%D8%B9%D8%A7%D8%B1-%D8%A7%D9%84%D9%87%D9%88%D8%A7%D8%AA%D9%81-%D8%A7%D9%84%D9%85%D8%B3%D8%AA%D9%88%D8%B1%D8%AF%D8%A9-%D9%85%D9%86-5-%D8%A5%D9%84%D9%89-7-) [news] |

No official reason for the October slide of the pound was found. Research on online prices says what to expect.
Exchange-rate changes pass through to online prices at **60–75%**, against 20–40% in physical shops
([Gorodnichenko & Talavera](https://www.nber.org/papers/w20406)) [paper]. Where inflation is high, price rises
dominate: in Argentina at 17% inflation, **68% of online price changes were increases**
([Cavallo 2018](https://www.nber.org/papers/w21490)) [paper].

**What this does to a 90-day median (own derivation).** Under steady inflation, a trailing median sits about half
its window, 45 days, in the past. At the headline rate of 13.9% a year that is only ln(1.139) × 45/365 ≈ 1.6% below
today's regular price. For phones (+55% in about nine months, roughly 5% a month) it is about **7% below**. A shop
that cuts a phone by a real 20% from today's regular price therefore shows only about **14%** below "typical", and
the tracker stays silent. To pass the 20% bar the cut must be about 26%.

The error goes one way: drift causes missed deals, not fake ones. Fake alerts come from the opposite case, a price
that was briefly *raised* and pulls the reference up. The median resists that as long as the raise lasted less than
half the window.

### The regulator's test is the previous one to two months

Law 181/2018 counts "price reductions contrary to the truth" as misleading information
([law text, unofficial copy](https://alberonsy.com/%D9%82%D8%A7%D9%86%D9%88%D9%86-%D8%B1%D9%82%D9%85-181-%D9%84%D8%B3%D9%86%D8%A9-2018-%D8%A8%D8%A5%D8%B5%D8%AF%D8%A7%D8%B1-%D9%82%D8%A7%D9%86%D9%88%D9%86-%D8%AD%D9%85%D8%A7%D9%8A%D8%A9-%D8%A7%D9%84/))
[official]. On **27 October 2026** the Consumer Protection Agency (CPA) announced its White Friday 2026 rules
([Amwal Al Ghad](https://amwalalghad.com/2026/10/27/%D8%AD%D9%85%D8%A7%D9%8A%D8%A9-%D8%A7%D9%84%D9%85%D8%B3%D8%AA%D9%87%D9%84%D9%83-%D9%8A%D8%B9%D9%84%D9%86-%D8%B6%D9%88%D8%A7%D8%A8%D8%B7-%D8%A7%D9%84%D8%AC%D9%85%D8%B9%D8%A9-%D8%A7%D9%84%D8%A8/),
[Cairo24](https://www.cairo24.com/2518994)) [news quoting the CPA]:

- shops must show the price **before and after** the discount, the real discount percentage, and the offer's
  duration and terms;
- online shops are covered by the same rules;
- an operations room works throughout November, and an "advertising observatory" watches platforms and social media;
- penalties reach **EGP 2 million or twice the product value**, whichever is greater. One spokesman says "the
  value", so the exact wording is uncertain.

The CPA spokesman named the trick directly: "زيادة السعر أولا ثم الإعلان عن خفضه لاحقا" (raise the price first, then
announce a cut). He said inspectors will **review prices from the past one or two months**
([Al-Mawqa'](https://almawq3.com/%D8%A8%D8%AA%D8%B2%D9%88%D9%91%D8%AF-%D8%A7%D9%84%D8%B3%D8%B9%D8%B1-%D9%88%D8%A8%D8%B9%D8%AF%D9%8A%D9%86-%D8%AA%D8%B9%D9%85%D9%84-%D8%AE%D8%B5%D9%85%D8%9F-%D8%AD%D9%85%D8%A7%D9%8A%D8%A9-%D8%A7%D9%84/))
[news quoting the CPA].

Two things follow. During November every Egyptian shop will show a crossed-out price, so its presence carries no
information. And the regulator's own test is "was the before-price really charged in the last 1–2 months", which a
tracker with its own history can apply directly. Egyptian shoppers have reported this kind of inflated "was" price
before: in 2022 a Jumia socket bought months earlier at EGP 245 was shown as "340 → 245"
([Egyptian Streets](https://egyptianstreets.com/2022/11/28/real-discounts-or-fake-deals-a-look-at-black-friday-in-egypt/))
[news].

### Sale calendar (Cairo time)

Egypt's summer time ends at midnight between Thursday 29 and Friday 30 October 2026. Cairo is **UTC+3 until then and
UTC+2 afterwards**, so every November time below is UTC+2: 00:00 Cairo = 22:00 UTC the day before. No shop has
announced 2026 dates yet (as of 28 Oct). The 2026 column is an expectation from the 2025 pattern.

| Event | Last seen | Expected 2026 | Start time seen | Source |
| --- | --- | --- | --- | --- |
| Jumia Black Friday | 30 Oct 2025, 22:00, to 30 Nov; extra drops each Friday, the biggest on Black Friday | could start 29–30 Oct 2026, this week | 22:00 Cairo | [Economic Key](https://economickey.com/2025/10/29/jumia-egypt-4/) [news, not loaded] |
| amazon.eg 11.11 | 7–12 Nov 2025 | about 7–12 Nov | not stated | [FollowICT](https://followict.news/en/amazon-eg-announces-11-11-sale-event-with-hundreds-of-thousands-of-deals-from-november-7-to-12/) [news] |
| noon 11.11 | UAE: from the evening of 7 Nov to 12 Nov 2025; Egypt dates not confirmed | about 7–12 Nov | evening | [Khaleej Times](https://www.khaleejtimes.com/uae/1111-sale-dh1-deals-90-discounts-gifts) [news] |
| amazon.eg White Friday | 20–30 Nov 2025, up to 70% off; Prime early access 24 h from 00:00 on 20 Nov | about 20–30 Nov | 00:00 Cairo | [Arab Finance](https://www.arabfinance.com/en/News/newdetails/20344), [Al Mal News](https://almalnews.com/2085755/%D8%A3%D9%85%D8%A7%D8%B2%D9%88%D9%86-%D9%85%D8%B5%D8%B1-%D8%AA%D8%B7%D9%84%D9%82-%D8%A3%D9%83%D8%A8%D8%B1-%D9%85%D9%88%D8%B3%D9%85-%D8%AA%D8%AE%D9%81%D9%8A%D8%B6%D8%A7%D8%AA/) [news] |
| noon Yellow Friday | 20–30 Nov 2025 | about 20–30 Nov | not stated | [Zawya](https://www.zawya.com/en/press-release/companies-news/yellow-friday-sale-launches-with-10-days-of-unbeatable-deals-wigzwq37) [news] |
| White Friday / Cyber Monday | | Fri 27 Nov / Mon 30 Nov 2026 | | [getwafarna](https://www.getwafarna.com/en/article/black-friday-2026-date) [blog] |
| CPA operations room | | all of November 2026 | | [Cairo24](https://www.cairo24.com/2518994) [news] |
| Jumia daily flash sale | countdowns to 10:00 on consecutive days | daily | 10:00 Cairo | [Jumia flash sales page](https://www.jumia.com.eg/groceries/flash-sales/) [shop] |
| amazon.eg Prime Day | 23–26 Jun 2026 (global event, Egypt included); first Egyptian edition 16–21 Jul 2024 | late June 2027 | | [aboutamazon](https://aboutamazon.com/news/retail/amazon-prime-day-june-2026), [Ahram Online](https://english.ahram.org.eg/News/526519.aspx) [Amazon, news] |
| amazon.eg Mega Sale | 12–18 May (probably 2026) | mid-May 2027 | | [Cairo360](https://cairo360.com/article/shopping/amazon-egs-mega-sale-is-back-with-seven-days-of-deals-across-every-category/) [news] |
| Ramadan / Eid | Ramadan began 19 Feb 2026; amazon.eg Eid sale 10–16 March | about 8 Feb 2027 onward | | [Egyptian Streets](https://egyptianstreets.com/2026/02/17/ramadan-in-egypt-starts-on-thursday-19-february/), [Arab Finance](https://www.arabfinance.com/en/home/tagresult/1836) [news] |

### Payment-side discounts are small and invisible

Bank and instalment offers apply only at checkout. The CIB noon card gives 5%, capped at EGP 150 per order, at most
twice a month ([CIB, not loaded](https://www.cibeg.com/en/personal/cards/credit/cib-noon)) [official]. Event offers
look like "Mastercard 10% capped at EGP 200"
([Egypt Today](https://www.egypttoday.com/Article/6/136259/The-Biggest-Sale-of-the-Year-Returns-From-November-20))
[news]. On a EGP 30,000 phone a EGP 150 cap is 0.5%. "0% instalments" carry admin fees of 6% (6 months) to 33% (36
months) on CIB ([CIB offers, not loaded](https://www.cibeg.com/en/personal/cib-offers)) [official], so they are not
discounts at all. The tracker should keep ignoring payment-side offers. During events it could at most mention that a
bank offer may apply.

## Our first three days: noon cuts stick, Amazon cuts often bounce back

Source: the live database, queried read-only on 28 October 2026 at about 16:30 UTC. **Preliminary:** there are only
1 to 3 days of history per shop (Amazon and noon since 25 Oct, Jumia since 26 Oct). Re-measure after 30 days or more
with the SQL in "How to re-measure". History is a step function: a row is written only when the price, crossed-out
price or stock changes.

**Price changes while in stock:**

| Shop | Changes | Rises | Drops | Drops ≥ 20% | Changes under 2% | Average rise | Average drop |
| --- | --- | --- | --- | --- | --- | --- | --- |
| noon | 7,430 | 5,998 | 1,432 | 155 | 15.1% | +10.8% | −8.3% |
| amazon.eg | 3,743 | 1,915 | 1,828 | 57 | 41.6% | +6.0% | −4.5% |
| Jumia | 3,005 | 2,010 | 995 | 102 | 12.1% | +11.3% | −8.2% |
| B.TECH | 591 | 454 | 137 | 1 | 16.9% | +5.9% | −4.2% |

Rises outnumber drops **4 to 1 on noon**, 3 to 1 on B.TECH and 2 to 1 on Jumia. This matches the pound's slide and
the phone price lists above, and the Argentine pattern in Cavallo's data. Amazon is the exception: rises and drops are
balanced, and 42% of its changes are under 2%. That is repricing-bot noise, as Chen et al. describe.

**What happened after big drops (15% or more, in stock before and after):**

| Shop | Big drops | Still low | Reverted to ≥ 95% of before | Reverted within 1 h | Partly back up | Average age of those still low |
| --- | --- | --- | --- | --- | --- | --- |
| noon | 279 | 227 (81%) | 7 (2.5%) | 0 | 45 | 30.7 h |
| amazon.eg | 120 | 53 (44%) | 51 (43%) | 32 (27%) | 8 | 33.8 h |
| Jumia | 151 | 106 (70%) | 39 (26%) | 35 | 3 | 26.6 h |

On Amazon, 33 of the 51 reversals went back to *exactly* the earlier price. On Jumia it was 35 of 39, which is the
shop's two alternating caches described in [JUMIA.md](JUMIA.md). Only 3 Amazon products reverted twice or more.

Here is what this means. A **noon drop is almost always a real repricing or campaign**. **An Amazon drop is often the
Featured Offer moving between sellers, or a short promotion**: it is still buyable, but often for under an hour. And
only 7 of 120 Amazon big drops reverted within 15 minutes, so infrastructure "jitter" is a small part of the
reversals.

**Hour of day (Cairo time, UTC+3, 26–28 Oct).** noon's drops of 10% or more fall in Cairo office hours: **292 of 391
(75%) between 09:00 and 16:59**. The peak is 10:00 (77 drops), with a second bump at 18:00–19:00 and almost none
between 20:00 and 08:00. That looks like sellers and category teams working by hand, or campaigns that start in the
morning. Amazon's drops are spread around the clock with no clear peak, as repricing bots would give. Jumia peaks at
13:00–15:00 and 19:00. The crawl runs around the clock, but two and a half days is too little to plan by. Treat this
as a hypothesis to confirm.

## Trackers and regulators agree: compare with what was really charged

**Keepa** measures a deal as the current price against a **weighted average** over 48 hours, 7, 31 or 90 days,
separately for each price type: Amazon, new third-party, Buy Box and others. It also has "lowest ever" and "lowest in
90 days" flags. Its deals feed has a minimum 10% delta, includes only products updated in the last 12 hours, and
records out of stock as `-1` rather than as a price
([Keepa Deal object](https://keepa.com/api-docs/deal-object.html),
[Keepa deals](https://keepa.com/api-docs/deals.html)) [official].

For Lightning and Warehouse deals Keepa switches the reference to the Amazon or new-offer price instead of the deal's
own history. This is the same idea as comparing a noon seller's offer with the whole item's market price. Products
someone tracks are refreshed at least hourly, according to Keepa's FAQ as quoted in search results (not loaded).
camelcamelcamel publishes no formula or refresh interval
([camelcamelcamel features](https://camelcamelcamel.com/features)) [official].

A mean, as Keepa uses, is pulled down by past flash sales and up by short spikes. The tracker's median is more
robust. And the tracker's 10-to-30-minute revisits are already several times faster than any tracker that publishes
its rate.

**Google Merchant Center** has the most concrete platform rules for an honest crossed-out price
([Google: sale price annotations](https://support.google.com/merchants/answer/9017019?hl=en)) [official]:

- the discount must be **more than 5% and less than 90%**;
- the base price must have been charged for **30 of the last 200 days** in most markets, or 5 of the last 30 days for
  US Shopping ads;
- local inventory ads need 60 of the last 200 days.

Google's consumer "typical" label uses the last **three months** across stores
([Google blog, 2025](https://blog.google/products-and-platforms/products/shopping/save-money-price-insights-price-alerts/))
[official]. Deal communities filter by people: Slickdeals editors check "price history, customer reviews, expert
reviews, availability, and inventory" before a deal reaches the front page
([How Slickdeals works](https://slickdeals.net/corp/how-slickdeals-works/)) [official].

**The law.** Only the EU has a hard formula. The "prior price" in any announced reduction is **the lowest price
applied in at least the 30 days before the reduction** (Directive 98/6/EC Art. 6a). In C‑330/23 (26 Sept 2024) the EU
Court of Justice confirmed that the advertised percentage must be computed from that price
([EUR-Lex](https://eur-lex.europa.eu/eli/dir/1998/6/2022-05-28),
[CJEU press release](https://curia.europa.eu/jcms/upload/docs/application/pdf/2024-09/cp240152en.pdf)) [official].
In a series of cuts within one campaign, the reference stays the price before the *first* cut.

The US rule (16 CFR 233.1) requires the former price to have been offered "for a reasonably substantial period of
time, in the recent, regular course" of business, and forbids "insignificant" reductions
([Cornell LII](https://www.law.cornell.edu/cfr/text/16/233.1)) [official]. UK trading-standards guidance says a
comparison is likely fair only if the sale price does not run longer than the higher price did
([Osborne Clarke](https://marketinglaw.osborneclarke.com/advertising-regulation/no-more-28-day-rule-pricing-and-promotions-under-the-spotlight/))
[blog, law firm]. Egypt's CPA "previous one to two months" test sits between the EU's 30 days and the tracker's 90.

**Fake discounts are the norm in big sales.**

| Study | Finding | Source |
| --- | --- | --- |
| Which?, UK Black Friday 2024 | 83% of 175 deals were the same price or cheaper on another day within six months | [ITV News](https://www.itv.com/news/2025-11-25/most-black-friday-deals-cheaper-or-the-same-price-at-other-times-study-finds) [news] |
| Which?, 2020 | about 92% | [FashionUnited](https://fashionunited.uk/news/retail/nine-in-10-black-friday-deals-same-price-or-cheaper-earlier-in-year-study-finds/2021112459557) [news] |
| EU sweep, Black Friday 2025 | 30% of 314 traders referenced discounts wrongly | [CCPC Ireland](https://www.ccpc.ie/news-and-media/news/article/2026/03/26/investigation-by-ec--ccpc-and-european-consumer-authorities-finds-1-in-3-traders-incorrectly-display-discounts-online-during-black-friday-sales) [official] |
| Prisjakt, Sweden | in October, 13% of nearly 3 million online prices rose more than 10%, so the 30-day lowest price was already high by Black Friday | [Sweden Herald](https://swedenherald.com/article/tips-before-the-phenomenon-black-friday-or-black-period) [news] |
| Consumers' Checkbook | at 12 of 25 US retailers, most items were "on sale" nearly every week | [Checkbook](https://www.checkbook.org/national/sale-fail/) [NGO] |

A counter-study of over a million prices found that most October rises were September campaigns ending, not planned
inflation ([ehandel.se](https://www.ehandel.se/idag-nollstalls-priserna-infor-black-friday-oppnar-for-reafallan))
[news]. Either way, pre-event raises usually start **3 to 6 weeks before** the event. In Egypt that means mid-October
to early November 2026, which overlaps the pound's slide. A raise caused by the pound and a raise made to set up a
fake sale look the same in the data.

## The science of sale detection backs the median, the rarity rule and a short look-back

Economists separate *temporary* sales from *regular* price changes in two ways:

- **"V-shape" filters**: a drop counts as a sale if the price returns to the old level within a window of 1–5 months
  in monthly data, or 1–2 weeks in online daily data
  ([Nakamura & Steinsson 2008](https://eml.berkeley.edu/~enakamura/papers/fivefacts.pdf),
  [Gorodnichenko, Sheremirov & Talavera](https://www.nber.org/papers/w20819)) [paper];
- **running-mode reference prices**: Kehoe and Midrigan take the most common price in a ±5-week window, accepted only
  if it holds more than a third of the window and is actually charged
  ([Kehoe & Midrigan](https://www.nber.org/papers/w16364)) [paper].

All of these look at prices *after* the drop, so a real-time alerter can only use a one-sided version. The facts they
produce are what support the current rules:

| Fact | Number | What it means for the tracker |
| --- | --- | --- |
| Share of time prices sit at their annual mode | 75% (Kehoe & Midrigan) | A price held at most 15% of the time is clearly not the regular price, so the rarity rule is well grounded |
| Online sale depth, US | 10.5–11.9% on average (Gorodnichenko et al.) | 20% is in the upper tail: the tracker catches deep sales and misses ordinary 10% promos by design |
| Online regular-price duration | about 3.5–4.5 months (Cavallo, [NBER w25138](https://www.nber.org/papers/w25138)) | A 90-day window often contains one regular price change, so the median is a smoothed regular price |
| Sales that return to the old price | 60–86% (Nakamura & Steinsson) | Reversal after an alert is the normal sign of a *temporary* sale, which is a real deal |

**One-sided rules that follow from this.** Three rules come out of the literature that an alerter can use in real
time:

- **Prior-price test.** Compare the cut price with the **lowest in-stock price of the 30 days before the cut began**.
  If the product was this cheap recently, the "drop" only undoes a recent rise or repeats a recent promo. This is the
  EU legal test, and it also fits the CPA's 1–2 month look-back.
- **Raise-then-cut flag.** Flag the case where the highest price of the 30 days before the cut was well above (by
  10–15%, more than normal monthly inflation) the median of the 30–120 days before. The cut then lands back near that
  older level.
- **Ignore staircases.** A further cut within 14 days cancels a "sale" label
  ([RBA RDP 2026-02](https://www.rba.gov.au/publications/rdp/2026/2026-02/appendix-a.html)) [paper]. A series of
  undercuts by repricing bots is not a promotion.

The median already blocks raises that lasted less than 45 days, so these rules add most against long raises and
repeated promos. No published algorithm specifically detects fake reference prices. The rules above apply the legal
tests (own derivation).

**Robust statistics for step data.** The natural unit is the *spell*: a price, its start and end, and whether it was
in stock. Every statistic should be weighted by duration, and out-of-stock time left out, as the tracker already
does. Spread measures such as MAD are often zero on step data, because many products have only one or two prices in
90 days. So relative thresholds (20% below) are safer than z-scores.

A *permanent* level shift can be detected online with CUSUM
([Page 1954](https://doi.org/10.1093/biomet/41.1-2.100)) or Bayesian online change-point detection
([Adams & MacKay 2007](https://arxiv.org/abs/0710.3742)) [paper]. When an upward shift is confirmed, the reference
could restart from the new level. Do this only after the new level has held for some time, because resetting the
reference up on a short spike is exactly what creates fake alerts. No paper applies these methods to retail sale
detection; the mapping is an inference.

**Measuring the detector without labels.** Three checks carry over from the literature:

- **Reversal rate**: does the price return to the reference after an alert?
- **Buyer-regret precision**: was the product at or below the alerted price again within 7 or 30 days?
- **Which?-style audit**: six months later, was the alerted price the lowest within ±6 months?

A two-sided V-shape filter run on the full history can label past sales. That gives recall and time-to-notice for
the real-time rules. No consumer price-alert service publishes precision or recall, so these numbers would be the
project's own.

## The science of fast detection: crawl drops, not churn, and treat feeds as gold

These formulas come from the web-crawling literature. Formulas marked "own derivation" are standard probability
applied to this problem. No paper states them in this form.

**The basic delay.** With a revisit every T minutes, a drop that lasts is noticed on average **T/2** after it happens,
and at worst after T. The crawl and Telegram delivery add 1–3 minutes, as measured in GUIDE.md.

**Short drops can be missed (own derivation).** If a drop lasts a fixed time D, the chance a visit lands inside it is
`min(1, D/T)`. If drop lengths are exponential with mean m:

```
P(seen) = (m/T) · (1 − e^(−T/m))        periodic visits every T
P(seen) = m / (m + T)                    random (Poisson) visits, always worse
```

| Revisit T | Mean drop length m | Seen | Missed |
| --- | --- | --- | --- |
| 10 min | 10 min | 63% | 37% |
| 15 min | 10 min | 52% | 48% |
| 15 min | 30 min | 79% | 21% |
| 15 min | 60 min | 88% | 12% |
| 60 min | 60 min | 63% | 37% |
| 60 min | 240 min | 88% | 12% |

To miss at most about 12%, T must be about m/4. Fixed-length deals such as Amazon Lightning Deals (4–12 hours) are
never missed once T is shorter than the deal, but stock can run out first. The waiting is also why near-periodic
visits, which the "most overdue" scheduler gives, beat random ones. Adding ±10–20% jitter costs little: the mean
delay grows by only Var(T)/(2·E[T]).

**Estimating how long drops last from our own data (own derivation).** Count how many crawls in a row still see a
drop. If a share s of drops is seen by only one crawl at interval T, then:

```
m̂ = −T / ln(1 − s)
```

For example, if 40% of drops are seen only once at T = 15 min, then m̂ ≈ 29 min and about 21% of drops are missed.
For irregular gaps, treat each drop's length as interval-censored (Turnbull's estimator, or a Weibull fit). Weight
the drops by 1/P(seen) to count the ones that were missed.

**Uniform beats proportional.** Cho and Garcia-Molina showed that revisiting pages *in proportion to how often they
change* is worse than revisiting all pages equally. The fast-changing pages go stale again at once and swallow the
budget. Their result also says the best schedule ignores pages that change too fast to keep fresh
([Cho & Garcia-Molina, ACM TODS 2003](https://doi.org/10.1145/958942.958945), summarised in
[Wikipedia: Web crawler](https://en.wikipedia.org/wiki/Web_crawler)) [paper, not loaded].

The tracker's rule "halve the interval when any price changed" is proportional, and it counts every change. On noon,
where rises are 4 times more common than drops, and on Amazon, where 42% of changes are under 2%, the speed-up is
mostly spent on price rises and bot noise. Neither can ever produce an alert. On Amazon this matters most, because
the loops are 86–91% busy (GUIDE.md).

**Value-weighted allocation (own derivation, consistent with Kolobov et al.).** Say slice i has a rate λᵢ of
alert-worthy drops, a value vᵢ per drop and a cost cᵢ in pages, and the shop allows a budget of B pages per minute.
Minimising the value-weighted waiting time gives the **square-root law**:

```
Tᵢ = √( μ · cᵢ / (λᵢ · vᵢ) ),   with   √μ = Σⱼ √(cⱼ · λⱼ · vⱼ) / B
```

A slice with twice the drop rate gets an interval √2 shorter (×0.71), not half. A slice twice as costly gets an
interval √2 longer. For short flash sales, give extra visits to the slices with the largest index λᵢ·vᵢ·mᵢ/cᵢ (the
expected drop-value-minutes per page) until Tᵢ approaches mᵢ.

Kolobov et al. solved the related "harmonic staleness" problem exactly
([NeurIPS 2019](https://papers.neurips.cc/paper_files/paper/2019/file/ad13a2a07ca4b7642959dc0c4c740ab6-Paper.pdf),
[code](https://github.com/microsoft/Optimal-Freshness-Crawl-Scheduling)) [paper]. Their crawl rate is
ρᵢ* = (√(Δᵢ² + 4μᵢΔᵢ/λ) − Δᵢ)/2. It never starves a source, which is what the tracker's group caps
(`max_interval_minutes`) guarantee in practice. Keep the caps as hard limits.

The budget must still add up (own derivation): `Σᵢ cᵢ / capᵢ ≤ B_shop`. If the caps ask for more pages than a shop's
workers can fetch, the "most overdue" scheduler quietly stretches every slice.

**Estimating the drop rate (own derivation).** For one slice with irregular gaps tⱼ, the Poisson estimate of λ solves:

```
Σ_{gaps with a drop} tⱼ / (e^(λ·tⱼ) − 1)  =  Σ_{gaps without} tⱼ
```

Solve it by bisection. Add one imaginary quiet gap so the result stays finite, as Kolobov et al. do. Count only drops
that would alert. Because rates change over time, estimate from the last few weeks. Hour-of-week rates can be shrunk
toward the slice average: λ̂(h) = (Xₕ + α·λ̄)/(Eₕ + α), where Xₕ is the drops seen in hour h, Eₕ the exposure in
minutes and α about 120 minutes.

**Feeds are worth the most per page.** Kolobov et al. found that sources which *announce* their changes, only 4% of
URLs in their data, **halved the total staleness cost** when they were crawled as such and given extra bandwidth.
The shops' deal feeds play that role here: Amazon's discount filter and deals JSON, noon's 7-day-low filter and
Jumia's flash-sale page. They tell the tracker where a drop happened for 1–2 pages each. Commercial price tools refresh
every 1–24 hours ([Price2Spy](https://www.price2spy.com/?p=12664), [Prisync](https://prisync.com/features/frequent-price-updates))
[blog, vendor]. The tracker's edge is in how it uses the feeds and the budget, not in raw speed.

## Recommendations for this project

Each row names what it touches, the evidence, the expected effect on the two goals, and the effort. "Basis" says
whether the rule is a published result applied directly ("Published") or the researcher's own reasoning from the
sources ("Own derivation"). After deploying any of these, check on the live system as CLAUDE.md asks: `make health`,
then the SQL below or the slice query in GUIDE.md.

### Do now

| # | Change | Touches | Evidence | Notice fast | Only real discounts | Effort | Basis |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Speed a slice up (halve its interval) only on a **drop of 3% or more or a new product**; never on rises or moves under 2–3% | the scheduler's adaptive rule (`interval_s` after each crawl) | Cho & Garcia-Molina 2003 (proportional schedules waste visits); our data: rises 4:1 on noon, 42% of Amazon changes under 2%; Amazon loops 86–91% busy | Better: the budget moves from noise to quiet slices, where flash sales start, and the Amazon backlog shrinks | No change | Low | Own derivation from a published result |
| 2 | Give the **deal-feed roots their own cap of 5–10 min**, independent of their group, and add the Amazon discount-filter root (`p_n_deal_type:26901036031`) to the groups that only have a `deals:<node>` root today: Kitchen machines, PC components, Desktops | per-root cap in the scheduler; `[[watch]]` targets; `min_interval_minutes` for feeds | Kolobov et al. 2019 (feed-like sources: 2× lower staleness); feeds cost about 20 Amazon and 30 noon pages a sweep (GUIDE.md); Lightning Deals 4–12 h and can sell out early (Amazon Deals Guide) | Much better for the deepest drops, for about 6–13% of Amazon's pages/hour at 10–5 min | No change: feed products are judged against the same history | Low–medium | Published result, own sizing |
| 3 | **November event mode, on Cairo time (UTC+2 from 30 Oct)**: mark priority groups and feeds due at known start times (Jumia 22:00; 00:00 for Amazon's Prime early access; the evening of about 7 Nov for 11.11; Fridays for Jumia) and tighten caps for 20–30 Nov | `max_interval_minutes` (temporary values); `jobs.next_due_at` (the UPDATE in GUIDE.md) from a timed job | The sale calendar above; CPA operations room all November | Better exactly when drops cluster | No change | Low | Own derivation |
| 4 | **Prior-price test**: for `price_drop` and `all_time_low`, also require the price to be below the **lowest in-stock price of the 30 days before the current low spell began**; otherwise no alert | `pricing.compute_stats` (new field), `pricing.evaluate` | EU Art. 6a and CJEU C‑330/23; CPA 1–2 month look-back (27 Oct 2026); EU sweep: 30% of traders wrong | No change | Better: blocks repeats of a recent promo once the 7-day no-repeat ends, and cuts that only undo a recent raise | Low | Published legal test, applied to alerts by the researcher |
| 5 | **noon market reference**: judge a seller's drop also against the item's **cheapest in-stock offer across sellers over time**, not only its own history. A new seller on an item we already track uses that item history instead of Path B | `alerts.check_product`, `db.sibling_offers`, `pricing.compute_stats` (min-across-sellers series) | noon Seller Help (one product, many offers; the shown offer is not always the cheapest); Keepa's reference switching and `isLowestOffer`; our snapshot: 8% of items multi-seller, 25% average gap, 492 items with 20%+ gap | No change | Better on noon: removes "20% below this seller's own price" when the item was only a little dearer elsewhere | Medium | Own derivation |
| 6 | **Keep the 90-day median through November.** Do not shorten it before White Friday | none (keep `typical_90d`) | Pre-event raises start 3–6 weeks before (Prisjakt, EU sweep); a median ignores raises shorter than 45 days; CPA warning of 27 Oct 2026 | No change | Protects against October raises | None | Own derivation |
| 7 | **Honest numbers in the alert**: show the drop against the 30-day prior price as well as against typical; when the price is not a new low, say "was X on <date>"; when the next crawl sees the price back up, send a short follow-up (Arabic: the offer ended) | `alerts.format_deal`, the bot (follow-up message) | CPA: the real percentage against the before-price; Keepa's per-window deltas; our data: 43% of big Amazon drops reverted, 27% within 1 h | Unchanged: alerts still go out at once | Better: the reader sees a Featured Offer flip or a recent repeat for what it is | Low (text), medium (follow-up) | Own derivation |
| 8 | **"High" confidence only with 30+ days of history**, and word the all-time low as "lowest since we started tracking (N days)" | the confidence rule in `pricing.evaluate`; `atl_min_history_days` (keep 14 for the alert, label below 30) | EU 30-day prior price; Google's 30-of-200-days rule; GUIDE.md: history starts 25 Oct | No change | Better labelling in the first month | Low | Published rules applied |
| 9 | **Path B plausibility**: reject any claimed discount of 90% or more (not only 60%+ off an RRP). Keep noon's 30-day badge excluded, and keep treating a noon crossed-out price as a claim | `list_max_claimed_drop` and a new 90% ceiling in `pricing.evaluate` | Google's 5–90% band; noon's deal rule (a deal price only needs to be below the seller's current price); 30-day badge on 19% of the catalogue | No change | Slightly better | Low | Published rule applied |

### Measure first (30 days of data or more)

| # | Change | Touches | Evidence | Notice fast | Only real discounts | Effort | Basis |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | **Detection-quality numbers in the daily report**: per alert, the upper bound on lateness (first seen minus the previous crawl); per shop and group, the seen-once share s, the estimated mean drop length m̂ = −T/ln(1−s) and P(miss); how often feeds saw a drop before the slice crawl | `health.py` / daily report, `jobs`, `price_history` | Formulas above; Kolobov et al. | Lets every scheduling change be judged by the first principle | None directly | Medium | Own derivation |
| 11 | **Monthly precision audit**: reversal rate after alerts; buyer-regret precision (min price in the next 7 and 30 days ≥ 98% of the alerted price); a Which?-style ±6 month check; 👍/👎 buttons on Telegram alerts | a new SQL report; `bot.py` | Nakamura & Steinsson and Kehoe & Midrigan (reversal as validation); Which?; Slickdeals and Pepper votes | None | Measures the false-alert rate, which no tracker publishes | Medium | Own derivation |
| 12 | **noon by the clock**: if 30 days confirm that about 75% of noon drops fall at 09:00–17:00 Cairo, tighten noon caps in the daytime and loosen them at night | scheduler (hour-dependent cap or λ(h)) | Our 2.5 days; fixed repricing cadences at online retailers (Brown & MacKay, [NBER w28860](https://www.nber.org/papers/w28860)) | Better on noon for the same budget | No change | Medium | Own derivation |
| 13 | **Square-root intervals**: set each slice's Tᵢ from λᵢ (alert-worthy drops, irregular-gap estimate), vᵢ and cᵢ, with the group caps as hard limits; first check that Σ cᵢ/capᵢ ≤ B per shop | the scheduler | Cho & Garcia-Molina; Kolobov et al. 2019; formulas above | Better overall time-to-notice | No change | High | Own derivation, consistent with published results |
| 14 | **Drift-aware reference for phones and electronics**: restart the reference after a confirmed upward step (new level held 7+ days and more than a third of the window), or use a 45–60 day window for these groups | `pricing.compute_stats`; a per-group window | Cavallo 2018 (68% rises at 17% inflation); Kehoe & Midrigan's one-third rule; our 4:1 rises; phones +55% in 2026; derived lag of about 7% for phones | No change | Fewer missed real deals; a risk of fake ones if the reference restarts on a short spike, so it must be one-way and slow | Medium–high | Own derivation |
| 15 | **Count-based rarity for Featured Offer flip-flops**: a price reached in two or more separate spells in 30 days is a regular level, even if each spell was short | `pricing.compute_stats`, `pricing.evaluate` | Chen et al. 2016 (Featured Offer churn); our data: 43% of Amazon big drops reverted, mostly to the exact price; the RBA 14-day rule | No change | Better on Amazon, *if* repeat flip-flops are common: only 3 products so far | Low | Own derivation |
| 16 | **Amazon quick re-check before sending** (fetch the product again 2–5 min later) only if the follow-ups in row 7 show many alerts reverting within one crawl | `alerts.check_product`, a targeted fetch | Chen et al. (stale prices for up to about 15 min); our data: only 7 of 120 reverted within 15 min | Slightly worse (adds 2–5 min) | Better if jitter turns out to be common | Medium | Own derivation |
| 17 | **Event prices out of the reference** after White Friday, as Amazon reportedly does for its Typical Price | `pricing.compute_stats` (an event calendar) | Blog quoting an Amazon FAQ (unverified); the sale calendar above | No change | Better after big events | Medium | Unverified practice |

Some ideas were left out on purpose. Folding bank discounts into the price was dropped: the caps make them 0.5–5% and
they cannot be seen from listings. Category-index deflation was dropped too: it is promising for devaluation steps,
but it needs many products per category. A cross-shop "cheapest in Egypt" check is the biggest signal missing compared
with deal communities. It needs product matching across shops without GTINs, so it belongs on the roadmap.

## How to re-measure

Run these monthly with `make sql` (read-only) against the live database, and replace the preliminary numbers above.
All four were tested on the 27 October snapshot. Prices are in piastres, times in UTC.

```sql
-- 1. Rises vs drops while in stock, per shop, last 30 days
WITH h AS (
  SELECT p.site, ph.price, ph.in_stock, ph.observed_at,
         LAG(ph.price)    OVER w AS prev_price,
         LAG(ph.in_stock) OVER w AS prev_in_stock
  FROM price_history ph JOIN products p ON p.id = ph.product_id
  WINDOW w AS (PARTITION BY ph.product_id ORDER BY ph.observed_at)
)
SELECT site,
       count(*)                        AS changes,
       sum(price > prev_price)         AS rises,
       sum(price < prev_price)         AS drops,
       sum(price <= prev_price * 0.90) AS drops_10,
       sum(price <= prev_price * 0.80) AS drops_20,
       sum(price <= prev_price * 0.60) AS drops_40,
       round(100.0 * avg(abs(price - prev_price) < prev_price * 0.02), 1) AS pct_under_2,
       round(100.0 * avg(CASE WHEN price > prev_price THEN price * 1.0 / prev_price - 1 END), 1) AS avg_rise_pct,
       round(100.0 * avg(CASE WHEN price < prev_price THEN price * 1.0 / prev_price - 1 END), 1) AS avg_drop_pct
FROM h
WHERE in_stock = 1 AND prev_in_stock = 1 AND price > 0 AND prev_price > 0
  AND price <> prev_price
  AND observed_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-30 days')
GROUP BY site ORDER BY changes DESC;
```

```sql
-- 2. What happened after each drop of 15% or more (in stock before and after), last 30 days.
-- min_to_next is the time to the crawl that saw the next change: an upper bound on the reversal time.
WITH h AS (
  SELECT p.site, ph.price, ph.in_stock, ph.observed_at,
         LAG(ph.price)        OVER w AS prev_price,
         LAG(ph.in_stock)     OVER w AS prev_in_stock,
         LEAD(ph.price)       OVER w AS next_price,
         LEAD(ph.in_stock)    OVER w AS next_in_stock,
         LEAD(ph.observed_at) OVER w AS next_at
  FROM price_history ph JOIN products p ON p.id = ph.product_id
  WINDOW w AS (PARTITION BY ph.product_id ORDER BY ph.observed_at)
), d AS (
  SELECT *,
         (julianday(next_at) - julianday(observed_at)) * 1440 AS min_to_next,
         next_in_stock = 1 AND next_price >= prev_price * 0.95 AS reverted
  FROM h
  WHERE in_stock = 1 AND prev_in_stock = 1 AND prev_price > 0
    AND price <= prev_price * 0.85
    AND observed_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-30 days')
)
SELECT site,
       count(*)                                                         AS drops_15,
       ifnull(sum(next_at IS NULL), 0)                                  AS still_low,
       ifnull(sum(reverted), 0)                                         AS reverted,
       ifnull(sum(next_in_stock = 1 AND next_price = prev_price), 0)    AS back_to_exact,
       ifnull(sum(next_in_stock = 0), 0)                                AS went_out_of_stock,
       ifnull(sum(next_in_stock = 1 AND next_price < price), 0)         AS fell_further,
       ifnull(sum(next_in_stock = 1 AND next_price > price AND NOT reverted), 0) AS partly_back,
       ifnull(sum(reverted AND min_to_next <= 15), 0)                   AS rev_15m,
       ifnull(sum(reverted AND min_to_next <= 60), 0)                   AS rev_1h,
       ifnull(sum(reverted AND min_to_next <= 360), 0)                  AS rev_6h,
       round(avg(CASE WHEN next_at IS NULL
                      THEN (julianday('now') - julianday(observed_at)) * 24 END), 1) AS still_low_avg_age_h
FROM d GROUP BY site ORDER BY drops_15 DESC;
```

```sql
-- 3. Drops and rises by Cairo hour, last 30 days.
-- Cairo is UTC+3 in summer time and UTC+2 in winter. The CASE covers winter 2026/27
-- (30 Oct 2026 to 30 Apr 2027); add the next boundaries before November 2027.
WITH h AS (
  SELECT p.site, ph.price, ph.in_stock, ph.observed_at,
         LAG(ph.price)    OVER w AS prev_price,
         LAG(ph.in_stock) OVER w AS prev_in_stock
  FROM price_history ph JOIN products p ON p.id = ph.product_id
  WINDOW w AS (PARTITION BY ph.product_id ORDER BY ph.observed_at)
), c AS (
  SELECT *, CAST(strftime('%H', observed_at,
              CASE WHEN observed_at >= '2026-10-29T21:00' AND observed_at < '2027-04-29T22:00'
                   THEN '+2 hours' ELSE '+3 hours' END) AS INTEGER) AS cairo_hour
  FROM h
  WHERE prev_price IS NOT NULL
    AND observed_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-30 days')
)
SELECT cairo_hour,
       sum(site = 'amazon_eg' AND in_stock AND prev_in_stock AND price <= prev_price * 0.90) AS amazon_drops_10,
       sum(site = 'noon_eg'   AND in_stock AND prev_in_stock AND price <= prev_price * 0.90) AS noon_drops_10,
       sum(site = 'jumia_eg'  AND in_stock AND prev_in_stock AND price <= prev_price * 0.90) AS jumia_drops_10,
       sum(in_stock AND prev_in_stock AND price >= prev_price * 1.05)                         AS rises_5_all,
       count(*)                                                                              AS change_rows
FROM c GROUP BY cairo_hour ORDER BY cairo_hour;
```

```sql
-- 4. noon items sold by more than one seller right now (offers seen in the last 24 hours)
WITH s AS (
  SELECT substr(sku, 1, instr(sku, '@') - 1) AS item, count(*) AS sellers,
         min(price) AS cheapest, max(price) AS dearest
  FROM products
  WHERE site = 'noon_eg' AND instr(sku, '@') > 0 AND in_stock = 1
    AND last_seen_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-1 day')
  GROUP BY item
)
SELECT count(*) AS items, sum(sellers > 1) AS multi_seller,
       round(100.0 * sum(sellers > 1) / count(*), 1) AS pct_multi,
       round(100.0 * avg(CASE WHEN sellers > 1 THEN dearest * 1.0 / cheapest - 1 END), 1) AS avg_gap_pct,
       sum(sellers > 1 AND dearest >= cheapest * 1.2) AS gap_20pct_or_more
FROM s;
```

To estimate how long drops last (row 10), take the alert-worthy drops from query 2 that were seen at only one crawl.
Their share is s, and m̂ = −T/ln(1 − s), with T the group's typical interval from the slice query in GUIDE.md.

## Conclusion

The two goals pull against each other less than they seem to. Most fake discounts in Egypt will come from the shops'
own crossed-out prices and from raises before an event. The tracker already ignores the first after three days of
history. Its 90-day median blocks the second as long as the raise lasted under 45 days, and the 30-day prior-price
test closes most of what remains without adding any delay. The one case where honesty and speed really conflict is
Amazon's Featured Offer flip-flop. There the better answer is to send at once and follow up when the price reverts,
rather than hold every alert for a second sighting.

On speed, the evidence says the gain is in *where* crawls go, not in crawling more. Rises and bot noise should stop
pulling slices to 10 minutes. The deal feeds, which see the whole category's drops for one or two pages, should get
the shortest intervals. And November should be run on a calendar in Cairo time. The open questions are empirical:
how long drops on each shop really last, whether noon really moves only in office hours, and how many real deals the
inflation lag hides. Thirty days of the tracker's own data will answer them better than any published study, because
no study covers amazon.eg or noon.

## Sources

Grouped by provenance. "Not loaded" means the page could not be fetched directly and its wording comes from a
search-engine extract or a secondary source.

**Amazon's own pages**
- Strike-Through Pricing and Savings (help page, not loaded: 503): https://www.amazon.com/gp/help/customer/display.html?nodeId=GQ6B6RH72AX8D2TD
- Seller Central staff post "Upcoming Improvements to Reference Pricing" (2026): https://sellercentral.amazon.com/seller-forums/discussions/t/f48a1fe5-aa8e-4806-b687-2d9aeec5c351
- SP-API Product Pricing changelog (WasPrice): https://developer-docs.amazon.com/sp-api/lang-ja_JP/changelog/update-product-pricing-api-v2022-05-01-expands-functionality-for-getcompetitivesummary-operation
- Amazon List Price (seller blog): https://sell.amazon.com/blog/amazon-list-price
- Seller promotions guide: https://sell.amazon.com/blog/seller-promotions
- Deals Guide PDF (AU, June 2024): https://m.media-amazon.com/images/G/35/sp-marketing-toolkit/Sellerfacingguides/Deals/Deals_Guide.pdf
- Lightning Deals help: https://digprjsurvey.amazon.com/csad/help/node/GW3L8JX7Q9FH8ALB
- Fair Pricing Policy (forum): https://sellercentral.amazon.com/seller-forums/discussions/t/57267418-e5fc-4d42-89b8-47b4bf8c76f6
- Featured Offer eligibility (forum): https://sellercentral.amazon.com/seller-forums/discussions/t/124c4f5e379bd12e69255f99aa12b811
- Potential pricing errors deactivation (EU forum): https://sellercentral-europe.amazon.com/forums/t/potential-pricing-errors-offer-deactivation-notification-in-pricing-health/369903
- Prime Day June 2026: https://aboutamazon.com/news/retail/amazon-prime-day-june-2026

**noon's own pages**
- Winning the buy box: https://support.noon.partners/portal/en/kb/articles/winning-the-buy-box
- Fixing listing errors in promotions: https://helpcenter.noon.partners/en/category/promotions-and-deals/how-can-i-fix-listing-errors-in-promotions
- noon mega deals (not loaded): https://support.noon.partners/portal/en/kb/articles/how-can-i-participate-in-noon-mega-deals
- Coupons and promo codes (not loaded): https://helpcenter.noon.partners/en/category/promotions-and-deals/how-to-participate-in-coupons-or-promo-codes
- Lowest-price pages: https://www.noon.com/uae-en/lowest-price-uae/ , https://www.noon.com/saudi-en/lowest-price-saudi/

**Other shops, banks and platforms (official)**
- Jumia Egypt flash sales: https://www.jumia.com.eg/groceries/flash-sales/
- CIB noon card (not loaded): https://www.cibeg.com/en/personal/cards/credit/cib-noon
- CIB offers (not loaded): https://www.cibeg.com/en/personal/cib-offers
- Keepa Deal object: https://keepa.com/api-docs/deal-object.html
- Keepa deals: https://keepa.com/api-docs/deals.html
- Keepa lightning deal object: https://keepa.com/api-docs/lightning-deal-object.html
- camelcamelcamel features: https://camelcamelcamel.com/features
- Google Merchant Center, sale price annotations: https://support.google.com/merchants/answer/9017019?hl=en
- Google price insights help: https://support.google.com/faqs/answer/10675605?hl=en
- Google Keyword blog, June 2025: https://blog.google/products-and-platforms/products/shopping/save-money-price-insights-price-alerts/
- How Slickdeals works: https://slickdeals.net/corp/how-slickdeals-works/
- Pepper promotable deal conditions: https://help.business.pepper.com/help/promotable-deal-conditions
- Honey Droplist: https://help.joinhoney.com/article/79-what-is-droplist

**Law and regulators**
- Egypt Law 181/2018 (unofficial copy): https://alberonsy.com/%D9%82%D8%A7%D9%86%D9%88%D9%86-%D8%B1%D9%82%D9%85-181-%D9%84%D8%B3%D9%86%D8%A9-2018-%D8%A8%D8%A5%D8%B5%D8%AF%D8%A7%D8%B1-%D9%82%D8%A7%D9%86%D9%88%D9%86-%D8%AD%D9%85%D8%A7%D9%8A%D8%A9-%D8%A7%D9%84/
- CPA White Friday 2026 rules (Amwal Al Ghad, 27 Oct 2026): https://amwalalghad.com/2026/10/27/%D8%AD%D9%85%D8%A7%D9%8A%D8%A9-%D8%A7%D9%84%D9%85%D8%B3%D8%AA%D9%87%D9%84%D9%83-%D9%8A%D8%B9%D9%84%D9%86-%D8%B6%D9%88%D8%A7%D8%A8%D8%B7-%D8%A7%D9%84%D8%AC%D9%85%D8%B9%D8%A9-%D8%A7%D9%84%D8%A8/
- CPA rules (Cairo24): https://www.cairo24.com/2518994 ; Al Borsa: https://www.alborsaanews.com/2026/10/27/2002197 ; El Watan: https://www.elwatannews.com/news/details/8385554
- CPA spokesman, 1–2 month look-back (Al-Mawqa'): https://almawq3.com/%D8%A8%D8%AA%D8%B2%D9%88%D9%91%D8%AF-%D8%A7%D9%84%D8%B3%D8%B9%D8%B1-%D9%88%D8%A8%D8%B9%D8%AF%D9%8A%D9%86-%D8%AA%D8%B9%D9%85%D9%84-%D8%AE%D8%B5%D9%85%D8%9F-%D8%AD%D9%85%D8%A7%D9%8A%D8%A9-%D8%A7%D9%84/ ; Dostor: https://www.dostor.org/5754641
- EU Directive 98/6/EC consolidated: https://eur-lex.europa.eu/eli/dir/1998/6/2022-05-28
- CJEU C‑330/23 press release: https://curia.europa.eu/jcms/upload/docs/application/pdf/2024-09/cp240152en.pdf
- US 16 CFR 233.1: https://www.law.cornell.edu/cfr/text/16/233.1
- EU Black Friday 2025 sweep (CCPC): https://www.ccpc.ie/news-and-media/news/article/2026/03/26/investigation-by-ec--ccpc-and-european-consumer-authorities-finds-1-in-3-traders-incorrectly-display-discounts-online-during-black-friday-sales
- UK "no more 28-day rule" (Osborne Clarke): https://marketinglaw.osborneclarke.com/advertising-regulation/no-more-28-day-rule-pricing-and-promotions-under-the-spotlight/
- Kogan penalty (McCabes): https://mccabes.com.au/kogan-ordered-pay-tax-time-promotion/
- Saudi Ministry of Commerce, nine rules: https://mc.gov.sa/en/mediacenter/News/Pages/26-09-22-01.aspx

**Research papers**
- Chen, Mislove & Wilson, WWW 2016: https://www.khoury.northeastern.edu/home/cbw/static/pdf/amazon-www16.pdf
- Nakamura & Steinsson 2008: https://eml.berkeley.edu/~enakamura/papers/fivefacts.pdf
- Kehoe & Midrigan, NBER w16364: https://www.nber.org/papers/w16364
- Eichenbaum, Jaimovich & Rebelo, NBER w13829: https://www.nber.org/papers/w13829.pdf
- Ray, Snir & Levy, arXiv 2306.17309: https://arxiv.org/abs/2306.17309
- RBA RDP 2026-02, Appendix A: https://www.rba.gov.au/publications/rdp/2026/2026-02/appendix-a.html
- Gorodnichenko, Sheremirov & Talavera, NBER w20819: https://www.nber.org/papers/w20819
- Gorodnichenko & Talavera, NBER w20406: https://www.nber.org/papers/w20406
- Cavallo 2018, NBER w21490: https://www.nber.org/papers/w21490
- Cavallo, NBER w25138: https://www.nber.org/papers/w25138
- Cavallo 2017, NBER w22142: https://www.nber.org/papers/w22142
- Brown & MacKay, NBER w28860: https://www.nber.org/papers/w28860
- Adams & MacKay 2007: https://arxiv.org/abs/0710.3742
- Page 1954 (CUSUM): https://doi.org/10.1093/biomet/41.1-2.100
- Cho & Garcia-Molina, ACM TODS 2003 (not loaded): https://doi.org/10.1145/958942.958945
- Cho & Garcia-Molina, ACM TOIT 2003 (not loaded): https://doi.org/10.1145/857166.857170
- Kolobov, Peres, Lu & Horvitz, NeurIPS 2019: https://papers.neurips.cc/paper_files/paper/2019/file/ad13a2a07ca4b7642959dc0c4c740ab6-Paper.pdf
- Avrachenkov, Patil & Thoppe, arXiv 2009.08142: https://ar5iv.arxiv.org/html/2009.08142
- Grimes et al., WWW 2008: https://archives.iw3c2.org/www2008/papers/pdf/p1149-grimes.pdf
- Turnbull 1976: https://doi.org/10.1111/j.2517-6161.1976.tb01597.x
- Web crawler re-visit and politeness policy (summary of Cho et al.): https://en.wikipedia.org/wiki/Web_crawler

**News**
- Profitero via Quartz (2013): https://qz.com/157828/amazon-changes-its-prices-more-than-2-5-million-times-a-day ; press release: https://profitero.com/2013/12/profitero-reveals-that-amazon-com-makes-more-than-2-5-million-price-changes-every-day
- RetailWire: https://retailwire.com/discussion/amazons-price-changing-machine/
- Project Nessie: https://www.theregister.com/2023/10/04/amazon_project_nessie_algorithm/ , https://www.pymnts.com/legal/2023/ftc-alleges-amazon-deployed-secret-algorithm-to-raise-prices/ , https://www.bclplaw.com/en-US/insights/the-ftc-and-state-case-against-amazon-highlights-risks-and-impacts-from-using-pricing-algorithms.html
- Price errors: https://archive.seattletimes.com/archive/20030129/amazon29/amazon-wins-small-claims-fight-over-price-mistakes , https://petapixel.com/2017/08/03/amazon-honoring-500-pricing-error-2200-sony-lens/ , https://petapixel.com/2019/09/14/amazon-lists-550-sony-lens-for-250-but-gives-buyers-20-instead
- amazon.eg launch: https://www.marketplacepulse.com/articles/amazon-launches-in-egypt
- Amazon Egypt and Prime Day 2026: https://www.dailynewsegypt.com/2026/06/24/amazon-egypt-sees-prime-day-as-key-driver-of-value-digital-growth/
- First Prime Day Egypt 2024: https://english.ahram.org.eg/News/526519.aspx
- valU on amazon.eg: https://www.finextra.com/newsarticle/40570/amazon-egypt-rolls-out-bnpl-options
- amazon.eg White Friday 2025: https://www.arabfinance.com/en/News/newdetails/20344 , https://almalnews.com/2085755/%D8%A3%D9%85%D8%A7%D8%B2%D9%88%D9%86-%D9%85%D8%B5%D8%B1-%D8%AA%D8%B7%D9%84%D9%82-%D8%A3%D9%83%D8%A8%D8%B1-%D9%85%D9%88%D8%B3%D9%85-%D8%AA%D8%AE%D9%81%D9%8A%D8%B6%D8%A7%D8%AA/ , https://www.egypttoday.com/Article/6/136259/The-Biggest-Sale-of-the-Year-Returns-From-November-20
- amazon.eg 11.11: https://followict.news/en/amazon-eg-announces-11-11-sale-event-with-hundreds-of-thousands-of-deals-from-november-7-to-12/
- amazon.eg Mega Sale: https://cairo360.com/article/shopping/amazon-egs-mega-sale-is-back-with-seven-days-of-deals-across-every-category/
- amazon.eg Eid sale: https://www.arabfinance.com/en/home/tagresult/1836
- noon Yellow Friday 2025: https://gulfbusiness.com/yellow-friday-noon-90-per-cent-off-meals-from-dh1/ , https://www.zawya.com/en/press-release/companies-news/yellow-friday-sale-launches-with-10-days-of-unbeatable-deals-wigzwq37
- noon 11.11 (UAE): https://www.khaleejtimes.com/uae/1111-sale-dh1-deals-90-discounts-gifts
- Jumia Black Friday 2025 (not loaded): https://economickey.com/2025/10/29/jumia-egypt-4/
- Fake discounts in Egypt, 2022: https://egyptianstreets.com/2022/11/28/real-discounts-or-fake-deals-a-look-at-black-friday-in-egypt/
- Inflation, Sep 2026: https://enterpriseam.com/egypt/2026/10/11/urban-inflation-eased-to-13-9-in-september-even-as-monthly-prices-continue-to-pick-up/ , https://www.dailynewsegypt.com/2026/10/10/egypts-annual-urban-inflation-eases-to-13-9-in-september/
- EGP rates 2026: https://www.alkhaleej.ae/2026-06-29/اقتصاد/أسواق-عربية-وعالمية/سعر-الدولار-مقابل-الجنيه-المصري-الاثنين-29-يونيو-2026-العملة-الأمريكية-تواصل-التراجع , https://www.alkhaleej.ae/2026-09-28/اقتصاد/أسواق-عربية-وعالمية/سعر-الدولار-في-مصر-اليوم-الاثنين-28-سبتمبر-2026ماذا-ينتظر-الجنيه , https://soutalomam.net/%d8%b3%d8%b9%d8%b1-%d8%a7%d9%84%d8%af%d9%88%d9%84%d8%a7%d8%b1-%d8%a7%d9%84%d9%8a%d9%88%d9%85-%d8%a7%d9%84%d8%b3%d8%a8%d8%aa-17-%d8%a3%d9%83%d8%aa%d9%88%d8%a8%d8%b1-2026-%d8%a3%d9%85%d8%a7%d9%85-%d8%a7/ , https://akher.news/80122/
- Phone prices 2026: https://www.banker.news/154827 , https://www.masrawy.com/news/news_economy/details/2026/10/22/3066897/%D8%B4%D8%B9%D8%A8%D8%A9-%D8%A7%D9%84%D9%85%D8%AD%D9%85%D9%88%D9%84-%D8%B2%D9%8A%D8%A7%D8%AF%D8%A9-%D9%85%D8%B1%D8%AA%D9%82%D8%A8%D8%A9-%D9%81%D9%8A-%D8%A3%D8%B3%D8%B9%D8%A7%D8%B1-%D8%A7%D9%84%D9%87%D9%88%D8%A7%D8%AA%D9%81-%D8%A7%D9%84%D9%85%D8%B3%D8%AA%D9%88%D8%B1%D8%AF%D8%A9-%D9%85%D9%86-5-%D8%A5%D9%84%D9%89-7-
- Ramadan 2026: https://egyptianstreets.com/2026/02/17/ramadan-in-egypt-starts-on-thursday-19-february/
- Which? Black Friday studies: https://www.itv.com/news/2025-11-25/most-black-friday-deals-cheaper-or-the-same-price-at-other-times-study-finds , https://fashionunited.uk/news/retail/nine-in-10-black-friday-deals-same-price-or-cheaper-earlier-in-year-study-finds/2021112459557
- Prisjakt October price rises: https://swedenherald.com/article/tips-before-the-phenomenon-black-friday-or-black-period ; counterpoint: https://www.ehandel.se/idag-nollstalls-priserna-infor-black-friday-oppnar-for-reafallan
- Consumers' Checkbook "Sale Fail": https://www.checkbook.org/national/sale-fail/

**Blogs and vendors (weakest)**
- InventoryHero on Amazon Was Price: https://www.inventoryhero.ai/blog/amazon-was-price
- ChannelX on reference pricing: https://channelx.world/2026/04/amazon-get-strict-on-reference-pricing/
- Lightning Deal thresholds: https://www.amalytix.com/en/knowledge/marketing/amazon-lightning-deals/ , https://www.helium10.com/blog/amazon-lightning-deals/ , https://ppc.land/lightning-deal/
- White Friday 2026 date: https://www.getwafarna.com/en/article/black-friday-2026-date
- Price2Spy: https://www.price2spy.com/?p=12664
- Prisync: https://prisync.com/features/frequent-price-updates
- Oxylabs fake-discount study: https://oxylabs.io/blog/oxylabs-research-on-fake-discounts-are-shopping-events-worth-the-deal
