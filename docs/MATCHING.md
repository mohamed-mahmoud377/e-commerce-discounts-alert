# The same product on different shops

`src/deals/matching.py` finds which listings on the eight shops are the same product, so a price can be
compared with what the other shops ask. Built 29 Oct 2026. For now it is **read-only**: it feeds the web UI and
the `deals models` command, and alerts don't use it yet. Alerts will get a "cheaper elsewhere" section once the
matches have been checked by eye (see "Next" below).

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
products ──(every hour, watchdog: python -m deals.matching, nice 19)──▶ product_models, models
                                                                          │
                       web UI product page, "Matching" queries, deals models ◀┘
```

- **Never while crawling.** `db.record()` and the page upload path are unchanged. A separate process
  (`rebuild_in_process`) recomputes every assignment from `products` once an hour, at the lowest CPU
  priority, and writes only the rows that changed, 1,000 per transaction. On the droplet a full pass takes
  about 16 s of CPU and 140 MB of memory, which the operating system gets back when the process exits.
  On the Mac it takes 2 s. It runs inside the coordinator's systemd memory limit (`MemoryMax=300M`; the
  coordinator itself uses about 60 MB), and it sets `oom_score_adj=1000`, so if memory ever runs out the
  kernel kills the matching process, not the coordinator.
- **Always recomputed from scratch**, so a rule change applies to everything at the next run, and nothing
  drifts. `VERSION` in `matching.py` is stored on every row so you can tell which rules produced it.
- **Contained.** If matching fails, the watchdog logs it ("product matching failed") and carries on with its
  other checks. Crawling and the deal rules don't read these tables.
- `deals models --rebuild` runs a pass now (in the CLI's own process).

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
| 10 | **Brand check**: the brand is the known brand the title starts with, else the shop's brand field (unless it is the seller's own name: "Lap-Tech" on a Dell monitor). Spellings of one brand count as one (`arzum`/`arzumokka`, `jamaky`/`jamakyitaly`). A listing whose brand disagrees with the model's main brand (70%+) is `brand_conflict`; with no main brand the model is `conflict` | DSP vs DSB, seller-typed brands |
| 11 | **Price check** (prices seen in the last 14 days): with 3+ prices, a listing below 0.5x or above 2.0x the median is `price_outlier`. Any model whose remaining prices differ by more than 2x is `conflict` | Jumia's 100,000 EGP placeholders (45 of them), a case at 300 EGP, desktop bundles. Between 1.6x and 2x the median are mostly real sellers asking a lot more |

When a title has several codes, it prefers one that another shop also has, then by length, position (after
"Model", "-", "(") and not after "+/with" (a bundle).

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

`models`: `model` (key: the code, plus ` 16GB+512GB` when the title gives capacities), `code`, `capacity`,
`brand`, `status` (`ok`, `conflict`, `spec` = a code rejected as a spec word, kept to show why), `products`,
`sites`, `site_list`, `low`/`median`/`high` (fresh prices of its listings), `note`, `version`, `updated_at`.

`product_models`: one row per product: `model`, `status` (`model`, `no_code`, `accessory`, `used`,
`brand_conflict`, `price_outlier`), `brand` and `brand_from` (`title`, `shop`, or `model` when taken from the
model's other listings), `codes` (every code read from the title, best first), `note` (why it is not in a
model), `version`, `updated_at`. Deleting a product deletes its row (`ON DELETE CASCADE`).

Rolling back the code leaves both tables in place, unused. `make health` then warns that the database is newer
than the code, and that's harmless. Keep migration 10 in `db.MIGRATIONS` if the feature is ever reverted, so no
later migration reuses its number.

## Limits and next steps

1. **The alert section** (the reason for all this): when a deal alert is formatted, append the same model's
   in-stock listings seen in the last 24 hours that are **cheaper**, only for models with status `ok`.
   The decision whether to alert stays exactly as it is. Put it behind a config switch.
2. **Phones** (6,600 listings, almost none with a code): match on brand + model line + RAM/storage
   ("samsung|galaxy a16|4/128") as a second key in the same tables.
3. **Fresher matches for new listings**: an alert for a product first seen minutes ago can match it on demand
   (`read_title` plus a lookup of its code in `models`) instead of waiting for the hourly pass.
4. **Manual fixes**: a small table of "same"/"not the same" overrides, read by `build`.
5. If products grow past ~500k, make the pass incremental (re-read only titles that changed).
