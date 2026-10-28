# How Amazon (global and amazon.eg) sets, changes and displays prices

Researched 28 Oct 2026. Source-quality tags: **[PRIMARY]** = Amazon's own page, Amazon staff post, court/agency filing or peer-reviewed paper; **[PRESS]** = reputable news; **[BLOG]** = seller-tool vendor or SEO blog (treat with caution).

Note on access: amazon.com and amazon.eg customer help pages returned HTTP 503 to the fetcher, and Seller Central help pages need a login. So the wording of Amazon's own help pages is quoted below from search-engine extracts of those pages, not from a direct page load.

---

## 1. Amazon's own retail pricing: how often it changes, price matching, Project Nessie

### Takeaway
Amazon reprices at very high frequency. The best-known figure, ">2.5 million price changes per day", comes from a 2013 Profitero vendor study of amazon.com. The "every 10 minutes" claim has no primary source: Profitero said "as often as every 15 minutes". Amazon's stated policy is to match other retailers' low prices. The FTC alleges that Amazon also ran a secret algorithm, "Project Nessie", that raised prices where it predicted rivals would follow. It reportedly ran 2014/2015 to 2019 and was switched on and off at least 8 times. Amazon disputes this, and it is an allegation, not a court finding.

### Cited Findings
- [PRESS/vendor study, 2013] In November 2013, Amazon.com made more than **2.5 million price changes per day**. Best Buy and Walmart made about 50,000 changes in the whole month, combined. Amazon's daily price changes had grown **ten-fold in 12 months**. — [Profitero press release (Dec 2013)](https://profitero.com/2013/12/profitero-reveals-that-amazon-com-makes-more-than-2-5-million-price-changes-every-day); [Quartz](https://qz.com/157828/amazon-changes-its-prices-more-than-2-5-million-times-a-day); [PYMNTS](https://www.pymnts.com/news/2013/report-amazon-makes-2-5m-price-changes-daily/)
- [PRESS] RetailWire, citing Profitero, says Amazon prices can change "as often as every **15 minutes**". The widely repeated "every 10 minutes" claim could not be traced to a primary source. One low-quality page attributes it to a "University of Chicago study" that no other source confirms. — [RetailWire (Dec 2013)](https://retailwire.com/discussion/amazons-price-changing-machine/); [latestdeals.co.uk (unverified attribution)](https://www.latestdeals.co.uk/news/does-amazon-change-prices-automatically?page=1)
- [PRIMARY, academic] Amazon's own documentation (as cited by Chen et al. 2016) says sellers may update prices only every **20 minutes**. Amazon also states it can take **up to 15 minutes** for all of its systems to converge on a new price. — [Chen, Mislove & Wilson, WWW 2016 (PDF)](https://www.khoury.northeastern.edu/home/cbw/static/pdf/amazon-www16.pdf)
- [PRESS, quoting Amazon GC] Amazon general counsel David Zapolsky: Amazon tries to "match other retailers' low prices" on products it sells itself. Amazon does not control third-party (3P) sellers' prices, but it gives them tools to price competitively. — [The Register (Oct 2023)](https://www.theregister.com/2023/10/04/amazon_project_nessie_algorithm/)
- [PRIMARY allegation via press] **Project Nessie** (FTC v. Amazon, unredacted complaint, 2023):
  - Nessie picked products where Amazon predicted other online retailers would follow its price increases, then raised prices on them. When rivals followed, the higher price stayed in place. — [BCLP law-firm analysis](https://www.bclplaw.com/en-US/insights/the-ftc-and-state-case-against-amazon-highlights-risks-and-impacts-from-using-pricing-algorithms.html); [Engadget](https://www.engadget.com/amazon-knows-its-policies-force-sellers-to-live-in-constant-fear-194800056.html)
  - The WSJ's unnamed sources say Nessie tested how high Amazon could raise prices before rivals undercut it. If competitors did not match, prices automatically went back to the lower level. It also discounted when a competitor cut prices, and kept those lower prices after a sale ended. — [The Register](https://www.theregister.com/2023/10/04/amazon_project_nessie_algorithm/)
  - The FTC alleges more than **$1 billion** in excess profit. The algorithm was turned on and off **at least 8 times between 2015 and 2019** and stopped in 2019. Sources disagree on when it started: "developed 2010" in one, "used from 2014" in another. — [PYMNTS](https://www.pymnts.com/legal/2023/ftc-alleges-amazon-deployed-secret-algorithm-to-raise-prices/); [GeekWire](https://www.geekwire.com/2023/ftc-vs-amazon-filing-reveals-more-claims-about-jeff-bezos-alleged-role-project-nessie-and-more/); [CBS News](https://www.cbsnews.com/sanfrancisco/news/ftc-says-amazon-used-algorithm-secretly-raise-prices-destroyed-internal-messages/)
  - Amazon says the FTC "grossly mischaracterized" Nessie. In Amazon's account, it was meant to stop price matching from producing unsustainably low prices, "didn't work as intended" and was "scrapped several years ago". It ran "for a few years on a subset of products". — [The Register](https://www.theregister.com/2023/10/04/amazon_project_nessie_algorithm/)
  - In the Oct 2024 ruling, the court let the Nessie claim proceed as a standalone FTC Act Section 5 unfair-competition claim, the first such claim upheld in over 40 years. — [BCLP](https://www.bclplaw.com/en-US/insights/the-ftc-and-state-case-against-amazon-highlights-risks-and-impacts-from-using-pricing-algorithms.html)
- [PRESS] The FTC also alleges that Amazon punishes sellers who offer lower prices elsewhere (anti-discounting). — [The Register](https://www.theregister.com/2023/10/04/amazon_project_nessie_algorithm/)

### Inferences
- The tracker should expect prices to move many times a day on competitive ASINs. Any single observation may be stale, because of the up-to-15-minute convergence window.
- Nessie-style behaviour means Amazon-retail prices can rise and then fall back to a baseline. A drop back to the old baseline after a temporary increase is not a real discount. The reference should be the long-run time-weighted level, not the most recent price.
- The 2.5M/day figure is 13 years old and covers amazon.com only. It should not be quoted as a current or amazon.eg figure.

### Gaps
- No current (2020s) primary figure on how often Amazon changes prices, and nothing at all for amazon.eg.
- The FTC v. Amazon trial outcome (trial was scheduled for 2027 in earlier reporting) was not researched. Nessie facts remain allegations.

---

## 2. Featured Offer (Buy Box), seller switching, and third-party repricers

### Takeaway
The price shown on a listing card is the Featured Offer (Buy Box) price. Amazon chooses it among sellers using price plus seller features such as feedback and whether Amazon is the seller, so the cheapest seller does not always win. The displayed price can therefore jump when the winning seller changes, even if no seller changed its own price. Buy Box prices are highly volatile: half of best-seller products had more than 14 Buy Box price changes in 6 weeks, and only 13% never changed. Algorithmic repricers make this much worse. Amazon also removes the Featured Offer when a price is "significantly higher than recent prices", which can make the card show no price or a different seller.

### Cited Findings
- [PRIMARY, peer-reviewed] Chen, Mislove & Wilson, "An Empirical Analysis of Algorithmic Pricing on Amazon Marketplace", WWW 2016. Source: [PDF](https://www.khoury.northeastern.edu/home/cbw/static/pdf/amazon-www16.pdf); [project page](https://personalization.ccs.neu.edu/Projects/Amazon)
  - **Data:** 4 months of data on all sellers of **1,641** best-seller products. Sellers were crawled every **25 minutes**. The paper identified **543 algorithmic sellers**.
  - **Calibration:** in a 3-day, once-per-minute crawl, **40% of price changes happened within 1 minute of the previous change**. Most changes are either under 1 minute or over 30 minutes apart. Sub-minute changes are mostly "jitter": old prices briefly reappear because Amazon's distributed systems are inconsistent. A cart cannot be checked out at the stale price.
  - **Buy Box dynamics over 6 weeks:** only **13%** of products had a static Buy Box price, **50% had more than 14 Buy Box price changes**, and some had hundreds. The Buy Box *seller* stayed constant for **31%** of products.
  - **Buy Box share of sales:** 82% of Amazon sales go through the Buy Box.
  - **The top-ranked (lowest-price) seller wins the Buy Box only ~60% of the time.** Feature importance: price ratio to lowest 0.33, positive feedback 0.10, "Amazon is the seller" 0.10, feedback count 0.06, FBA 0.02. Price alone predicts the winner with 50–60% accuracy.
  - **Repricer behaviour:** algorithmic sellers mostly target the overall lowest or second-lowest price, rather than Amazon's price. **70%** of those that match the lowest price sit within **$1** of it, but only 40% are within $1 of Amazon's price. With a ≥20-change threshold, **2.4%** of all sellers are algorithmic, which is **38%** of sellers with ≥20 changes on some product. Prices on some algorithmic seller/product pairs change 100–1,000 times. For non-algorithmic sellers, the price **never changed for 65%** of pairs.
  - **Volatility:** **20%** of products *without* algorithmic sellers had zero Buy Box price changes, against only **2%** of products *with* them. Press coverage summarised these products as ~10x more volatile. — [Northeastern news](https://news.northeastern.edu/?p=63580); [Vice](https://develop.vice.com/en/article/youre-paying-too-much-on-amazon-study-says/)
  - Amazon's own built-in "match lowest price" tool adjusted price only once and did not keep tracking the lowest price.
- [PRESS] **Runaway repricer, 2011:** *The Making of a Fly* reached **$23,698,655.93** on amazon.com. Two sellers' bots fed off each other: profnath priced at 0.9983x bordeebook, and bordeebook priced at 1.270589x profnath. After publicity the prices fell to $106.23 and $134.97, probably through manual intervention. — [Techdirt](https://www.techdirt.com/2011/04/25/infinite-loop-algorithmic-pricing-amazon-how-book-flies-cost-2369865593/); [The Register](https://www.theregister.com/2011/04/26/amazon_pricing_algorithms/); [Scholarly Kitchen](https://scholarlykitchen.sspnet.org/2011/04/25/algorithmic-pricing-on-amazon-is-a-book-about-flies-worth-23698655-93-plus-shipping/)
- [PRIMARY: Amazon Fair Pricing Policy, as quoted in Seller Central forums] Setting a price "significantly higher than recent prices offered on or off Amazon" harms customer trust. Amazon "may remove the Featured Offer or offer, suspend shipping, or terminate selling privileges". Pricing Health shows sellers the message "Not eligible to be the Featured Offer due to significantly high price". Amazon does not publish what "recent" or "significantly higher" means. — [Seller Central forum: Fair Pricing Policy](https://sellercentral.amazon.com/seller-forums/discussions/t/57267418-e5fc-4d42-89b8-47b4bf8c76f6); [Seller Central forum: Managing price to maintain Featured Offer eligibility](https://sellercentral.amazon.com/seller-forums/discussions/t/124c4f5e379bd12e69255f99aa12b811); [BQool blog on suppressed Buy Box](https://blog.bqool.com/decoding-amazon-buy-box-enigma-suppressed-buy-box)
- [PRIMARY, forum] Amazon runs "potential pricing error" offer deactivation through Pricing Health. Listings priced far outside the expected range, low or high, are deactivated, and sellers can set their own min/max price limits. — [Seller Central EU forum: potential pricing errors deactivation](https://sellercentral-europe.amazon.com/forums/t/potential-pricing-errors-offer-deactivation-notification-in-pricing-health/369903); [EU forum: potential high price error](https://sellercentral-europe.amazon.com/seller-forums/discussions/t/bd1cfd19-f354-4a46-9659-981335fcdadb)

### Inferences
- For tracker rules: a drop in the displayed card price may only be a **Buy Box seller switch**, for example from Amazon to a cheaper 3P seller with weak feedback, or the reverse. It is better to key history on (ASIN, seller) where the page exposes the seller. The project already does this for noon (`SKU@seller`). Otherwise, treat a sudden one-observation drop that reverts within one crawl as possible Buy Box churn or jitter.
- Sub-minute price "jitter" is real and comes from Amazon's infrastructure. Confirming a drop with a second observation a few minutes later, or seeing it in two sources (search listing plus deals JSON), filters jitter without hurting minute-scale time-to-notice much.
- Repricers cause frequent small moves: 70% sit within $1 of the lowest price. A minimum-percentage threshold should be used, not "any drop".
- When the Featured Offer is suppressed (the card shows "See all buying options" or no price), record it as "no price" rather than as a price change.

### Gaps
- No public current data on repricing frequency. The 2016 paper predates today's Amazon-native "Automate Pricing" tool, and repricer vendors' claims ("reprices every 2 minutes", etc.) were not verified.
- No data on Buy Box dynamics on amazon.eg specifically.

---

## 3. Reference prices: List Price, Typical/Was Price, strikethrough; changes in 2026

### Takeaway
On amazon.com, a strikethrough price is either the **List Price** or the **Typical Price** (formerly "Was Price"). Typical Price is the **90-day median price customers paid**, excluding limited-time promotional prices. A List Price is shown only if the product sold at or above it in the last **90 days** (180 days for seasonal goods), or if another retailer offered it at that price. Two 2026 changes tighten this. From **23 Apr 2026**, a List Price must be validated by a Featured-Offer sale on Amazon or a recent offer at another retailer. From **18 May 2026**, if the price was below the non-promotional median on more than half of the last 90 days, Typical Price is computed from *all* sales, so "permanent promotions" no longer prop up a fake reference. Amazon says reference pricing is now aligned with the **price-history graph on product pages** (lowest Featured Offer price per day). Whether amazon.eg applies identical rules is unconfirmed.

### Cited Findings
- [PRIMARY: Amazon customer help page "Strike-Through Pricing and Savings", wording as extracted by the search engine] "The Typical Price is determined using the **90-day median price paid by customers** for the product on Amazon. We **exclude prices paid by customers when the product has been on a promotion for a limited time**, and prices paid in the Amazon Haul or Amazon Now store." — [amazon.com help GQ6B6RH72AX8D2TD](https://www.amazon.com/gp/help/customer/display.html?nodeId=GQ6B6RH72AX8D2TD)
- [PRIMARY: same page] List Price is the manufacturer- or vendor-suggested price. "Except for books, Amazon will only display a List Price if the product was purchased by customers on Amazon or offered by other retailers at or above the List Price in the past **90 days** for non-seasonal products". The window is **180 days** for seasonal products. — [amazon.com help](https://www.amazon.com/gp/help/customer/display.html?nodeId=GQ6B6RH72AX8D2TD)
- [PRIMARY: Amazon developer docs] In the SP-API Product Pricing API v2022-05-01, `getCompetitiveSummary` returns **WasPrice**, "the 90-day median price that customers pay for the product on Amazon". — [SP-API changelog](https://developer-docs.amazon.com/sp-api/lang-ja_JP/changelog/update-product-pricing-api-v2022-05-01-expands-functionality-for-getcompetitivesummary-operation)
- [PRIMARY: Amazon staff post, "Upcoming Improvements to Reference Pricing", Seller Central US, posted ~early 2026] — [Seller Central forum](https://sellercentral.amazon.com/seller-forums/discussions/t/f48a1fe5-aa8e-4806-b687-2d9aeec5c351)
  - From **23 April 2026**, a List Price is validated only if (a) the product has recently been offered at that price by another retailer, or (b) customers have bought it **as the Featured Offer on Amazon** at that price.
  - Typical Price = "the median **non-promotional** price customers paid for a product over the last **90 days**".
  - From **18 May 2026**: "if more than half of the days in a product's 90-day Price history are below the non-promotional median price, we'll calculate Typical Price using all sales, including promotional sales". Discounts not advertised as promotions count as non-promotional.
  - Reference pricing is aligned with the **price-history graph on product detail pages**, which shows the **lowest Featured Offer price each day**.
  - Third-party coverage of the same change: [ChannelX (13 Apr 2026)](https://channelx.world/2026/04/amazon-get-strict-on-reference-pricing/); [EcommerceBytes](https://www.ecommercebytes.com/qcwe); [InventoryHero blog](https://www.inventoryhero.ai/blog/amazon-was-price)
- [BLOG, quoting an Amazon FAQ] "Sales that occur during **peak events** are always excluded from the Typical Price calculation." — [InventoryHero](https://www.inventoryhero.ai/blog/amazon-was-price) (via search extract; not verified on an Amazon page)
- [PRESS/legal, historical] In 2021, California district attorneys sued Amazon over reference pricing. At the time Amazon's policy used a **30-day** median for "Was Price", later changed to 90 days. — [Legal Insurrection](https://legalinsurrection.com/2021/03/california-counties-sue-amazon-over-alleged-misleading-pricing/amp); [FOX 5 San Diego](https://digital-release.fox5sandiego.com/news/business/san-diego-da-other-prosecutors-sue-amazon-for-alleged-misleading-pricing/amp/)
- [PRIMARY: Amazon seller blog] Both List Price and Typical Price are "Validated Reference Prices" shown as strikethrough prices. — [sell.amazon.com: Amazon List Price](https://sell.amazon.com/blog/amazon-list-price)
- [PRIMARY: Amazon Deals Guide PDF, AU marketplace, last updated 6 Jun 2024] Deals must "Comply with Amazon Policy on Reference Prices". Deals can be "suppressed at any time" if they stop meeting eligibility. — [Amazon Deals Guide PDF](https://m.media-amazon.com/images/G/35/sp-marketing-toolkit/Sellerfacingguides/Deals/Deals_Guide.pdf)

### Inferences
- Amazon's Typical Price is conceptually close to the tracker's own reference: a robust central value of the price actually paid over 90 days, excluding promotions. The tracker's time-weighted own history is a reasonable analogue. A 90-day window with a median, or a time-weighted median, matches Amazon's own definition and is defensible.
- The 18-May-2026 rule targets exactly the "fake Was" pattern: a product that is "on promotion" most days. Mirror it: if the price has been below the "normal" level on more than 50% of days in the window, the reference collapses to the overall median, so the "discount" disappears.
- **List Price strikethrough ≠ real discount.** It is MSRP-like, validated only by one Featured-Offer sale or another retailer's price. The tracker should ignore List Price for alerting, or at most show it for information.
- The Typical Price strikethrough is more trustworthy than List Price, but it is a 90-day median of *paid* prices. A product whose price was just raised and then "cut" would still show a strikethrough if the median is high.
- Amazon now shows a native price-history graph (lowest Featured Offer per day). This is daily granularity: intra-day flash drops are invisible there, but the tracker can capture them.

### Gaps
- Could not confirm whether **amazon.eg** applies identical reference-price rules or has rolled out the 2026 changes. The amazon.eg help page returned 503. The 2026 staff post was on Seller Central US and says "across our store" without naming marketplaces.
- Exact current wording of amazon.eg's strikethrough labels (Arabic "السعر السابق" / "سعر القائمة"?) not verified; check the tracker's saved fixtures.
- Not verified on an Amazon page: whether deal prices (Lightning/Best Deal) count as "limited-time promotions" excluded from Typical Price. Highly likely, but no direct quote found.

---

## 4. Deal types, their mechanics and eligibility rules; amazon.eg sale events

### Takeaway
**Lightning Deals** run **4–12 hours** (duration set by Amazon). They show a **% claimed bar** and a timer; when all units are claimed or held in carts, a waitlist opens, and a buyer has **15 minutes** to check out. **Best Deals** run for "a select number of days" (blogs say up to 14). Deal eligibility in the 2024 Amazon guide (AU marketplace) is at least **15% off the lowest price in the last 30 days** and a rating of at least 3 stars; other marketplaces and years differ (20%, 90-day, etc.). Amazon Egypt runs Prime Day (first in July 2024; in **23–26 June 2026** it was part of the global event), **White Friday** in late November (~20–30 Nov), **11.11** (~7–12 Nov), a **Mega Sale** (~12–18 May), plus Eid and Ramadan sales. Bank instant discounts are applied at checkout and are not in the listing price.

### Cited Findings
**Lightning Deals / Best Deals: mechanics and eligibility**
- [PRIMARY: Amazon Deals Guide, AU, last updated 6 Jun 2024] "A Lightning Deal is a time-bound, promotional offer where an item is featured for a limited number of hours, usually **4 to 12 hours** (as determined by Amazon), on the Amazon Deals page." "A Best Deal is ... featured for a **limited number of days**." Requirements: Professional seller; sales history; Prime-eligible (limited exceptions); **average rating ≥ 3 stars** where there are ≥5 reviews, or 0 reviews; comply with deal-frequency and reference-price policies. Discount: "**At least 15% off the lowest price in last 30 days**"; seller-funded. Deals "may be suppressed at any time if they do not meet the eligibility criteria". For a Lightning Deal the seller sets the deal price and a **minimum deal quantity**. — [Amazon Deals Guide PDF](https://m.media-amazon.com/images/G/35/sp-marketing-toolkit/Sellerfacingguides/Deals/Deals_Guide.pdf)
- [PRIMARY: Amazon seller blog] Lightning Deals run 4–12 hours as determined by Amazon. Best Deals run for "a select number of days". — [sell.amazon.com: seller promotions guide](https://sell.amazon.com/blog/seller-promotions)
- [BLOG, conflicting] Other sources give different thresholds: "15% off and deal price ≤ lowest price of past 60 days"; "20% off, undercutting the 30-day low by ≥10%, rating ≥3.5"; "20% below the lowest price of the past 90 days". One source notes Amazon now computes a **maximum deal price and minimum discount from recent selling history**, and that published figures disagree. — [Amalytix](https://www.amalytix.com/en/knowledge/marketing/amazon-lightning-deals/); [Helium10](https://www.helium10.com/blog/amazon-lightning-deals/); [BeBold Digital](https://www.bebolddigital.com/blog/amazon-lightning-deal); [ppc.land](https://ppc.land/lightning-deal/)
- [BLOG] Best Deals can run "up to 14 days" (not confirmed on an Amazon page). Fees: US Lightning Deal $150 off-peak / $500 peak until 1 Jun 2025, then a different structure; Best Deal $300 (conflicting). — [NivoAds](https://nivoads.com/?p=5415); [ppc.land](https://ppc.land/lightning-deal/)
- [PRIMARY: Amazon customer help "Lightning Deals"] The deal page shows a **status bar with the percentage claimed** and a timer. When all deals are sold or sitting in carts, "Join Waitlist" replaces "Add to Cart". When a waitlisted item lands in the cart, the buyer has **15 minutes** to purchase; otherwise the next person is notified. The waitlist ends when the promotion ends. — [Amazon help: Lightning Deals](https://digprjsurvey.amazon.com/csad/help/node/GW3L8JX7Q9FH8ALB)
- [PRIMARY: Keepa API docs] Keepa's Lightning Deal object has deal start and end times, deal state and percent claimed. Keepa notes the claimed % can lag because of collection delays. — [Keepa API: lightning deal object](https://keepa.com/api-docs/lightning-deal-object.html)
- [BLOG/forum] Coupons ("clip coupon") are applied at checkout, not in the listing price. Since the March 2024 policy change, coupon eligibility is judged against the **lowest price in the prior 90-day rolling period**. — [SellersAskSellers forum](https://sellersasksellers.com/t/amazons-new-coupon-policy-3-12-24/3627?page=3)

**Global tentpoles (dates)**
- [PRIMARY + PRESS] **Prime Day 2026: 23–26 June 2026** (moved from July, reportedly to avoid the FIFA World Cup). Egypt is among the participating countries; the reported country count is 22–26. Some countries (Australia, Brazil, India, Japan) held it later. — [aboutamazon.com](https://aboutamazon.com/news/retail/amazon-prime-day-june-2026); [NBC Select](https://www.nbcnews.com/select/shopping/amazon-prime-day-dates-2026-rcna347962); [ChannelX](https://channelx.world/2026/06/amazon-prime-day-2026-23rd-26th-june/); [MobileMasr](https://mobilemasr.com/en/blogs/amazon-prime-day-2026-in-june)
- [PRESS] **Prime Big Deal Days 2026: 6–7 Oct 2026** (48 h, 00:01 PDT to 23:59 PDT). Earlier editions: 7–8 Oct 2025, 8–9 Oct 2024, 10–11 Oct 2023. Whether Egypt took part was not found. — [GeekSeller](https://www.geekseller.com/blog/amazon-prime-big-deal-days-return-october-6-7-2026/); [Technobezz](https://www.technobezz.com/news/october-prime-day-dates-and-start-time); [aboutamazon FAQ](https://www.aboutamazon.com/md/news/retail/amazon-prime-big-deal-days-faq)
- [PRESS] Prime Day 2025 (global) ran 8–11 July 2025. Members in Egypt, Brazil, India, Mexico, KSA and UAE were to shop "later this summer". No Egypt-specific 2025 dates were found. KSA ran 25–31 July 2025. — [Android Headlines](https://www.androidheadlines.com/2025/06/amazon-prime-day-2025-starts-july-8-and-wont-end-until-youve-bought-something.html); [Sabq (KSA)](https://sabq.org/article/i8cZoNb)

**amazon.eg events**
- [PRESS] **Prime launched in Egypt on 14 June 2022**: 30-day trial, then **EGP 29/month**. In June 2026 the price was EGP 29/month or **EGP 249/year**. — [The Brand Berries](https://thebrandberries.com/amazonprimearrivesinegyptshoppingsavingsandentertainmentbundledtogetherinonemembershipprogram/); [Daily News Egypt (24 Jun 2026)](https://www.dailynewsegypt.com/2026/06/24/amazon-egypt-sees-prime-day-as-key-driver-of-value-digital-growth/)
- [PRESS] **First Prime Day Egypt: 16–21 July 2024** (6 days, 30+ categories, amazon.eg/primeday). — [Ahram Online](https://english.ahram.org.eg/News/526519.aspx)
- [PRESS] Amazon Egypt calls Prime Day, Eid and Ramadan its peak seasons. Prime Day offers "hundreds of thousands of exclusive deals" plus "additional discounts through banking partners". About 2,000 employees, 26 delivery stations covering 25 governorates. HarrisX survey: 87% of Egyptians are more likely to shop during sales, and 85% compare prices before buying. — [Daily News Egypt (Jun 2026)](https://www.dailynewsegypt.com/2026/06/24/amazon-egypt-sees-prime-day-as-key-driver-of-value-digital-growth/)
- [PRESS] **White Friday** (Amazon's name in Egypt and the Gulf; "Yellow Friday" is noon's sale): **20–30 November**, up to 70% off, 24-hour early access for Prime members. The year is not explicit in the articles; probably 2024 or 2025. One guide says the 2024 window was 24–30 Nov, which conflicts. — [Egypt Today](https://www.egypttoday.com/Article/6/136259/The-Biggest-Sale-of-the-Year-Returns-From-November-20); [Almal News (Arabic)](https://almalnews.com/1901323/من-20-إلى-30-نوفمبر-«أمازون-مصر»/); [AR-Pay guide](https://ar-pay.com/blog/en/news/amazon-egypt-white-friday/); noon Yellow Friday: [Gulf Business](https://gulfbusiness.com/?p=235897)
- [PRESS] **11.11 sale on amazon.eg: 7–12 November** (year not stated), "hundreds of thousands of deals", 30 categories. — [FollowICT](https://followict.news/en/amazon-eg-announces-11-11-sale-event-with-hundreds-of-thousands-of-deals-from-november-7-to-12/)
- [PRESS] **Mega Sale: 12–18 May** (7 days, likely 2026), with 0% instalments through partner banks and instant discounts from Banque Misr, NBE, QNB, ADIB, Vodafone Cash and Bebasata. — [Cairo360](https://cairo360.com/article/shopping/amazon-egs-mega-sale-is-back-with-seven-days-of-deals-across-every-category/)
- [PRESS] **Eid Sale: 10–16 March** (year probably 2026). — [Arab Finance tag page](https://www.arabfinance.com/en/home/tagresult/1836)
- [PRESS] Example bank offers in a White Friday sale: 0% instalments up to 12 months with CIB and Banque Misr; Mastercard 10% extra, capped at EGP 200; Banque Misr up to 20%. — [Egypt Today](https://www.egypttoday.com/Article/6/136259/The-Biggest-Sale-of-the-Year-Returns-From-November-20)

### Inferences
- Lightning Deals last 4–12 h and can sell out well before the timer ends, because units are capped by the minimum deal quantity and the waitlist. To catch them, the tracker's deals lane must poll at well under 1 h; 10–15 min is a reasonable target. A Lightning Deal seen at 100% claimed is effectively gone, though it can briefly reappear via abandoned carts.
- Amazon's own deal-eligibility floor (≥15% below the 30-day low in the 2024 guide) is a good sanity bar for "real deal". A tracker alert threshold of about 15% below its own 30–90-day reference matches Amazon's own definition of a deal-worthy discount.
- Expect noise bursts around amazon.eg tentpoles: Mega Sale in mid-May, Prime Day (late June 2026), 11.11 in early November, White Friday from ~20 November, Ramadan and Eid. Per the reported "peak events excluded from Typical Price" rule, the tracker could also exclude event-period prices from its reference, so post-event prices don't look like rises or falls against a polluted baseline.
- Coupons and bank discounts are invisible in the listing price. The tracker cannot see them, and they should not be confused with list-price drops.

### Gaps
- No primary source confirms that **amazon.eg runs Lightning Deals** or what the Egypt deal-eligibility thresholds are. The tracker's own `deals:<node>` JSON lane is the best evidence: check whether it returns `LIGHTNING_DEAL` types.
- No official 2025 dates for Prime Day Egypt, and no confirmed 2026 White Friday dates for Egypt (expected late Nov 2026, i.e. about 3 weeks from now).
- **Lightning Deal start-time slots:** no reliable source found on fixed start times, globally or for Egypt. Keepa's API exposes per-deal start times, so the tracker could measure this itself.
- Exact Prime Exclusive Discount and Subscribe & Save mechanics were not researched in depth. S&S appears not to be offered on amazon.eg (not verified).

---

## 5. Price errors and glitches

### Takeaway
Amazon's Conditions of Use let it cancel orders placed at mistaken prices, or contact the buyer with the correct price, and a Seattle court upheld this in 2003. In practice Amazon sometimes cancels, sometimes honours (2017 Sony lens, some 2019 Prime Day camera gear), and sometimes gives goodwill credit. Amazon also auto-deactivates 3P offers it flags as "potential pricing errors". No frequency statistics exist.

### Cited Findings
- [PRESS] Amazon's posted policy (2000): if the correct price is higher, Amazon will contact the customer, or cancel. — [The Register (2000)](https://www.theregister.com/2000/09/13/amazon_reneges_on_dvd_prices/)
- [PRESS] A Seattle small-claims judge held that no contract formed because the card had not been charged, so Amazon need not honour the mistaken price (2003). — [Seattle Times archive](https://archive.seattletimes.com/archive/20030129/amazon29/amazon-wins-small-claims-fight-over-price-mistakes); [DWW](https://www.dww.com/articles/seattle-court-rules-amazoncom-not-required-to-honour-mistaken-price?amp)
- [PRESS] Amazon honoured a $500 price on a $2,200 Sony lens (2017). In 2019 it gave buyers of a $550 lens listed at $250 a $20 credit instead. Some 2019 Prime Day camera-gear errors were honoured. — [PetaPixel 2017](https://petapixel.com/2017/08/03/amazon-honoring-500-pricing-error-2200-sony-lens/); [PetaPixel 2019](https://petapixel.com/2019/09/14/amazon-lists-550-sony-lens-for-250-but-gives-buyers-20-instead); [FOX6](https://www.fox6now.com/news/apparent-prime-day-pricing-error-may-cost-amazon)
- [PRIMARY: forum] Amazon deactivates offers flagged as potential pricing errors, and sellers can set min/max price limits. — [Seller Central EU forum](https://sellercentral-europe.amazon.com/forums/t/potential-pricing-errors-offer-deactivation-notification-in-pricing-health/369903)
- [PRIMARY, academic] Stale prices during Amazon's up-to-15-minute propagation cannot be added to the cart. — [Chen et al. 2016](https://www.khoury.northeastern.edu/home/cbw/static/pdf/amazon-www16.pdf)

### Inferences
- Treat implausibly deep drops (for example more than 70–80% below reference) as "possible error". These may be worth alerting fast, because they are often corrected quickly by the seller or by Amazon's deactivation, but they should be labelled as uncertain, since the order may be cancelled.
- A one-crawl price that disappears on the next crawl is consistent with either an error fix or jitter.

### Gaps
- No data on how often price errors happen or how fast they are corrected (minutes vs hours). Nothing specific to amazon.eg or its Arabic conditions of use was found.

---

## 6. Time-of-day / day-of-week patterns

### Takeaway
No credible published data on hour-of-day or day-of-week patterns in Amazon price changes was found. Online claims are SEO filler without data. The only solid timing structure is event calendars and the start and end times of US events (00:01 PT).

### Cited Findings
- [BLOG, low quality] Generic guides assert that prices change with "time of day" but cite no measurement. — [Priceva blog](https://priceva.com/blog/best-amazon-price-tracker) (representative)
- [PRIMARY, academic] Inter-change times are bimodal: sub-minute jitter, or more than 30 minutes. — [Chen et al. 2016](https://www.khoury.northeastern.edu/home/cbw/static/pdf/amazon-www16.pdf)
- [PRESS] US Prime Big Deal Days 2026 started 00:01 PDT, 6 Oct, and ended 23:59 PDT, 7 Oct. — [Technobezz](https://www.technobezz.com/news/october-prime-day-dates-and-start-time)

### Inferences
- The tracker's own SQLite history is the best source for amazon.eg hour-of-day patterns. Group the timestamps of observed drops by Cairo hour and weekday to tune the schedule empirically.

### Gaps
- No academic, Keepa or camelcamelcamel analysis of intra-day timing was found. No information on whether amazon.eg events start at 00:00 Cairo time.

---

## 7. amazon.eg specifics

### Takeaway
amazon.eg launched on **1 Sept 2020**, replacing Souq.com Egypt, with all Souq sellers migrated and FBA available. Prime followed in June 2022 and valU BNPL in July 2022. Its marketplace is heavily 3P, inherited from Souq. Checkout-only bank and instalment discounts are a big part of event "deals" and never show in the listing price.

### Cited Findings
- [PRESS] Amazon launched in Egypt on **1 September 2020**, replacing the localised Souq.com store. All sellers were previously selling on Souq.com Egypt. Prime was not available at launch. Souq sellers could register in Seller Central and use FBA. — [Marketplace Pulse](https://www.marketplacepulse.com/articles/amazon-launches-in-egypt); [Enterprise](https://enterprise.press/?p=144248)
- [PRESS] valU BNPL instalments were integrated at amazon.eg checkout in **July 2022** (announced 4 Jul 2022). — [Crowdfund Insider](https://www.crowdfundinsider.com/2022/07/193333-bnpl-amazon-egypt-introduces-valus-buy-now-pay-later-services-in-egypt/); [PYMNTS](https://www.pymnts.com/amazon/2022/amazon-offers-bnpl-option-to-shoppers-in-egypt/); [Finextra](https://www.finextra.com/newsarticle/40570/amazon-egypt-rolls-out-bnpl-options)
- [PRESS] Event bank offers are checkout-only: CIB and Banque Misr 0% for up to 12 months; Mastercard 10% capped at EGP 200; Banque Misr up to 20%; in 2026, NBE, QNB, ADIB, Vodafone Cash and Bebasata instant discounts. — [Egypt Today](https://www.egypttoday.com/Article/6/136259/The-Biggest-Sale-of-the-Year-Returns-From-November-20); [Cairo360](https://cairo360.com/article/shopping/amazon-egs-mega-sale-is-back-with-seven-days-of-deals-across-every-category/)
- [PRESS] "Thousands of local sellers"; about 2,000 staff; fulfilment storage of nearly 100,000 m³. — [Daily News Egypt (Jun 2026)](https://www.dailynewsegypt.com/2026/06/24/amazon-egypt-sees-prime-day-as-key-driver-of-value-digital-growth/)

### Inferences
- Because most amazon.eg sellers are 3P (ex-Souq), the Buy Box seller-switching effects from section 2 should be strong on amazon.eg.
- For alerting, the tracker can at most mention "possible extra bank discount at checkout during [event]". It cannot fold bank discounts into price history.

### Gaps
- No figures on amazon.eg's 3P vs 1P share, number of sellers, or the share of ASINs with multiple offers.
- Whether amazon.eg shows "Typical price" vs "List Price" labels and the native price-history graph was not verified. Check live pages or the fixtures in `tests/fixtures/`.
