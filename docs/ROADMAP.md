# Roadmap: making it better

Ordered by value for effort. "Why" says what problem each one solves.

## Now: do these first

1. **Connect Telegram** (2 min, needs you): see the README. Until then alerts are only visible with `make alerts`.
2. **Let history build up for 1–2 weeks, then tune.** The strongest rules (`price_drop`, `all_time_low`)
   need our own history, so the first days rely on "new product" rules. After two weeks, review the
   alerts you got and tune `min_drop`, per-group `min_drop` and `min_price` in the config.
3. **Keep a home machine awake.** The Mac is the only machine that can crawl noon (and reliably
   Amazon). When it sleeps, crawling pauses (you'll get a ⚙️ "stalled" alert). Options: Energy Saver →
   "Prevent automatic sleeping when the display is off" while plugged in, **or a Raspberry Pi / old
   laptop at home** as an always-on worker (≈ 5 W). A second worker on a friend's connection
   doubles capacity and adds redundancy.

## Next: better deals, fewer false alarms

4. **Verify before alerting** (high value). For `price_error` and `new_product` deals, fetch the
   product page once before sending: confirm the buy-box price, seller (Amazon vs. unknown
   third party), condition (new vs used/renewed), and coupons. Listing cards occasionally show another
   seller's or a used offer's price.
5. **Volatility-aware thresholds.** Some products ping-pong ±20% every week between sellers. Add a
   percentile rule: alert only if the price is below the 5th percentile of its 90-day time-weighted
   distribution **and** ≥ X% below typical. A flat 30% then works for stable products, while volatile
   ones need a real outlier.
6. **Cross-site matching** (high value). *Matching done 29 Oct 2026* ([MATCHING.md](MATCHING.md)): model
   codes from titles group 23,700 listings into 5,000 products found on 2+ shops; shown in the web UI and
   `make models`, not used by alerts yet. Next: the "cheaper elsewhere" section in alerts, then phones
   (no code in their titles). Originally: match the same model on Amazon and noon (brand + model
   number from titles, e.g. `UA55CU7000`, `MQ5235`). Then:
   - "cheapest across both sites" alerts,
   - a real reference price for **new listings** (the other site's typical price), which is better than the
     seller's crossed-out price,
   - one alert per model instead of one per listing.
7. **Group listings by model** on the same site (many sellers and colours of one product) and compute
   the typical price per model, which helps the same cold-start problem.
8. **Telegram buttons and commands**: 👍/👎 on each alert (feedback to tune rules), `/track <url> <price>`
   for a personal target price on one product, `/mute <sku>`, `/status`, `/top` (best deals today), and
   a **daily digest** (top 10 deals, system health).
9. **Effective price**: parse coupons ("Save 10% with coupon") and bank offers; alert on the price
   you'd actually pay.
10. **Fast lane for hot items.** *Half done 27 Oct 2026:* the shops' own deal feeds are crawled as extra
    roots of each priority group (Amazon `p_n_deal_type`, noon `min_offer_price=7_days`, Jumia
    `/flash-sales/`), and priority groups cap their backoff (`max_interval_minutes`). Still open: re-check
    products that are near an alert threshold, or on a personal watchlist, every few minutes via their
    product page.

## Later: scale and polish

11. **HTTPS / private network for workers.** Today workers talk to the coordinator over plain HTTP with a
    token. Better: **Tailscale** (free; machines join a private network, so port 8787 can be closed to
    the internet) or Caddy + a domain for automatic HTTPS.
12. **Web dashboard**: product page with a price chart, search, freshness per group, alert history.
    The data is all in SQLite already; FastAPI can serve it next to the API.
13. **More shops**: Raya (B.TECH, 2B, Dream 2000 done 27 October 2026, Carrefour 28 October, Dubai Phone and Hardware Market 30 October). Each needs one `sites/<shop>.py` (`fetch_page` +
    parser) and a saved-page test; everything else (planner, scheduling, alerts) is shared. Jumia Egypt was
    added on 26 October 2026 ([JUMIA.md](JUMIA.md)); its open ideas: crawl `/flash-sales/` to mark flash
    deals, use the Official Store badge and the other-sellers price (`mpg`) in alerts.
14. **CI**: when the repo is on GitHub, run `pytest` on every push and deploy `main` automatically (the
    same `scripts/deploy.sh`).
15. **Parser health checks**: daily fill-rate report (share of listings with price, rating, stock),
    so a *partial* layout change is caught, not just "0 products".
16. **Amazon from the droplet**: datacenter IPs are mostly refused. If you ever need it without a home
    machine, the options are fetching through the headless browser (slow, memory-heavy) or a paid
    residential proxy. Not needed while a home worker runs.
17. **Machine-learned ranking** (once there are a few hundred 👍/👎 labels): learn which alerts you
    actually like from drop size, history length, volatility, badges, brand and rating, and rank or
    filter by that score.

## Known limits (today)

- Prices come from listing cards, not product pages (see item 4).
- New listings can only be judged by the shop's own claims plus signals until we have ~3 days of history.
- noon's crossed-out price is seller-set and treated with suspicion; its "lowest price in N days" badge is trusted more.
- Amazon category browsing works from home IPs only (tested 2026-10-25).
