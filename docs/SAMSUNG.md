# Samsung Egypt (samsung.com/eg): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/samsung_eg.py` (site name `samsung_eg`); tests are `test_samsung_*` in `tests/test_parsers.py` against `tests/fixtures/samsung_tvs.json.gz` and `samsung_phones.json.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | سامسونج مصر |
| What it sells | Samsung's own phones, TVs, appliances. The official price, so a useful reference for other shops' Samsung products |
| Platform | AEM, Akamai in front, a search API on another host |
| What we fetch | `https://searchapi.samsung.com/v6/front/b2c/product/finder/global?type=<8 digits>&siteCode=eg&start=<n>&num=48&sort=newest&onlyFilterInfoYN=N&keySummaryYN=N` |
| Products per request | 48 families, about 1.5 MB, each with its models |
| Exact count | `totalRecord`, in **families**, not models |
| Price filter | None |
| Who can crawl it | **Anyone** so far: Mac and droplet |
| Keyed by | Model code, with the colour taken out of phones' (below) |
| Catalogue | About 140 families, 430 models, **about 150 with a price** |

`robots.txt`: under `User-agent: *` nothing we read is disallowed (`/eg/` is disallowed for Yandex only). `searchapi.samsung.com/robots.txt` answers HTTP 500.

## The listing

| Asked | Answered |
| --- | --- |
| `type=01010000`, `start=1`, `num=48` | 25 families of phones, 179 models |
| `start=21`, `num=10` | Families 21 to 25 |
| `start=26` of 25 | `productList: []` |
| `type=99990000`, unknown | HTTP 200, 0 records: **looks like an empty category**, so the adapter checks the type's shape and no more |

Types, 5 Nov 2026 (families, models with a price): phones `01010000` (25, 96), tablets `01020000` (7, 0), watches `01030000` (9, 0), buds `01040000` (6, 3), accessories `01050000` (8, 1), TVs `04010000` (22, 37), lifestyle TVs `04020000` (5, 5), The Frame `04030000` (1, 2), monitors `07010000` (27, 0), washers `08010000` (12, 4), refrigerators `08030000` (8, 3), vacuums `08070000` (3, 0), microwaves `08110000` (5, 0). `08050000` and `08090000` are empty. **The page's own requests are where these come from**: load a category in a browser and read the `finder/global?type=` call.

## Prices and stock

- **Most models have no price** (`ctaType: learnMore`, no `stockStatusText`). The adapter hands them over with no price, and the coordinator doesn't store a new product without one, so one appears the day it gets a price, as a new product. 17 of 53 phone listings, 41 of 78 TVs: 88 products were stored on the first pass.
- `promotionPrice`, when set, is what you pay, and `price` is then the crossed-out one. `priceDisplay` always shows what you pay. `formattedPriceSave` says the same in words.
- **`ctaType: whereToBuy` doesn't mean elsewhere:** its page says "اشتري الآن" and has a cart, with `stockStatusText: inStock`. In stock means `inStock`, which pre-orders have too (Fold8, firm prices).
- **Crossed-out prices:** 35 of 37 TVs in stock, median 15%, round numbers (109,999 from 164,999). None of the 34 phones in stock. So rule `samsung-no-new-listing-alerts`.
- Compared with the page in a browser, 5 of 5 prices and crossed-out prices matched (5 Nov 2026).

## Identity: colours fold, versions don't

An `SM-` code is model, two letters of colour, one of version, three of region: `SM-A185G ZK K EGY`. Colours cost the same, so they fold into one listing at the cheapest in stock, keyed `SM-A185G-K`, with no colour left in the key. **The version is a real difference, even where the page gives both the same size:** `SM-A185GZKK` is 14,240 and `SM-A185GZKL` 16,470, both "128GB". When two versions of a family read the same, the title carries the version (`SM-A185G-K`), or matching would take them for one product. Otherwise it carries the plain model (`SM-A185G`), which is what other shops write.

TV and appliance codes have no colour: each code is its own listing. Titles name the code, so `slug_codes` isn't needed even though every link has it.

A new phone's `pdpUrl` opens a marketing page with no price (Fold8's). `marketingpdpYN: Y` means use `configuratorUrl`, its `/buy/` page, which has one.

## How we crawl it

| Group | Roots | Pages |
| --- | --- | --- |
| TVs | `category:04010000`, `category:04020000`, `category:04030000` | 3 |
| Phones | `category:01010000` | 1 |
| Tablets | `category:01020000` | 1 |
| Refrigerators | `category:08030000` | 1 |
| Monitors | `category:07010000` | 1 |
| Headphones | `category:01040000` | 1 |
| Wearables | `category:01030000` | 1 |
| Kitchen machines | `category:08110000` | 1 |
| Home & more | `category:08010000`, `category:08070000` | 2 |

**Sums:** 12 pages a pass, at 2 to 4 s between them and about 2 s each, under 2 minutes. At the groups' fastest schedule (10 minutes, phones and tablets) that adds at most about 20 pages an hour to each IP. Both the droplet and the Mac crawl it. No block history. No deal feed: a pass is cheaper than one would be.

## Running and checking it

```bash
.venv/bin/deals crawl samsung_eg "category:01010000" --pages 1 --show 10
```

## Troubleshooting

- `HTTP 403` or a non-JSON answer: Akamai refused. `Blocked`, so that IP rests.
- A category reading 0 products may have been renumbered: load its page and read the `finder/global` call.
- `/eg/` is Arabic only: there is no English page to prefer, though the API's names are English.

## Re-checking

Have tablets, watches or monitors got prices yet? They had none on 5 Nov 2026, so the alerts for those groups can't start until they do.

## Alert screenshots

`#sgDevPriceAreaBase` on a model's page, `.hubble-price-bar__price` on a `/buy/` page. Takes 4 to 9 s.
