# Raneen (raneen.com): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/raneen_eg.py` (site name `raneen_eg`); tests are `test_raneen_*` in `tests/test_parsers.py` against `tests/fixtures/raneen_dishwashers.html.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | رنين |
| What it sells | A general marketplace: appliances, mobiles, electronics, kitchen, home, fashion. Many sellers on one product |
| Platform | Magento (Hyvä theme), Vertex search |
| What we fetch | `/en/<category path>?p=N`, category pages |
| Products per request | 20 (page 1 has a 21st, repeated on page 2), about 1.2 MB |
| Exact count | `.toolbar-amount`: "Showing 1 - 20 of 83 products" |
| Price filter | Not used: `robots.txt` disallows it |
| Who can crawl it | **Home connections only.** The droplet gets HTTP 403, "403 Forbidden", 48 bytes. The Mac gets 200 |
| Keyed by | The card's `data-sku` |
| Catalogue | appliances 6,946, kitchen 18,816, home 4,705, electronics 3,563, mobiles 2,604. Sitemap: 2 files of up to 50,000 links per language |

## The rules we keep to

`robots.txt` has **`Crawl-delay: 10`** and allows query strings only for pagination:

```
Allow: /*?p=
Disallow: /*shopbyAjax=
Disallow: /*?
Disallow: *?*=*
```

So: 10.5 to 13 s between pages (`[sites.raneen_eg]`), and **no `?price=`, `?product_list_order=`, `?product_list_limit=`**, though Magento has them. **Don't lower the delay or the Pace setting, or add a filter, to go faster.**

**GraphQL answers HTTP 403 "The GraphQL API is not available to this client."** from anywhere. That's a clear no: don't look for a way round it.

## The listing

| Asked | Answered |
| --- | --- |
| `?p=5` of 5 pages | 3 products, "Showing 81 - 83 of 83" |
| `?p=6` | HTTP 200, no cards |
| An unknown category | HTTP 404, 537 KB |
| `/en/raneen-offers` | HTTP 200, no cards: a landing page |

**Whole walks, 5 Nov 2026:** laptops-notebooks 8 pages, 147 of 148. air-conditioners 26 pages, 498 of 502, 1 repeated. First live pass: refrigerators 548 of 562, washers 357 of 368, TVs 448 of 455, phones 614 of 621. There's no stable sort to ask for, so about 1 to 3% are missed in a pass.

## Prices and stock

- `[data-price-type="finalPrice"]`, `[data-price-type="oldPrice"]`, whole pounds in `data-price-amount`. Promo codes on the card ("USE Code WF500") aren't in the price.
- **A marketplace: the card shows the cheapest seller.** `-conf` products have one option per seller: J300BLK-conf from 5,949 to 8,159 across 4 sellers, EL1102828BNS-conf from 3,169 to 4,839 across 7. The product page's JSON lists them (`offers`, `sellerName`). So when the cheapest seller sells out, the card's price rises, and falls when they restock: a real change to what you'd pay, but a flip-flop (`raneen-cheapest-seller-is-the-price`). No seller on the card; the screenshot's seller box shows it.
- **Crossed-out prices** on 147 of 147 laptops (median 5%) and 438 of 484 air conditioners (90%, median 12%): `raneen-no-new-listing-alerts`.
- **"Only N left"** (`data-rn-stock-left`): 3 or fewer on 121 of 147 laptops (82%), 190 of 484 air conditioners (39%): `raneen-low-stock-is-everywhere`.
- Sold-out products seem to be hidden: all 147 laptops are in stock. 14 of 498 air conditioners have no price, and aren't counted as in stock.
- Compared with the page in a browser, 6 of 6 prices and crossed-out prices matched (5 Nov 2026).

## Which categories, and why so few

| Group | Root under `/en/` | Products | Pages |
| --- | --- | --- | --- |
| Air conditioners | `appliances/large-appliances/air-conditioners` | 502 | 26 |
| Refrigerators | `appliances/large-appliances/refrigerators-and-freezers` | 562 | 29 |
| Cooking & dishwashers | `appliances/large-appliances/cookers` | 310 | 16 |
| Cooking & dishwashers | `appliances/large-appliances/dish-washers` | 65 | 4 |
| TVs | `electronics/televisions-accessories` | 453 | 23 |
| Phones | `mobiles/mobiles-tablets` | 621 | 32 |
| Laptops | `electronics/computers/laptops-notebooks` | 148 | 8 |
| Home & more | `appliances/large-appliances/washers-and-dryers` | 368 | 19 |

**Sums: 157 pages, about 3,000 products. At about 13 s a page, a pass takes 34 minutes on one IP.** With no filter or sort allowed, that is the time to notice a drop on Raneen, **whatever the group's cap says** (15 minutes for air conditioners and phones, 30 for TVs). **Measured on the first pass: all 8 roots done in 53 minutes** (20:55 to 21:48, 5 Nov 2026), longer than the sum because the 15-minute groups come round again in that time. A second home worker (`abdelrahman-mac`) would halve it. Adds at most 280 pages an hour, 330 MB, from the Mac; no block history.

Left out for that reason: `kitchen` (18,816), `home` (4,705), `electronics/computers` beyond laptops (1,716), `electronics/game-gate` (520), `mobiles/accessories` (1,502), `mobiles/wearable` (476), `appliances/small-appliances` (1,372), `appliances/kitchen-appliances` (2,556), `built-in-appliances` (647), `water-heaters` (240). Each would add to every drop's wait.

**Links like `mobiles/mobiles-tablets/dubaiphone` aren't sub-categories:** they're the seller or brand filter, in the path.

## Running and checking it

```bash
.venv/bin/deals crawl raneen_eg "category:appliances/large-appliances/dish-washers" --pages 1 --show 5
```

## Troubleshooting

- `no product cards in the page (layout changed?)`: a category that's a landing page, or a new theme. Save the page into `tests/fixtures/`.
- When testing its product pages in a browser, keep to 11 s apart, as for its listings.

## Re-checking

Whether `robots.txt` still says what's quoted above: if it starts allowing the price filter, this shop could be read far faster.

## Alert screenshots

`.rn-price` (`.price-container` is `display: contents`, so has no box of its own), and the seller's card `div:has(> .rn-seller__chips)`. Arabic at `/ar/`. About 3 s. Marketplace: the seller's name shows, the alert's warning is noon's only.
