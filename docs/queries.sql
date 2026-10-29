-- Useful queries for the deals database.
-- Open data/server.db in IntelliJ (run `make db` first to download a fresh copy),
-- put the cursor inside a query and press Cmd+Enter.
-- Prices are stored in piastres: divide by 100 for EGP. Times are UTC.

-- 1. Overview
SELECT (SELECT COUNT(*) FROM products)                                   AS products,
       (SELECT COUNT(*) FROM products WHERE in_stock)                    AS in_stock,
       (SELECT COUNT(*) FROM price_history
         WHERE observed_at >= strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-1 day')) AS changes_24h,
       (SELECT COUNT(*) FROM alerts WHERE kind != 'system')             AS deal_alerts,
       (SELECT COUNT(*) FROM jobs WHERE kind = 'crawl')                 AS slices;

-- 2. Products per group and site
SELECT grp, site, COUNT(*) AS products, SUM(in_stock) AS in_stock,
       ROUND(MIN(price) / 100.0) AS cheapest_egp, ROUND(MAX(price) / 100.0) AS priciest_egp
FROM products GROUP BY grp, site ORDER BY grp, site;

-- 3. Workers: who is crawling, when they were last seen
SELECT name, last_ip, sites, last_seen_at, pages, jobs_done, version FROM workers ORDER BY last_seen_at DESC;

-- 4. What is being crawled right now
SELECT site, kind, grp, target, lease_worker, run_count AS products_so_far, lease_expires_at
FROM jobs WHERE lease_expires_at > strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now');

-- 5. Crawl schedule: each slice, how often it is revisited, when it is due next
SELECT grp, site, target, expected_total, interval_s / 60 AS every_min, last_done_at, next_due_at,
       last_count, last_changes, failures, last_error
FROM jobs WHERE kind = 'crawl' ORDER BY next_due_at;

-- 6. Latest price changes (old -> new)
SELECT p.site, p.grp, p.title, h.observed_at,
       ROUND(LAG(h.price) OVER w / 100.0) AS old_egp, ROUND(h.price / 100.0) AS new_egp,
       ROUND(100.0 * (h.price - LAG(h.price) OVER w) / LAG(h.price) OVER w, 1) AS change_pct
FROM price_history h JOIN products p ON p.id = h.product_id
WINDOW w AS (PARTITION BY h.product_id ORDER BY h.observed_at, h.id)
ORDER BY h.observed_at DESC LIMIT 200;

-- 7. Biggest drops in the last 3 days
WITH moves AS (
  SELECT product_id, observed_at, price,
         LAG(price) OVER (PARTITION BY product_id ORDER BY observed_at, id) AS before
  FROM price_history)
SELECT p.site, p.grp, p.title, m.observed_at, ROUND(m.before / 100.0) AS before_egp,
       ROUND(m.price / 100.0) AS now_egp, ROUND(100.0 * (m.price - m.before) / m.before, 1) AS change_pct, p.url
FROM moves m JOIN products p ON p.id = m.product_id
WHERE m.before > 0 AND m.price IS NOT NULL
  AND m.observed_at >= strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-3 days')
ORDER BY change_pct LIMIT 50;

-- 8. Deal alerts (newest first)
SELECT a.created_at, a.kind, ROUND(a.drop_pct * 100) AS drop_pct, ROUND(a.price / 100.0) AS price_egp,
       ROUND(a.reference_price / 100.0) AS reference_egp, a.score, p.site, p.grp, p.title, p.url,
       a.sent_at, a.error
FROM alerts a LEFT JOIN products p ON p.id = a.product_id
WHERE a.kind != 'system' ORDER BY a.created_at DESC;

-- 9. System alerts (blocked, stalled, failing)
SELECT created_at, message, sent_at, error FROM alerts WHERE kind = 'system' ORDER BY created_at DESC;

-- 10. Scarce products on a deal right now (few left + a deal/history badge)
SELECT site, grp, title, ROUND(price / 100.0) AS price_egp, ROUND(list_price / 100.0) AS crossed_out_egp,
       stock_left, badges, rating, reviews, url
FROM products
WHERE in_stock AND stock_left <= 5
  AND (badges LIKE '%deal%' OR badges LIKE '%lowest%')
ORDER BY stock_left, price;

-- 11. Full price history of one product (change the sku)
SELECT h.observed_at, ROUND(h.price / 100.0) AS price_egp, ROUND(h.list_price / 100.0) AS crossed_out_egp, h.in_stock
FROM price_history h JOIN products p ON p.id = h.product_id
WHERE p.sku = 'B00L5G12DW'
ORDER BY h.observed_at;

-- 12. Find a product by name
SELECT site, sku, grp, title, ROUND(price / 100.0) AS price_egp, in_stock, last_seen_at, url
FROM products WHERE title LIKE '%samsung%55%' ORDER BY price;

-- 13. Sites currently blocking an IP
SELECT site, ip, strikes, until, reason FROM blocks ORDER BY until DESC;

-- 14. The same product on every shop (change the model code; see docs/MATCHING.md)
SELECT p.site, ROUND(p.price / 100.0) AS price_egp, p.in_stock, pm.status, pm.note, p.last_seen_at, p.title, p.url
FROM product_models pm JOIN products p ON p.id = pm.product_id
WHERE pm.model = 'AHXP12BME' ORDER BY pm.status != 'model', p.price;

-- 15. Products on 3+ shops with the widest price spread right now
SELECT model, brand, sites, site_list, ROUND(low / 100.0) AS low_egp, ROUND(high / 100.0) AS high_egp,
       ROUND(100.0 * (high - low) / high) AS spread_pct
FROM models WHERE status = 'ok' AND sites >= 3 ORDER BY spread_pct DESC LIMIT 100;
