# How price trackers, deal sites and regulators define a real discount and an honest reference price

Scope: what Keepa, camelcamelcamel, Google Shopping, PriceSpy/Prisjakt, Honey, Pricena/Yaoota, Slickdeals and Pepper/mydealz do; the legal reference-price rules in the EU, UK, US, Australia and the Gulf; evidence on how common fake discounts are; and how all of this maps onto our tracker's rules. Those rules are: a time-weighted 90-day in-stock median as the typical price, alert at 20% or more below it, a rarity rule of at most 15% of time at or below the current price, an all-time-low rule (15% or more below typical, at least 14 days of history), a 60% possible-error flag, and a new-listing rule (crossed-out price at least 30% higher plus one real signal).

Research date: 28 Oct 2026. Primary sources are marked **[P]**. Blogs, vendors and news are marked **[S]**.

## Q1. Keepa and camelcamelcamel: update frequency, price types, drop % calculation, alerts

### Takeaway
Keepa measures a deal as the percentage difference between the **current** price and a **weighted average** over a selectable window: 48 h (labelled "day"), 7 days, 31 days or 90 days. It does this separately for each price type: Amazon, New 3rd-party, Buy Box, List, Warehouse, Lightning, and others. It also offers "lowest ever" and "lowest in 90 days" flags. Products someone tracks are refreshed at least hourly; others only "several times a day". camelcamelcamel publishes no refresh interval or formula. It gives high, low and average figures over 1, 3, 6 and 12 months, and alerts on a user-set desired price or % drop.

### Cited findings
- Keepa Deal object fields: `current` is the price at the last update (`-1` when no offer or out of stock). `avg` holds "the weighted averages in the respective date range and price type". `delta` is average minus current. `deltaPercent` is the same in percent. `deltaLast` is the previous price minus the current one. A value of `0` means "did not change or could not be calculated". **[P]** [Keepa Deal Object](https://keepa.com/api-docs/deal-object.html)
- Keepa date ranges: index 0 = "day", which in fact averages "the last **48 hours**, not 24 hours"; 1 = 7×24 h; 2 = 31×24 h; 3 = 90×24 h. The weighting method is not documented; we presume it is time-weighted. **[P]** [Keepa Deal Object](https://keepa.com/api-docs/deal-object.html)
- For **Lightning Deals, Prime Exclusive and Warehouse deals**, Keepa computes delta and deltaPercent "with the Amazon or New price as the reference price" rather than the same price type's own history. `lightningEnd` gives the scheduled end time. **[P]** [Keepa Deal Object](https://keepa.com/api-docs/deal-object.html)
- Keepa deals query:
  - `deltaPercentRange` **minimum is 10%** (80% for sales rank).
  - `dateRange` can be 24 h, 7 d, 31 d or 90 d.
  - Flags:
    - `isLowest`: lowest since tracking began.
    - `isLowest90`: lowest in the past 90 days.
    - `isLowestOffer`: lowest of all New offers.
    - `isBackInStock` / `isOutOfStock`: stock changed within the last 24 h.
    - `isRisers`: the price rose.
    - `hasReviews`, `minRating`, `mustHaveAmazonOffer`.
  - Sort options: deal age, absolute delta, % delta or sales rank.
  - Deals only include products "updated within the last **12 hours**".
  - Keepa's guidance: "shorter intervals favor significant drops but may miss gradual ones."

  **[P]** [Keepa Browsing Deals](https://keepa.com/api-docs/deals.html)
- Price types with deal data include AMAZON, NEW, USED, SALES (rank), LISTPRICE, COLLECTIBLE, REFURBISHED, NEW_FBM_SHIPPING, LIGHTNING_DEAL, WAREHOUSE, NEW_FBA and BUY_BOX_SHIPPING (index 18, Buy Box including shipping; `-1` when no offer has the Buy Box). The Statistics object has `buyBoxPrice` and `lastBuyBoxUpdate`. **[P]** [Keepa Product Finder](https://keepa.com/api-docs/product-finder.html), [Keepa Statistics Object](https://keepa.com/api-docs/statistics-object.html)
- Keepa update frequency: it "varies, depending on the general interest in the product". Products tracked by any user are updated **at least once every hour**; most others "several times daily". The API refreshes a product whose data is older than about 1 h before answering. This comes from the Keepa FAQ as quoted in search results; the FAQ page itself would not render, so it is **[P, indirect]**. [Keepa](https://keepa.com/#!faq), [Keepa Product Request docs](https://keepa.com/api-docs/product.html)
- camelcamelcamel says data is updated from Amazon "constantly" and gives no interval. **[P]** [camelcamelcamel features](https://camelcamelcamel.com/features)
- camelcamelcamel prioritises refreshing products with an active Price Watch. Amazon prices can change "as often as every 15 minutes", so a day's lowest or highest price may be missed. **[S]** [Techlicious](https://www.techlicious.com/tip/how-to-use-camelcamelcamel/)
- camelcamelcamel charts show high, low and average over 1 month, 3 months, 6 months and 1 year. **[S]** [search summary of camelcamelcamel/Techlicious/Clark](https://clark.com/shopping-retail/how-to-use-camelcamelcamel/)

### Inferences
- Keepa's "drop %" is **current vs. weighted mean**. Our median is more robust: a mean is pulled down by short past flash sales, which makes later drops look smaller, and pulled up by short spikes, which makes drops look bigger. Keepa adds `isLowest90` and `isLowest` as a separate, cheap "real deal" signal. Our all-time-low rule plus the rarity rule cover the same ground and are stricter.
- Keepa's 10% floor is far below our 20%. Keepa is a filter tool and users pick their own thresholds. A push-alert system needs a higher bar, so 20% is consistent with "alert only on real discounts".
- Keepa reports a separate drop % per window (48 h, 7 d, 31 d, 90 d). One option for us: require the 90-day drop and *also* report the 7-day or 31-day drop. A large 48-hour delta alongside a small 90-day delta is the signature of a price that was raised just before the "deal".
- Keepa measures Lightning and Warehouse prices against the Amazon/New price rather than their own history. For noon's `SKU@seller` keys, the equivalent is to compare a seller's offer against the product's other-seller or Buy Box history, not only that seller's own history.
- Keepa guarantees hourly refresh only for tracked products. That is slower than our goal of noticing drops in minutes, and our deal-feed fast lanes already go beyond what Keepa promises.

### Gaps
- The exact Keepa weighting (time-weighted or sample-weighted) and its treatment of out-of-stock gaps in `avg` are undocumented.
- The Keepa FAQ and camelcamelcamel FAQ pages were blocked or would not render (403 / JavaScript only). The camelcamelcamel "Top price drops" and "Popular products" criteria, its "average price" definition, and its refresh interval were **not found** in any primary source.
- Keepa alert latency, the time from a price change to the notification, is not documented anywhere I found.

## Q2. Google Shopping: price insights, typical range, sale-price annotation rules

### Takeaway
Google's consumer label (low / typical / high) is based on "how stores have priced it over the past **three months**", across sellers on the web. Its Merchant Center rules for showing a strikethrough price are the most concrete published "honest reference" thresholds from any platform:
- The discount must be **more than 5% and less than 90%**.
- The base price must have been charged for **30 of the past 200 days** in most markets, or **5 of the past 30 / 15 of the past 200 days** for US Shopping ads.
- Local inventory ads require **60 of the past 200 days**.

### Cited findings
- "The discount of the sale must be greater than 5% and less than 90%." **[P]** [Google Merchant Center: About sale price annotations](https://support.google.com/merchants/answer/9017019?hl=en)
- Base-price duration rules **[P]** [same page](https://support.google.com/merchants/answer/9017019?hl=en):
  - **Shopping ads, US:** the base price was charged for either **5 days within the past 30 days** or **15 days within the past 200 days**, non-consecutive.
  - **Shopping ads, AU/BR/CA/FR/DE/IN/IT/JP/KR/NL/ES/UK, and free listings in BR/FR/DE/IN/IT/KR/NL/ES/UK:** "valid for **30 days within the past 200 days** (doesn't need to be 30 consecutive days)".
  - **Free listings, US/CA/AU/JP:** no duration rule; the sale price only has to be below the base price and both must appear on the landing page.
  - **Local inventory ads:** **60 days within the past 200 days**, or **60 consecutive days** if the product has been active for less than 200 days.
  - Google "regularly validates the sale price on your website". The sale price must be the most prominent price on the landing page.
- Localized versions of the page have shown different numbers. The Latvian version listed 60 of 200 days, so the rules have varied by version and language. **[S]** [search summary](https://support.google.com/merchants/answer/9017019?hl=lv)
- A "Price drop" badge can be shown automatically when the price is below the merchant's **average price over the past 60 days**. This comes from search-result summaries of Google help content and is not confirmed on the page fetched, so treat it as **[S/unverified]**. [Channable blog](https://www.channable.com/blog/Google-Shopping-sale-price-annotations)
- Practitioners say a sale price should not run for more than 30 days in a row and should be rotated back to the base price. This is practitioner advice, not an official rule. **[S]** [Channable](https://www.channable.com/blog/Google-Shopping-sale-price-annotations)
- Consumer price insights:
  - Labels show whether a price is "high, low, or typical, based on how stores have priced it over the past three months".
  - Users can view price history per retailer.
  - "Track price" sends an email "when the price drops at any store".
  - A "best price" badge highlights deals from high-quality sellers (US).

  **[P]** [Google Keyword blog, 25 Jun 2025](https://blog.google/products-and-platforms/products/shopping/save-money-price-insights-price-alerts/)
- The typical price draws on sellers across the web, not only Google Shopping. Users can see whether a price "has recently dropped to its **30-day low**", based on the total price (item, discount and shipping). **[P]** [Google Help: Get price insights](https://support.google.com/faqs/answer/10675605?hl=en)

### Inferences
- Google's 30/200 rule says a "was" price is honest only if it held for about **15% of the last 200 days**. That is a mirror image of our rarity rule. Candidate rule: the shop's crossed-out price is "credible" only if our own history shows the product at or above that price for **at least 30 in-stock days of the last 200**, or at least 5 of the last 30 for fast-moving items.
- The 5%–90% band is a reasonable plausibility gate on shop-supplied crossed-out prices. Treat anything claiming 90% or more as a likely error or fake; this lines up with our 60% "possible price error" flag.
- Google's "typical" uses a 3-month window across stores, the same 90-day horizon we use. Its cross-store comparison suggests a further signal: whether this price is also below the same product's price at the other Egyptian shops we crawl.

### Gaps
- Google does not publish how the "typical range" is computed (percentile band, standard deviation or anything else). I found no official numeric definition of "low".
- The 60-day average "Price drop" badge rule could not be confirmed from a primary page.

## Q3. Other trackers and deal communities

### Takeaway
Consumer trackers (Honey Droplist, PriceSpy, Pricena) mostly use **user-chosen target prices**, not statistical deal detection. Deal communities (Slickdeals, Pepper/mydealz) use **community votes as the first filter** and then human checks. Slickdeals editors explicitly check **price history, availability and reviews** before a deal reaches the front page. Pepper promotes a deal at **100°** ("Hot") in DE/FR/UK/MX and **300°** in Poland.

### Cited findings
- **Honey Droplist:** the user picks a target price and a tracking period of **30, 60, 90 or 120 days**. Alerts come by email or push. Shipping, tax and handling are excluded from the drop. **[P]** [Honey help: What is Droplist?](https://help.joinhoney.com/article/79-what-is-droplist), [Honey help: price increased](https://help.joinhoney.com/article/94-why-did-my-droplist-item-increase-in-price)
- **PriceSpy/Prisjakt:**
  - Price alerts fire when the price reaches the user's desired level, and per-product price history is kept. **[P, app listing]** [PriceSpy App Store](https://apps.apple.com/gb/app/pricespy-compare-save-money/id369151774)
  - Third-party scrapers report "2 year price history" and "price drop" fields. **[S]** [Apify Prisjakt scraper](https://apify.com/studio-amba/prisjakt-scraper)
  - No published deal formula was found.
- **Prisjakt as a market monitor:** Prisjakt analysed nearly **3 million prices** in October before Black Friday. **13%** of online prices rose by more than 10% that month. Its expert called this a trend since Sweden's 30-day rule: prices are raised early in October so the 30-day lowest price is already high. **[S]** [Sweden Herald](https://swedenherald.com/article/tips-before-the-phenomenon-black-friday-or-black-period), [SVT](https://www.svt.se/nyheter/inrikes/nya-black-friday-fusket-sa-rundar-butiken-lagen)
  - Another report citing Prisjakt: 3 in 10 products rose in price during October, and 1 in 10 rose by more than 10%. **[S]** [Epoch Times SE](https://www.epochtimes.se/nya-black-friday-fusket-sa-rundar-butiken-lagen)
  - Counterpoint from Prisgrisen (over 1 million prices): the differences mostly reflect September campaigns ending, not deliberate increases. Only about 5% of stove and fridge prices were more than 10% above their usual level. **[S]** [ehandel.se](https://www.ehandel.se/idag-nollstalls-priserna-infor-black-friday-oppnar-for-reafallan)
- **Pricena (UAE, Egypt, other MENA markets):**
  - Keeps daily price records for partner stores.
  - Shows a price-trend arrow on product pages.
  - Offers email price-drop alerts via a bell icon.
  - Sends shoppers to the shop rather than selling.

  **[P, company blog]** [Pricena: Everything you need to know](https://ae.pricena.com/news-reviews/en/?p=755), [eg.pricena.com about](https://eg.pricena.com/en/about)
- **Yaoota (Egypt):** a shopping search engine comparing prices across online stores, launched June 2014. No evidence found of price-history or deal-detection features. **[S]** [Al-Monitor 2016](https://al-monitor.com/originals/2016/11/egypt-shopping-portal-startup-economic-ecommerce.html), older source.
- **Slickdeals:**
  - Deals reach the Frontpage after gaining community interest, mainly votes. Deal Editors then check "price history, customer reviews, expert reviews, availability, and inventory". **[P]** [How Slickdeals Works](https://slickdeals.net/corp/how-slickdeals-works/)
  - Staff say editors weigh "price history, availability, ratio of votes". **[P, staff forum post]** [Slickdeals FAQ thread, 4 Nov 2025](https://slickdeals.net/f/18766330-from-company-ownership-to-front-page-your-top-slickdeals-questions-answered)
  - Votes are thumbs up or down and can be retracted. **[P]** [Slickdeals help: voting](https://help.slickdeals.net/hc/en-us/articles/35502751623579-How-to-Vote-on-Slickdeals)
  - No numeric vote threshold for the Frontpage is published. A community thread ([how many upvotes for FP](https://slickdeals.net/f/18788455-how-many-upvotes-for-fp)) indicates it is editorial.
- **Pepper network (mydealz DE, hotukdeals, dealabs, and others):**
  - "Each deal has a temperature based on user votes". The "Hot" page threshold is **100°** in DE/FR/UK/MX and **300°** in Poland. "Super Hot" is 300° in countries without a Highlights page. These are thresholds for promotion pages. **[P]** [Pepper business help: promotable deal conditions](https://help.business.pepper.com/help/promotable-deal-conditions), [Running your first promotion](https://help.business.pepper.com/help/start-promoting-your-deal)
  - Temperatures can go negative. **[S]** [Apify Pepper scraper](https://apify.com/northbell/pepper-deals-scraper)

### Inferences
- The signals deal communities check are price history (vs. the historical low), availability or stock, the seller and reviews, and price elsewhere. Our tracker covers history and stock. A **cross-shop check** (is this the lowest price for the same product across the eight Egyptian shops?) would be the main missing signal, though matching products across shops is hard without GTINs.
- Votes act as a human false-positive filter. In a personal bot the equivalent would be a 👍/👎 feedback button on Telegram alerts, used to tune thresholds.

### Gaps
- No published algorithm for PriceSpy/Prisjakt "deal" labels, Pricena's trend arrow, or Pepper's temperature formula (vote weighting or time decay).
- No MENA camelcamelcamel equivalent with documented deal rules was found.

## Q4. Legal standards for reference ("was") prices, and formula candidates

### Takeaway
- **EU:** the only hard formula. The prior price is the **lowest price in at least the 30 days before the reduction**, and the CJEU (C-330/23, 26 Sep 2024) requires the advertised % to be computed from it.
- **US (16 CFR 233.1):** "openly and actively offered… for a reasonably substantial period of time, in the recent, regular course of business", and a reduction must not be "insignificant".
- **UK:** the old 28-consecutive-day rule (2010) was dropped in 2016 for factors. The main one is that the sale should not run longer than the higher price did.
- **Australia (ACCC):** a "reasonable period" that depends on the product (two weeks either side in Kogan; 12 weeks in the supermarket case).
- **Saudi Arabia and UAE:** require discount licences and both prices on tags, but set no lookback formula.

### Cited findings
**EU**
- Article 6a(2) of Directive 98/6/EC (inserted by Omnibus Directive 2019/2161): the prior price is "the lowest price applied by the trader during a period of time not shorter than 30 days prior to the application of the price reduction". Member States may make exceptions for perishables, for goods on the market for less than 30 days, and for progressive reductions. **[P via law-firm summary]** [HSF Kramer](https://www.hsfkramer.com/notes/ip/2024-posts/cjeu-confirms-that-price-reduction-claims-must-be-based-on-lowest-price-in-last-30-days); [EC Price Indication Directive page](https://commission.europa.eu/law/law-topic/consumer-protection-law/unfair-commercial-practices-law/price-indication-directive_en)
- **Progressive reductions** within one uninterrupted campaign: the prior price is the lowest price in the 30 days before the *first* reduction and remains the reference for later steps. **[S]** [Sellforte summary of Commission guidance](https://sellforte.com/blog/omnibus-directive-how-does-it-impact-your-business)
- The Commission Notice on Art. 6a was adopted 17 Dec 2021 ([2021/C 526/02](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:52021XC1229(06))). It is interpretive, not binding. **[P reference; text not retrieved]**
- **CJEU C-330/23 Aldi Süd (26 Sep 2024):** an announced reduction (e.g. "-20%" or "price highlight") must be calculated on the basis of the prior price, i.e. the lowest price in the previous 30 days. The aim is to stop traders "juggling with prices". **[P]** [CJEU press release CP 24/152](https://curia.europa.eu/jcms/upload/docs/application/pdf/2024-09/cp240152en.pdf); [OJ C/2024/6621](https://eur-lex.europa.eu/eli/C/2024/6621/oj/eng); [Lewis Silkin](https://www.lewissilkin.com/insights/2024/09/30/more-on-pricing-and-discount-claims-cjeu-clarifies-basis-for-calculating-reducti-102jkhq)
- An industry joint paper (Feb 2024) says the guidance has led to "diverging interpretation" of how percentage reductions are computed. **[S, industry]** [EuroCommerce et al.](https://www.eurocommerce.eu/app/uploads/2024/03/20240229-pid-joint-paper-ee-eurocommerce-ire-final.pdf)
- **Netherlands:** ACM fined five webshops a total of **€621,000** (June 2024) for fake discounts based on misleading "van" (was) prices. **[S, unverified]** This appeared in a web-search summary. I did not open the ACM press release, so verify it at acm.nl before relying on it.

**US**
- 16 CFR §233.1:
  - A former price is genuine if the article was "openly and actively offered for sale, for a reasonably substantial period of time, in the recent, regular course of his business, honestly and in good faith".
  - Example: raising a pen to $10 for a few days and then "cutting" it to $7.50 is deceptive.
  - A price never offered, or used only in the distant past without disclosure, is fictitious.
  - The reduction must not be "so insignificant as to be meaningless".

  **[P]** [16 CFR 233.1 (Cornell LII)](https://www.law.cornell.edu/cfr/text/16/233.1)

**UK**
- The 2010 BIS Pricing Practices Guide allowed a saving claim if the higher price had been charged for **at least 28 consecutive days** (old rule). The 2016 CTSI *Guidance for Traders on Pricing Practices* dropped it for factors:
  - the duration of the higher price relative to the sale;
  - recency (the reference should be the most recent price charged);
  - whether other promotions ran during the higher-price period.

  Its rule of thumb is that the comparison is likely to be fair only if the **discounted price does not run longer than the higher price did**. **[S]** [Osborne Clarke "No more 28 day rule"](https://marketinglaw.osborneclarke.com/advertising-regulation/no-more-28-day-rule-pricing-and-promotions-under-the-spotlight/), [Lewis Silkin 2017](https://www.lewissilkin.com/insights/2017/03/17/important-new-guidelines-for-advertising-prices-to-uk-consumers); guidance hosted at [Business Companion](https://www.businesscompanion.info/en/guidance-for-traders-on-pricing-practices)
- After-promotion price claims: unfair if the price is not raised to the stated after-promotion price when the promotion ends. **[P, CTSI]** [Business Companion](https://www.businesscompanion.info/en/guidance-for-traders-on-pricing-practices)
- Under the DMCC Act 2024 (consumer enforcement from April 2025), reference pricing sits in CMA207 (unfair commercial practices, misleading actions and omissions). The CMA can fine up to **10% of global turnover**. CMA209 (price transparency, final 18 Nov 2025) covers drip pricing and does **not** address reference pricing. In August 2024 the CMA also issued reference-pricing guidance for online mattress sales, saying any comparison "must reflect a genuine price advantage". **[P/S]** [CMA209 PDF](https://assets.publishing.service.gov.uk/media/691b10065a253e2c40d705d9/Price_transparency_-_CMA209_.pdf), [HSF Kramer UK round-up 2025](https://www.hsfkramer.com/insights/2025-12/uk-consumer-protection-round-up-2025), [CMS](https://cms.law/en/gbr/legal-updates/price-transparency-guidance-lands-as-cma-launches-first-consumer-law-investigations-using-its-new-dmcc-act-powers)
- One source claims the CTSI guide was updated in April 2026 under the DMCC Act, using factors such as length, recency and sales volume. **[S, unverified]** [Budget Prams blog](https://budgetprams.com/was-400-now-250-on-a-pram-what-trading-standards-guidance-says-about-reference-prices-great-britain/)

**Australia**
- The ACCC sets no fixed number of days. A was price must have been offered for a "reasonable period" before the promotion, which depends on the product, the market and how often its price changes. The trader should also have sold reasonable quantities at that price. **[S]** [Lexology](https://www.lexology.com/library/detail.aspx?g=3ce79000-7db8-4cc0-8f37-cde333458696)
- **Kogan (2020):** prices were raised on 600+ products just before a "10% off" tax-time code. The court treated the two weeks either side as the reasonable period and imposed a **A$350,000** penalty, against an ACCC request of about A$2 million. **[S]** [McCabes](https://mccabes.com.au/kogan-ordered-pay-tax-time-promotion/)
- **Coles/Woolworths "Prices Dropped" case** (filed 2024, 500+ products): the 12-week period at issue was specific to supermarkets. **[S]** [The Conversation](https://theconversation.com/woolworths-and-coles-sued-by-accc-for-allegedly-misleading-shoppers-over-the-price-of-more-than-500-products-239585), [Addisons](https://addisons.com/article/discount-pricing-done-right-after-the-acccs-win-against-coles/)

**Gulf**
- **Saudi Arabia (Ministry of Commerce):**
  - Discounts need a licence.
  - Tags must show the original and discounted price, with the percentage clearly stated (nine rules).
  - Seasonal discounts (e.g. National Day) may not be announced more than 15 days before they start.
  - Breaches are fined under the Anti-Commercial Fraud Law.
  - In 2025 the ministry ran 14,000 inspection visits for National Day.

  **[P]** [MC: nine rules](https://mc.gov.sa/en/mediacenter/News/Pages/26-09-22-01.aspx), [MC: 14,000 visits](https://mc.gov.sa/en/mediacenter/News/Pages/24-09-25-02.aspx)
- **UAE:** Federal Law No. 15 of 2020 requires a pre-sale licence for discount sales. Ads must show pre- and post-discount prices. Dubai's consumer authority says a percentage alone ("25%") is not enough. **[S, academic]** [ResearchGate: Emirati vs French law](https://www.researchgate.net/publication/365923420_Penal_Protection_for_Consumers_from_Fake_or_Unreal_Discounts_A_Comparative_Study_of_Emirati_and_French_Laws); **[S]** [Emirates 24|7](https://www.emirates247.com/uae/dubai-retailers-must-clearly-display-pre-discount-and-post-discount-prices/4739)

### Inferences: formula candidates for our "typical" price and the honesty of shop "was" prices
1. **EU-style floor (the strictest honest reference):** `ref_EU = min(in-stock price over [t_drop − 30d, t_drop))`. A drop is a "legal-grade" discount only if `current < ref_EU`. Advertise the % against `ref_EU` and never against the shop's crossed-out price. This defeats the "raise early October, then cut" trick only partly (Prisjakt evidence), because a raise of more than 30 days resets the floor. Our 90-day median catches that and the 30-day floor does not. **Recommendation:** alert only if `current ≤ (1 − 0.20) × median90` **and** `current < min30`.
2. **Progressive reductions:** for consecutive steps down, anchor at the price before the *first* step and keep `ref` fixed through the campaign. This matches the EU guidance and is easy with a step function: reuse the reference from the last alert if the price has not returned above it since.
3. **US/UK "substantial period" test for shop crossed-out prices (new listings):**
   - Accept a shop's was price only if our own history shows the product **at or above** it for at least a set fraction of observed time:
     - Google: 30/200 days ≈ 15%, or 5/30 days ≈ 17%.
     - Local inventory ads: 60/200 = 30%.
   - Treat it as unverifiable if we have less than 30 days of history (EU exception for goods on the market less than 30 days).
   - CTSI rule of thumb: the sale should not have lasted longer than the time spent at the higher price. Concretely, once `time_at_current_price > time_at_reference_before_drop`, stop calling it a discount and stop re-alerting.
4. **Plausibility band:** Google's 5% < discount < 90%. Treat a claimed or observed drop of 90% or more as an error by default. Our 60% "possible price error" flag is a softer version.
5. **Minimum meaningful drop:** the FTC says reductions must not be "insignificant". Google uses more than 5%, Keepa a 10% floor; our 20% is well above all of them. Fine for push alerts.

### Gaps
- The full text of Commission Notice 2021/C 526/02 could not be retrieved (EUR-Lex returned an empty page). Its exact wording on goods on the market under 30 days, introductory prices, comparisons with RRP or other traders, and perishables is taken from secondary summaries only.
- The current CMA207 text on reference pricing was not retrieved. Whether the UK now has any numeric guidance is unconfirmed.
- I did not reach primary documents on enforcement in Germany (beyond the Aldi Süd referral by Verbraucherzentrale Baden-Württemberg) or Poland (UOKiK).
- **Egypt:** I found no sourced information on whether Consumer Protection Law 181/2018 or Egyptian Consumer Protection Agency rules define a reference price or require discount licences. This needs a follow-up search in Arabic.

## Q5. Evidence on fake discounts

### Takeaway
Fake or inflated reference prices are the norm, not the exception:
- **83–92%** of UK Black Friday "deals" were the same price or cheaper at another time within 6 months (Which?, 2019–2024).
- **About 30%** of 314 EU traders displayed discount references wrongly in the Black Friday 2025 EU sweep.
- About a **quarter** of Amazon vacuum cleaners showed price-increase-then-"discount" behaviour (academic study).
- Vendor and NGO studies put "manipulative" reference prices at 30–60% of event deals.

### Cited findings
- **Which? (UK):**
  - Black Friday 2024 study, published Nov 2025: 175 products from 8 retailers. **83%** were cheaper or the same on at least one other date within six months either side, outside the four-week sale period. **None** was at its cheapest of the year on Black Friday itself. **[S, news report of Which?]** [ITV News, 25 Nov 2025](https://www.itv.com/news/2025-11-25/most-black-friday-deals-cheaper-or-the-same-price-at-other-times-study-finds), [LBC](https://www.lbc.co.uk/article/most-black-friday-deals-same-price-or-cheaper-in-the-six-months-beforehand-DWz5xD_2/)
  - Black Friday 2020: 201 deals from 6 retailers; **184 (about 92%)** were the same price or cheaper in the 6 months before. Only **1** was cheapest on Black Friday alone. **[S]** [FashionUnited, Nov 2021](https://fashionunited.uk/news/retail/nine-in-10-black-friday-deals-same-price-or-cheaper-earlier-in-year-study-finds/2021112459557)
  - Black Friday 2019: 219 products; **85%** were cheaper or the same in the previous 6 months. **[S]** [Yahoo Finance](https://finance.yahoo.com/news/black-friday-deals-same-price-cheaper-sales-which-092945013.html)
  - Another report: **98%** of Black Friday deals were not the cheapest price of the year. **[S]** [AOL](https://www.aol.com/reveals-98-black-friday-deals-144448607.html)
- **EU sweep, Black Friday/Cyber Monday 2025** (published 26 Mar 2026), covering 314 online traders in 23 EU states plus Iceland and Norway:
  - **30%** referenced discounts incorrectly (not the lowest 30-day price).
  - 34% displayed price comparisons, and 6 in 10 of those did not explain the reference.
  - 18% used pressure selling (false scarcity, countdowns).
  - 36% pre-added optional items.
  - 10% used drip pricing.

  **[P]** [CCPC Ireland press release](https://www.ccpc.ie/news-and-media/news/article/2026/03/26/investigation-by-ec--ccpc-and-european-consumer-authorities-finds-1-in-3-traders-incorrectly-display-discounts-online-during-black-friday-sales); [EC sweeps page](https://commission.europa.eu/topics/consumers/consumer-rights-and-complaints/enforcement-consumer-protection/sweeps_en)
  - One authority reported the reference was correct in "just under 40%" of cases checked. **[S]** [heise](https://www.heise.de/en/news/Discount-trap-Black-Friday-Every-third-online-retailer-tricks-with-prices-11226422.html)
  - Example from the sweep: a price "discounted" until 2 Nov, then raised to €39.99 (the earlier reference) and held stable until 24 Nov, then "discounted" again. **[S]** [kiprinform](https://www.kiprinform.com/en/news/eu-sweep-finds-widespread-misleading-online-discounts-during-black-friday-and-cyber-monday/)
- **Academic (Amazon):**
  - A *Marketing Science* study describes "price-increase and list-price synchronization": sellers raise the price and the list price together to show a discount. **[S, news on paper]** [EcomCrew](https://www.ecomcrew.com/amazon-sellers-are-offering-fake-discounts/)
  - University of Florida / Arizona State work found about **a quarter of Amazon vacuum cleaners** temporarily raised prices while showing a discount. **[S]** [Reynolds Center](https://reynoldscenter.substack.com/p/leaning-into-deceptive-practices)
- **Consumers' Checkbook (US nonprofit):** tracked 25 retailers for 6 months. At **12 of 25**, more than half of tracked items were at "sale" prices every or nearly every week, which makes them fake discounts. Fake sales have become "far more prevalent" over 10 years. **[P, NGO]** [Checkbook "Sale Fail"](https://www.checkbook.org/national/sale-fail/)
- **Oxylabs (vendor, Black Friday 2024):** **2,717 of 4,529 products (60%)** were "most probably manipulative" because the reference price had not been regularly offered for a substantial period. **[S, vendor]** [Oxylabs](https://oxylabs.io/blog/oxylabs-research-on-fake-discounts-are-shopping-events-worth-the-deal)
- **KnownHost (US, vendor):** about **30% of 65** anticipated Black Friday tech deals had price hikes beforehand, averaging **+20.5% (about $51)**. **[S]** [via search summary](https://www.itmagination.com/blog/analysis-finds-that-black-friday-discounts-are-just-an-illusion)
- **Walmart thesis (OSU):** 400 products tracked around Black Friday. Some prices exceeded their prior maximum by an average of 44%. **[S, thesis]** [Elsbree, Oregon State](https://ir.library.oregonstate.edu/downloads/w3763f83m)
- **Sweden (Prisjakt):** 13% of online prices rose more than 10% in October before Black Friday, which avoids the 30-day rule. **[S]** [Sweden Herald](https://swedenherald.com/article/tips-before-the-phenomenon-black-friday-or-black-period)
- **India malls:** 78% of products surveyed showed inflated-then-slashed pricing. This is a case study and not representative. **[S, academic]** [MDPI Sustainability 2019](https://www.mdpi.com/2071-1050/11/17/4637)

### Inferences
- Pre-event price inflation usually starts **3–6 weeks before** the event: early October for late-November Black Friday in Sweden, and early November in the EU sweep example. A 90-day median absorbs this better than a 30-day minimum. Keeping our window at 90 days, and **excluding the last N days (e.g. 30–45) before a known sale event** or down-weighting recent upward steps, would protect the reference. Egypt's events are White Friday (Amazon/noon/Jumia, November), Ramadan and the 11.11 / Yellow Friday sales.
- The Which? measure, "cheaper or the same within ±6 months", suggests a rule we can audit afterwards: log every alert, then after 6 months compute the share where the product was at or below that price at another time. That gives us our own measured false-positive rate.
- The rarity rule (≤15% of in-stock time at or below current) directly targets the Checkbook pattern of "perpetual sale". This research supports keeping it.

### Gaps
- No primary-source Which? report pages were fetched; numbers come from news coverage of the reports. I found no Which? study on Black Friday 2025 as of this research.
- I could not find any BEUC-branded prevalence study, or any Egypt/MENA study of fake discounts on amazon.eg, noon or Jumia.

## Q6. How deal-alert systems avoid false alerts (glitches, seller switches, outliers, minimum history, dedup, re-alerts)

### Takeaway
Little is published on how other systems do this. The documented mechanisms are:
- Keepa: per-price-type tracking (it never mixes the Buy Box with third-party offers), `-1` for out of stock (excluded rather than counted as zero), a minimum 10% delta, a 12-hour freshness limit, `isLowest90`, and reference-switching for Lightning and Warehouse deals.
- Google: validates the merchant's sale price against the landing page and applies a 5–90% band.
- Honey: excludes shipping and tax from drops.
- Communities: human review of price history, availability and stock, plus negative votes.

### Cited findings
- Keepa keeps out-of-stock as `-1` ("no offer was available"), and a delta of `0` means not computable. This separates missing data from price data. **[P]** [Keepa Deal Object](https://keepa.com/api-docs/deal-object.html)
- Keepa's deals feed only includes products "updated within the last 12 hours". Shorter `dateRange` windows "favor significant drops but may miss gradual ones". **[P]** [Keepa Browsing Deals](https://keepa.com/api-docs/deals.html)
- Keepa's `isLowestOffer` checks whether the price is the lowest of all New offers. `mustHaveAmazonOffer` lets users exclude marketplace-only deals. Both are seller-switch and seller-quality guards. **[P]** [Keepa Browsing Deals](https://keepa.com/api-docs/deals.html)
- Keepa's `isBackInStock` (back within 24 h) and `isOutOfStock` treat stock transitions as separate events, not as price drops. **[P]** [Keepa Browsing Deals](https://keepa.com/api-docs/deals.html)
- Google "regularly validates the sale price on your website". Annotations require the discount to be greater than 5% and less than 90% and the base price to have held for the required days. **[P]** [Google Merchant Center](https://support.google.com/merchants/answer/9017019?hl=en)
- Google's 30-day-low label uses the **total price including shipping**. Honey excludes shipping and tax. The two approach costs differently, so we should be explicit about which we use. **[P]** [Google Help](https://support.google.com/faqs/answer/10675605?hl=en), [Honey help](https://help.joinhoney.com/article/79-what-is-droplist)
- Slickdeals editors check availability and inventory before front-paging. Pepper temperatures can go negative when voters reject a deal. **[P]** [How Slickdeals Works](https://slickdeals.net/corp/how-slickdeals-works/); **[S]** [Apify Pepper](https://apify.com/northbell/pepper-deals-scraper)
- Practitioners hold that a sale price shown for more than 30 days stops being a "sale". **[S]** [Channable](https://www.channable.com/blog/Google-Shopping-sale-price-annotations)

### Inferences: concrete guard rules to consider
- **Seller switch (noon `SKU@seller`, Amazon Buy Box):**
  - Like Keepa, keep each seller's history separate.
  - Before alerting on a new seller's low offer, require the seller to have been seen at least once before, or compare against the product's min-across-sellers history (Keepa's Lightning/Warehouse approach).
- **Glitch confirmation:** for drops of 60% or more, re-fetch the product page and confirm the same price within a few minutes before sending, or send it labelled "possible error". Google's 90% ceiling suggests treating 90% or more as invalid by default.
- **Minimum history:** use about 30 days of in-stock history for full-confidence "typical" claims, matching the EU 30-day concept and Google's 30-of-200 rule. Our 14-day minimum for the all-time-low rule is shorter than every standard found. Consider 30 days, or label alerts with less than 30 days of history as "limited history".
- **Re-alert or dedup:**
  - Under the EU progressive-reduction logic, a further step down within the same campaign is still the same deal.
  - Re-alert only if the price falls a meaningful extra amount (e.g. at least 5–10% below the last alerted price), or if it returned above the reference and then dropped again.
  - Stop treating a price as a discount once it has lasted longer than the preceding higher price (CTSI rule of thumb), or more than 30 days (Google practitioner rule).
- **Stock transitions:** do not treat "back in stock at a lower price" as a drop unless the in-stock price history supports it. Our in-stock-only median already does this, consistent with Keepa's `-1` handling.
- **Shipping:** decide whether the alert price includes delivery fees. Google uses the total price; Honey uses the item price.

### Gaps
- No tracker (Keepa, camelcamelcamel, PriceSpy, Honey, Pricena) publishes its outlier filtering, glitch confirmation, dedup or re-alert cooldown logic. The guard rules above are inferred from documented data-model choices and legal standards, not copied from a documented system.
- I found no published false-positive rates for any commercial price-alert service.
