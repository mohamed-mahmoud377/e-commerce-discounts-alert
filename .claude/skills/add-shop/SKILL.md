---
name: add-shop
description: The complete walkthrough for adding a new shop or store to the deals price tracker, from the first probe to verified live alerts (probing access, the adapter, tests, screenshots, shop rules, matching, watch groups, docs, the safe shipping order, live checks). Use whenever the user names a shop or URL to add, track or support (add El Araby, track Raya, can we crawl this site), or asks what adding a shop takes. Use it together with work-on-deals, in place of the general steps there.
---

# Adding a shop to deals

Adding a shop is one adapter, one table row, one config block and one doc. **Core code does not change.** The
real work is learning how the shop behaves, because a wrong assumption about it means false alerts or a blocked
home IP. Eleven shops were added this way. Their docs in `docs/` are worked examples.

Work through the stages in order. Each ends in a gate: don't start the next stage until it holds. Everything
below was checked against the code on 5 Nov 2026 by rehearsing a fake shop. If the code disagrees, trust the code
and fix this file.

Run `make health`, `git status` and `git log -5` first (the `work-on-deals` skill, step 1). Its three promises
apply throughout: notice drops fast, alert only on real discounts, don't get blocked.

**Ask the user only for:** the shop's URL; what to do if `robots.txt` forbids crawling; a new watch group if the
shop fits none; and approval to commit. Decide everything else from measurements.

## What you don't touch

`alerts`, `bot`, `publish`, `health`, `uptime`, `audit`, `webui`, `matching`, `planner`, `coordinator` and `db` read
`deals/shops.py` or don't care which shop it is. New shops get their Arabic names, UI filters, Shops page, health
lines, uptime and error book for free. **There is no migration**: `products.site` is plain text.

The only core edits a new shop might need, each explained below: `scheduler.DEAL_FEED_MARKS` (stage 6), and the
noon-only seller warning (stage 4). If you think you need another one, you are adding a capability: say so to the
user first.

## Stage 1: Probe the shop, write down what you find

Every probe comes from the same home IP the crawler depends on. Go at about one request every 2 s, and **stop at the
first 403, 429 or challenge.** Fill in `docs/<SHOP>.md` as you go, with dates and numbers.

Start from the closest shop, it saves most of the work:

| Signs | Read first | How it is crawled |
|---|---|---|
| `cdn.shopify.com`, `/products.json` answers | `DREAM2000.md` | Collection JSON. No price filter, no search |
| `/wp-json/wc/store/v1/products` answers | `HARDWAREMARKET.md` | WooCommerce Store API. Ask for sold-out products; use `_fields` |
| Magento (`/graphql`, `.product-info-main`) | `2B.md` | GraphQL API |
| PrestaShop (`#our_price_display`) | `CAIROSALES.md` | HTML pages |
| Next.js (`/_next/`, an `RSC: 1` header works) | `DUBAIPHONE.md`, `CARREFOUR.md`, `BTECH.md` | RSC payload in the page, or a private API. `btech_eg._rsc_payload` and `_balanced` are reusable |
| JSON embedded in the HTML (`__STORE__`) | `JUMIA.md` | Parse the embedded JSON |
| A store inside a shop we have, with its own prices | `AMAZON_NOW.md` | A small subclass of the existing adapter |

Answer every one of these:

1. **Who can reach it?** Test from the Mac *and* the droplet (`ssh root@207.154.206.127`), with plain `curl` and with
   `curl_cffi` as Chrome. Sets `home_only` and which workers crawl it. What a refusal looks like (status, body).
2. **`robots.txt`:** record it. If it forbids crawling, tell the user and let them decide (they chose to crawl Dubai
   Phone anyway).
3. **The listing:** HTML, JSON, GraphQL? Page size? The page cap? Past the cap, does it stop or repeat? (Carrefour repeats
   page 20 forever.)
4. **An exact count?** Essential, see the planner trap in stage 2.
5. **A price filter?** On the current price, not the crossed-out one? Inclusive at both ends (ask for one product's exact
   price)? Does it hide sold-out products (Dubai Phone's does)?
6. **A stable sort?** Relevance order repeats and misses products between pages (Carrefour). Use id or price.
7. **Emptiness:** what do an empty price range and an unknown category answer? (Jumia redirects to the parent, Carrefour
   gives an empty 200, Dubai Phone a 404 page with HTTP 200.) They must be told apart.
8. **Sold-out products:** listed? Hidden unless asked for (Hardware Market)? Priced at a placeholder (Dubai Phone's 1 EGP)?
9. **Freshness:** a CDN cache delays every drop (Vercel's 10 minutes, so Dubai Phone's URLs carry the minute).
10. **Identity:** a stable product id? One price per id? Several sellers per product? Variants at different prices?
11. **A deal feed:** flash sales, a discount filter, sorting by last modified? Flash sales show up there first.
12. **Categories:** the tree, and the parent categories to watch. A parent holds products that are in none of its children.
13. **Product pages:** the Arabic URL, where price and seller sit, any barcode or model number.

**Gate:** all 13 are answered in the doc with measured values. Stop and ask if the shop has no exact count anywhere, or
refuses the Mac.

## Stage 2: The adapter

```bash
make new-shop NAME=elaraby LABEL="El Araby" URL=https://www.elarabygroup.com
```

This writes `src/deals/sites/elaraby_eg.py` and `docs/ELARABY.md`, and prints the `Shop(...)` row. Names are lowercase
letters and digits; `_eg` is added. A name starting with a digit can't be imported, so rename the file (`2b_eg` lives in
`twob_eg.py`). Keep `fetch_page` thin and parsing in plain functions that take a page's text, so tests can call them.

**The contract.** The class needs `name`, `max_slice_total`, `__init__(self, state_dir=None)` and `fetch_page(slice, page)`.
Keep the HTTP client in `self._session`: `details.fetch_details` uses it. What you return decides what happens:

| The adapter returns or raises | What the system does |
|---|---|
| `total` at or under `max_slice_total` | The planner keeps the slice as one job |
| `total` above it, or `None` with `has_next` | "Too big": the planner **splits by price** |
| `total=0` and no listings | Truly empty, no alarm. Empty ranges are dropped, an empty `deals:` feed is kept |
| No listings, on a slice planned with products | "0 products parsed": job failure, Telegram after 3 |
| `has_next=True` | The crawl loop asks for the next page, with **no limit of its own** |
| `Blocked` | No retries. That IP leaves the shop for 15 min, doubling to 24 h; Telegram after 3 strikes |
| `UnknownTarget` | The job fails at once with no retries; filed as "Category not found" |
| Any other exception | Retried after 5, 20 and 60 s, then the job fails |

**Traps** (the first was measured in the rehearsal):

- **The planner runaway.** A shop that never gives a count, or a total over `max_slice_total` with a filter that doesn't
  narrow it, keeps splitting down to 1 EGP ranges: **over 3,000 requests on one root from a home IP.** `None` is right only
  while a slice is too big and narrower ones get counted (Amazon's "over 1,000"). If there is no price filter, set
  `max_slice_total` above the biggest category and ignore the price part (Dream 2000, Dubai Phone).
- **Every root can carry a price.** `Watch.roots()` adds the group's `min_price`, so `category:tvs|price:2000-` arrives
  even at a shop with no filter. Ignoring it is fine: the coordinator applies the group's minimum to new products.
- **`has_next` must end.** Past the shop's cap, return `False`, or a shop that repeats its last page loops forever.
- **Don't raise `Blocked` for a 500.** It benches the IP. Only real refusals: 403, 429, a challenge body, a non-JSON answer
  from a JSON API.
- **`parse_money` reads `1,999.50` only.** It turns Hardware Market's page format `2.999,00` into 3 EGP.
- **Refuse what the shop lacks.** Raise `UnknownTarget` for slice kinds it has no answer to (`search:`, `deals:`), as Dream
  2000 and Amazon Now do.
- **Error text decides the error book.** `audit.RULES` reads it: a `ValueError` saying "layout" lands in "Could not read the
  page", one without lands in "Bug". Write `f"{self.name}: no product data in the page (layout changed?)"`.

**Each `Listing`:**

| Field | Rule |
|---|---|
| `sku` | Stable forever, never from the title. Several sellers at different prices: `SKU@seller` (`db.sibling_offers` looks for the `@`) |
| one key, one price | Colours fold into one listing at the cheapest in stock. Sizes that cost more are separate (`8GB / 256GB`), or a sold-out big model looks like a price drop (Dream 2000) |
| `price` | Whole piastres, `None` if none shown. Never the crossed-out price |
| `list_price` | Only when above `price`, else `None` |
| `list_price_kind` | **Default `"list"`.** `"was"` only if the shop says it is its own recent price: `"was"` counts as evidence alone and skips the 60% fake-discount limit (`pricing.py`), so a wrong one sends false alerts |
| `in_stock` | Keep sold-out products visible whenever the shop allows it |
| `url` | Absolute https, the English page (screenshots swap to Arabic) |
| `title` | English where possible: matching reads it. Unescape HTML entities |
| `brand`, `gtins` | Help matching. `gtins` only through `models.valid_gtin` |
| `seller`, `stock_left`, `badges` | When shown. Use the normalised names in `models.py`: strong badges count as evidence for an alert |

**Gate:** from the Mac, these look right, and 5 random products match the shop's page in a browser (price, crossed-out
price, stock):

```bash
.venv/bin/deals crawl <shop> "category:<id>" --pages 2 --show 5
.venv/bin/deals plan <shop> "category:<id>|price:1000-"   # watch the request count: a small category must take few
.venv/bin/python .claude/skills/add-shop/measure_shop.py <shop> "category:<id>" --pages 5
```

`measure_shop.py` walks real pages and reports repeats, count mismatches and the numbers stage 4 needs. Register the
shop first (stage 3) for these to find it.

## Stage 3: Register it and make `make test` pass

1. Paste the `Shop(...)` row at the end of `shops.SHOPS`:
   - `arabic`: the name Egyptians use, in Arabic script. `badge`: 1 to 3 letters, unique. `label`: unique.
   - `kind`: a `KINDS` key (a new kind is a new line there).
   - `home_only`: **display only.** `DEALS_SITES` decides who crawls it.
   - `roots`: what `deals categories <shop>` prints. `slug_codes`: stage 5.
2. Import the class in `sites/__init__.py` and add it to `SITES` **at the same position**; the test compares the order.
3. Then fix what fails. The scaffold alone keeps tests green; they fail from the moment it is registered:

| Failing test | Fix |
|---|---|
| `test_every_shop_has_a_screenshot_spec_doc_and_config` | `shots.SPECS` entry (stage 4); link the doc from `CLAUDE.md`; `[sites.<shop>]` in `config.example.toml` |
| `test_every_shop_parses_its_saved_listing_page` | Save a real page (below); add it to `LISTING_PAGES` in `tests/test_shops.py` |
| `test_every_shop_is_in_the_worker_script_and_setup_docs` | Append to `DEALS_SITES=noon_eg,...` in `scripts/mac-worker-env.sh`, `README.md`, `docs/RUN_A_WORKER.md`, `docs/GETTING_STARTED.md`, `docs/GUIDE.md` |
| `test_every_shop_with_alerts_has_a_spec` | Add it to `SAVED` in `tests/test_shots.py` (stage 4) |
| `test_a_shop_filter_can_be_a_kind` | Only for `marketplace` or `grocery`: that test lists those shops by hand. Add it |

Save a real listing page, from the Mac for a home-only shop, never hand-written. Choose a small category holding a crossed-out
price and a sold-out product (fixtures are up to about 250 KB):

```python
import gzip
from pathlib import Path
from deals.sites import SITES
site = SITES["<shop>"](Path("data"))
r = site._session.get("<listing url>", timeout=60)
assert r.status_code == 200
with gzip.open("tests/fixtures/<short>_category.html.gz", "wt") as out:
    out.write(r.text)
```

**Not checked by any test, so do it anyway:** parser tests in `tests/test_parsers.py`, copying `test_hardwaremarket_*`: exact
values of 2 or 3 real products, the URL builder with its price filter, `Blocked` and `UnknownTarget` with a monkeypatched session.

**Gate:** `make test` is green.

## Stage 4: Screenshot, and what the shop's data gets wrong

**Screenshot.** Add a `shots.SPECS` entry. Open the **Arabic** product page in a browser and pick stable hooks:

- `boxes`: the price block first (without it there is no screenshot), then the seller block **only if the shop shows a
  seller**. Each box lists alternative selectors. With CSS-module classes, match the stable part: `div[class*="_priceOfferCtr_"]`.
- `context` (title, main image), `ready` (visible once the product has rendered), `arabic=("/en/", "/ar/")` (`None` if
  English only), `hide` for in-page banners, `block` for a heavy font the page waits on.

```bash
.venv/bin/deals shot <shop> "<product url>" --out shot.jpg     # then look at the image
.venv/bin/python .claude/skills/add-shop/save_rendered.py <shop> "<product url>" tests/fixtures/<short>_product_rendered.html.gz
```

Add a row to `SAVED` in `tests/test_shots.py` with text its price box must hold, and the seller's name or `None`. Delete `shot.jpg`
after looking. A worker with `DEALS_SCREENSHOTS=1` that also crawls the shop takes these (`mac` and `abdelrahman-mac` both do).
Noon-style bot walls also need `shots.Shooter`'s user agent: `save_rendered.py` doesn't set it.

**Shop rules.** Use `measure_shop.py`'s numbers from at least 2 categories, and walk a whole slice. Most false alerts have come
from here (Jumia's placeholders caused 25). Any quirk found becomes a `ShopRule` in `shop_rules.RULES` with real products, the date
and a test in `tests/test_shop_rules.py`, plus `docs/SHOP_RULES.md` and the shop's doc:

| You measure | Precedent | Do |
|---|---|---|
| A round price repeated on unrelated products, or 1 EGP | Jumia 100,000 and 50,000; Dubai Phone under 10 EGP | `placeholder` rule |
| A round price that is sometimes real | Jumia 10,000 | `doubtful_reference` rule |
| A deposit or a "coming soon" price | Dubai Phone's 500 EGP pre-orders | Skip it in the adapter, plus a `note` rule |
| Nearly everything in stock has a crossed-out price | Hardware Market 98%, Dream 2000 | Kind `list`. If new-listing claims look made up, a `no_claim_alerts` rule (noon) |
| "Only N left" on 40% or more of in-stock products | Existing rules: 42% to 57% | `low_stock_noise` rule |
| A habit the adapter handles | Jumia's empty-range redirect | `note` rule naming the code |

**Third-party sellers.** The "not the shop itself, check the seller" warnings in `alerts.py` (`format_deal`) and `publish.py` are
hard-coded to `noon_eg`. Jumia, B.TECH and Carrefour are marketplaces without them. Mention this to the user for a new marketplace
rather than widening it quietly.

**Gate:** the screenshot looks right; every number above is in the doc; `make test` is green.

## Stage 5: Matching

Matching reads new titles by itself at its next pass. Choose only:

- **`slug_codes=True`** only if the **shop itself** writes the model code in its links' last words, one link per model (B.TECH, Cairo
  Sales, Carrefour). Not when sellers write them (Jumia), or one link covers several sizes (Dream 2000). `measure_shop.py` shows
  how many links look like it; read 20 yourself, because that number alone doesn't settle it.
- **Product pages** (optional, later): only if titles lack model codes, and **after a quiet week of crawling**. Add a parser that reads
  only the product's structured block to `details.PARSERS`, a test in `tests/test_details.py`, and the shop to `[details] sites` on the
  server. It adds up to 1,500 pages a day per IP, so state that. A page under 20,000 bytes counts as a refusal (`fetch_details`).

Don't bump `matching.VERSION` for a new shop; that is for rule changes (`docs/MATCHING.md`).

## Stage 6: Which groups, how fast

1. **Delays.** `[sites.<shop>]` in `config.example.toml`. A missing block means 2 to 4 s. Start at 2 to 4 s; go lower only with
   evidence (Hardware Market runs at 1 to 2 s, after about 150 clean test pages, some with no pause). A shop sharing a WAF with another needs more (Amazon Now 3 to 5 s).
2. **Roots.** Add the shop's parent categories to the existing `[[watch]]` groups, under a key named after the shop:
   `hardwaremarket_eg = ["category:39", "deals:39"]`. TVs, Refrigerators, Air conditioners, Gaming consoles, Phones, Tablets, Laptops,
   Desktops, Monitors, PC components, Headphones, Kitchen machines, Kitchen appliances, Cooking & dishwashers, Gaming peripherals,
   Wearables, Chargers & power banks. Skip groups where it has nothing above `min_price`. A new group needs `name`, Arabic `label`,
   `interval_minutes`, `min_price` and a `max_interval_minutes` cap: ask the user.
3. **Noisy sources** get `require_words` and `exclude_words` on the group, which affect every shop in it: see Gaming consoles.
4. **Fast lane.** If the shop has a feed, add it as a root named `deals:<id>`, kept to one page with `has_next=False`. Feeds are capped
   at `feed_max_interval_minutes` (10 min live). A feed that can't be named `deals:` needs its mark in `scheduler.DEAL_FEED_MARKS`,
   or it silently gets the slow cap.
5. **Do the sums, and report them** (promises 1 and 3):
   - Pages per pass = sum over roots of products above the minimum ÷ page size.
   - Pass time = pages × (response time + average delay). A worker runs one job per shop per IP at a time.
   - **Pass time must fit well inside the group's cap**, or the worst-case time to notice is the pass time. If not, raise the minimum price
     or drop a root rather than cutting delays.
   - State the pages per hour this adds and from which IPs, with that shop's block history.

A shop much over 20,000 products is a memory question too: the coordinator has `MemoryMax=300M` on a 1 GB droplet, and matching takes the
most. Look at memory after its first matching pass.

**Gate:** the sums are written in the doc's "How we crawl it" section.

## Stage 7: The docs

To find every list that must grow:

```bash
grep -rnE "Hardware ?Market|hardwaremarket" README.md CLAUDE.md docs .claude/skills/work-on-deals --include='*.md' | grep -v '^docs/HARDWAREMARKET'
grep -rnE "\b(eleven|11) shops|all eleven" README.md CLAUDE.md docs src
```

| File | What |
|---|---|
| `docs/<SHOP>.md` | Every scaffold section, with dates and measured numbers |
| `CLAUDE.md` | The opening list, the doc link, what the droplet crawls if not home-only, "all eleven shops" |
| `README.md` | Opening list, the diagram's shop lists, the code tree |
| `docs/GUIDE.md` | Opening list, adapters row, diagram, a paragraph on the shop, the access and signal tables, fast lanes table, screenshot list and table |
| `docs/INTERNALS.md` | Code map row, worker thread list, delays row, watch keys row, the dates in "Add a shop", "all eleven" on `shots.py` |
| `docs/MONITORING.md` | One row per new log line: what it means, what to do |
| `docs/PRODUCT_FLOW.md` | Step 1's list of card readers |
| `docs/CATEGORIES.md` | A section with its category ids |
| `docs/RUN_A_WORKER.md`, `docs/GETTING_STARTED.md` | The lists of shops (`DEALS_SITES` is tested, the prose isn't) |
| `docs/ROADMAP.md` | The "More shops" item |
| `docs/SHOP_RULES.md` | Its rules |
| `.claude/skills/work-on-deals/SKILL.md` | Promise 3's list of home-only shops, if it is one |

If this shop taught you something this skill doesn't say, add it here too.

## Stage 8: Ship, in this order

**The order matters.** Tested: a coordinator on old code **refuses to start** on a config naming an unknown shop (`watch 'TVs': unknown keys`),
which stops the whole system. A worker on old code **refuses to start** with an unknown shop in `DEALS_SITES`, and launchd restarts it in a loop.

1. **Check.** `make test`, and `.venv/bin/python -m pytest -q` (if they disagree, a test imports `from tests...`). Look at `git status`:
   `make deploy` ships uncommitted files too.
2. **Code.** Ask before committing. Then push to `main` (GitHub Actions deploys) **or** `make deploy`, never both. Confirm with
   `ssh root@207.154.206.127 cat /opt/deals/REVISION`.
3. **Workers** (no jobs exist yet, so this is harmless):
   - This Mac: `make mac-worker-env && make install-mac-worker`, which rewrites `worker.env` with every shop.
   - Teammates (`abdelrahman-mac`): **they `git pull` first**, then add it to their `DEALS_SITES`, then restart. Tell the user to pass this on;
     it is the step that gets forgotten (Amazon Now sat uncrawled on the Mac this way).
   - Only if not home-only: add it to `DEALS_SITES` in `/etc/deals/worker.env` and `systemctl restart deals-worker`.
4. **Server config**, last:
   ```bash
   ssh root@207.154.206.127 'cp /etc/deals/config.toml /etc/deals/config.toml.bak-$(date +%Y%m%dT%H%M)'
   # add [sites.<shop>] and the roots to /etc/deals/config.toml, same as config.example.toml, then check before restarting:
   ssh root@207.154.206.127 "/opt/deals/.venv/bin/python -c \"from pathlib import Path; from deals.config import load; print(len(load(Path('/etc/deals/config.toml')).watches), 'watches ok')\""
   ssh root@207.154.206.127 systemctl restart deals-coordinator
   ```
   At start, `sync_watches` creates a plan job per root. `make health` shows `crawl <shop>` as FAIL until the first slice finishes: expected for
   minutes, a problem if it lasts.

**Going wrong?** `make pause SITE=<shop> HOURS=3` stops it at once and ends by itself. To remove it, restore the config backup and restart
the coordinator. `worker.env` can stay: with no jobs the worker just waits.

## Stage 9: Verify live

**In the first hour.** `make health`, `make devices`, `make errors SITE=<shop> DAYS=1`, the web UI's Shops page, then `make sql`:

```sql
-- plans done? slices failing or too big?
SELECT kind, count(*) n, sum(last_done_at IS NOT NULL) done, sum(failures > 0) failing, sum(oversized) too_big, sum(expected_total) expected
FROM jobs WHERE site = '<shop>' GROUP BY kind;
-- products per group: compare with the shop's own numbers
SELECT grp, count(*) products, sum(in_stock) in_stock, sum(price IS NULL) no_price, sum(list_price IS NOT NULL) crossed_out
FROM products WHERE site = '<shop>' GROUP BY grp;
-- does what was read match what the shop counted? a few off is normal drift (625 vs 629), a big gap is not
SELECT target, expected_total, last_count, failures, last_error FROM jobs
WHERE site = '<shop>' AND kind = 'crawl' AND last_count != expected_total ORDER BY abs(last_count - expected_total) DESC LIMIT 10;
-- blocked? (also any home shop, if this one shares a WAF)
SELECT * FROM blocks WHERE site = '<shop>';
-- keeping pace with the groups' caps? minutes late, per group
SELECT grp, count(*) slices, round(max((julianday('now') - julianday(next_due_at)) * 1440)) most_late_min, round(min(interval_s) / 60.0) fastest_min
FROM jobs WHERE site = '<shop>' AND kind = 'crawl' GROUP BY grp;
```

Check 5 random products in a browser against their stored history, then test the whole alert path, which sends to the admin alone:

```bash
ssh root@207.154.206.127 runuser -u deals -- /opt/deals/.venv/bin/deals product <shop> <sku> --config /etc/deals/config.toml
ssh root@207.154.206.127 runuser -u deals -- /opt/deals/.venv/bin/deals test-alert <shop> <sku> --config /etc/deals/config.toml
```

Then confirm cross-shop results with `make models FIND=<a model other shops sell>`. A wrong match goes in `tests/fixtures/matching_gold.tsv`, per
`docs/MATCHING.md`.

**Silence on day one is normal.** A slice's first pass never alerts on new products (`bootstrap`), early drops need 1 day of history and ordinary drops 3.
So **come back after 1 day and after 3 days**, and tell the user you can't do this yourself unless they ask you to schedule it:

- `blocks`, `make errors SITE=<shop>`, the pace query again.
- `SELECT a.created_at, a.kind, p.title, p.url FROM alerts a JOIN products p ON p.id = a.product_id WHERE p.site = '<shop>' ORDER BY a.id DESC LIMIT 30;`
  Open them. A false alert from a placeholder or a made-up crossed-out price means a new shop rule (stage 4). Deals reach the admins' review group before the
  public channel, so this is the moment to catch them.
- `make health`'s flip-flop count: a jump means one key is holding two prices (stage 2's "one key, one price").

## Stage 10: Report and remember

Tell the user: what the shop is, its products and in-stock counts, who crawls it, the rules it needed with their evidence, and the actual output of the live checks.
Answer `work-on-deals`' impact questions: worst-case time to notice per group, whether alerts can come from the shop's anchor price, **pages per hour and from which IPs**,
old workers and old config. Say plainly what is unverified, including the 1-day and 3-day checks if they are still ahead, and which teammates still must pull.

Update memory, which the repo can't hold: `site-access-findings` (one line on how it is reached) and `deployment-state` (who crawls it, config backup name).

## What each shop taught (add to this)

Lessons from Raya, Vodafone, Dokkan Tech, Etisal and Orange (5 Nov 2026), each one nearly shipped wrong or cost an hour:

- **Stage 2's gate catches what nothing else does: do it.** Comparing 5 random products with their real pages found Orange's sizes. Compare the price, and read every mismatch; two of the three shops flagged were only rounding (Raya's 6,298.75 shows as 6,299) or display (Dokkan's "199.00" reads `19900`).
- **"One key, one price" fails silently on a listing that shows one price per product.** If a product page has size options, read one product's details: does the listing's price match every size? Orange's listing shows only the cheapest (53 of 98 phones). Measure the share per category; watch the clean categories, leave the rest and tell the user the cost of doing them per size.
- **An option that looks like packaging can be a price.** Dokkan's `Box` (Asia, MENA): 41 of 66 differ. Before adding an option to `IGNORED_OPTIONS`, count how many products price it differently. Fold nothing you haven't counted.
- **A refusal isn't always a block. Before writing a shop off, try the droplet and a second client.** Orange closes `/api` on `curl_cffi` whatever it impersonates, from every IP, yet answers urllib: the same failure from the droplet shows it's the client. Vodafone resets every second HTTP/2 request: `http_version="v1"` fixes it. Note Stage 1's rule still holds: stop at a 403, 429 or challenge.
- **Don't trust names or counts.** Dokkan's `wearables-2` is headphones, Orange's `special-offers` is air conditioners: sample titles (5 lines of code). Shopify's `products_count` can be wrong in both directions: stop paging on a short page.
- **Unknown parameters are often ignored silently.** Etisal returned the whole shop for `min_price=`, `category=` and `sub_category=`, with HTTP 200. Check that the *count changes* for each parameter you rely on.
- **SPA with a hidden API:** load the listing once in Playwright and log `xhr`/`fetch` responses; the request's headers show what the API wants. With introspection off, a Magento API names its fields in its errors ("Cannot query field").
- **Guest tokens can create a customer at the shop:** look in the JWT (`sub`, `CustomerId`), keep it for the session, and say so in the doc.
- **Screenshots: look at several, not one.** Etisal failed on 3 of 6 and passed on the others: `Spec.settle` waits out a page that redraws itself after hydration. A frame drawn in the wrong place while the rectangles are right means CSS `zoom` on `<html>` (Vodafone 0.82); `_DRAW` handles it now. Measure repeatedly through `Shooter.shoot`: image sizes that differ between runs of one page show the problem without opening them.
- **Before asking the user, finish what doesn't depend on the answer.** Fashion shops (no group, `robots.txt` forbids their listing pages) went into `docs/research/fashion-shops.md`, and the other five shipped.
