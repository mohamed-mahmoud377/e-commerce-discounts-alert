# The same product on different shops

`src/deals/matching.py` finds which listings on the eight shops are the same product, so a price can be
compared with what the other shops ask. Built 29 Oct 2026. It feeds the web UI, the `deals models` command and
the end of each deal alert (the same product's price on every other shop, cheapest first, and whether this
is the cheapest). **It never changes what is alerted**: the section is appended after the deal rules have
decided, the lookup is read-only, and if it fails the alert goes out without it (`alerts.show_other_shops`
turns it off).

## The identities, strongest first

| Tier | Key | Example | Status | In alerts |
| --- | --- | --- | --- | --- |
| Barcode | a GTIN-12/13/14 in the title, checksum verified; numbers starting with 2 (GS1 "restricted circulation": a store's own) are not barcodes | `hervy GTIN6223007910729` | ok | yes |
| Model code | brand + the maker's code | `sharp AHXP12BME`, `touchelzenouki 40566` | ok | yes |
| Product line | brand + line + variant (storage, RAM, network, CPU, GPU) | `samsung galaxya16 128GB 4GB 4G` | ok | yes |
| Spec signature | brand + product type + exact specs + decisive features, one product per shop, similar titles, close prices | `ata ~microwave 1200W+20L digital` | **probable** | **no** |
| By hand | your "same" / "not the same" | `- #12345` | ok | yes |

Each listing gets the strongest identity it has; a barcode is used when the title has no lettered code
(lettered codes are far more common, and choosing them keeps barcode and non-barcode listings together).

## Why model codes

The identifiers retailers match on are, in order: a barcode (GTIN/EAN), then **brand + manufacturer part
number (MPN)**, then titles and attributes (Google Shopping asks for brand + MPN when there is no
barcode). We checked what the shops give us:

- **Barcodes:** no shop puts one on its listing pages. Carrefour's SKUs are sometimes 13 digits, but they are
  in-store codes starting with 2. Fetching every product page for one would multiply the requests, and with
  them the block risk.
- **SKUs:** each shop's own (Amazon ASIN, noon `N...`/`Z...`, Jumia `XX123HA...`, B.TECH hashes). They never
  match across shops.
- **Model codes in titles:** most titles carry the maker's code: "Sharp ... - AH-XP12BME", "Sony WH-1000XM5",
  "Haier HSU-12KCSID". The shops write it differently (`WH-1000XM5`, `WH1000XM5`, `Wh-1000Xm5`, `WH-1000XM5/LM`),
  but it normalises to one string.

So a **model** is a normalised code (plus capacities, see below), and listings that share it are one product.
Matching on titles with machine learning (embeddings, as in Ditto or the WDC product corpus) was considered. A
model would not fit the 1 GB droplet, it can't explain its matches, and where a code exists the code is
almost always right. It may later help phones, which carry no code (see "Limits").

## How it runs

```
products ──1. extract (new / changed titles only)──▶ product_codes, product_code_index
                                                          │
          2. group: SQL counts + streamed decisions ──▶ temp tables ──3. diff in SQL──▶ product_models, models
                                                                                             │
                                        web UI product page, "Matching" queries, deals models ◀┘
```

Every hour the watchdog runs `python -m deals.matching` as a separate process at the lowest CPU priority
(`nice 19`, `oom_score_adj=1000`: if the coordinator's `MemoryMax=300M` is ever reached, the kernel kills
this process, not the coordinator). It is built to stay small however many products there are:

- **Extract once.** The codes of a title are read once and kept in `product_codes` with a hash of
  (title, brand, seller) and the rules `VERSION`. A pass re-reads only new or changed titles, or every
  title after a rules change. Reading a title takes about 25 µs.
- **Count in SQL, decide in streams.** Everything that needs the whole catalogue (which codes many
  brands use, which codes are on 2+ shops, which words are series words, which brand owns a code) is a
  `GROUP BY`. The per-product decisions stream through temporary tables in product order and the
  per-model checks in model order, so Python holds one model's listings at a time plus the small lookups.
- **Diff in SQL.** The new tables are compared with the stored ones in SQL and only differing rows are
  written, in short transactions (2,000 rows each), so the coordinator's uploads never wait long.
- **Contained.** A failure is logged ("product matching failed") and the watchdog carries on; crawling
  and the deal rules don't read these tables. `deals models --rebuild` runs a pass now.

Measured (Mac; the droplet's CPU is about 7x slower, memory the same):

| Products | Full pass (every title read) | Hourly pass (nothing changed) |
| --- | --- | --- |
| 84,000 (today) | 5.5 s, 120 MB (droplet: 41 s, 86 MB) | 1.5 s (droplet: 8 s, 41 MB) |
| 310,000 (today's data x3.7, codes only) | 12 s, 207 MB | 4.4 s, 102 MB |

Before this design the 310,000 case needed 367 MB, over the service's limit. Past about a million products
the per-pass SQL counts would be the next thing to make incremental; nothing else grows.

## The rules

Precision comes first: showing a wrong "same product" is worse than showing none. Each rule can only take
listings out.

| # | Rule | Catches |
| --- | --- | --- |
| 1 | **Code shape**: letters and digits, 5+ characters after removing `-` and `.`, and not a spec. Specs are a fixed list of patterns: units (`3200MHz`, `260-Pin`, `10-Core`, `1.5HP`), CPUs/GPUs (`i7-13620H`, `1355U`, `RTX4060`), memory (`DDR5`, `PC4-25600`), standards (`HDR10`, `ATX3.1`, `R32`, `LGA1700`), years, product lines (`Prestige13`, `Vivobook15`, `Buds3`), Lenovo generations (`15IRH10`, `15ARP9`) | spec words that look like codes |
| 2 | **Glued words**: a hyphenated token loses plain words (`5G-Middle-East` → `5G`). If it is made of two or more specs (`i7-13620H-16G`, `DDR5-4800-512GB`) it is dropped | junk codes |
| 3 | **Accessories**: an accessory word in the first 4 words, or just before "replacement"/"compatible" (pads, case, cover, battery, keyboard, charger, cable...), followed by "for"/"compatible". The codes after it belong to the product it fits. Any "compatible with X" / "replacement for X" clause names another product, and so does "+ monitor/mouse/..." in a bundle | "Ear Pads for WH-1000XM5", "RAM Compatible with Synology DS1621+", "PC + MONITOR DELL P2425H" |
| 4 | **Spec words learned from data**: a code used by 3+ brands, none of which has 70% of its listings | codes the fixed list misses (`QCC3071`-style chip names, `DCI-P3`) |
| 5 | **Brand names**: a code equal to a brand name in the data is not a model | `V2COM` (the brand V2Com) |
| 6 | **Series**: a short code that starts with a digit (`660NC`, `255XF`) keeps the word before it when one brand uses that word before several of its codes and the code appears with 2+ such words. A listing that doesn't say which series is left out | JBL *Tune* 660NC vs *Live* 660NC (two different headphones); Lenovo *LOQ* vs *Slim* |
| 7 | **`+` suffix**: `XL2586X+` is a different, newer model than `XL2586X` | BenQ monitors, "S24+" |
| 8 | **Capacity**: GB/TB numbers in the title are part of the model (`X1504VA-NJ001W 8GB+512GB`), and so is a TV size (32"+) the code doesn't contain (`U8000H 43IN`) | one laptop code sold in several configurations; a laptop vs. a mouse with the same code; a TV series sold in four sizes |
| 9 | **Condition**: Renewed / Refurbished / Pre-owned / Open box / "(Used)" listings are left out | 2,500 renewed laptops and phones on noon |
| 10 | **Brand-scoped key**: the model is *brand + code*, the identifier Google Shopping asks for when there is no barcode, so the same code from two makers is two models (more categories means more code reuse). The brand is the known brand the title starts with (known = used by 2+ shops), else the shop's brand field (unless it is the seller's own name: "Lap-Tech" on a Dell monitor). Spellings of one brand on a code fold together (`arzum`/`arzumokka`, `jamaky`/`jamakyitaly`). A listing with no brand joins the one brand that has its code; when several brands have it, it is left out as `ambiguous_brand` | Sokany vs DSP `SK-2010`; noon titles with no brand |
| 11 | **Price check** (prices seen in the last 14 days): with 3+ prices, a listing below 0.5x or above 2.0x the median is `price_outlier`. Any model whose remaining prices differ by more than 2x is `conflict` | Jumia's 100,000 EGP placeholders (45 of them), a case at 300 EGP, desktop bundles. Between 1.6x and 2x the median are mostly real sellers asking a lot more |

When a title has several codes, it prefers one that another shop also has, then by length, position (after
"Model", "-", "(") and not after "+/with" (a bundle). Codes are also read when a shop writes them with spaces
(`CFB 9433 XFE`, `MQ 5200`, `BGGR 11228 GXA`, `ELIO 75-545`, `KSD 25`), as a plain number after "Model"/"Code"
(`model 3065`), as a plain 5-9 digit number that is not a spec, a CPU or a store number (`Touch Elzenouki 40566`,
`Hans 9710-24`, `Fresh 17511`: Egyptian brands number their products), and in 4-character shapes (`C75X`, `H390`,
`Q20I`). Plain-number and 4-character codes are only used with the listing's brand (unbranded, they are too
weak). A word before the number is never part of the code when it is a stop word (`TV 32`, `RAM 16`), a brand
(`DELL 7020`) or a spec (`ARGB 240`); a unit after it stays out (`WKM 4215 W` is `WKM4215`).

## The second identity: brand + product line + variant

Half of all listings have no code in the title: nearly every phone, tablet and console, most headphones. For
them the identity is the one a shop assistant would use: **brand + product line + the variant that sets the
price** (`apple iphone16 128GB`, `samsung galaxya16 128GB 4GB 4G`, `sony playstation5slimdisc 1TB`,
`lenovo loq15irx9 512GB 16GB I713650HX RTX4050`). Rules (`read_line`, `_lines`):

| Rule | Why |
| --- | --- |
| The line is the words after the brand up to the first separator or spec, minus fillers ("Mobile", "Dual SIM", "Tax Paid", "with Face ID", colours). `+` becomes `plus`; sub-brands fold (Redmi → Xiaomi) | "Samsung Mobile Galaxy A16 , 128GB" and "Galaxy A16 Dual SIM 128GB" are one line |
| **The line must be a name**: a word that mixes letters and digits (`a16`, `x9d`, `s11`, `t490`) or a short number after a word (`iphone 16`, `momentum 4`). A number followed by a unit, or a bare spec, is not a name and ends the line | "Fresh Chopper 6 Liter 1500 Watt", "Carrier 3 Horse Power Inverter Cooling Only" get no line: those words don't identify one product |
| Variant attributes: storage (GB/TB ≥ 32), RAM (a GB value marked RAM or the smaller GB value), network (4G/5G), CPU, GPU. Apple RAM is ignored (shops guess it) | the price-setting differences |
| Listings of one (brand, line) are a **block**. A listing's key is its attributes; one that omits an attribute joins the single fuller variant consistent with it, or is `ambiguous_variant` when several fit | Carrefour's "Galaxy A16, 128GB, 4G" (no RAM) joins 4/128 if that is the only 128 GB variant |
| A bundle ("+ game", "2 Controllers", "with Keyboard & Stylus", "bundle") is left out as `bundle` | its price is the set's, not the product's |
| Same price checks as for codes (outliers, conflict) | |

What a code can't say is left alone: cool-only vs cool/heat ACs, GPU-less laptop titles, and "international
vs Middle East version" phones stay one product (their prices differ by warranty, not by the item).

## What it found (snapshot of 29 Oct 2026, 84,001 products)

| Shop | Listings (3 days) | With a model | On 2+ shops |
| --- | --- | --- | --- |
| Carrefour | 2,034 | 65% | 55% |
| Dream 2000 | 1,305 | 59% | 45% |
| 2B | 1,658 | 56% | 43% |
| B.TECH | 6,575 | 62% | 42% |
| Jumia | 15,517 | 54% | 41% |
| Cairo Sales | 1,884 | 78% | 39% |
| Amazon | 13,455 | 47% | 24% |
| noon | 31,877 | 38% | 19% |

15,100 models, 5,000 of them on 2+ shops (23,700 listings). Not in a model: 40,700 without a code in the
title, 2,500 used/renewed, 840 accessories, 475 price outliers, 160 brand conflicts; 460 models are
`conflict`. noon and Amazon score lowest because many of their titles have no code (noon's often drop the
brand too, and noon has most of the renewed laptops).

**Precision, checked by reading the titles of random cross-shop models:**

| Sample | Correct | Wrong, and fixed since |
| --- | --- | --- |
| 3 general samples, 130 models (mostly appliances, TVs, ACs) | 130 | none |
| gold set (77 labelled rows, see below) | 100% precision, 100% recall | |
| electronics, first try, 40 | 35 | JBL Tune/Live, accessory keyboards, desktop bundles with a monitor |
| electronics after fixes, 40 + 40 | 38, 38 | `ATX3.1`, `V2COM`, JBL `660NC` (series threshold), `XL2586X+`, `EARBUDS10MM` |
| electronics blind, 30 | 27 | "compatible with DS1621+" RAM, Lenovo `15IRH10`/`15IRX10` generations |
| laptops / PC parts after the last fixes, 25 | 25 | |

Known remaining mistakes: variants whose difference is a word away from the code (MSI `272QP` X50 vs X24,
MSI `H610M-E` DDR4 vs DDR5), and colour suffixes count as the same product (Philips `HR7530/01` white vs `/10`
black), which is usually what you want for a price comparison.

## Reading the matches

- **Web UI**, product page: "Same product at other shops", every listing of the model with its price, stock,
  difference and when it was last seen. Listings left out by a rule are shown with the reason. A product
  with no model says why ("no model code found in the title").
- **Web UI**, Queries → Matching:
  - *Same product on several shops*: the cheapest in-stock listing per model and how far the prices spread.
  - *Matches to check by eye*: every listing of each multi-shop model, model after model.
  - *Matching: listings left out*: what the brand and price rules removed.
  - *Matching coverage per shop*: the table above, live.
- **CLI**: `make models` (coverage), `make models FIND=AH-XP12BME` (one model, or title words),
  `make models SAMPLE=10` (random models with all their listings).
- **SQL** (`make sql`): `SELECT * FROM models WHERE code = 'AHXP12BME'`,
  `SELECT p.site, p.price, p.title FROM product_models pm JOIN products p ON p.id = pm.product_id WHERE pm.model = 'AHXP12BME'`.

## Tables

`product_codes` (one row per product, incremental): `title_hash`, `version`, `heads` (the title's first 1-4
words as brand candidates), `shop_brand`, `own` (codes, best first), `mentions` (codes of other products the
title names), `before` (the word before a short code), `capacity`, `used`, `accessory`.
`product_code_index`: (`product_id`, `code`, `rank`), the `own` codes one per row, indexed by code, for the
SQL counts.

`models`: `model` (key: `<brand> <code>[ <capacity>]`, e.g. `sharp AHXP12BME`, `hp 15FD0015NE 8GB+512GB`,
`- SK09031` when no brand is known), `kind` (`mpn` now; a later `attrs` kind can match phones by brand +
line + storage in the same tables), `code`, `capacity`, `brand`, `status` (`ok`, `conflict` = its prices can't
be one product, `spec` = a code rejected as a spec word, kept to show why), `products`, `sites`, `site_list`,
`low`/`median`/`high` (fresh prices of its listings), `note`, `version`, `updated_at`.

`product_models`: one row per product: `model`, `status` (`model`, `no_code`, `accessory`, `used`,
`ambiguous_brand`, `price_outlier`), `brand` and `brand_from` (`title`, `shop`, or `model` when taken from the
model's other listings), `codes` (every code read from the title, best first), `note` (why it is not in a
model), `version`, `updated_at`. Deleting a product deletes its rows everywhere (`ON DELETE CASCADE`).

Rolling back the code leaves the tables in place, unused. `make health` then warns that the database is newer
than the code, and that's harmless. Keep migrations 10 and 11 in `db.MIGRATIONS` if the feature is ever
reverted, so no later migration reuses their numbers.

## Measuring a rule change

`tests/fixtures/matching_gold.tsv` is a hand-labelled set of real titles: rows with the same group number are
one product, different numbers are different products, `x` rows must be in no model. The test runs the whole
pipeline on it and requires every pair the matcher puts together to be labelled together (precision 100%) and
at least 90% of the labelled pairs to be found (100% today, 77 rows). **When a wrong or missed match turns up,
add its titles to the file first**, then change the rules; the file is how the rules stay honest as
categories are added.

## The probable tier: spec signatures

For listings with a brand but neither a code nor a named line ("Fresh Stand mixer 8Litres 1900 W, Beige"),
`read_signature` gives the product type (40 types, most specific first: chest freezer before freezer), every
number with its unit (W, L, HP, BTU, cm, burners, speeds, cups, drawers, places...), and the features that make
two otherwise equal products different (inverter, cooling only vs heat, no frost, built-in, digital, glass, fan,
grill, steam, smart). Listings with the same brand, type, specs (2 or more) and features form a block, and the
block becomes a model only when:

- **it is one product on each shop** (all of a shop's listings in the block are alike, TF-IDF cosine ≥ 0.6,
  and priced within 15%): a shop selling two Haier 1.5 HP cooling-only ACs makes the signature name nothing;
- **the shops' listings look alike** (cosine ≥ 0.35) **and are priced alike** (within 1.4x).

It is the record-linkage pattern of blocking, then a uniqueness constraint, then pairwise similarity. It is
labelled *probable* in the web UI and the CLI, and `other_shops` (the alert section) only uses `ok` models, so
a probable match never reaches an alert. 69 such products on 2+ shops; all 25 sampled were right.

## Barcodes from the shops' own data, and ranking the evidence

Dream 2000's Shopify feed (which the crawler already downloads) puts each variant's barcode in its SKU: 481 of
498 variants in the saved page are valid GTINs. The parser now passes them on (`Listing.gtins`, all colours
of a listing); the coordinator stores them in `product_barcodes` (source `feed`), never touching
`db.record()`, and a listing without barcodes (an older worker) changes nothing. Live on 29 Oct 2026:
about 1,000 barcodes on 660 products within the first hour.

Listings that share a barcode (from `product_barcodes` or from a title) are grouped through any chain of
shared barcodes (union-find). The group joins the most trusted identity its listings already have, by rank:
**by hand > barcode > model code > product line** (the probable tier ranks below all of them). Two different
identities of that same top rank mean the evidence disagrees (a shop typed a wrong barcode, or two products
share one), so nothing is merged and the listings get a note ("barcode … also links models …: not merged").
Used, accessory and bundle listings never join through a barcode.

## Product pages: the pilot (30 Oct 2026)

Do product pages give identifiers that titles lack? Measured, not guessed: 145 product pages from the Mac
(the IP that crawls these shops anyway), one shop at a time, one request every 8-12 s, 25 pages per shop
(Amazon and Dream 2000: 10); 20 unmatched + 5 matched products each. **No shop refused or challenged a single
request.**

| Shop | Pages with a barcode | Model number on the page | Agrees with our title code (matched) | Notes |
| --- | --- | --- | --- | --- |
| 2B | 7/25 | 19/25 | 2/5 (the others: maker part numbers, `90NB15Z2M003U0`, `DL7S0EA`) | best barcode source |
| Jumia | 5/25 | 23/25 | 2/5 (page text also shows Jumia's own SKUs and other products' codes) | needs its spec table parsed, not free text |
| Amazon | 3/10 | 6/10 | 3/3 | the shop most likely to block: slowest pace only |
| B.TECH | 0/25 | 25/25 | 5/5 | model number on every page (`L43HYNDA630` for a code-less Hyundai TV) |
| Cairo Sales | 0/25 | 15/25 | 4/5 | `mpn` in the page markup |
| Carrefour | 1/25 | rare | - | the barcode/model block is on few products |
| Dream 2000 | 0/10 | 3/10 | 2/2 | not needed: the feed has the barcodes |
| noon | - | - | - | a plain page request gets a 2.6 KB bot wall; would need noon's product API |

Findings: 7 of the 15 barcodes found on pages were already in Dream 2000's feed, so page barcodes connect
straight into the barcode tier. "Disagreements" were mostly *other true identifiers* of the same product (an
ASUS part number next to its order code, a Realme model `RMX5377` next to the name `C100i`), which the ranking
must treat as aliases, not conflicts; and generic text scraping picks up noise (Jumia's SKUs, related
products), so production needs a parser per shop for its spec table. The pilot also caught a title-reading bug
(`12 GB 512 GB` read as code `GB512`), fixed the same day.

## Reading product pages (live from 30 Oct 2026)

Built from the pilot: `details.py` reads a product's own page for its barcode, the maker's model or part
number and up to 25 spec pairs, with **one parser per shop that reads only that shop's structured product
block** (Jumia's product JSON-LD, 2B's Magento spec table, B.TECH's spec pairs and its description's "Model
Number", Cairo Sales' data sheet and `itemprop="mpn"`, Amazon's product details table). Each parser is tested
on a saved real page (`tests/fixtures/*_product*.html.gz`). Jumia's and 2B's pages for the OPPO A6 give the
same barcode, `6932169394079`: a certain match for a phone with no code in either title.

How it stays safe (`detailq.py`, `[details]` in the config):

- **Only when idle.** A page is offered in the lease reply only when no crawl job is due on that shop for at
  least 10 seconds; the worker reads it and then waits out the rest. Price crawling is never delayed, and the
  crawl jobs, leases and blocks are not touched.
- **Slow and capped.** One page per shop per IP every `every_seconds` (30), at most `daily_cap` (1,500) a day.
  Shops: 2B, Jumia, B.TECH, Cairo Sales. Amazon has a parser but is off (the most block-prone shop); noon has
  none (its pages are behind a bot wall).
- **Never fights a challenge.** The worker uses the shop's own crawler session (its TLS fingerprint and
  cookies: Jumia only answers Safari's, as the pilot showed with two 403s). At the first 403/429/503, challenge
  marker or truncated page it reports a refusal and does not retry; the coordinator then offers no pages of that
  shop to that IP for `pause_hours` (24). A shop already refusing an IP's crawling gets no pages from it either.
- **Once per product.** `product_details` keeps what each page gave; a page is read again only if the title
  changes (or a failed read, after a week). The queue (`detail_queue`, refilled by every matching pass) puts
  unmatched products first, then probable and line matches; used, accessory and bundle listings are skipped.
- **Off switch:** `[details] enabled = false`, or remove a shop from `sites`. `make models` shows pages read
  per shop, barcodes and model numbers found, the queue and any pause.

**Measured, 30 Oct 2026 (night test):** with the Mac's crawling of the four shops paused for two hours (the
pause feature), 895 pages were read at the normal pace (one per shop every 30 s) with **no refusal from any
shop**. Barcodes: 2B 81 of 223 pages, Jumia 44 of 209 (its phones), B.TECH and Cairo Sales none (model numbers:
B.TECH 40, Cairo Sales 143). Matching the same snapshot with and without the page data: **+226 listings on
2+ shops at confidence 75 or more, 5 fewer** (each of those a safety rule declining to guess, e.g. a Dream 2000
listing whose barcodes point at both the 4G and the 5G model). Gains on all eight shops: a page barcode on one
shop links listings on the others (Infinix Note Edge: barcode and page model number agree on 4 shops,
confidence 100). One finding on the way: a shop that blocks one device's IP (Cairo Sales and the friend's Mac)
leaves it uncrawled if the other device is paused; the stall alarm caught it.

What matching does with it: page barcodes go to `product_barcodes` (source `page`) and join the barcode tier;
page model numbers rank after the title's own codes as candidates for the model code (so a title without a
code gets one, and an unshared title code can give way to a page model number another shop has).

## Confidence (30 Oct 2026)

Every model gets a confidence from 0 to 100 and the evidence behind it in words (`models.confidence`,
`models.evidence`, `matching.confidence`), shown on the product page, in the Matching queries and in
`make models`:

| Evidence | Effect |
| --- | --- |
| How the listings were matched | by hand 100, barcode 95, model code 85, product line 75, probable 45 |
| A shared barcode agrees | +10 |
| A product page names the model | +5 |
| 3 or more shops | +5 |
| A short (4-character) or plain-number code | -10 |
| No brand known | -15 |
| Prices more than 1.5x / 1.8x apart | -10 / -20 |
| The listings state different specs for one unit, more than 30% apart (600 W vs 1,200 W, 4 vs 5 burners) | -15 |
| The model is a conflict | at most 30 |

**Alerts quote other shops only for matches at 75 or more** (`alerts.other_shops_min_confidence`): a model code
passes unless two doubts stack up, a product line only when nothing casts doubt on it, a probable match never.
On 30 Oct: 5,892 such products on 2+ shops.

## Other evidence and safety rules added on 30 Oct 2026

- **Codes in product links** (`slug_codes`): B.TECH, Cairo Sales and Carrefour end a product's link with its
  model code ("...-receiver-l43hynda630"); used like a page's model number. Not Dream 2000 (one link covers every
  size of a product: its "55 inch" and "50 inch" listings carry the 75-inch code), not Jumia (sellers write
  theirs), and not B.TECH links that are a random id (`/p/b4b8405b-78f5-...`). A link's code counts only when the
  title has no code or the link spells the title's code (`hob90000ss` for HOB90SS, `65s90f` for QA65S90FAEXEG: one
  holds the other once zeros are dropped, `matching.related`): B.TECH reuses a product's link for another seller's
  offering (`...-d40eubkne-...` selling the red C40-EU-RE-NE5 at 12,999 against the black D40's 6,999; `...-ch200`
  selling the CH140STS freezer). Found on 30 Oct by reading the 88 listings whose code came from the link while
  the title had its own: 86 were spellings of one code, those 2 were not.
- **Page and link codes only when another shop has them**: an unshared one would only pull a listing out of a
  working product-line match (Itel P55 and its model number `A666L`).
- **Prices are compared per shop**: the reference is the median of each shop's cheapest listing, so one shop
  listing a product three times can't outvote the others, and a shop's dear extra listing is set aside. With 4+
  shops a listing above 1.7x the reference is set aside (one 75,999 among four around 41,000 no longer makes the
  model a conflict); below half stays the limit (that may be a real deal).
- **Spec words** need 5 brands now (keys are brand + code, so a code a few brands share is not a risk).
- **Product lines** read up to 7 words (5 counted the brand's and cut "Redmi Note 15 Pro *Plus*" to "Pro"); a
  listing that doesn't state its CPU, GPU or ANC never joins a variant that has one (one laptop line comes with
  very different processors; "AirPods 4" and "AirPods 4 with Active Noise Cancellation" are two products);
  motherboards have no product line (a chipset plus words: "B760 GAMING X").
- **Spaced codes** with a 2-digit number need a 3-letter prefix and no size after them ("Rainbow ST 60*60" is a
  cooker's size); refrigerant gases are named exactly (R32, R410A...), so RAF's `R.179`, `R.382` are codes again.
- **The page queue learns**: a shop's group whose first 15 pages gave a barcode or model number less than 10% of
  the time is read last (Jumia's appliances: sellers write "9" or "5 درج" as the model; its phones carry barcodes).

Measured on a snapshot of the live data before and after these rules: listings matched across shops 32,691 ->
34,184; the 185 that left were checked one pattern at a time (desktop bundles that sat in a monitor's model,
laptops without their CPU, "Pro Plus" phones, a conflict from overpriced sellers), the gained ones by reading
random samples.

## Matches by hand

When you see a wrong or missing match: the product page in the web UI has a **Not the same** button on each
listing of the model, and a field to link another listing by its product id (the number in its page link),
for the admin token only; or `deals models --same A B`, `--different A B`, `--forget A B`, `--overrides`. They
are kept in `match_overrides` and applied at the end of every pass (a "same" B joins A's model, or both start a
model of their own; a "different" B leaves A's model, status `split`); the web UI starts a pass within a minute.
Add the titles to the gold set too, so the rules learn the case.

## Coverage (29 Oct 2026)

| | Listings in a model | On 2+ shops |
| --- | --- | --- |
| Codes only | 39,334 (47%) | 23,739 |
| Codes + lines | 52,803 (63%) | 31,389 |
| + barcodes, spaced and number codes, probable tier | 54,577 (65%) | 32,524 |

Per shop, listings on 2+ shops: Carrefour 75%, 2B 70%, Dream 2000 67%, B.TECH 62%, Jumia 59%, Cairo Sales 45%,
Amazon 30%, noon 26%.

Line models found on 2+ shops: 469. Reviewed by eye: 60 random line models with all their listings,
56 right; the wrong ones (bundles read as the product, a decimal screen size, `X360` as a code) were fixed
and added to the gold set.

## Limits and next steps

1. **Two-product bundles** whose title names both products ("French Press ... + Mienta coffee maker cm31316a")
   can still join the second product's model; telling them from "Hand Blender + Whisk + Chopper KM1117" by rule
   would lose real matches. The confidence score and "Not the same" on the product page cover them.
2. **Fresher matches for new listings**: an alert for a product first seen minutes ago can match it on demand
   (`read_title` plus a lookup of its code in `models`) instead of waiting for the hourly pass.
3. **What is left unmatched** (24,800 listings): mostly titles with no brand at all (noon and Amazon's
   "Wireless Earbuds, 2025 New Bluetooth 5.4..."), single-spec titles ("Haier Split AC 1.5 HP Cooling Only",
   where several real products share the words), and products only one shop sells.
4. **Attaching codeless listings to code models** (a noon listing that drops the code of a product other shops
   list by code) by spec signature: possible, but every wrong attach would show a wrong price in an alert;
   the hand-made "same" covers the cases you care about.
