# noon's pricing machinery and Egypt's pricing environment (research notes, 28 Oct 2026)

Notes for the price tracker (noon catalog API, SKU@seller offers, amazon.eg, Jumia, B.TECH, 2B, Carrefour etc.).
Source types are marked: **[official]** = noon / bank / government / the shop's own page; **[news]** = Egyptian or regional
press; **[3rd-party]** = blogs, coupon / affiliate sites, seller-tool vendors (weaker). Facts dated before 2025 are marked
"(older)". "Project observation" = something this repo's own docs record, not an outside source.

## 1. noon marketplace mechanics: which offer is shown first, fulfilment, how sellers set prices

### Takeaway
noon lists each product once and lets several sellers attach offers; one offer wins the "Buy Box" (shown first, "Add to
Cart"), the others sit behind "View All Offers". The winner is chosen from price plus seller performance, and for
Fulfilled-by-noon (FBN) offers only from **price and customer-initiated returns**, so the shown offer is not always the
cheapest. Sellers set prices themselves in Seller Lab inside a min/max price range; I found no official noon repricer.

### Cited Findings
- [official] noon does not duplicate products: "a specific product is presented to our customers with offers from different sellers. An offer is the combination of the seller's price and chosen processing time." — [noon Seller Help Center, "Winning the buy box"](https://support.noon.partners/portal/en/kb/articles/winning-the-buy-box)
- [official] "The Buy Box is the button on the product display page that allows a customer to directly add a product to their cart ... the best offer wins the Buy Box"; offers that do not win are listed under "View All Offers". — [same page](https://support.noon.partners/portal/en/kb/articles/winning-the-buy-box)
- [official] Buy Box parameters: I. Offer Price ("maintaining competitive prices will continue to play an important role"), II. Seller Reviews and Ratings (stars), III. Seller Score (warehouse performance: seller cancellations, respecting chosen processing time), IV. Customer-initiated returns (fake/defective items), V. Fast fulfilment (short processing times). No weights are published. — [same page](https://support.noon.partners/portal/en/kb/articles/winning-the-buy-box)
- [official] "Cross-dock B2B Offer Rating will be affected by all of the below parameters. FBN Offer Rating will be affected by two parameters, the Offer Price, and Customer Initiated Returns." — [same page](https://support.noon.partners/portal/en/kb/articles/winning-the-buy-box)
- [official] Seller-chosen processing time per product ranges "from same day up to 4 days". — [same page](https://support.noon.partners/portal/en/kb/articles/winning-the-buy-box)
- [official] Sellers must maintain a per-product (PSKU) **minimum and maximum price range**, uploaded via a "Partner SKU price range template from Seller Lab"; noon also sets its own range per catalogue product (NSKU): rejection reasons "Not in NSKU price range" (noon hasn't set a range) and "Not in PSKU price range" (seller hasn't). — [noon Seller Help Center, "How can I fix listing errors in promotions?"](https://helpcenter.noon.partners/en/category/promotions-and-deals/how-can-i-fix-listing-errors-in-promotions)
- [3rd-party] A seller guide summarises the same factors and repeats that a lower price alone does not guarantee the Buy Box; another guide mentions a noon feature called "Win my Box" (unverified against noon docs). — [syncme.io guide](https://syncme.io/blog/sell-on-noon-complete-guide), [Worldwide Formations](https://www.worldwideformations.com/how-to-sell-on-noon-com/)
- [3rd-party] Search for noon-specific automated repricing tools returned only Amazon repricers; nothing documented for noon. — (search, no noon result; e.g. [Jungle Scout repricer list](https://www.junglescout.com/resources/articles/amazon-repricer-tools/))
- Project observation: noon products in the catalog API are keyed `SKU@seller` and one product has several sellers at very different prices (CLAUDE.md / docs).

### Inferences
- Because FBN offers are ranked only on price and returns while cross-dock offers also need ratings/score, the buy-box
  offer can be a slightly dearer FBN offer. For "real discount" logic the tracker should compute the **minimum across all
  sellers of an SKU** (and its history) as well as per-offer history; a "drop" on one seller's offer that is still above
  another seller's long-standing price is not a real market low.
- A new seller attaching an offer below the incumbent will look like a "drop" for the SKU even though no existing offer
  changed; per-offer history (SKU@seller) is the right primary key, the per-SKU min is the right reference.
- The seller-set min/max PSKU range bounds how low an offer (and any deal price) can go without the seller editing the
  range, so extreme glitch prices on noon are less likely than on shops without such guard rails (inference, untested).

### Gaps
- No official description found of noon Express / "Fulfilled by noon" badge rules, express delivery cut-offs, or how
  "Express" interacts with the Buy Box beyond the FBN rule above.
- No source on noon's own retail (1P) pricing policy, or on noon price-matching competitors (search found nothing).
- No official noon repricing / "auto price" tool found; whether Seller Lab offers rule-based repricing is unknown.
- Buy Box weights / tie-breaking (e.g. how much cheaper a cross-dock offer must be to beat an FBN offer) are not published.

## 2. noon discount and badge rules: was-prices, deal rules, badges, Yellow Friday, flash sales, fake discounts

### Takeaway
noon's deal machinery is price-band based: a deal or coupon price must be **below the offer's current price and inside
the min/max range**; mega deals are scheduled within the next 30 days and category deals must be set ≥4 days ahead.
I found **no official definition** of the "Lowest price in 30 days / a year" badges, of "Deal" vs "Mega Deal", or of what
the strikethrough "was" price must represent; there is no public evidence of a "lowest in N days" eligibility rule.

### Cited Findings
- [official] Promotion rejection reason "Deal price greater than current price": "The discounted price should be less than the marketplace price and within the min and max price range." Other reasons: deal not live / inactive in the period, partner not eligible (business model, category), product family not valid for the deal, PSKU not live, "PSKU already enrolled". — [noon Seller Help Center](https://helpcenter.noon.partners/en/category/promotions-and-deals/how-can-i-fix-listing-errors-in-promotions)
- [official, from search snippet; page did not render for me] For noon mega deals "Deal price should be lower than the current offer price but should be more than the SKU Price Minimum"; "Deal start_date and end_date should be within the upcoming 30 days period"; for category deals "products must be added to a category deal at least four days before it starts". — [noon Seller Help Center, "How can I participate in noon mega deals?"](https://support.noon.partners/portal/en/kb/articles/how-can-i-participate-in-noon-mega-deals)
- [official, from search snippet] Coupon / promo-code participation is rejected if the pre-coupon price "is less than the SKU Price Minimum suggested by noon or more than the Current Price of the SKU". — [noon Seller Help Center, coupons](https://helpcenter.noon.partners/en/category/promotions-and-deals/how-to-participate-in-coupons-or-promo-codes)
- [3rd-party] A UAE/KSA seller guide says noon suggests an "enrolment price" per product and the deal tag is granted only at or below it; products that cannot reach it can use a "Price Drop" that goes live immediately. Another blog claims seasonal events require 15–30% minimum discounts; **not confirmed** in noon docs. — [Biteora, Noon Seller Pricing Guide 2026](https://biteora.com/en/blog/noon-seller-pricing-guide-2026/), [Ugly Creatives, Yellow Friday 2026 for sellers](https://uglycreatives.com/noon-yellow-friday-sale-2026/)
- [official listing pages] noon runs "Lowest Prices" landing pages built on these badges; the KSA page shows both "Lowest price in 30 days" and "Lowest price in 7 days". — [noon KSA lowest-price page](https://www.noon.com/saudi-en/lowest-price-saudi/), [noon UAE lowest-price page](https://www.noon.com/uae-en/lowest-price-uae/)
- Project observation: noon's catalog API exposes the badge as a "Price drop" filter `f[min_offer_price]` with values `7_days`, `30_days`, `365_days`, and a `deal_tag` filter (`deals-generic`, `mega-deal`); "lowest price in 30 days" is on 19% of the catalogue and "lowest in 365 days" on 8% (docs/CATEGORIES.md, docs/GUIDE.md).
- [news, region incl. Egypt] noon Yellow Friday 2025 ran **20–30 November 2025** (11 days), "up to 90 per cent off". — [Gulf Business, 20 Nov 2025](https://gulfbusiness.com/yellow-friday-noon-90-per-cent-off-meals-from-dh1/); a Dubai-datelined release for UAE, KSA **and Egypt** gives the same 20–30 Nov window with up to 80% off and BNPL options — [Zawya press release](https://www.zawya.com/en/press-release/companies-news/yellow-friday-sale-launches-with-10-days-of-unbeatable-deals-wigzwq37)
- [news, older: 21 Nov 2022] noon Egypt Yellow Friday 2022 ran 21–27 Nov; claimed caps: electronics & home appliances up to 65%, mobiles up to 50%, fashion/beauty up to 80%; "daily flash deals" (عروض البرق اليومية) mentioned but no hourly schedule. — [El Mogaz, 21 Nov 2022](https://www.elmogaz.com/745091)
- [news, older] An earlier Egyptian Gazette piece describes Yellow Friday "running until midnight on 28 November"; first noon Yellow Friday in Egypt was 2019. — [Egyptian Gazette](https://egyptian-gazette.com/technology/noon-coms-craziest-yellow-friday-sale-is-here-with-up-to-80-off/), [MENAFN / Daily News Egypt](https://menafn.com/1099247593/Noon-launches-yellow-Friday-discounts-in-Egypt)
- [news, UAE] noon 11.11 2025 ran "from the evening of November 7 to 12" (UAE coverage, Egypt dates unconfirmed); an older year ran "until midnight on November 12th". — [Khaleej Times](https://www.khaleejtimes.com/uae/1111-sale-dh1-deals-90-discounts-gifts), [Absolute Geeks](https://www.absolutegeeks.com/?p=50372)
- [3rd-party] Coupon sites give conflicting 2025 Egypt Yellow Friday dates (21–27, 21–28, 23–30 Nov) and say 2026 runs ~20–30 Nov; none official. — [alcoupon Egypt](https://egypt.alcoupon.com/en/blog/noon-yellow-friday-sale-dates), [yellowfridayoffers.com](https://yellowfridayoffers.com/%D9%83%D9%84-%D9%85%D8%A7%D8%AA%D8%B1%D9%8A%D8%AF-%D9%85%D8%B9%D8%B1%D9%81%D8%AA%D9%87-%D8%B9%D9%86-%D8%A7%D9%84%D8%AC%D9%85%D8%B9%D8%A9-%D8%A7%D9%84%D8%B5%D9%81%D8%B1%D8%A7%D8%A1-%D9%85%D9%86-%D9%86/)
- [news, older: 28 Nov 2022] Egyptian shoppers documented inflated "was" prices on Black Friday: a Jumia power socket bought months earlier at EGP 245 shown as "340 → 245"; a kitchen appliance at EGP 3,700 before the sale shown as "4,500 → 3,700". No data on how common this is; the CPA and Ministry did not respond to the reporter. — [Egyptian Streets](https://egyptianstreets.com/2022/11/28/real-discounts-or-fake-deals-a-look-at-black-friday-in-egypt/)
- [official, 27 Oct 2026] The CPA spokesman names the exact trick: "زيادة السعر أولا ثم الإعلان عن خفضه لاحقا" (raise the price first, then announce a cut), and says complaints in sales seasons include prices raised before items enter promotions. — [Al-Mawqa', 27 Oct 2026](https://almawq3.com/%D8%A8%D8%AA%D8%B2%D9%88%D9%91%D8%AF-%D8%A7%D9%84%D8%B3%D8%B9%D8%B1-%D9%88%D8%A8%D8%B9%D8%AF%D9%8A%D9%86-%D8%AA%D8%B9%D9%85%D9%84-%D8%AE%D8%B5%D9%85%D8%9F-%D8%AD%D9%85%D8%A7%D9%8A%D8%A9-%D8%A7%D9%84/)

### Inferences
- noon's rule "deal price < current offer price" means the strikethrough on a deal is the seller's **own current price at
  enrolment**, which the seller controls; nothing found forces that price to have been charged for any period. So the
  noon strikethrough should be treated like Amazon's "List:" (claimed, not verified) and the tracker's own history stays
  the reference.
- Deals are scheduled in advance (≤30 days ahead; category deals locked ≥4 days ahead) with start and end dates, so drops
  cluster at deal start times; the API's `timeRangeFilters` (`STARTS_TODAY`, `ENDS_TODAY`) are the natural hook for a
  "deal starting today" fast lane.
- "Lowest price in 30 days" on ~19% of the catalogue is too common to mean "real deal" by itself; it is consistent with
  being a mechanical comparison against the offer's own 30-day price series (which a seller can game by a short price
  rise). "Lowest in a year" (8%) is a stronger but still self-referential signal. Treat both as corroboration only.
- In Egypt's inflationary environment (section 4), a product whose price has risen every few weeks will hit "lowest in
  30 days" after any small give-back; this further weakens the 30-day badge as a deal signal.

### Gaps
- No official noon definition found for: the "Lowest price in 7/30 days / a year" computation (which price series,
  per offer or per product, whether deal prices count); "Deal" vs "Mega Deal" badge criteria; "Selling out fast" and
  "N sold recently" thresholds; what the strikethrough "was" price must represent.
- No noon Egypt official 2025 Yellow Friday dates (only regional releases), no 2026 dates yet, and no evidence on whether
  noon Egypt releases deals hourly or at fixed times of day during Yellow Friday.
- No reports found of the CPA acting against noon specifically for fake discounts.
- No noon Egypt Ramadan-sale or 11.11 Egypt-specific dates found.

## 3. Payment-side discounts not visible in the listing price

### Takeaway
Bank and BNPL offers are real but small and capped: the CIB noon card gives 5% capped at EGP 150 per order (twice a
month); historical event offers were "extra 20% up to EGP 250". Installment offers are 0% interest but with admin fees
(6% for 6 months up to 33% for 36 months on CIB). For electronics above a few thousand EGP these move the effective price
by at most ~1–5%, so they should not drive alerts.

### Cited Findings
- [official, via search snippet of the bank page] CIB noon credit card: "instant 5% discount per transaction on noon website/app, capped at EGP 150", "Limited to a maximum of two times per month per customer"; 1.5 bonus points per EGP outside noon. — [CIB noon credit card](https://www.cibeg.com/en/personal/cards/credit/cib-noon); launch: [CIB newsroom](https://www.cibeg.com/en/newsroom/news/cibxnoon), [Daily News Egypt, 11 Jun 2023](https://www.dailynewsegypt.com/2023/06/11/cib-launches-special-credit-card-with-exclusive-offers-on-noon/)
- [official, via search snippet] CIB 0% installments on noon.com valid 01/01/2026–31/12/2026; admin fee by tenor from 6% (6 months) to 33% (36 months); minimum EGP 500; credit cards only. — [CIB offers](https://www.cibeg.com/en/personal/cib-offers)
- [3rd-party] NBE credit cards: 0% interest over 6 months on noon, minimum EGP 500, listed valid to 31 May 2026 (unconfirmed by NBE). — [Barakatalan noon Egypt coupons](https://www.barakatalan.com/store/noon-egypt-coupons/)
- [news, older: Yellow Friday 2022] NBE cards 18 months interest-free; Visa cards extra 20% off "up to EGP 250"; Tabby 4 months and valU 12 months interest-free. — [El Mogaz, 21 Nov 2022](https://www.elmogaz.com/745091)
- [news] Amazon.eg White Friday 2025 advertised "extra discounts and instant bank offers" and Prime-exclusive deals; Prime costs EGP 29/month or EGP 249/year; Prime members got 24h early access from midnight 20 Nov (Cairo). — [Al Mal News](https://almalnews.com/2085755/%D8%A3%D9%85%D8%A7%D8%B2%D9%88%D9%86-%D9%85%D8%B5%D8%B1-%D8%AA%D8%B7%D9%84%D9%82-%D8%A3%D9%83%D8%A8%D8%B1-%D9%85%D9%88%D8%B3%D9%85-%D8%AA%D8%AE%D9%81%D9%8A%D8%B6%D8%A7%D8%AA/), [Alamrakamy](https://www.alamrakamy.com/article/83396)
- [3rd-party] Cashback portals advertise up to 19.2% cashback on Amazon Egypt via ShopCash (affiliate claim). — [eDahab](https://edahabapp.com/article/1772)
- [3rd-party] Affiliate promo codes for noon Egypt (e.g. extra "up to 20% discount or cashback") circulate; validity unconfirmed. — [alcoupon noon Egypt codes](https://egypt.alcoupon.com/en/discount-codes/noon/noon-yellow-friday-coupon-code-2024)

### Inferences
- A capped 5%/EGP 150 bank discount equals 5% only up to EGP 3,000; on a EGP 30,000 phone it is 0.5%. Event caps like
  "20% up to EGP 250" are similar. The tracker can ignore payment-side discounts for alerting, or at most mention a known
  card offer in the alert text.
- "0% installments" with 6–33% admin fees are not discounts; never fold them into an effective price.

### Gaps
- No current (2026) list of noon Egypt bank offers (NBE, Banque Misr, etc.) or noon credits/cashback rates was found.
- No data on how often noon Egypt promo codes apply to electronics, or typical code values in 2026.

## 4. Egypt market context affecting price history (EGP, inflation, phones, import rules)

### Takeaway
Egyptian prices drift upward fast: urban inflation 13.9% y/y in Sep 2026 with 1.3% m/m, the pound weakened from ~49.3
(late June 2026) to ~53.5–54.5 per USD by mid/late October 2026, and **locally made phones rose ~55% since January
2026 in steps of 5–7% every 10–20 days**. Discrete step-ups are the norm, so long-window references (a year) overstate
"normal" prices less than they understate them: old lows are often unreachable, and a "drop" can just undo a recent rise.

### Cited Findings
- [news] Devaluation history: Nov 2016 float (pound lost 48%, LE18.16 end-Nov); 21 Mar 2022 −5%; 27 Oct 2022 LE19.53→24.17 (−14%); Jan 2023 LE24.63→30.8 (~−25%); 6 Mar 2024 float to ~LE50.1 within days (LE49.10 a week later). — [Ahram Online, 14 Mar 2024](https://english.ahram.org.eg/News/519335.aspx)
- [news] 6 Mar 2024: CBE raised its key rate by an unprecedented 6 points to 27.25% alongside the float; the pound fell to ~LE45 within an hour (Mada Masr) and around LE50 thereafter; analysts said 50–60% of the economy already priced at parallel rates since Apr 2023. — [US News/Reuters, 6 Mar 2024](https://money.usnews.com/investing/news/articles/2024-03-06/for-struggling-egyptians-devaluation-promises-price-pain), [Mada Masr](https://www.madamasr.com/en/2024/03/06/news/economy/long-waited-devaluation-arrives-value-of-egyptian-pound-falls-to-le45-in-hour/), [Middle East Eye](https://www.middleeasteye.net/news/egypt-lets-market-rate-set-value-currency-attempt-stabilise-economy)
- [news] EGP in 2026: CBE 49.33/49.43 on 29 Jun 2026 (dollar below 50); 51.07/51.21 on 1 Aug; 50.14/50.24 on 17 Aug; 51.66/51.76 on 28 Sep; ~52 on 7 Oct; 53.00/53.18 on 16 Oct; 53.42/53.52 on 17 Oct. — [Al Khaleej, 29 Jun 2026](https://www.alkhaleej.ae/2026-06-29/اقتصاد/أسواق-عربية-وعالمية/سعر-الدولار-مقابل-الجنيه-المصري-الاثنين-29-يونيو-2026-العملة-الأمريكية-تواصل-التراجع), [Akhbar El Yom, 1 Aug 2026](https://m.akhbarelyom.com/news/newdetails/4858997/1/سعر-الدولار-الأمريكي-أمام-الجنيه-المصري-/amp), [Amwal Al Ghad, 17 Aug 2026](https://en.amwalalghad.com/?p=230243), [Al Khaleej, 28 Sep 2026](https://www.alkhaleej.ae/2026-09-28/اقتصاد/أسواق-عربية-وعالمية/سعر-الدولار-في-مصر-اليوم-الاثنين-28-سبتمبر-2026ماذا-ينتظر-الجنيه), [Arab Finance, 7 Oct 2026](https://www.arabfinance.com/ar/news/newdetails/أسعار-الدولار-مقابل-الجنيه-اليوم-الأربعاء-7-أكتوبر-2026), [Soutalomam, 17 Oct 2026](https://soutalomam.net/%d8%b3%d8%b9%d8%b1-%d8%a7%d9%84%d8%af%d9%88%d9%84%d8%a7%d8%b1-%d8%a7%d9%84%d9%8a%d9%88%d9%85-%d8%a7%d9%84%d8%b3%d8%a8%d8%aa-17-%d8%a3%d9%83%d8%aa%d9%88%d8%a8%d8%b1-2026-%d8%a3%d9%85%d8%a7%d9%85-%d8%a7/); ~54.5 in main banks per the phone traders' division — [Akher News](https://akher.news/80122/); a converter shows 54.38 on 22 Oct (unofficial) — [moneyexchangerate.org](https://moneyexchangerate.org/currencyexchange/usd/egp/1). No official CBE explanation for the October slide found.
- [news / official data] Urban headline inflation (CAPMAS): Sep 2025 11.7%; Aug 2026 14.5%; **Sep 2026 13.9%** (lowest since Feb 2026), monthly +1.3% vs 0.3% forecast; monthly June 2026 −0.4%, Jul/Aug ~0; core 14.1% (Aug 14.9%); food 7.2% y/y; housing & utilities 35.2% y/y; transport 24.3%; home furnishings 17.4% y/y. Monthly urban prints Sep–Dec 2025: 1.8%, 1.8%, 0.3%, 0.2%. Nationwide headline 12.8%. — [EnterpriseAM, 11 Oct 2026](https://enterpriseam.com/egypt/2026/10/11/urban-inflation-eased-to-13-9-in-september-even-as-monthly-prices-continue-to-pick-up/), [Daily News Egypt, 10 Oct 2026](https://www.dailynewsegypt.com/2026/10/10/egypts-annual-urban-inflation-eases-to-13-9-in-september/), [Business Today Egypt](https://www.businesstodayegypt.com/Article/1/7983/Egypt-s-annual-urban-inflation-eases-to-13-9-in-), [Zawya](https://www.zawya.com/en/economy/egypts-annual-urban-inflation-slows-to-13.9-in-september-2026-cbe-1767887)
- [news] Outlook: CBE projects FY2026/27 average inflation 16.6%, target 7% ±2 by 2H 2027; HC forecasts Oct 2026 m/m +2.1% with a ~10% fuel price rise; Ahly Pharos flags a Q4 2026 spike to 16–16.5%; CBE corridor top 20%, rates held five meetings in a row; MPC meets 29 Oct and 17 Dec 2026. — [EnterpriseAM, 11 Oct 2026](https://enterpriseam.com/egypt/2026/10/11/urban-inflation-eased-to-13-9-in-september-even-as-monthly-prices-continue-to-pick-up/)
- [news] Phones: locally made phone prices up ~55% since start of 2026, raised "every 10 to 20 days by roughly 5% to 7% each time"; drivers named: RAM/ROM chip costs and AI-driven chip demand; cheapest phone ~EGP 7,000 vs ~4,500 in January. — [Banker, 3 Oct 2026](https://www.banker.news/154827)
- [news] Samsung mid-range up to 78.1% since start of 2026 (to September), with rises at Xiaomi, Oppo, Realme. — [Misr Elaan](https://misrelaan.com/article/135263/%D8%A7%D8%B1%D8%AA%D9%81%D8%A7%D8%B9-%D8%A3%D8%B3%D8%B9%D8%A7%D8%B1-%D8%A7%D9%84%D9%85%D9%88%D8%A8%D8%A7%D9%8A%D9%84%D8%A7%D8%AA-%D8%A7%D9%84%D9%85%D8%B5%D9%86%D8%B9%D8%A9-%D9%81%D9%8A-%D9%85%D8%B5%D8%B1-%D9%8A)
- [news] 22 Oct 2026: phone traders' division expects imported phones (incl. iPhone) to rise 5–7% on the dollar move; Samsung agents issued updated price lists for Galaxy A/S models and chargers dated 22 Oct 2026; some companies add a hedging margin for FX swings; sales down ~20% over six months. — [Masrawy, 22 Oct 2026](https://www.masrawy.com/news/news_economy/details/2026/10/22/3066897/%D8%B4%D8%B9%D8%A8%D8%A9-%D8%A7%D9%84%D9%85%D8%AD%D9%85%D9%88%D9%84-%D8%B2%D9%8A%D8%A7%D8%AF%D8%A9-%D9%85%D8%B1%D8%AA%D9%82%D8%A8%D8%A9-%D9%81%D9%8A-%D8%A3%D8%B3%D8%B9%D8%A7%D8%B1-%D8%A7%D9%84%D9%87%D9%88%D8%A7%D8%AA%D9%81-%D8%A7%D9%84%D9%85%D8%B3%D8%AA%D9%88%D8%B1%D8%AF%D8%A9-%D9%85%D9%86-5-%D8%A5%D9%84%D9%89-7-), [Cairo24](https://www.cairo24.com/2516553), [Cairo24](https://www.cairo24.com/2516439), [Akher News](https://akher.news/80122/)
- [official / news] Phone import tax: from 1 Jan 2025 phones brought from abroad pay customs/taxes (not retroactive) via a registration system; the one-time levy is reported as ~38.5% of device value (sources vary 37.5–38.5%), replacing a 10% duty; the one-phone traveller exemption ended at noon on 21 Jan 2026; Egyptians abroad got a window extended from 90 to 120 days from 1 Apr 2026; unregistered/unpaid phones are cut off after 90 days (Telephony app). — [SIS (official)](https://sis.gov.eg/en/media-center/news/egyptians-to-start-paying-taxes-on-imported-mobiles-1-january-2025-not-retroactively/), [Ahram Online explainer](https://english.ahram.org.eg/News/538141.aspx), [Egyptian Streets, 20 Jan 2026](https://egyptianstreets.com/2026/01/20/egypt-ends-customs-exemption-on-imported-phones-carried-by-passengers/), [The National, 21 Jan 2026](https://www.thenationalnews.com/business/economy/2026/01/21/egypts-phone-import-tax-branded-unfair-as-grace-period-comes-to-an-end/), [Lexis Middle East, 25 Mar 2026](https://www.lexismiddleeast.com/news/2026-03-25_41/en)
- [news] Egyptian Streets estimate: iPhone 17 ~EGP 63,000 before customs vs ~EGP 86,625 with 37.5% duty (estimate). — [Egyptian Streets, 6 Mar 2026](https://egyptianstreets.com/2026/03/06/how-egypts-phone-import-rules-are-hitting-everyday-users/)

### Inferences
- Implied drift: 13.9% y/y ≈ 1.1% per month on average; phones ≈ +5% per month in 2026 (55% in 9 months) in discrete
  5–7% steps. A price that is 5–7% below its 30-day peak may simply be the pre-hike price, not a discount.
- Rules to consider: (a) detect **step increases** (≥5% jumps that hold) as "rebases" and measure discounts against the
  post-rebase level only if it held for a minimum time (e.g. ≥7 days), otherwise against the pre-rebase level; (b) prefer a
  30–60 day time-weighted reference over a 365-day one for phones/electronics; (c) a new all-time low within a short
  window after an FX shock is rare and therefore meaningful; (d) when the USD/EGP rate jumps (as in Oct 2026), expect
  waves of list-price increases at all shops within days to weeks, which will make subsequent "drops" look larger.
- After a big FX move, shops may raise prices before the November sales; the CPA's 1–2 month look-back (section 5) is a
  reasonable reference horizon for judging November discounts.

### Gaps
- No hard data found on how fast Egyptian e-tailers (noon, Amazon.eg, B.TECH, 2B) repriced electronics after the March
  2024 float (days vs weeks); only qualitative statements.
- No official statement on the cause of the October 2026 pound weakening; converter figures (54.38) are unofficial.
- Import restrictions (e.g. 2022–2023 letters-of-credit rules) were not researched in depth.
- A "50% customs duty on iPhones" headline exists ([Egypt Independent](https://www.egyptindependent.com/government-source-elaborates-on-egypts-50-customs-duty-on-iphones/)) but was not opened; its relation to the 38.5% levy is unverified.

## 5. Egyptian law on discounts and sale seasons

### Takeaway
Law 181/2018 treats "price reductions contrary to the truth" as misleading information; the CPA's White Friday 2026 rules
(27 Oct 2026) require showing the price **before and after** the discount and the real discount %, offer duration and
terms; inspectors will compare with **invoices and prices of the previous one to two months**; penalties up to EGP 2
million or (per most reports) twice the product value, whichever is greater. Physical-store seasonal sales (أوكازيون)
need prior approval from Ministry of Supply directorates.

### Cited Findings
- [law text, unofficial copy] Law 181/2018 Article 1 lists among misleading information "وجود تخفيضات على السعر على خلاف الحقيقة" (price reductions contrary to the truth); suppliers must announce prices clearly, inclusive of taxes, per the executive regulations. — [alberonsy.com law text](https://alberonsy.com/%D9%82%D8%A7%D9%86%D9%88%D9%86-%D8%B1%D9%82%D9%85-181-%D9%84%D8%B3%D9%86%D8%A9-2018-%D8%A8%D8%A5%D8%B5%D8%AF%D8%A7%D8%B1-%D9%82%D8%A7%D9%86%D9%88%D9%86-%D8%AD%D9%85%D8%A7%D9%8A%D8%A9-%D8%A7%D9%84/), [egyptlaw.ai](https://egyptlaw.ai/ar/laws/eg-egyptian-consumer-protection-law-law-181-2018)
- [official gazette metadata] Executive regulations: Prime Minister Decree 822/2019, published 1 Apr 2019 (Official Gazette no. 13 bis (A)), effective 2 Apr 2019; Article 7 deals with displaying all-inclusive prices on the item/label. — [Eastlaws](https://site.eastlaws.com/GoogleSearch/Tash?RecID=1937298), [manshurat.org text](https://manshurat.org/node/70791), [Masrawy, 2 Apr 2019](https://www.masrawy.com/news/news_egypt/details/2019/4/2/1543031/-%D9%81%D8%AA%D8%B1%D8%A9-%D8%A5%D8%B1%D8%AC%D8%A7%D8%B9-%D9%88%D8%A7%D8%B3%D8%AA%D8%A8%D8%AF%D8%A7%D9%84-%D9%84%D9%84%D8%B3%D9%84%D8%B9-%D9%85%D8%AF%D8%A8%D9%88%D9%84%D9%8A-%D9%8A%D8%B5%D8%AF%D8%B1-%D8%A7%D9%84%D9%84%D8%A7%D8%A6%D8%AD%D8%A9-%D8%A7%D9%84%D8%AA%D9%86%D9%81%D9%8A%D8%B0%D9%8A%D8%A9-%D9%84%D9%80-%D8%AD%D9%85%D8%A7%D9%8A%D8%A9-%D8%A7%D9%84%D9%85%D8%B3%D8%AA%D9%87%D9%84%D9%83-)
- [news quoting CPA, 27 Oct 2026] White Friday 2026 controls (CPA head Ibrahim El-Sigini): announce clearly the price before and after the discount ("ضرورة الإعلان بوضوح عن السعر قبل التخفيض وبعده"), state the real discount %, terms and duration; no sham discounts; online offers covered by the same rules and must show offer duration, warranty and extra fees; invoices required (Art. 10); return/exchange rights continue during sales; competitions must be notified ≥3 days ahead; central operations room for the whole of November plus an "advertising observatory" monitoring platforms and social media; penalties "تصل إلى مليوني جنيه أو مثلي قيمة المنتج محل المخالفة، أيهما أكبر". — [Amwal Al Ghad, 27 Oct 2026](https://amwalalghad.com/2026/10/27/%D8%AD%D9%85%D8%A7%D9%8A%D8%A9-%D8%A7%D9%84%D9%85%D8%B3%D8%AA%D9%87%D9%84%D9%83-%D9%8A%D8%B9%D9%84%D9%86-%D8%B6%D9%88%D8%A7%D8%A8%D8%B7-%D8%A7%D9%84%D8%AC%D9%85%D8%B9%D8%A9-%D8%A7%D9%84%D8%A8/), [Al Borsa News](https://www.alborsaanews.com/2026/10/27/2002197), [Cairo24](https://www.cairo24.com/2518994), [El Watan](https://www.elwatannews.com/news/details/8385554)
- [news quoting CPA spokesman Islam El-Gazzar, 27 Oct 2026] Inspectors will examine invoices and records of previous prices and **review prices from the past one or two months**, in shops, e-commerce sites and social-media sellers; his wording of the penalty is "up to EGP 2 million or the value of the goods" (differs from "twice the value" above). — [Al-Mawqa'](https://almawq3.com/%D8%A8%D8%AA%D8%B2%D9%88%D9%91%D8%AF-%D8%A7%D9%84%D8%B3%D8%B9%D8%B1-%D9%88%D8%A8%D8%B9%D8%AF%D9%8A%D9%86-%D8%AA%D8%B9%D9%85%D9%84-%D8%AE%D8%B5%D9%85%D8%9F-%D8%AD%D9%85%D8%A7%D9%8A%D8%A9-%D8%A7%D9%84/), [Dostor](https://www.dostor.org/5754641)
- [official, via news] Winter sales season 2026 (الأوكازيون الشتوي): Ministerial Decree 14/2026 opened the first seasonal clearance from Mon 9 Feb 2026 for one month, **each shop limited to two weeks**; Decree 48/2026 extended it to 21 Mar 2026; participating shops need prior approval from their supply directorate and must show each item's price before and after the cut. — [Dostor](https://www.dostor.org/5414028), [Al Masdar](https://www.almasdar.com/167816), [Youm7, 19 Mar 2026](https://www.youm7.com/story/2026/3/19/%D9%86%D9%87%D8%A7%D9%8A%D8%A9-%D8%A7%D9%84%D8%A3%D9%88%D9%83%D8%A7%D8%B2%D9%8A%D9%88%D9%86-%D8%A7%D9%84%D8%B4%D8%AA%D9%88%D9%89-2026-%D9%81%D9%89-%D9%87%D8%B0%D8%A7-%D8%A7%D9%84%D9%85%D9%88%D8%B9%D8%AF/7345815)
- [news] CPA received 31,600+ e-commerce complaints in 2024 (~95% resolved) and launched a "Regulated E-Commerce" initiative; complaints hotline 19588. — [Business Today Egypt](https://www.businesstodayegypt.com/amp/1/5769/Consumer-Protection-Agency-launches-“Regulated-E-Commerce”-initiative-to-address)

### Inferences
- The regulator's own test for a fake discount is "was the before-price actually charged in the last 1–2 months?". That
  maps directly onto a tracker rule: a claimed was-price is credible only if our own history shows it was the selling
  price for a meaningful share of the last 30–60 days; flag offers whose price rose within ~60 days before a sale and then
  "dropped" back (the CPA's named trick).
- The law requires showing before/after prices, so Egyptian shops will almost always show a strikethrough during
  November; its mere presence carries no information.

### Gaps
- Exact article of Decree 822/2019 governing discount announcements (and whether it sets a reference period) not found;
  the full text is at manshurat.org/lawhub but was not read.
- The statutory penalty article of Law 181/2018 for misleading discounts not verified (reports conflict: "value" vs
  "twice the value").
- No documented CPA enforcement case against noon, Amazon.eg or Jumia for fake discounts was found.
- Whether online-only sales (White Friday) need a Ministry of Supply permit like physical أوكازيون sales: not found
  (the 27 Oct 2026 CPA rules mention no permit). The summer أوكازيون dates were not researched.

## 6. Egyptian e-commerce sale calendar and timing

### Takeaway
November is the dominant season: Jumia's month-long Black Friday (2025: from 30 Oct 22:00 to 30 Nov, weekly Friday
peaks), Amazon.eg and noon's ~11-day White/Yellow Friday (2025: 20–30 Nov), and 11.11 (Amazon.eg 7–12 Nov 2025).
White Friday 2026 is Friday 27 Nov, Cyber Monday 30 Nov. Starts happen at midnight or in the evening Cairo time.

### Cited Findings
- [news/official release] Amazon.eg White Friday 2025: 20–30 Nov 2025, up to 70% off across 30+ categories; Prime early access 24h starting midnight 20 Nov Cairo time. — [Arab Finance](https://www.arabfinance.com/en/News/newdetails/20344), [Fintech Gate, 19 Nov 2025](https://fintechgate.net/2025/11/19/%D8%A3%D9%85%D8%A7%D8%B2%D9%88%D9%86-%D9%85%D8%B5%D8%B1-%D8%AA%D8%B7%D9%84%D9%82-%D8%A3%D9%83%D8%A8%D8%B1-%D9%85%D9%88%D8%B3%D9%85-%D8%AA%D8%AE%D9%81%D9%8A%D8%B6%D8%A7%D8%AA-%D9%81%D9%8A/), [Alamrakamy](https://www.alamrakamy.com/article/83396)
- [news] Amazon.eg 11.11 2025: 7–12 Nov 2025. — [Follow ICT](https://followict.news/en/amazon-eg-announces-11-11-sale-event-with-hundreds-of-thousands-of-deals-from-november-7-to-12/)
- [news] noon Yellow Friday 2025: 20–30 Nov (UAE/KSA/Egypt). — [Gulf Business](https://gulfbusiness.com/yellow-friday-noon-90-per-cent-off-meals-from-dh1/), [Zawya](https://www.zawya.com/en/press-release/companies-news/yellow-friday-sale-launches-with-10-days-of-unbeatable-deals-wigzwq37)
- [news] Jumia Egypt Black Friday 2025 ("Best Deals of the Year"): from 30 Oct 2025 at 10:00 PM to 30 Nov; Friday drops 31 Oct, 7, 14, 21 Nov and the biggest on Black Friday 28 Nov; plus an 11.11 event. — [Economic Key, 29 Oct 2025](https://economickey.com/2025/10/29/jumia-egypt-4/) (page blocked my fetch; facts from search snippet)
- [3rd-party] White Friday 2026 = Friday 27 Nov 2026, Cyber Monday 30 Nov; Amazon/noon expected ~20–30 Nov 2026 (prediction, not announced). — [getwafarna](https://www.getwafarna.com/en/article/black-friday-2026-date), [alcoupon](https://egypt.alcoupon.com/en/blog/white-friday-date), [foodidu](https://foodidu.com/blog/white-friday-2026/)
- [official via news] CPA White Friday operations room runs throughout November 2026. — [Cairo24](https://www.cairo24.com/2518994)
- [news] Ramadan 2026 began Thursday 19 Feb 2026 in Egypt. — [Egyptian Streets](https://egyptianstreets.com/2026/02/17/ramadan-in-egypt-starts-on-thursday-19-february/)
- [news, older: 2023] e-commerce back-to-school offers run in late August. — [Daily News Egypt, 27 Aug 2023](https://www.dailynewsegypt.com/2023/08/27/back-to-school-offers-from-e-commerce-platforms/)

### Inferences
- Expect three November waves for 2026: Jumia from ~end of October (Friday peaks), 11.11 (~7–12 Nov), and White/Yellow
  Friday (~20–30 Nov, peak Fri 27 Nov). Tighten crawl intervals on priority groups for these windows, especially at the
  start times seen: 00:00 Cairo (Amazon Prime early access, end of sales at midnight), 22:00 (Jumia launch), evening
  (noon 11.11), and Jumia's daily 10:00 flash-sale slot.
- During these windows, raise the evidence bar for "claimed discount" alerts (strikethroughs are universal and the CPA
  itself warns of pre-sale price inflation), and lean on own-history lows.

### Gaps
- No official 2026 dates for any shop yet (as of 28 Oct 2026); no Egypt-specific noon 11.11 or Ramadan sale dates.
- No source on B.TECH, 2B or Carrefour Egypt sale calendars or deal-release hours.
- Whether deals on noon/Amazon.eg drop hourly during White Friday ("deal of the hour") is undocumented in what I found.
- Eid, summer and 12.12 campaigns for Egypt not found.

## 7. Jumia Egypt flash sales mechanics

### Takeaway
Jumia Egypt's `/flash-sales/` page currently shows countdown timers with 10:00 AM starts on consecutive days and "up to
70%" claims; older (2020) reporting described 12 flash-sale "shows" a day from midnight to 22:00. Exact 2025/2026 Egypt
slot times are not documented publicly.

### Cited Findings
- [official page] Jumia Egypt flash-sales page shows countdown timers with 10:00 AM start times on consecutive days and "prices slashed by up to 70% off, every deal is a race against the clock". — [Jumia Flash Sales](https://www.jumia.com.eg/groceries/flash-sales/)
- [news, older: 11 Nov 2020] Jumia Egypt ran 12 flash-sale shows daily, starting at 12 am and ending at 10 pm, up to 80% off. — [Daily News Egypt, 11 Nov 2020](https://www.dailynewsegypt.com/2020/11/11/e-commerce-companies-compete-for-sales-on-black-friday/)
- [news, Nigeria not Egypt] Jumia Nigeria Black Friday slots: 12 am, 12:30 am, 10 am, 12 pm, 2/4/6 pm, 10 pm. — [Jumia Nigeria blog](https://blog.jumia.com.ng/all-you-need-to-know-about-jumia-black-friday-2025-do-pass-yourself/)
- Project observation: the Egypt page lists ~409 products with a stock bar ("3 items left"); the same products on category pages carry the same flash price but no flash marker; category pages alternate between flash and regular price because of two caches (docs/JUMIA.md).

### Inferences
- A 10:00 Cairo daily rollover suggests running the `category:flash-sales` lane at ~10:00–10:15 Cairo every day, and at
  more slots during Black Friday (the 2020 Egypt and 2025 Nigeria patterns suggest midnight and 22:00 slots too).
- Because flash prices also appear on category pages (from one of two caches), a flash price followed by the regular price
  on the next crawl is cache flip-flop, not a price change; require two consistent reads before treating a rise as the end.

### Gaps
- No official Jumia Egypt documentation of slot times, slot length, or how many products per slot; 2025 Black Friday
  Egypt flash-slot times not found.
- No data on whether Jumia flash prices are lower than the same product's 30-day low (the real-discount question).
