# The same product on different shops

`src/deals/matching.py` finds which listings on the eight shops are the same product, so a price can be
compared with what the other shops ask. Built 29 Oct 2026. It feeds the web UI, the `deals models` command and
the end of each deal alert (the same product's price on every other shop, cheapest first, and whether this
is the cheapest). **It never changes what is alerted**: the section is appended after the deal rules have
decided, the lookup is read-only, and if it fails the alert goes out without it (`alerts.show_other_shops`
turns it off).

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
(`CFB 9433 XFE`, `MQ 5200`), as a plain number after "Model"/"Code" (`model 3065`), and in 4-character shapes
(`C75X`, `H390`, `Q20I`) when the listing has a brand (an unbranded 4-character code is too weak).

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

## Coverage after the second identity (29 Oct 2026)

| | Listings in a model | On 2+ shops |
| --- | --- | --- |
| Codes only | 39,334 (47%) | 23,739 |
| Codes + lines | 52,803 (63%) | 31,389 |

Line models found on 2+ shops: 469. Reviewed by eye: 60 random line models with all their listings,
56 right; the wrong ones (bundles read as the product, a decimal screen size, `X360` as a code) were fixed
and added to the gold set.

## Limits and next steps

1. **Titles with neither a code nor a named line** (appliances described by type and specs: "Fresh Jumbo Gas
   Cooker 90x60 5 Burners"): the remaining tier would be title similarity inside a (brand, group) block,
   with every number having to agree. It is the least precise tier; worth doing only with a lower-confidence
   label in the UI.
2. **Fresher matches for new listings**: an alert for a product first seen minutes ago can match it on demand
   (`read_title` plus a lookup of its code in `models`) instead of waiting for the hourly pass.
3. **Manual fixes**: a small table of "same"/"not the same" overrides, applied at assignment.
