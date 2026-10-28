# Noticing price drops fast on a fixed crawl budget: freshness theory, change-rate estimation and revisit scheduling for the slice scheduler

Notes on sources. Formulas marked **[verified]** were read in the primary text this session. Those marked **[paper, not re-read]** come from the cited paper, but its full text could not be fetched this session (the Stanford/UCLA servers refused connections), so check the notation before relying on it. Formulas marked **[derived here]** are my own derivations from the stated models. They are standard probability, but no paper was found that states them in this form.

Notation used throughout:
- slice `i` has drop/change rate `λ_i` (events per minute) and value `v_i`;
- `c_i` is its crawl cost (pages);
- `T_i = 1/f_i` is its revisit interval;
- `B` is the request budget (pages per minute, per shop).

---

## 1. Cho and Garcia-Molina: Poisson changes, freshness and age, uniform vs proportional refresh, optimal allocation, change-rate estimators

### Takeaway
Under a Poisson change model, freshness is a concave, saturating function of revisit frequency. That is why a uniform schedule beats a proportional one, and why the freshness-optimal schedule gives *zero* visits to items that change too fast to keep fresh. Change rates can be estimated from "changed / unchanged since last visit" observations, with a bias-corrected log estimator (regular intervals) or an MLE (irregular intervals).

### Cited Findings
- **Definitions [verified].** Freshness of a copy at time t is 1 if the local copy equals the live page and 0 otherwise. Age is 0 if the page has not changed since the copy was taken, and otherwise equals t minus the page's last modification time. The crawler maximises average freshness or minimises average age. Coffman et al. (J. Scheduling 1998) restated the goal as minimising the fraction of time pages stay outdated, as a polling system. — [Wikipedia, Web crawler, "Re-visit policy" (citing Cho & Garcia-Molina 2003, ACM TODS 28(4):390–426)](https://en.wikipedia.org/wiki/Web_crawler)
- **Uniform beats proportional [verified, secondary].** Cho & Garcia-Molina compared uniform revisits (every page at the same rate) with proportional revisits (rate ∝ change rate). "The uniform policy outperforms the proportional policy in both a simulated Web and a real web crawl": proportional spends too many fetches on fast-changing pages, which go stale again almost immediately. — [Wikipedia, Web crawler](https://en.wikipedia.org/wiki/Web_crawler), citing [Cho & Garcia-Molina, "Effective page refresh policies for Web crawlers", ACM TODS 28(4):390–426, 2003](https://doi.org/10.1145/958942.958945)
- **Shape of the optimum [verified, secondary].** For freshness, the optimal policy ignores pages that change too often. For age, visit frequency should rise *sub-linearly* with change rate. Both optima are closer to uniform than to proportional. Page changes fit an exponential (Poisson) inter-change model well. — [Wikipedia, Web crawler](https://en.wikipedia.org/wiki/Web_crawler)
- **Expected freshness for a page with Poisson rate λ revisited every I = 1/f [paper, not re-read]:** `F̄(λ, f) = (f/λ)·(1 − e^{−λ/f}) = (1 − e^{−λI})/(λI)`. Expected age: `Ā(λ, f) = 1/(2f) − 1/λ + (f/λ²)(1 − e^{−λ/f})`. The optimal allocation under `Σ f_i = F_total` solves `∂F̄(λ_i, f_i)/∂f_i = μ` (equal marginal freshness per fetch) for every page with `f_i > 0`. When `λ_i` is very large, the derivative stays below μ for every f, so `f_i = 0`. — Cho & Garcia-Molina, ACM TODS 2003, [doi:10.1145/958942.958945](https://doi.org/10.1145/958942.958945); the same paper as SIGMOD 2000, "Synchronizing a database to improve freshness", [doi:10.1145/342009.335391](https://doi.org/10.1145/342009.335391)
- **Freshness under randomised (Poisson) crawling at rate p [verified]:** time-averaged weighted freshness is `F(p) = Σ_i w_i·p_i/(p_i + Δ_i)`, subject to `Σ p_i ≤ B`, where Δ_i is the change rate. It can be maximised in O(N log N). — [Avrachenkov, Patil & Thoppe, "Online Algorithms for Estimating Change Rates of Web Pages", arXiv 2009.08142 (2020/21), §5](https://ar5iv.arxiv.org/html/2009.08142)
- **Change-rate estimators [paper, not re-read; form corroborated].** These are from Cho & Garcia-Molina, "Estimating frequency of change", ACM TOIT 3(3):256–290, 2003 ([doi:10.1145/857166.857170](https://doi.org/10.1145/857166.857170)). With n regular visits at interval I and X visits that detected a change:
  - Naive: `λ̂ = X/(nI)`. It is biased low, because several changes inside one interval count once.
  - Bias-reduced: `λ̂ = −ln((n − X + 0.5)/(n + 0.5)) / I`. The +0.5 terms remove the singularity when every visit sees a change (X = n), where the plain MLE `−ln(1 − X/n)/I` is infinite.
  - [Grimes et al., WWW 2008, "Microscale evolution of web pages"](https://archives.iw3c2.org/www2008/papers/pdf/p1149-grimes.pdf) restate the estimator in this log form, as "subtracting off the expected bias" plus a small adjustment against singularities. They report noticeably lower bias than the naive estimator even with fewer than 50 observations, especially when the change interval is about 0.5–2× the crawl interval.
- **MLE singularities and smoothing [verified].** Kolobov et al. note that "the MLE … yields Δ̂_w = ∞ if all crawls detect a change". Their fix is to add imaginary observation intervals of length 0.5, and an imaginary 0.5 observation for change-notification sources. They also warn that change rates are non-stationary, so estimate from a recent *suffix* of history, not the whole history. — [Kolobov, Peres, Lu & Horvitz, NeurIPS 2019, §6](https://papers.neurips.cc/paper_files/paper/2019/file/ad13a2a07ca4b7642959dc0c4c740ab6-Paper.pdf)
- **Online estimators [verified].** Avrachenkov et al. give cheap online alternatives to the MLE: an LLN estimator `x_k = p·Î_k/(k + α_k − Î_k)` (with p the crawl rate and Î_k the number of crawls that saw a change), and a stochastic-approximation update `y_{k+1} = y_k + η_k[I_{k+1}(y_k + p) − y_k]`, both converging almost surely. They note that the MLE and moment-matching estimators are unstable (infinite when every access sees a change) and expensive to recompute. — [arXiv 2009.08142](https://ar5iv.arxiv.org/html/2009.08142)

### Inferences (what this means for the slice scheduler)
- **Irregular-interval MLE [derived here].** Our slices have irregular gaps because of the adaptive interval and worker availability. For one slice or one product with gaps `t_j`, the Poisson likelihood of "changed in gap j" is `1 − e^{−λ t_j}`, and of "unchanged" it is `e^{−λ t_j}`. Setting the derivative to zero gives

  `Σ_{changed j} t_j / (e^{λ t_j} − 1) = Σ_{unchanged j} t_j`

  Solve it by bisection on λ. Add Kolobov-style pseudo-observations, for example one imaginary unchanged gap of 0.5·median gap, so the estimate stays finite. This is the estimator to store per slice. Here "changed" means a *drop of interest*, i.e. one that would alert, not any price change.
- **Slice rate from product rates.** A slice crawl observes every product in it, so estimate per-product drop rates and sum them: `λ_slice = Σ_p λ_p` (a superposition of Poisson processes is Poisson). This gives far more data per slice than "did anything change".
- **Uniform beats proportional, applied here.** The current rule of speeding up to 10 min when prices changed is proportional-like. Under a *freshness* objective it would over-spend on slices with constant churn: noon multi-seller repricing, or Amazon sellers repricing every few minutes. Count only alert-worthy drops as "changes", not every price twitch. Otherwise the slices with repricing bots eat the budget, which is the exact failure Cho describes.
- **Freshness formula as a diagnostic.** With `λ_slice` estimated, `(1 − e^{−λT})/(λT)` is the fraction of time the stored prices for that slice are current. Show it in `make devices` and the UI as a per-slice health number.

### Gaps
- The full texts of the TODS 2003 / SIGMOD 2000 / TOIT 2003 papers could not be fetched (server refusal), so the Ā(λ,f) expression and the exact estimator notation are from the papers as I know them, not re-read. The TOIT paper also has estimators for when the last-modified date is known. Those don't apply here, since shops expose no price timestamps.

---

## 2. Later work: Wolf et al., Olston & Pandey, Kolobov et al. (LambdaCrawl), Azar et al., Upadhyay et al., Whittle index. Which policy minimises time-to-notice vs maximises freshness

### Takeaway
The best-fitting modern result is Kolobov et al. (NeurIPS 2019). They minimise a *harmonic staleness penalty*, which charges for every unseen change (not only the first). The optimum is a closed-form, square-root-like allocation that never starves a source. Sources with *complete change observability*, such as feeds and notifications, should be crawled on a different rule and get disproportionate bandwidth. For our real objective, catching value-weighted drops fast, the derivations below give square-root allocation for persistent drops and a greedy index `λ·v·m/c` for short flash sales.

### Cited Findings
- **Harmonic staleness objective [verified].** Policy cost is `J^π = lim_{T→∞} E[(1/T)∫_0^T Σ_w μ_w·C(N_w(t)) dt]`. Here N_w(t) is the number of changes at source w since its last crawl, μ_w is importance, and `C(n) = H(n) = Σ_{i=1}^{n} 1/i` (0 if n = 0). C increases strictly (it penalises every missed change, unlike binary freshness) and is discrete-concave (diminishing penalty). — [Kolobov et al., NeurIPS 2019, §3](https://papers.neurips.cc/paper_files/paper/2019/file/ad13a2a07ca4b7642959dc0c4c740ab6-Paper.pdf)
- **Incomplete observability (we only learn of a change by crawling) [verified].** For Poisson crawling at rate ρ_w, `J^π = −Σ_w μ_w·ln(ρ_w/(Δ_w + ρ_w))`, which is infinite if any ρ_w = 0, so starvation is ruled out. The unique optimum under `Σ ρ_w = R` is

  `ρ_w* = (√(Δ_w² + 4·μ_w·Δ_w/λ) − Δ_w) / 2`

  with the multiplier λ > 0 found by bisection (Algorithm LambdaCrawl-IncomplObs, O(log((λ_upper−λ_lower)/ε)·|W|)). Prop. 9: if μ_w/Δ_w is the same constant for all sources, then `ρ_w* = μ_w·R/Σμ`. — [Kolobov et al. 2019, Props. 1–3, 9](https://papers.neurips.cc/paper_files/paper/2019/file/ad13a2a07ca4b7642959dc0c4c740ab6-Paper.pdf)
- **Complete observability (the source tells you when it changed) [verified].** Policy: on each change notification for w, crawl w with probability p_w. Then `J^π = −Σ μ_w ln(p_w)`. The unconstrained optimum is `p̂_w* = R·μ_w/(Δ_w·Σ_{w'} μ_{w'})`, i.e. expected crawl rate p_w·Δ_w ∝ importance. LambdaCrawl-ComplObs handles the p ≤ 1 constraints optimally in O(|W|²). The full LambdaCrawl splits bandwidth between the two classes by golden-section search. — [Kolobov et al. 2019, Props. 4–7](https://papers.neurips.cc/paper_files/paper/2019/file/ad13a2a07ca4b7642959dc0c4c740ab6-Paper.pdf)
- **Results [verified].**
  - Dataset: 18,532,314 URLs crawled daily for 14 weeks, with bandwidth R = 20% of the URLs.
  - Harmonic-optimal LambdaCrawl is near-optimal on the binary metric too, but not the other way round: LC beats BinaryLambdaCrawl by 35% on harmonic cost.
  - Uniform (UC) and change-rate-proportional (CC) heuristics "did poorly".
  - The complete-observability URLs (only 4% of the data) gave a **2× reduction in harmonic cost** when treated as such rather than conventionally, and LC gives them a disproportionately large share of bandwidth.
  - Code: [github.com/microsoft/Optimal-Freshness-Crawl-Scheduling](https://github.com/microsoft/Optimal-Freshness-Crawl-Scheduling).
  - LambdaLearnAndCrawl alternates estimating rates and re-optimising, and converges to the optimal policy (Prop. 8).
  - Source: [Kolobov et al. 2019, §5–7](https://papers.neurips.cc/paper_files/paper/2019/file/ad13a2a07ca4b7642959dc0c4c740ab6-Paper.pdf); [MSR page](https://www.microsoft.com/en-us/research/?p=596461)
- **Azar et al. [verified via citation].** "Tractable near-optimal policies for crawling" (PNAS 2018) gives BinaryLambdaCrawl, optimal for binary freshness. It "may crawl-starve sources", and any Poisson-rate policy can be de-randomised into an approximately periodic schedule. — cited and characterised in [Kolobov et al. 2019](https://papers.neurips.cc/paper_files/paper/2019/file/ad13a2a07ca4b7642959dc0c4c740ab6-Paper.pdf)
- **Wolf et al. 2002 [verified via citation].** "Optimal crawling strategies for web search engines" (WWW 2002) minimises *embarrassment*: the chance a user is served a stale result, which weights staleness by how often the page is actually used. Kolobov et al. classify it, with binary freshness, age and harmonic staleness, as a convex separable resource-allocation problem, implemented through specially built importance scores. — [Kolobov et al. 2019, related work](https://papers.neurips.cc/paper_files/paper/2019/file/ad13a2a07ca4b7642959dc0c4c740ab6-Paper.pdf)
- **Olston & Pandey 2008 [abstract-level].** "Recrawl scheduling based on information longevity" (WWW 2008, pp. 437–446) separates ephemeral from persistent content. Ephemeral material is generally not worth recrawling for, and persistent content is. They report better freshness at lower cost than earlier policies. A later summary reports that longevity is not strongly correlated with change frequency (not verified in the primary text). — [WWW 2008 archive](https://archives.iw3c2.org/www2008/papers/fp597.html)
- **Whittle index for ephemeral content.** Avrachenkov & Borkar, "Whittle index policy for crawling ephemeral content" (IEEE CDC 2015 / IEEE TCNS 2016):
  - Crawling is cast as a restless-bandit average-reward control problem in which each source's content gives reward (clicks) that decays with time.
  - Because the exact problem is intractable, it is decoupled per source with a Whittle index, and the policy crawls the highest-index sources.
  - The index is derived for a deterministic model, with an extension to a stochastic one.
  - A follow-up learns the index with LSPE(0) reinforcement learning.
  - Sources: [arXiv 1503.08558](https://arxiv.org/pdf/1503.08558); [HAL follow-up](https://hal-univ-tlse3.archives-ouvertes.fr/INRIA/hal-02416599)
- **Upadhyay et al., "Learning to Crawl"** (AAAI 2020: 6046–6053; arXiv 1905.12781): it exists and addresses learning crawl schedules when change rates are unknown. Its content was not retrieved this session. — [dblp](https://dblp.org/pid/25/8778); [arXiv 1905.12781](https://arxiv.org/abs/1905.12781)
- **Kolobov et al., "Optimal Freshness Crawl Under Politeness Constraints"** (SIGIR 2019) adds per-host politeness limits to the freshness optimisation. Content not retrieved. — [Webis anthology entry](https://ir.webis.de/anthology/2019.sigirconf_conference-2019.52)

### Inferences (what this means for the slice scheduler) [derived here]
- **Objective A: minimise value-weighted delay to notice a *persistent* drop.** A drop that lasts until seen is noticed on average T_i/2 after it happens with periodic visits, or 1/f_i with Poisson visits.
  - Minimise `Σ_i λ_i v_i T_i/2` subject to `Σ_i c_i/T_i = B`.
  - The Lagrangian `Σ a_i T_i + μ(Σ c_i/T_i − B)` with `a_i = λ_i v_i/2` gives `a_i = μ c_i/T_i²`, so

    `T_i = √(μ·c_i/(λ_i·v_i))`, i.e. visit rate `f_i ∝ √(λ_i·v_i/c_i)`.

  - This is the **square-root law**: sub-linear in change rate, like Cho's age-optimal policy, and Kolobov's ρ* ≈ √(μΔ/λ) for small Δ. Doubling a slice's drop rate should cut its interval by √2 (×0.71), not halve it. A slice twice as expensive (more pages) gets an interval √2 longer.
  - Solve for μ from the budget: `√μ = Σ_i √(c_i λ_i v_i) / B`.
- **Objective B: maximise the value of *transient* drops caught (flash sales that revert).** Suppose drops in slice i arrive at rate λ_i, last D ~ Exp(mean m_i) and are worth v_i if seen while live.
  - With periodic visits every T, the catch probability is `P_catch = (m/T)(1 − e^{−T/m})`. This has the same functional form as Cho's freshness, with λ replaced by 1/m.
  - With Poisson visits at rate f it is `f/(f + 1/m)`, Kolobov's ρ/(Δ+ρ) with Δ = 1/m.
  - Maximise `Σ λ_i v_i·g_i(f_i)` with `g(f) = m f (1 − e^{−1/(m f)})`. Its marginal is `g'(f) = m(1 − e^{−1/(mf)}) − e^{−1/(mf)}/f`. That tends to m when T ≫ m (linear regime: every extra visit buys the same probability m/T) and decays like 1/(2 m f²) when T ≪ m (saturation).
  - Optimum: `λ_i v_i g'_i(f_i) = μ c_i` for active slices, solved by bisection on μ exactly as in LambdaCrawl.
  - Practical reading: while intervals are much longer than drop durations, the problem is nearly linear. Give marginal visits to the slices with the largest index `λ_i·v_i·m_i/c_i` (expected drop-value-minutes per page) until their T approaches m_i.
  - Slices whose drops are *very* short relative to any affordable T have a tiny λ·v·m index and get starved, Cho's "ignore pages that change too fast". That is right unless their v is large.
- **Keep the per-group caps (`max_interval_minutes`).** Kolobov's harmonic objective is infinite if any source is starved, and the paper shows harmonic-optimal policies stay near-optimal on other metrics. The caps are the practical version of that non-starvation guarantee. Objective B alone would starve some slices, which breaks the "honest reference" rule (own history must stay populated), so keep the caps as constraints.
- **The current "most overdue relative to own interval" scheduler** (`argmax elapsed_i/T_i`) is a good *executor* of a target-rate vector. It de-randomises Poisson rates into near-periodic visits, which Azar et al. show is near-equivalent and which is better than Poisson for both objectives. The weak part is how T_i is *set*: "10 min on change, back off to cap when quiet" is a heuristic. Replace it with T_i from the square-root formula (Objective A) or the bisection (Objective B), recomputed every few hours from the estimated λ_i, v_i, m_i, c_i and the measured per-shop B.
- **A Whittle-style "next slice" index that needs no planned T_i**, if wanted: expected value waiting in slice i is `v_i·(1 − e^{−λ_i τ_i})/c_i` (P(at least one drop since last visit) per page), or `v_i·λ_i·τ_i/c_i` under the harmonic/count view, with τ_i the time since the last visit. Hand out the slice with the highest index. This is the greedy index for Objective A. It is not the exact Whittle index of the papers above, which I did not retrieve.
- **Deal feeds are the "complete observability" class.** Amazon Today's Deals JSON, noon's "lowest price in 7 days" and Jumia flash sales tell us *that* and *where* a drop happened. Kolobov's 4%-of-URLs → 2× cost reduction result says these deserve disproportionate bandwidth. The optimal reaction is: on each feed event, fetch the affected product or slice with probability p ∝ importance/event-rate (p = 1 when budget allows). Feed polling rate should be set by how fast the feed itself turns over.

### Gaps
- The exact Whittle index formulas (Avrachenkov & Borkar), the SIGIR 2019 politeness algorithm and the Upadhyay et al. method were not retrieved. Wolf et al. and Olston & Pandey were seen only via abstracts or citations.
- No paper was found that optimises "delay to detect a *transient* event that reverts" under a crawl budget. The Objective B derivation above fills that gap from first principles.

---

## 3. Signals for prioritising: change rate, time-of-day/week, event calendars, contagion, feeds, popularity, predicting price changes

### Takeaway
The literature supports change-rate learning from a recent window (non-stationarity) and importance weighting. Self-exciting point processes (Hawkes) are the standard model for "one change makes more changes likely soon", which is the formal version of our speed-up-after-change rule and of campaign contagion. No e-commerce-specific study of price-change timing was retrieved this session.

### Cited Findings
- **Change rates drift.** Estimate from a suffix of recent history, not all history. Rates can also be shared across similar sources rather than learned per source, which helps when per-source data is thin. — [Kolobov et al. 2019, §6](https://papers.neurips.cc/paper_files/paper/2019/file/ad13a2a07ca4b7642959dc0c4c740ab6-Paper.pdf)
- **Importance weighting** (w_i, μ_i, embarrassment/clicks) is built into every modern objective. Wolf et al. weight by user-facing usage; Avrachenkov & Borkar weight by clicks or search requests on ephemeral content. — [Kolobov et al. 2019](https://papers.neurips.cc/paper_files/paper/2019/file/ad13a2a07ca4b7642959dc0c4c740ab6-Paper.pdf); [Avrachenkov & Borkar, arXiv 1503.08558](https://arxiv.org/pdf/1503.08558)
- **Self-exciting processes for "when to act" scheduling.** RedQueen (Zarezade, Upadhyay, Rabiee & Gomez-Rodriguez, WSDM 2017) is cited by Kolobov et al. as related online scheduling on point-process models. — [Kolobov et al. 2019 references](https://papers.neurips.cc/paper_files/paper/2019/file/ad13a2a07ca4b7642959dc0c4c740ab6-Paper.pdf). Hawkes process definition: [Hawkes, "Spectra of some self-exciting and mutually exciting point processes", Biometrika 58(1):83–90, 1971](https://doi.org/10.1093/biomet/58.1.83)
- **Amazon Lightning Deal start times are set by Amazon, not the seller.** The seller proposes a week, and Amazon picks the date and time of day. So the timing of these drops is driven by the platform, not random per product. — seller guides, e.g. [AdBadger](https://www.adbadger.com/blog/how-amazon-lightning-deals-work-for-sellers-a-complete-guide/), [ppc.land](https://ppc.land/lightning-deal/)

### Inferences (what this means for the slice scheduler) [derived here]
- **Hour-of-week rates.** Replace the single λ_i by λ_i(h) for hour-of-week bin h, estimated with the irregular-interval MLE per bin. Shrink thin bins toward the slice mean with a Gamma prior: `λ̂_i(h) = (X_i(h) + α·λ̄_i)/(E_i(h) + α)`, where X is drops seen, E the exposure in minutes and α a pseudo-exposure, e.g. 120 min. Plug λ_i(now) into the square-root/T formulas. Where drops cluster, for example at Cairo midnight when campaigns flip, intervals tighten automatically. This is checkable directly in `deals.db`: histogram first-seen times of alert-worthy drops by Cairo hour and weekday.
- **Contagion as a Hawkes intensity.** Model `λ_i(t) = λ_i^0 + Σ_{drops k in related slices} α_{rel}·e^{−β(t − t_k)}`, where "related" means same slice, same category in another price band, same brand, or a deal feed event in the category. The current "speed up to 10 min when prices changed" is a crude step version: α is large and lasts until the next quiet crawl. Fit α and β from `price_history`: given a drop in slice A at t, what is the excess rate of drops in slice B over the next 10/30/60 min versus baseline? Keep only pairs with clear excess. A deal-feed hit in category C should raise λ for all of C's slices, not just the product's slice.
- **Event calendar.** Multiply λ^0 by a known factor during sale seasons (White Friday, Ramadan, Prime Day equivalents, 11.11, payday), learned from last year's data in the DB if available. Also shorten group caps during those windows, since the budget is the same but λ·v rises.
- **Popularity / value v_i.** Use what the user cares about: watch-list hits, past alert clicks or subscriptions per category, price level, and discount depth (bigger expected discount means more value). Value enters both formulas only through λ·v, so a doubled v is the same as a doubled drop rate.

### Gaps
- No peer-reviewed study of *when* (hour/day) online retailers change prices, or of cross-product price-cut contagion on marketplaces, was retrieved this session. Candidate leads, all unverified here: Cavallo's scraped-online-price studies, e.g. "Scraped Data and Sticky Prices", REStat 2018; Chen, Mislove & Wilson, "An Empirical Analysis of Algorithmic Pricing on Amazon Marketplace", WWW 2016. Our own DB is the most relevant data source.

---

## 4. Politeness and anti-bot: rate limits, jitter, budget vs revisit, narrower or sorted slices

### Takeaway
Classic crawler etiquette is a fixed delay per host (seconds to a minute). The budget constraint `Σ c_i/T_i ≤ B_shop` is per host, so each shop is a separate allocation problem. Sorted listings (by discount or newest) and deal feeds turn "find the drop" into reading one page. In Kolobov's terms they move items from incomplete to (near-)complete observability, which is where the paper found the largest gains.

### Cited Findings
- **Published politeness intervals.**
  - Koster proposed 60 s between requests (which makes a 100k-page site take more than two months).
  - Cho used 10 s, WIRE 15 s, and Dill et al. 1 s.
  - Mercator waits 10× the previous download time.
  - Logs of known crawlers show 20 s to 3–4 min.
  - robots.txt's non-standard `Crawl-delay` is honoured by some engines.
  - Source: [Wikipedia, Web crawler, "Politeness policy"](https://en.wikipedia.org/wiki/Web_crawler)
- **Per-host politeness limits** can be built into freshness-optimal scheduling (Kolobov et al., SIGIR 2019), but details were not retrieved. — [Webis entry](https://ir.webis.de/anthology/2019.sigirconf_conference-2019.52)
- **Complete-observability sources** are a small share of URLs (4%) but cut harmonic cost 2× and get disproportionate bandwidth. — [Kolobov et al. 2019](https://papers.neurips.cc/paper_files/paper/2019/file/ad13a2a07ca4b7642959dc0c4c740ab6-Paper.pdf)

### Inferences (what this means for the slice scheduler) [derived here]
- **Budget arithmetic per shop.** The smallest uniform interval is `T_min = Σ_i c_i / B_shop`. Example: at about 1,900 pages/h total, with Amazon about 90% busy, if Amazon gets about 1,000 pages/h and its slices sum to about 1,000 pages, a full uniform sweep cannot be faster than about 60 min. Any slice with a 15-min cap then costs 4× its share. Check that `Σ_i c_i/cap_i ≤ B_shop` holds for the caps in config; if it doesn't, the caps are infeasible and the most-overdue scheduler silently stretches everyone. Real numbers come from `make devices` / the DB.
- **Pages, not slices, are the unit of cost.** A slice of 10 pages costs 10× a slice of 1 page. Use c_i in pages so deep slices with few deals do not crowd out shallow, deal-dense ones. Narrower price bands only help if they raise λ·v per page.
- **Sorted-by-discount or "newest" slices as cheap detectors.** If a listing can be sorted by discount %, and a newly discounted item lands on page 1, then crawling page 1 every few minutes covers the *whole* category's big drops for c = 1. That gives near-complete observability for the drops that matter. Fall back to the full slice crawl at a slower T for the honest reference history. The same logic applies to "price low→high" for watch thresholds and "newest" for new listings.
  - Caveats: the sort is often by *displayed* discount against a list price (which can be fake), and sort order can be cached or stale. Validate by checking how often a drop later found by the full crawl appeared on the sorted page 1 first; that is its recall.
- **Jitter.** The most-overdue scheduler plus random worker timing already de-periodises visits. Add ±10–20% random jitter to T_i so request times do not form a fingerprintable schedule. The theory says near-periodic is optimal, and small jitter costs little: for Objective A, mean delay rises only by about Var(T)/(2E[T]).
- **Anti-bot budget as a constraint, not a target.** Treat B_shop as the measured safe rate (no 503/captcha), learned per worker IP. Spending above it causes blocks that make T effectively infinite, the worst outcome for time-to-notice.

### Gaps
- No quantitative public data was found on Amazon.eg, noon or Jumia rate-limit thresholds. These must come from our own block logs.

---

## 5. Industry practice: price-intelligence update frequencies and flash-deal durations

### Takeaway
Commercial price-intelligence tools refresh hourly to daily, which is far slower than our 10–60 min. Amazon Lightning Deals typically run about 4–12 h but end early when promo stock sells out. A 15–60 min revisit catches most of them, but the stock-limited ones can be gone in minutes.

### Cited Findings
- **Price2Spy.** Basic plans check once a day or less often (configurable down to once in 14 days). Premium checks up to 8 times a day (every 3 h). A vendor comparison page claims monitoring "as frequently as every hour". — [Price2Spy](https://www.price2spy.com/?p=12664); [Price2Spy vs Prisync](https://price2spy.com/?p=155420); [Price2Spy hourly history](https://www.price2spy.com/blog/competitor-pricing-history-now-showing-prices-per-hour/)
- **Prisync.** Its Professional plan updates 3 times a day, and channel-based or GTIN-based tracking once a day. — [Prisync frequent price updates](https://prisync.com/features/frequent-price-updates); [Capterra Prisync pricing](https://www.capterra.com/p/153451/Prisync/pricing/)
- **Keepa.** A Keepa response quoted on an older add-on review page says tracked products update hourly, with very popular ones more often. A 2026 third-party blog claims about every 15 minutes. These conflict, and no official Keepa documentation was found. — [Firefox add-on reviews](https://firefox.marketplace.browsertotal.com/en-US/android/addon/keepa/reviews/?page=15); [ProfitPath blog](https://profitpath.com/en/blog/how-to-set-up-amazon-price-alerts-with-keepa)
- **camelcamelcamel.** It says Amazon prices "appear to change as often as we can check them", and that a price watch is how you get a product checked regularly (priority by user interest). The page returned 403 to the fetcher, so this is the search snippet only. — [camelcamelcamel blog](https://camelcamelcamel.com/blog/how-our-price-checking-system-works)
- **Amazon Lightning Deals.**
  - Several seller guides say they typically run 4–12 h; older guides say "up to 6 hours".
  - They end early when the promotional stock sells out.
  - Amazon chooses the date and time.
  - One guide reports that in August 2025 Amazon told sellers deals would run 12 h on main event days.
  - No official Seller Central page was found.
  - Sources: [AdBadger](https://www.adbadger.com/blog/how-amazon-lightning-deals-work-for-sellers-a-complete-guide/), [ppc.land](https://ppc.land/lightning-deal/), [Helium 10](https://www.helium10.com/blog/amazon-lightning-deals/), [Canopy Management](https://canopymanagement.com/the-complete-guide-to-amazon-lightning-deals/)

### Inferences (what this means for the slice scheduler) [derived here]
- **Lightning-deal-type drops (fixed D ≥ 4 h)** are always caught at T ≤ 60 min, with mean delay T/2 (7.5 min at T = 15). The real risk is stock-out: the effective D is min(scheduled duration, sell-out time). So the Amazon deals JSON fast lane, which shows the claimed % and the end time, matters more than slice T for these.
- **Our 10–60 min revisits are already 3–100× faster than commercial tools.** The competitive edge is in the deal-feed fast lanes and the allocation rule, not raw frequency.

### Gaps
- No official Amazon, noon or Jumia statement on flash-sale durations in Egypt was found. Jumia Flash Sales and noon "deal" windows need measuring from our own feeds, where end times are often in the feed payload.

---

## 6. Measuring detection latency and missed drops; estimating drop durations from censored observations

### Takeaway
Under memoryless drop durations, the number of consecutive crawls that see a drop is geometric. That gives a one-line estimator of mean drop duration, and from it the miss rate. For non-exponential durations, treat each drop as an interval-censored duration (Turnbull NPMLE or parametric Weibull MLE) and correct for the length bias, since short drops are under-sampled.

### Cited Findings
- **Binary catch probability for a Poisson-visited source** is `ρ/(Δ + ρ)`, and its log is the per-source harmonic cost term, so this is the standard building block. — [Kolobov et al. 2019, Prop. 1](https://papers.neurips.cc/paper_files/paper/2019/file/ad13a2a07ca4b7642959dc0c4c740ab6-Paper.pdf); [Avrachenkov et al., §5](https://ar5iv.arxiv.org/html/2009.08142)
- **Turnbull's NPMLE** is the standard nonparametric survival estimator for arbitrarily grouped, censored and truncated data. — [Turnbull, JRSS-B 38(3):290–295, 1976](https://doi.org/10.1111/j.2517-6161.1976.tb01597.x) (not re-read this session)

### Inferences (formulas to implement) [derived here]
- **Catch probability with periodic revisits every T.** A drop that starts at a uniformly random phase is seen iff a visit lands inside it.
  - Fixed duration D: `P(seen) = min(1, D/T)`, `P(miss) = max(0, 1 − D/T)`. Mean detection delay given seen is T/2 when D ≥ T, and D/2 when D < T.
  - D ~ Exp(mean m): `P(seen) = (m/T)·(1 − e^{−T/m})`, `P(miss) = 1 − (m/T)(1 − e^{−T/m})`.
  - Random (Poisson) visits at rate 1/T: `P(seen) = m/(m + T)`, which is worse than periodic. Keep the scheduler near-periodic.
- **Worked numbers (exponential durations):**

  | T (revisit) | m (mean drop duration) | P(seen) | P(miss) |
  |---|---|---|---|
  | 15 min | 10 min | 0.52 | 0.48 |
  | 15 min | 30 min | 0.79 | 0.21 |
  | 15 min | 60 min | 0.88 | 0.12 |
  | 60 min | 60 min | 0.63 | 0.37 |
  | 60 min | 240 min | 0.88 | 0.12 |
  | 10 min | 10 min | 0.63 | 0.37 |

  Rule of thumb: with exponential durations you need T ≈ m/4 to miss ≤ 12%. Fixed-duration deals are much more forgiving (no misses once T ≤ D).
- **Estimating m from our own data (memoryless case).**
  - Count K = number of consecutive crawls at which a drop is still live, starting from its first sighting.
  - Under Exp(m) and visits every T, after the first sighting the remaining life is again Exp(m), so `P(K ≥ k+1 | K ≥ k) = e^{−T/m}`. K is geometric, and `P(K = 1) = 1 − e^{−T/m}` = the share of drops seen only once.
  - MLE: `m̂ = −T / ln(1 − 1/K̄)`. Equivalently `m̂ = −T/ln(1 − s)` with s the fraction seen only once.
  - Example: if 40% of alert-worthy drops are seen at only one crawl at T = 15, then m̂ = −15/ln 0.6 ≈ 29 min, so P(miss) ≈ 21%.
- **Irregular gaps / non-exponential durations: interval-censored survival.**
  - For a drop first seen at crawl t_a, last seen at t_b, with the previous crawl at t_{a−1} (not low) and the next crawl at t_{b+1} (reverted), the duration D lies in `(t_b − t_a, t_{b+1} − t_{a−1})`. Drops still live now are right-censored at `> t_now − t_a`.
  - Fit with Turnbull's NPMLE, or with a parametric Weibull/log-normal MLE: `L = Π_closed [S(L_j) − S(R_j)] × Π_open S(L_j)`, with `S(x) = exp(−(x/η)^k)` for Weibull.
  - A Weibull shape k < 1 means many very short drops plus a long tail: price glitches and repricing bots alongside real sales.
- **Length-bias correction (to count what we miss).** We only observe drops that were caught, and P(seen | D) = min(1, D/T). Estimate the *true* drop rate with Horvitz–Thompson weights: `λ̂_true = Σ_{observed drops} 1/min(1, D̂_j/T_j) / exposure`. Equivalently, `#missed ≈ #seen × (1/P̄(seen) − 1)` using the fitted duration law. Fit the duration distribution with the selection term included: the likelihood of an observed drop is its interval probability divided by `P(seen)`.
- **Reversal times as a direct check.** `time_reverted − time_first_seen` is the residual life. Its distribution should match the fitted law. If drops that revert within one crawl are common (s is high), the slice's T is too long for its drop durations. That is exactly the signal for moving T_i under Objective B.
- **Detection-latency metric to log per slice.** For each alert, `latency_upper = t_first_seen − t_prev_crawl` (an upper bound on how late we were) and `latency_mean ≈ (t_first_seen − t_prev_crawl)/2`. Track p50/p90 per shop and per group in the daily report. Pair it with s (seen-once share) and estimated P(miss) so changes to scheduling can be judged against the first principle.
- **Cross-check with deal feeds.** Where a feed gives a deal's start/end time (Amazon deals JSON, Jumia flash sale), compute the empirical P(seen by slice crawl) and the delay directly. That is ground truth for the formulas above.

### Gaps
- Turnbull 1976 was not re-read this session. No paper was found that applies interval-censored survival analysis specifically to retail price-drop durations. The geometric-run estimator and length-bias correction above are derived here, not taken from a source.
