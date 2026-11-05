# El Araby Group (elarabygroup.com): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/elaraby_eg.py` (site name `elaraby_eg`); tests are `test_elaraby_*` in `tests/test_parsers.py` against `tests/fixtures/elaraby_ducts.json.gz` and `elaraby_televisions.json.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | العربي جروب |
| What it sells | Its own Tornado, and Toshiba, Sharp, Hoover, Sony, TCL, Seiko, Alba: appliances, TVs, ACs, watches, spare parts |
| Platform | Magento 2, Varnish in front, standard layout |
| What we fetch | `POST https://www.elarabygroup.com/graphql`, header `Store: en` (`ar` is the default) |
| Products per request | 100 |
| Exact count | `total_count` |
| Price filter | `price: {from, to}` on the current price, inclusive (58,340 finds the duct AC, its crossed-out 60,770 doesn't) |
| Who can crawl it | **Anyone**: the API answers plain curl, the Mac and the droplet |
| Keyed by | `sku`. Every product is a `SimpleProduct`: one key, one price |
| Catalogue | 2,270 products (2,228 with a price); 795 in stock |

## robots.txt

Allows the product and category pages; blocks checkout, account, `catalogsearch`, `?p=`, `?limit=` and filter parameters on the HTML. The API isn't mentioned, and we send none of those URLs.

## Traps

- **`price: {from: "0"}` finds nothing**, in a category or not. The adapter leaves a zero lower bound out.
- **`product_count` is stale:** Watches says 3,780 and holds 1,057, Gaming says 12 and holds 2. Only `total_count` counts.
- **`manufacturer` is the country of origin**, not the brand (Egypt 599, China 898). The brand is found in the title (`BRANDS` in the adapter); all 2,270 got one.
- **Unknown filter fields can be ignored:** `url_key: {eq: ...}` returned the same unrelated Hitachi fridge for every key. Test what a filter returns.
- **The HTML pages sometimes answer 404**, with a full 190 KB error page, to browsers too: 5 of 40 product pages in one sample on 5 Nov 2026. It passes: minutes later the same five answered 200, 15 of 15 in a browser, and one page answered 200 on 14 of 14 tries. The cause isn't known. The API wasn't affected (14 of 14). So a screenshot or a click can occasionally hit a 404 on a good product; the alert then goes out as text. It also hit the home page from plain curl and from the droplet, so don't read a 404 here as a removed product.
- A wrong `Store` header answers Varnish's 503 "Guru Meditation": not a block.

## The listing

- `sort: {name: ASC}`. `price` can repeat: 2,229 rows gave 2,228 SKUs.
- An empty range and an unknown id in the filter: `total_count: 0`. `categoryList` answering `[]` tells unknown from empty.
- Introspection is on, unlike Raya: `__type(name: "ProductInterface")` lists the fields.

## Prices and stock

- `final_price`, and `regular_price` when higher (kind `list`). **Only 4 of 2,229 have one** (TCL 55P6K 21,999 "was" 27,079, three Sharp ducts, with `special_to_date`): honest, so no rule. Watch how alerts behave: a drop here is likely real.
- **Two thirds sold out and listed with a price** (1,434 of 2,229, page says "غير متوفر"). 42 show a price of 0: read as no price (`elaraby-zero-price`).
- No low-stock numbers: `only_x_left_in_stock` is 0 or null.
- Two round prices among the watches, 40,000 and 110,000, are real Seiko models, not placeholders.
- Sells its own stock: no seller box.
- Reviews on 1,736 products.

## Catalogue and categories

Root `2`; docs/CATEGORIES.md has the ids. **All of it is watched.** A walk of the whole catalogue against these roots on 5 Nov 2026 read every product, none missing. Collections like `flash-sale` (1709, 240 products, 180 of them sold out), `new-products`, `coming-soon-products` overlap the real tree and aren't needed; the whole shop is read so often that no fast lane is.

## How we crawl it

| Group | Min EGP | Roots |
| --- | --- | --- |
| TVs | 3,000 | 155 |
| Refrigerators | 5,000 | 84, 86 |
| Air conditioners | 8,000 | 92, 1710, 1853, 1973 |
| Gaming consoles | 8,000 | 1826 |
| Laptops | 8,000 | 94, 1641 |
| Headphones | 800 | 263 |
| Kitchen machines | 1,500 | 192, 193, 215, 319 |
| Kitchen appliances | 2,000 | 200, 189, 87, 201 |
| Cooking & dishwashers | 5,000 | 89, 90, 249, 91, 234 |
| Chargers & power banks | 500 | 344, 342 |
| **Watches** (new) | 1,000 | 64 (1,055) |
| **Home & more** (new) | see the config | 317, 186, 1927, 93, 309, 65, 66, 320, 1966, 217, 360, 1652, 1497: the parents, so nothing is left out |

About 55 pages a pass at most, under 4 minutes of one worker at 2 to 4 s. **About 80 pages an hour**, shared by the droplet and home workers. No block history.

## Running and checking it

```bash
.venv/bin/deals categories elaraby_eg 317
.venv/bin/deals crawl elaraby_eg "category:155|price:3000-" --show 5
```

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `screenshot ... Timeout` now and then | A random 404 from the shop | Nothing; the alert goes out as text |
| `HTTP 503` | Varnish: its own trouble | Retried by itself |
| `GraphQL error` | A field changed | Introspection names the new one |

## Matching

`slug_codes=True`: the shop writes the model code at the end of its links, one link per model (1,746 of 2,229; 11 not in the title, all filters quoting a model). Read 20 by hand on 5 Nov 2026: `AH7BV8X1`, `XP12BME`, `85C6K`.

## Alert screenshots

`/ar/` for `/en/`. Price: `.product-info-main .product-info-price`. Context `h1.page-title`, `.fotorama__stage__frame.fotorama__active img`. 3 to 4 s, same image 3 of 3. Page checks on 5 Nov 2026: 10 of 11 products matched on price, crossed-out price and stock; the 11th timed out on a random 404 and loaded on retry. Saved page: `tests/fixtures/elaraby_product_rendered.html.gz`.
