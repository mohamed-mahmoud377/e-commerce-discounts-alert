# Roadmap: making it better

Ordered by value for effort. "Why" says what problem each one solves.

## Now: the public release

**Admins review every deal, approved ones go to the public channel** (built 4 Nov 2026, [PUBLISHING.md](PUBLISHING.md)).
Next on that path: a Review page in the web UI; editing a public post when the deal ends; the deals that can skip review,
learned from the admins' decisions (until then, publishing waits for a person, which costs flash sales); affiliate links.

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

11. **Shop rules for the other shops.** *Started 4 Nov 2026* with Jumia's placeholder prices (100,000 and
    50,000 EGP as no price, 10,000 as a doubtful reference; [SHOP_RULES.md](SHOP_RULES.md)). Still open: the
    candidates seen on amazon.eg (100,000 and 50,000 from third-party sellers, probably needing a per-seller
    rule), noon (10,000 on 25 products) and B.TECH, listed in SHOP_RULES.md. *4 Nov 2026:* noon's new-listing alerts are off
    (58% unproven); a new listing could alert again from the same model's price on other shops instead of the
    seller's claim (31 of the 428 would have qualified).

## Later: scale and polish

11. **HTTPS / private network for workers.** Today workers talk to the coordinator over plain HTTP with a
    token. Better: **Tailscale** (free; machines join a private network, so port 8787 can be closed to
    the internet) or Caddy + a domain for automatic HTTPS.
12. **Web dashboard**: product page with a price chart, search, freshness per group, alert history.
    The data is all in SQLite already; FastAPI can serve it next to the API.
13. **More shops**: Raya (B.TECH, 2B, Dream 2000 done 27 October 2026, Carrefour 28 October, Dubai Phone and Hardware Market 30 October, Amazon Now 3 November). Each needs one `sites/<shop>.py` (`fetch_page` +
    parser) and a saved-page test; everything else (planner, scheduling, alerts) is shared. *Ready for 20+
    shops since 4 Nov 2026*: one table of shops (`deals/shops.py`), `make new-shop` for the skeletons and
    `tests/test_shops.py`, which fails until a new shop has everything (adapter, screenshot spec, a saved page
    that parses, doc, config, worker script and setup docs); the web UI's Shops page, shop filters by kind,
    and a Devices table with shops down the side. Jumia Egypt was
    added on 26 October 2026 ([JUMIA.md](JUMIA.md)); its open ideas: crawl `/flash-sales/` to mark flash
    deals, use the Official Store badge and the other-sellers price (`mpg`) in alerts.
14. **CI**: when the repo is on GitHub, run `pytest` on every push and deploy `main` automatically (the
    same `scripts/deploy.sh`).
15. **Parser health checks**: *prices done 4 Nov 2026*: a page whose in-stock listings mostly come without a
    price files a parser error and fails `parser <shop>` in health (0 of 120,356 tracked products were in
    stock without a price that day, so this only fires on a broken parser). Still open: ratings, stock and
    titles the same way, and not recording the broken page's "no price" into history.
18. **When the catalogue passes ~250,000 products** (measured 4 Nov 2026 at 120,000): title search
    (`title LIKE '%word%'`) takes 0.17 to 0.19 s on the droplet, a full scan; an FTS5 trigram index
    (about 40 MB more) would make it instant. The matching pass takes 15 to 18 s and about 70 MB in its own
    process; the coordinator holds 52 MB of its own memory (the rest of its 160 MB is page cache the kernel
    takes back; 0 out-of-memory kills under the 300 MB limit). Neither needs work before then.
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
- The coordinator speaks plain HTTP on port 8787: since 4 Nov 2026 the web UI and every data API need an admin
  login (username + password, 90-day JWT), but the password and token cross the network unencrypted. HTTPS
  (Caddy in front, with a domain) would close that.
