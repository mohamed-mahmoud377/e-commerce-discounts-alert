# Telling temporary sales from regular price changes in price time series, and how online prices behave

Scope: what economics, statistics and data mining say about separating temporary sales from regular (permanent) price changes, how online prices actually move, and how this maps onto the tracker's data. The tracker stores a step function per product (one row per price or stock change), samples it irregularly, has stock-outs, and runs under high, devaluation-driven inflation in Egypt.

Current method, for reference: "typical" = time-weighted median of in-stock prices over the last 90 days, excluding the current price. Alert when the price is ≥20% below typical AND the product spent ≤15% of in-stock time at or below the current price. All-time low = below every earlier price and ≥15% below typical. ≥60% below = possible price error. Early alert with 1–3 days of history when ≥30% below and below every price seen so far.

How the sources were read: I downloaded and read the full text of Nakamura & Steinsson, Kehoe & Midrigan (NBER WP), Gorodnichenko–Sheremirov–Talavera, Cavallo (three papers), Brown & MacKay, Chen–Mislove–Wilson, Adams & MacKay and Ray–Snir–Levy. For the Eichenbaum–Jaimovich–Rebelo PDF, text extraction failed beyond the abstract, so its details come from the abstract and search summaries. Citations marked "(standard reference, not fetched this session)" are well-known papers cited by DOI. Their formulas are textbook material, but I did not re-read them for this note.

---

## 1. Sale filters from macroeconomics (Nakamura–Steinsson, Kehoe–Midrigan, Eichenbaum–Jaimovich–Rebelo, Chahrour, Guimaraes–Sheedy, BLS flags, later variants)

### Takeaway
The literature uses two families of method:
- **V-shape sale filters** (Nakamura & Steinsson). A price drop counts as a sale when the price returns to the old regular price within a window of 1–5 months (monthly data) or 1–2 weeks (online daily data).
- **Running-mode reference prices.** Kehoe & Midrigan: modal price in a ±5-week window, counted only if the mode takes ≥1/3 of the window and is actually charged. Eichenbaum–Jaimovich–Rebelo: modal price per quarter. Chahrour: 13-week rolling mode plus smoothing.

Every one of these is **two-sided** (it needs future prices), so a real-time alerter can only run a one-sided version. All of them were built for low-inflation US data and have no explicit drift adjustment. Robustness comes from the window being short relative to how often regular prices change.

### Cited Findings

**Nakamura & Steinsson (2008), "Five Facts about Prices: A Reevaluation of Menu Cost Models," QJE 123(4):1415–1464** — [PDF](https://eml.berkeley.edu/~enakamura/papers/fivefacts.pdf)
- Their main sales measure is the **BLS sale flag** that price collectors record. As an alternative, they "look for V-shaped patterns in the data and identify these patterns as sales". Clearance sales are *not* sales under the filter approach. — [N&S 2008](https://eml.berkeley.edu/~enakamura/papers/fivefacts.pdf), Sec. III.H
- **Sale filter B** removes only *symmetric* V's: the price returns to exactly the original price within a set number of periods. **Sale filter A** also allows "regular price changes immediately preceding or following sales or asymmetric V's". Windows for the return to the original price run from **1 to 5 months**. The detailed algorithm (J, K, L parameters) is in the QJE online supplement, which I did not retrieve. — [N&S 2008](https://eml.berkeley.edu/~enakamura/papers/fivefacts.pdf), Table IX notes
- Table IX (1998–2005, median monthly frequency of price change, without / with product substitutions), from [N&S 2008](https://eml.berkeley.edu/~enakamura/papers/fivefacts.pdf):

  | Measure | Without subs. | With subs. |
  |---|---|---|
  | Sale filter B, 1-month window | 15.3% | 16.4% |
  | Sale filter A, 1-month window | 13.3% | 14.7% |
  | Sale filter A, 3-month window | 11.9% | 14.1% |
  | Sale filter A, 5-month window | 11.4% | 13.3% |
  | All posted price changes | 19.4% | 20.5% |
  | Regular price changes (BLS sale flag) | 8.7% | 10.9% |
  | Regular price changes (sale flag) plus clearance | 10.7% | 13.0% |

  So filter A with a 5-month window lands close to the flag-based number. Results depend heavily on the parameters.
- **Two known failure modes of filters:**
  1. With monthly sampling, a V followed by a regular change within the same month is missed.
  2. In volatile categories (gasoline), filters "identify sales even when there are none", because prices move by discrete amounts (e.g. $2.49 → $2.59). The BLS flag shows almost no gasoline sales.

  — [N&S 2008](https://eml.berkeley.edu/~enakamura/papers/fivefacts.pdf)
- **Headline facts:**
  - The median frequency of non-sale price change is **9–12%/month**, about half the rate including sales, which implies a **median regular-price duration of 8–11 months**.
  - The expenditure-weighted **fraction of price changes due to sales is 21.5%**. It is **87.1% in apparel** and 66.8% in household furnishings.
  - After a one-period sale, prices **return to the original regular price 60.0–86.3% of the time**, depending on the major group.
  - About one-third of regular price changes are decreases.
  - Implied duration is computed as −1/ln(1−f).

  — [N&S 2008](https://eml.berkeley.edu/~enakamura/papers/fivefacts.pdf)
- On inflation: "the frequency of price increases covaries quite strongly with the rate of inflation, whereas the frequency of price decreases and the size of price increases and decreases do not." — [N&S 2008](https://eml.berkeley.edu/~enakamura/papers/fivefacts.pdf)

**Kehoe & Midrigan, "Prices are Sticky After All," NBER WP 16364 (2010); published J. Monetary Economics 75 (2015):35–53** — [NBER](https://www.nber.org/papers/w16364)
- **Exact algorithm (appendix):**
  - **Parameters:**
    - window half-width **l = 2 months** for BLS data and **l = 5 weeks** for Dominick's weekly data, so the window is 2l+1 = 11 weeks;
    - cutoff **c = 1/3**;
    - data requirement **a = 0.5**: the window needs ≥ 2·a·l periods with data.
  - **Step 1.** For each t, compute the window mode pM_t = mode(p_{t−l}…p_{t+l}) and f_t, the share of available periods at the mode. If the window holds too few observations, mark it as missing.
  - **Step 2.** Recursion: pR_t = pM_t if pM_t exists **and f_t > 1/3 and p_t = pM_t** (the mode must actually be charged in period t). Otherwise pR_t = pR_{t−1}.
  - **Step 3.** Repeat 5 times: a regular change that occurs without a change in the actual price is moved to the period where the posted price actually changed (the sets R, C and P in the appendix).

  — [Kehoe & Midrigan NBER w16364](https://www.nber.org/papers/w16364)
- **Facts:**
  - BLS: posted prices change 22%/month, regular prices 6.9%/month. **72% of price changes are temporary**, and 50% of temporary changes return to the previous regular price.
  - Prices sit at their annual mode **75% of the time**.
  - Dominick's (weekly): **94% of price changes are temporary**, and **80% return to the pre-existing price**. Prices change once every ~3 weeks, but regular prices only about once every 8 months.
  - Temporary prices occur about a quarter of the time but account for almost **40% of units sold**.

  — [Kehoe & Midrigan](https://www.nber.org/papers/w16364)

**Eichenbaum, Jaimovich & Rebelo, "Reference Prices, Costs, and Nominal Rigidities," AER 101(1) (2011):234–262** — NBER [w13829](https://www.nber.org/papers/w13829.pdf)
- The reference price is defined as "the most common price … within a given quarter", i.e. the quarterly mode. Reference prices "have an average duration of roughly one year, even though weekly prices change roughly every two weeks". Data: weekly scanner prices and costs from a major US retailer with more than 1,000 stores. — [EJR NBER w13829](https://www.nber.org/papers/w13829.pdf); [Kellogg summary](https://www.kellogg.northwestern.edu/academics-research/research/detail/2010/reference-prices-costs-and-nominal-rigidities/?p=1)
- The retailer resets the reference price so that realized markups over reference cost stay within roughly **±20%** of the desired markup (CEPR version, as summarized in search results). This is state dependence: the reference price changes when cost drifts enough. — [Bank of Portugal conference slides / CEPR summary](https://www.bportugal.pt/sites/default/files/2008cme_martin_eichenbaum.pdf)
- Kehoe & Midrigan cite the **"fraction of time at the annual mode"** statistic from Hosken & Reiffen (2004) and EJR as a filter-free measure of low-frequency stickiness. — [Kehoe & Midrigan](https://www.nber.org/papers/w16364)

**Chahrour (2011), "Sales and price spikes in retail scanner data," Economics Letters 110(2):143–146** — [IDEAS](https://ideas.repec.org/a/eee/ecolet/v110y2011i2p143-146.html)
- As described by Ray, Snir & Levy: Chahrour's algorithm uses a **13-week rolling window** and sets the reference price to the window mode, then "makes some modifications to smoothen" the series. It removes temporary changes **in either direction**, including short price *spikes* (e.g. a 1-week price hike). It "is less accurate near the endpoints". Following Chahrour, sales are assumed to last **≤6 weeks**. — [Ray, Snir & Levy, "Retail Pricing Format and Rigidity of Regular Prices," Economica (forthcoming), arXiv 2306.17309](https://arxiv.org/abs/2306.17309)
- Klenow & Malin (2011) define the reference price as the mode of a **13-observation rolling window**. Chahrour (2011) and Kehoe & Midrigan (2015) note that rolling-mode references can change "too early or too late" and offer fixes. — [Ray, Snir & Levy](https://arxiv.org/abs/2306.17309)

**Validation against retailer-declared sales (Ray, Snir & Levy):**
- Data: one year of weekly data from 3 Canadian stores that publish their own "regular price".
- At the Hi-Lo store: **508 store-promoted sale events, 509 events found by N&S filter A, 497 by Chahrour's reference-price method**.
- At the EDLP store, the store promoted only 12 sales, but filter A found 265 temporary cuts and Chahrour 261. The store treats its temporary cuts as regular-price cuts.
- Weekly regular-price change probability: 4.06% (Hi-Lo), 5.34% (HYB) and 13.38% (EDLP) by store definition, versus about 4.25–4.50% for the filtered series.
- Filter A misses a sale at the end of the sample because it "cannot find" the return. This is the endpoint and real-time problem.

— [Ray, Snir & Levy](https://arxiv.org/abs/2306.17309)

**A recent central-bank implementation on retailer-reported prices (RBA RDP 2026-02, Fink & Hambur, "Shifts in Australian Price-setting Behaviour around Large Shocks," Appendix A):**
- **Temporary sale:** the advertised price falls and then returns to an **equal or higher** level within **3 months**. The last 3 months of each item's life are excluded so a return can be observed.
- The flag is **removed if the post-discount price is followed by another decline within 14 days**, so that volatile pricing is not counted as discounting.
- **Clearance:** a fall of **≥25%** with the item exiting within **1 month**.
- Gaps of **≥30 days** are treated as stale.
- Changing the windows had "only minor effects".
- Validated against ABS regular and discount series: they "generally align well". No confusion matrix is reported.

— [RBA RDP 2026-02 Appendix A](https://www.rba.gov.au/publications/rdp/2026/2026-02/appendix-a.html)

**Guimaraes & Sheedy, "Sales and Monetary Policy," AER 101(2) (2011):844–876** — [DOI](https://doi.org/10.1257/aer.101.2.844); [CEPR DP 6940](https://ideas.repec.org/p/cpr/ceprdp/6940.html). This is a theory paper (sales as price discrimination). I found no sale-filter parameters in it; see Gaps.

### Inferences
- The tracker's "≤15% of in-stock time at or below the current price" is the mirror of the EJR and Kehoe–Midrigan "share of time at the reference/mode" statistic. In the data, regular prices hold about 75% of the time (Kehoe–Midrigan, annual mode), so a price held ≤15% of the time is clearly not the regular price. This criterion is well grounded.
- The **time-weighted median** is close to a **time-weighted mode** when prices sit at one level most of the time. The mode is the literature's choice because it ignores the depth and number of sale prices. The median moves when sales take more than 50% of the window, as with Hi-Lo or "permanent sale" shops. A drop-in alternative that copies Kehoe–Midrigan into one-sided, real-time form:
  - Regular price R_t = the **time-weighted modal in-stock price over the trailing W days**. Bucket prices to about 1% to merge algorithmic ±1-piastre jitter.
  - Accept R_t only if that mode holds **> 1/3 of in-stock time** in the window and **was actually charged** at some point in the last few days. Otherwise keep the previous R_{t−1}.
  - Kehoe–Midrigan used 11 weeks for weekly grocery data. For Egypt a **trailing 6–8 weeks** keeps drift small (Section 4).
- An **online V-shape confirmation** (N&S filter B/A in real time) gives labels after the fact. If the price returns to ≥ R within K days (GST use 1–2 weeks; Chahrour ≤6 weeks; RBA 3 months), the event was a temporary sale. If it stays down for more than K days and becomes the new mode, it was a regular price cut.
- These labels cannot gate an alert, because the alert must go out immediately. They can be used to evaluate the detector (Section 7) and to *update the reference* (keep sale prices out of R).
- The RBA "**another decline within 14 days removes the sale flag**" rule is useful: a staircase of cuts (algorithmic undercutting) is not a V-shaped promotion.

### Gaps
- The exact N&S filter algorithm (J, K, L) lives in the QJE online appendix, which I did not retrieve. Table IX gives only the window lengths (1–5 months) and the A/B definitions.
- Chahrour's exact smoothing steps were not retrieved: the article is paywalled. Only the 13-week window and the bidirectional spike removal are confirmed via Ray–Snir–Levy.
- I could not extract EJR's share of weeks at the reference price (the PDF text failed). The "±20% markup band" comes from a search summary of the CEPR version, not from the paper text.
- Guimaraes & Sheedy: no filter parameters found; the paper appears to be theory only.
- "Sales Spotter: An Algorithm to Identify Sale Prices in Point-of-Sale Data" (Swedish WP 2015-13) exists, but I could not read its method — [IDEAS](https://ideas.repec.org/p/swe/wpaper/2015-13.html).

---

## 2. How online prices move (Cavallo; Gorodnichenko–Talavera; Gorodnichenko–Sheremirov–Talavera; Amazon)

### Takeaway
Online regular prices change every ~1–4 months. That is roughly twice as often as offline and has been rising: US multichannel retailers went from 6.5 to 3.65 months between 2008 and 2017. Online sales are **rarer and shallower** than offline: a weekly sale frequency of about 1.3–2.7% and a typical depth of about 5–12%, versus 20–30% offline. A ≥20% threshold therefore sits in the **upper tail** of normal online sales, which suits "alert only on real discounts". Under high inflation, most price changes are increases (Argentina: 68%).

### Cited Findings

**Gorodnichenko, Sheremirov & Talavera, "Price Setting in Online Markets: Does IT Click?" JEEA 16(6) (2018); NBER w20819** — [NBER](https://www.nber.org/papers/w20819)
- Data: daily US and UK listings with click counts from a large shopping platform. The **sale filter** treats a change as temporary "if the price returns to its original level within one or two weeks" (the "one-week" and "two-week two-side" filters). Missing values are handled either as series breaks or by imputation.
- US mean **weekly frequency of sales: 1.3–2.2%**, or **1.7–2.7%** with click weights. **No sales are found in more than half of the products.**
- Average sale depth: **10.5–11.9%** unweighted and **4.4–5.3%** click-weighted, "smaller than the size of sales in regular stores (about 20–30 percent)".
- Duration of price spells is about **7–20 weeks** depending on how sales are treated.
- Only **11.9%** of goods have a constant price over their life, versus 77% in an offline apparel study.
- Synchronization of sales across goods within a seller is <3% (US) and 4% (UK). Sales are staggered, not storewide.
- Thanksgiving and Christmas have seasonal effects; Monday clicks are 33% higher.

— [GST NBER w20819](https://www.nber.org/papers/w20819)

**Gorodnichenko & Talavera, "Price Setting in Online Markets: Basic Facts, International Comparisons, and Cross-Border Integration," AER 107(1) (2017); NBER w20406** — [NBER](https://www.nber.org/papers/w20406)
- Online prices change "approximately once every 3 weeks or less", versus about 4 months or more in regular stores. The typical size is **~4%**, versus ~10% offline.
- Exchange-rate pass-through is 60–75% online versus 20–40% offline, with a half-life of about 2.25 months (search summary). — [NBER w20406](https://nber.org/system/files/working_papers/w20406/w20406.pdf)

**Cavallo, "Scraped Data and Sticky Prices," REStat 100(1) (2018); NBER w21490** — [NBER](https://www.nber.org/papers/w21490)
- Daily scraped supermarket prices for Argentina, Brazil, Chile, Colombia and the USA, 2007–2010. Table 6:

  | | Argentina | Brazil | Chile | Colombia | USA |
  |---|---|---|---|---|---|
  | Inflation | 17.1% | 5.1% | 2.7% | 4.2% | 0.1% |
  | Price increases as share of changes | **68%** | 57% | 54% | 55% | 52% |
  | Freq+/Freq− | **2.00** | 1.36 | 1.16 | 1.20 | 1.0 |
  | Implied duration (days) | 64 | 38 | 75 | 45 | 42 |
  | Implied duration (months) | 2.1 | 1.3 | 2.5 | 1.5 | 1.4 |

  Mean absolute changes are 10–16% except in the USA, where increases average 29% and decreases 22%. — [Cavallo 2018](https://www.nber.org/papers/w21490)
- Sales are flagged in **19% of US observations** (79% of US products) and 3–8% in Latin America. — [Cavallo 2018, Table 2](https://www.nber.org/papers/w21490)
- The well-known prevalence of **very small price changes is largely an artefact** of weekly averaging, unit values and imputation in scanner and CPI data. Daily scraped prices show far fewer tiny changes. Hazard functions slope upward at first rather than being flat. — [Cavallo 2018](https://www.nber.org/papers/w21490)

**Cavallo, "More Amazon Effects: Online Competition and Pricing Behaviors," Jackson Hole 2018; NBER w25138** — [NBER](https://www.nber.org/papers/w25138)
- At large US multichannel retailers, the implied duration of **regular prices (excluding sales)** fell from **6.48 months (2008–10) to 4.47 (2011–13) to 3.65 (2014–17)**. Over the same periods:
  - monthly frequency of regular price change rose from 15.4% to 22.4% to 27.4%;
  - absolute size fell from 17.5% to 16.2% to 15.0%;
  - the share of changes under 1% was 6.6%, 5.2% and 8.0%;
  - **sales as a share of price changes were only 4.0%, 4.0% and 3.3%.**

  — [Cavallo NBER w25138, Table 1](https://www.nber.org/papers/w25138)
- Walmart products that are easy to find on Amazon have implied durations about **20% shorter**. Online retailers mostly practise **uniform pricing** across locations. **Missing-price gaps shorter than 90 days were filled with the last available price** ("standard procedures in the literature"). — [Cavallo NBER w25138](https://www.nber.org/papers/w25138)
- The online frequency of price change is about twice the CPI frequency in comparable categories: an implied duration of about **3.5 months versus 7.6 months**. — [Cavallo NBER w25138](https://www.nber.org/papers/w25138)

**Cavallo, "Are Online and Offline Prices Similar? Evidence from Large Multi-Channel Retailers," AER 107(1) (2017):283–303; NBER w22142** — [NBER](https://www.nber.org/papers/w22142)
- Online and offline prices are identical **72%** of the time (83% in electronics, 92% in clothing). **Only 36% of sale prices are identical** across channels, and sales are 11% of observations. There is no evidence of IP-based or browsing-history price discrimination. — [Cavallo 2017](https://www.nber.org/papers/w22142)

### Inferences
- Online sales average about 5–12% in depth (GST), so a fixed **≥20%** cutoff is conservative. It catches the deep tail and misses ordinary 10% promos. That is consistent with the "real discount" principle, but it means recall on moderate sales is low by design.
- Regular prices change every ~2–4 months online (Cavallo 2018 and NBER w25138). A 90-day window therefore often **straddles a regular price change**. A 90-day median is in effect a smoothed regular price, not a fixed one.
- The "very few tiny changes in daily data" finding supports storing only true changes. The project already deduplicates into a step function.

### Gaps
- No peer-reviewed study of price dynamics on amazon.eg, noon, Jumia or other Egyptian e-commerce was found.
- Median *sale duration* online (in days) is not given as a single number in the papers I read. GST imply sales are usually ≤1–2 weeks by construction of their filter.

---

## 3. Robust statistics and change-point detection for step-function price series

### Takeaway
The usable toolbox:
- **Robust level:** time-weighted median or mode.
- **Robust spread:** MAD, scaled by 1.4826 to estimate σ under normality.
- **Spike flagging:** Hampel filter (|x − median| > k·MAD).
- **Online detection of a permanent level shift** that should reset the reference: CUSUM or Bayesian online change-point detection (BOCPD).
- **Offline segmentation** for analytics and labelling: PELT.

For a step function the natural unit is the *spell* (price, start, end, in-stock), not the sample. Every statistic should be **duration-weighted**, and spells should be truncated at stock-outs.

### Cited Findings
- **BOCPD (Adams & MacKay 2007, "Bayesian Online Changepoint Detection," arXiv:0710.3742).**
  - The method maintains the posterior over the **run length r_t** (time since the last change point), using a prior gap distribution P_gap and a hazard function H(τ).
  - At each new observation, the joint P(r_t, x_{1:t}) is updated by message passing. The run either grows (r_t = r_{t−1}+1, weighted by (1−H)·predictive probability) or resets to 0 (weighted by H·predictive probability, summed over r_{t−1}).
  - The predictive π(x_t | r, x^{(r)}) uses conjugate exponential-family sufficient statistics, so updates are O(number of retained run lengths).
  - The method is explicitly online.

  — [Adams & MacKay 2007](https://arxiv.org/abs/0710.3742)
- **PELT (Killick, Fearnhead & Eckley 2012, JASA 107:1590–1598)** gives exact penalized-likelihood segmentation in linear expected time. It is offline. — [DOI](https://doi.org/10.1080/01621459.2012.737745) (standard reference, not fetched this session)
- **CUSUM (Page 1954, Biometrika 41:100–114):** S_t = max(0, S_{t−1} + (x_t − μ_0 − κ)), signal when S_t > h. This is the classic sequential detector for a persistent mean shift. — [DOI](https://doi.org/10.1093/biomet/41.1-2.100) (standard reference, not fetched this session)
- **MAD and robust scale (Rousseeuw & Croux 1993, JASA 88:1273–1283).** MAD = median|x − median(x)|; σ̂ = 1.4826·MAD under normality. Its breakdown point is 50%. — [DOI](https://doi.org/10.1080/01621459.1993.10476408) (standard reference, not fetched this session)
- **Hampel (1974), "The influence curve and its role in robust estimation," JASA 69:383–393.** This is the basis of the Hampel identifier: flag x if |x − med| > k·1.4826·MAD, with k ≈ 3 typical. — [DOI](https://doi.org/10.1080/01621459.1974.10482962) (standard reference, not fetched this session)
- **Endpoint problem.** Rolling-mode references are "less accurate near the endpoints", and filters cannot classify a cut at the end of a sample because they cannot see the return. — [Ray, Snir & Levy](https://arxiv.org/abs/2306.17309)
- **Discreteness problem.** Filters find spurious "sales" when prices move by discrete steps (gasoline). — [N&S 2008](https://eml.berkeley.edu/~enakamura/papers/fivefacts.pdf)

### Inferences (mapping onto the tracker's step function)
- **Represent the series as spells** (p_i, t_start, t_end, in_stock). Time-weighted quantiles are computed by sorting spells by p_i and accumulating duration. That is what the current "typical" already does.
  - **Stock-outs:** drop out-of-stock time from the weights. Cavallo carries the last price forward across gaps <90 days, but for an alerter it is safer *not* to give out-of-stock time to any price.
  - Treat the first in-stock price after a long stock-out (e.g. >30 days, the RBA stale rule) with suspicion: it is often a reset price.
- **Irregular sampling.** A step-function store is already the right way to handle irregular sampling, but a spell's *true* start is uncertain within the crawl interval. Weight by observed duration. Do not count a spell as "long" unless crawls confirmed it at several points.
- **MAD on step data.** Many products have one or two distinct prices in 90 days, so MAD = 0. Robust z-scores then blow up. A Hampel or z rule needs a floor, e.g. σ̂ = max(1.4826·MAD, 2–3% of the median). Relative thresholds (≥20% below) are more robust for such sparse-valued series than z-scores. This matches the current design.
- **Permanent shift versus sale.**
  - Run a one-sided **CUSUM on log price relative to the reference**, or a BOCPD with hazard ≈ 1/(expected regular-spell length ≈ 60–120 days, from Section 2).
  - When the posterior says "new regime" *and* the new level has held for more than X days (Kehoe–Midrigan's requirement that the mode takes ≥1/3 of the window), **reset the reference to the new level**.
  - For upward shifts (devaluation repricing), reset fairly quickly so real sales after a devaluation are not missed. But require the new level to persist first: a higher reference makes measured discounts *larger*, so resetting on a short spike would create false alerts (see Sections 4 and 5).
  - For downward shifts, require persistence before lowering the reference. Otherwise a long sale silently becomes "typical" and later sales are measured against a too-low base. That only causes misses, not false alerts.
- **PELT** on the full history (offline, nightly) can produce regime segments for the UI and for evaluation labels. **BOCPD** is the principled online option. A simpler persistence rule (new modal level holding >1/3 of the trailing window) is probably enough and easier to explain.

### Gaps
- I found no published application of BOCPD or PELT specifically to retail sale detection. The mapping above is my inference.

---

## 4. Handling inflation and drift (Egypt context)

### Takeaway
The macro sale filters handle drift only implicitly, through short windows (±2 months, ±5 weeks, a quarter, 13 weeks) and by letting the regular price step up when a new mode takes hold. High-inflation data (Argentina at 17% a year) show *more frequent, mostly upward* changes. Increases outnumber decreases two to one, and pass-through online is fast. So the reference should come from a **short, regime-aware window**, not a long median.

### Cited Findings
- **Under higher inflation, upward changes dominate.** Price increases are 68% of changes in Argentina (17.1% inflation) versus 52% in the USA (0.1%), and Freq+/Freq− is 2.00 versus 1.0. Implied duration for Argentina is 64 days. — [Cavallo 2018](https://www.nber.org/papers/w21490)
- The **frequency of increases rises with inflation**; frequency of decreases and sizes do not. — [N&S 2008](https://eml.berkeley.edu/~enakamura/papers/fivefacts.pdf)
- **Online exchange-rate pass-through is 60–75%**, with a half-life of about 2.25 months (versus 20–40% offline). — [Gorodnichenko & Talavera NBER w20406](https://www.nber.org/papers/w20406); online competition also raises pass-through of nationwide shocks (gas prices, exchange rates) — [Cavallo NBER w25138](https://www.nber.org/papers/w25138)
- EJR's reference price resets when markup over reference cost drifts outside about ±20%. Regular prices follow costs in steps. — [EJR](https://www.nber.org/papers/w13829.pdf); [summary](https://www.bportugal.pt/sites/default/files/2008cme_martin_eichenbaum.pdf)
- Some studies deflate before filtering. Cavallo's simulation step divides each price by the geometric average of price changes in the same category ("cell-relative imputation"), a category-relative adjustment. — [Cavallo 2018](https://www.nber.org/papers/w21490)

### Inferences
- **Lag bias of a trailing median.** With steady inflation π a year, a trailing W-day median sits about W/2 days in the past. At 30% a year, W = 90 gives ln(1.3)·45/365 ≈ 3.2% below the current regular price.
- **Effect on errors.** Under steady upward drift the reference is *too low*, so measured discounts are understated. The tracker misses some real deals but does not produce false alerts. After a devaluation step (+20–40% overnight), a 90-day median stays far below the new price for 45+ days. During that time even a genuine 20% sale back to the old price is invisible.
- **False positives come from the opposite case:** a price that was temporarily *high* (a spike or "raise-then-discount") pulling the reference up. A median resists spikes shorter than half the window. The ≤15% time-at-or-below rule also protects.
- **Practical drift fixes, in rough order of simplicity:**
  1. **Shorter window**: trailing 42–60 days for the mode or median. Online regular prices change every ~2–4 months, so this still usually covers one regular spell.
  2. **Regime-aware reference**: when a persistent upward shift is confirmed (new mode >1/3 of time, or CUSUM/BOCPD), compute the reference **only from spells since the change point**.
  3. **Category or shop index deflation**: build a chained index from the median log change of regular prices of products in the same category per week (Cavallo's cell-relative idea). Express history in today's money before taking the median: p̃_s = p_s · I_now / I_s.
  4. **Asymmetric treatment**: allow quick upward resets and slow downward resets of the reference.
- Option 3 matches Egypt's devaluation shocks best because it moves all products at once, but it needs enough products per category. Options 1–2 work per product.

### Gaps
- I found no paper that evaluates sale filters specifically under high or hyper-inflation (Argentina, Turkey, Egypt). The inflation evidence above is about frequency and direction of changes, not filter accuracy.
- I did not retrieve Egyptian CPI or devaluation figures in this session.

---

## 5. Fictitious reference prices and "raise then discount"

### Takeaway
Regulators define an honest "before" price as the **lowest price charged in the ≥30 days before the reduction** (EU Price Indication Directive Art. 6a, introduced by the Omnibus Directive 2019/2161, confirmed by the CJEU in C‑330/23). Field evidence shows inflated reference prices work on buyers and are widespread online. Detection follows directly: compare the cut price with the *pre-cut* minimum, not with the shop's strikethrough price, and flag price rises in the weeks before a cut.

### Cited Findings
- **EU Art. 6a:** any announced price reduction must show the "prior price", the **lowest price applied during a period of not less than 30 days prior to the reduction**. CJEU case **C‑330/23 (26 Sept 2024)** confirmed that the percentage discount must be computed from that lowest 30-day price. Member states may set exceptions for perishables, products on sale <30 days, and progressive reductions (where the prior price is the price before the *first* reduction). — [EUR-Lex consolidated Dir. 98/6/EC](https://eur-lex.europa.eu/eli/dir/1998/6/2022-05-28); [Baker McKenzie on C‑330/23](https://insightplus.bakermckenzie.com/bm/international-commercial-trade/european-union-the-cjeu-confirms-that-a-price-reduction-must-be-calculated-based-on-the-lowest-price-in-the-last-30-days); [EUMonitor](https://www.eumonitor.nl/9353000/1/j4nvk6yhcbpeywk_j9vvik7m1c3gyxp/vitgbgi39zzb)
- **Ngwe (HBS):** for an outlet retailer, each **$1 increase in the posted "original price" raised willingness to pay by $0.77**. Many outlet "original prices" "were never selling prices". — [HBS Working Knowledge](https://www.library.hbs.edu/working-knowledge/bargain-hunters-beware-a-store-s-original-price-might-not-be-after-all)
- Staelin, Urbany & Ngwe (2023, *Journal of Marketing*), "Competition and the Regulation of Fictitious Pricing": the FTC stopped enforcing its fictitious-pricing guides about 50 years ago, and the practice has spread. — [Notre Dame Mendoza summary](https://mendoza.nd.edu/news/protecting-consumers-from-deceptive-pricing/)
- **Advocacy evidence (not peer-reviewed).**
  - Consumer Watchdog: 45% of 1,005 Amazon listings showed a reference price. Some "was" prices averaged **70% above** the highest price seen on a price tracker; Amazon disputed the study. — [Consumer Watchdog](https://consumerwatchdog.org/privacy/amazon-still-inflating-discounts-despite-new-name-consumer-watchdog-says/)
  - Consumers' Checkbook tracked 25 retailers for 6 months from Feb 2025 and found discounts at most stores were continuous, so "regular" prices were rarely charged. — [news report, Jul 2026](https://www.kold.com/2026/07/27/how-some-retailers-inflate-original-prices-create-fake-discounts/)
- Chahrour's method explicitly removes **short upward price spikes** as well as sales, i.e. it treats temporary *increases* as non-regular. That is exactly what defeats "raise then discount". — [Ray, Snir & Levy](https://arxiv.org/abs/2306.17309)

### Inferences (implementable rules)
- **Honest discount** = 1 − p_now / min{ in-stock price over [t_cut − 30d, t_cut) }, where t_cut is the start of the current low spell. Use the minimum, not the median, of the 30 days before the cut. This is the strictest legal-style test. Using both gives a two-level check:
  - "real" means p_now is ≥20% below the time-weighted median of 90 days **and** below the 30-day pre-cut minimum by some margin (e.g. ≥10%);
  - otherwise the cut merely undoes a recent rise.
- **Raise-then-discount flag:** max in-stock price over [t_cut − 30d, t_cut) exceeds the time-weighted median over [t_cut − 120d, t_cut − 30d) by more than X%, with X ≈ 10–15% (above normal monthly inflation), and the cut lands near that older median. Then suppress the alert or label it as "back to normal".
- **Spike-robust reference:** exclude spells whose price exceeds the window median by more than k·max(1.4826·MAD, 3%) *and* lasted less than Y days (Chahrour-style spike removal) before computing typical.
- The shop's strikethrough or "was" price (which the noon, Amazon and Jumia parsers may capture) can be audited by the same logic. If the shop's "was" price is more than 10% above the highest observed in-stock price in 90 days, mark it fictitious. This audit is a useful feature in itself.

### Gaps
- I did not find a peer-reviewed *statistical detection algorithm* for fictitious reference pricing beyond the legal 30-day rule. The rules above are inferences.
- No Egyptian regulation equivalent to Art. 6a was researched.

---

## 6. Pricing-algorithm research relevant to detection

### Takeaway
Online retailers reprice on **fixed cadences**: within the hour for the fastest firms, daily, or weekly early on Sunday morning. Algorithmic third-party sellers on Amazon (and by analogy noon) **match the lowest or Amazon price** and generate frequent small oscillations. For a detector this means:
1. expect many small, short-lived changes that are not sales;
2. the time-of-day and day-of-week cadence of each shop tells you when to crawl;
3. Buy-Box or lowest-offer volatility is higher where algorithmic sellers are present.

### Cited Findings
- **Brown & MacKay, "Competition in Pricing Algorithms," AEJ: Microeconomics 15(2) (2023):109–151; NBER w28860.**
  - Data: hourly prices for over-the-counter allergy drugs at the 5 largest US online retailers, Apr 2018 to Oct 2019.
  - "Two firms have high-frequency algorithms that change prices within an hour, one firm updates prices once per day, and the remaining two have weekly pricing technology, updating their prices early every Sunday morning."
  - Faster firms react quickly to slower rivals' changes. Firms with slower technology charge different (higher) prices, so price dispersion is linked to asymmetric pricing frequency.
  - In equilibrium, simple algorithms can **raise** prices.

  — [Brown & MacKay NBER w28860](https://www.nber.org/papers/w28860)
- **Chen, Mislove & Wilson, "An Empirical Analysis of Algorithmic Pricing on Amazon Marketplace," WWW 2016.**
  - They identified **over 500 sellers very likely using algorithmic pricing**, defined as sellers whose prices track a target (the lowest price or Amazon's price). Detection uses a change-count threshold (≥20 changes) plus correlation with the target.
  - Algorithmic sellers win the **Buy Box more often**, even when they are not cheapest.
  - Products with algorithmic sellers have **more volatile lowest price and Buy Box**.
  - **65% of non-algorithmic seller–product pairs never changed price.**

  — [Chen, Mislove & Wilson 2016](https://mislove.org/publications/Amazon-WWW.pdf)
- **Synchronization is low.** Online sales are staggered across goods: <3–4% synchronization. — [GST](https://www.nber.org/papers/w20819). Online and offline price changes coincide only 19% of the time — [Cavallo 2017](https://www.nber.org/papers/w22142)

### Inferences
- **noon `SKU@seller` keys.** On noon and on Amazon's marketplace, the *offer* price of a given seller and the *lowest* or Buy-Box price behave differently. Algorithmic sellers oscillate by small amounts, so a sale detector should ignore sub-2–3% changes when computing "time at or below the current price". Bucketing prices before computing modes handles this.
- **Algorithmic undercutting.** A drop caused by one algorithmic seller undercutting another (a staircase down, then a reset up) is an Edgeworth-like pattern, not a promotion. The RBA rule "another decline within 14 days cancels the sale flag" is a usable heuristic for labelling.
- **Crawl timing.** Shops with weekly cadences tend to change prices at a fixed weekday and hour (Brown–MacKay found Sunday early morning). The tracker can learn each shop's change-time histogram from its own change log. It could then schedule extra crawls right after the usual repricing moment, which helps time-to-notice.

### Gaps
- No direct evidence on repricing cadences of amazon.eg, noon or Jumia. Edgeworth-cycle evidence in e-commerce specifically (as opposed to gasoline) was not retrieved this session.

---

## 7. Evaluating a deal detector without labelled data

### Takeaway
The literature validates filters in three ways:
1. against **retailer-declared sale or regular flags**: BLS sale flag (N&S), store regular prices (Ray–Snir–Levy), ABS full and discount series (RBA);
2. by checking **ex-post reversal**, i.e. whether the price returns to the old regular level (N&S: 60–86% return; Kehoe–Midrigan: 50% BLS, 80% Dominick's);
3. by checking that filter-derived aggregates match flag-derived ones (N&S Table IX).

All three carry over to a tracker that collects the shop's own strikethrough or "deal" badges and its own future prices.

### Cited Findings
- **Agreement with retailer labels.** At a Hi-Lo store, filter A found 509 and Chahrour 497 versus 508 store-promoted sales. At an EDLP store, filters flagged about 260 temporary cuts that the store did *not* call sales (12 promoted). Agreement depends heavily on the shop's pricing format. — [Ray, Snir & Levy](https://arxiv.org/abs/2306.17309)
- **Aggregate agreement.** Filter A with a 5-month window gives 11.4%/month regular-change frequency versus 8.7% (sale flag) or 10.7% (sale flag plus clearance). Results swing widely with the window and whether substitutions count. — [N&S 2008](https://eml.berkeley.edu/~enakamura/papers/fivefacts.pdf)
- **Return rates as a validity signature.** In N&S, 60.0–86.3% of one-period sales return to the original regular price. Kehoe–Midrigan report 50% (BLS) and 80% (Dominick's weekly) of temporary changes returning. — [N&S 2008](https://eml.berkeley.edu/~enakamura/papers/fivefacts.pdf); [Kehoe & Midrigan](https://www.nber.org/papers/w16364)
- **Validation against an independent series.** The RBA validated its imputed regular and sale series against ABS-recorded ones using level, trend, duration and change-size distributions, not classification accuracy. Retailer labels themselves are inconsistent at fine levels. — [RBA RDP 2026-02](https://www.rba.gov.au/publications/rdp/2026/2026-02/appendix-a.html)
- **Sales and volume.** About 40% of units sell during temporary-price periods even though those periods are about 25% of the time, so sales are when people actually buy. — [Kehoe & Midrigan](https://www.nber.org/papers/w16364)

### Inferences (concrete metrics the tracker can compute from its own DB)
- **Reversal rate (precision proxy).** For each alert at time t with reference R, check whether the in-stock price returns to ≥ 0.95·R within N days (N = 14, 30, 60).
  - High reversal means the alert caught a *temporary* sale, i.e. a real deal.
  - No reversal means either a regular price cut (the reference was stale; a lower-value alert) or a permanent new level.
  - Under inflation, also count "returns to ≥ the drifted reference".
- **Buyer-regret precision.** P(min price over the next N days ≥ alerted price · (1 − ε)): would a buyer have found it cheaper soon after? This directly measures "alert only on real discounts". Use N = 7 and 30 and ε = 2%.
- **Recall proxy.** Run the detector offline (a backtest replaying history) and compare with **ex-post labels**. A two-sided Kehoe–Midrigan or N&S filter A run on the full history, with future data allowed, labels every V-shaped sale ≥20% deep. Recall = share of those labelled sales that the online detector alerted, together with the **time-to-notice** (alert time − sale start). Precision against the same labels = share of alerts that fall inside a labelled sale.
- **Shop labels as weak supervision.** Where parsers capture a strikethrough price or a deal badge (Amazon Today's Deals, noon deals), measure agreement between detector alerts and badges, discounting badges whose "was" price fails the 30-day-minimum audit (Section 5).
- **Human spot labels.** Sample about 100 alerts and about 100 near-misses each month and label them by hand. This is cheap and gives confidence intervals; the literature's equivalent is the BLS collectors' sale flag.
- **Stability checks.** Report how alert counts change when the window (60/90 days), the threshold (15/20/25%) or the time-share cutoff (10/15/33%) vary. N&S Table IX shows how parameter-sensitive filters are.

### Gaps
- I found no published evaluation of a *consumer-facing* deal alerter (Keepa or CamelCamelCamel style) with precision and recall figures. The metrics above are inferences that adapt the literature's validation approaches.
