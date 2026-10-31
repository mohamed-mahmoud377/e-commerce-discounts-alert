# Hardware Market (hardwaremarket.net): site analysis and how we crawl it

Everything we learned about hardwaremarket.net (Hardware Market, a shop for gaming peripherals and computer
and phone accessories) while adding it as the tenth shop (30 October 2026). The adapter is
`src/deals/sites/hardwaremarket_eg.py` (site name `hardwaremarket_eg`, Arabic name in alerts: هاردوير ماركت); its
tests are in `tests/test_parsers.py` (`test_hardwaremarket_*`) against the saved API answer
`tests/fixtures/hardwaremarket_headsets.json.gz` (Headsets, page 6 of 6 by id). Sizes are from 30 October 2026 and
drift.

## Summary

| Question | Answer |
| --- | --- |
| Platform | **WordPress + WooCommerce** (WoodMart theme, Yoast SEO, PHP 8.3) behind Cloudflare. English only, prices in EGP |
| What to fetch | WooCommerce's public **Store API**: `GET /wp-json/wc/store/v1/products`: JSON, no login, no HTML parsing |
| Whole shop in one listing | **Yes**: the products endpoint without a category is the whole catalogue (43 pages of 100 with sold-out products, 11 without) |
| Products per request | 1 to 100 (`per_page=101` is HTTP 400 `rest_invalid_param`) |
| Exact count | Yes: `X-WP-Total` and `X-WP-TotalPages` headers |
| Price filter | `min_price` / `max_price` in **piastres** (minor units), on the current price, inclusive |
| Prices | Strings in piastres (`"price": "399900"` = 3,999 EGP), exactly our unit |
| Sold-out products | **Hidden by default**; `stock_status[]=instock&stock_status[]=outofstock&stock_status[]=onbackorder` returns them too |
| Bot protection | Cloudflare. From the Mac even plain `curl` gets everything; from the droplet everything is a Cloudflare challenge (HTTP 403, `cf-mitigated: challenge`), Chrome's fingerprint included |
| Who can crawl it | **Home connections only**: the Mac's `DEALS_SITES` has it, the droplet's doesn't |
| How products would be keyed | WooCommerce's numeric product id (`204796`) |
| Catalogue | 4,235 products, **1,046 in stock**; 78 categories; all `simple` products (no variations) |
| robots.txt | Allows everyone (`Allow: /`) except a list of AI-training bots; Cloudflare's content signals say `search=yes, ai-train=no` |

## Access and blocking (tested 30 October 2026)

| Client | Where | Result |
| --- | --- | --- |
| `curl` with a browser User-Agent | Mac | 200 on the API, pages and sitemaps |
| `curl_cffi` without a fingerprint | Mac | 200 |
| `curl_cffi` Chrome, and plain `curl` | Droplet | **403**, `cf-mitigated: challenge`, on the API and on HTML pages |

Load from the Mac with no refusal: two full catalogue walks (11 and 43 pages of 100 at one a second), 30
pages with no pause, 40 with 4 in parallel, plus about 30 single requests. Every answer was 200 and the
time per page stayed at 1.3 to 3 s. Cloudflare does not cache the API (`cf-cache-status: DYNAMIC`), so
every answer is fresh.

## The Store API

```
GET https://hardwaremarket.net/wp-json/wc/store/v1/products
    ?per_page=100&page=1&orderby=id&order=asc
    &stock_status[]=instock&stock_status[]=outofstock&stock_status[]=onbackorder
    &_fields=id,name,sku,permalink,prices,is_in_stock,on_sale,low_stock_remaining,categories,brands
```

**`_fields` matters.** Without it a page of 100 is **4.0 MB** (full HTML descriptions and image lists);
with the fields above it is **99 KB**, and 45 KB with only `id,name,sku,prices.price,prices.regular_price,
is_in_stock` (nested names work). The server time stays about 2.5 to 3 s a page either way: it builds
the whole product objects before trimming.

| Field | Example | Use |
| --- | --- | --- |
| `id` | 204796 | key |
| `name` | `5K Pocket Vlogging Camera … 1.69&#8243; IPS Touchscreen …` | title. **HTML entities** in 2,202 of 4,235 names (`&#8243;`, `&#8211;`, `&amp;`): unescape |
| `sku` | `Logitech \| G305 White`, `Redragon \| GS560`, `RZ01-03340100-R3C1` | free text, never empty, unique. Usually "Brand \| Model", so it carries the model code for matching. No barcodes |
| `prices.price` | `"399900"` | current price, piastres |
| `prices.regular_price` / `sale_price` | `"449900"` / `"399900"` | the crossed-out price and the sale price |
| `prices.currency_minor_unit` | 2 | always 2 |
| `on_sale` | true | see below |
| `is_in_stock`, `is_on_backorder` | true, false | no backorders anywhere |
| `low_stock_remaining` | 1 | set on 71 in-stock products ("Only 1 left in stock") |
| `categories[]` | `{"id": 64, "slug": "webcam"}` | every assigned category, parents included |
| `brands[]` | `{"name": "Redragon"}` | 1,289 products have one (387 of those in stock) |
| `type` | `simple` | all 4,235: colours are separate products (`G305 Black` and `Logitech \| G305 White` both at 2,099) |

**Parameters checked:**

| Request | Answer |
| --- | --- |
| no `stock_status` | in-stock only: `X-WP-Total` 1,046 |
| `stock_status=outofstock` (or `stock_status[]=…`) | 3,189 |
| all three statuses | 4,235 (the product sitemaps list 4,234 URLs) |
| `category=headsets` or `category=39` | slug or id both work, **children included** (`headsets` 559 vs 557 tagged directly; `hardware` 63 vs 59) |
| `category=9999999` | 200, `[]`, total 0 (no error: validate against `/products/categories`) |
| `min_price=135000&max_price=135000` | the products at exactly 1,350 EGP current price (their regular price is 1,499) |
| `min_price=135001&max_price=135099` | none |
| `page=12` when there are 11 | 200, `[]` |
| `orderby=foo` | 400; allowed: `date, modified, id, include, title, slug, price, popularity, rating, menu_order, comment_count` |
| `/products/999999999` | 404 `woocommerce_rest_product_invalid_id` |
| `type=variable` | 0 |

**Order for paging**: `orderby=id&order=asc` does not move when prices or stock change or new products
arrive (new ids go to the end). The default (`date` descending) shifts every page when a product is added.

**A "recently changed" lane**: `orderby=modified&order=desc` lists the most recently edited products first.
The top one on 30 October had been modified 22 minutes earlier (its page's JSON-LD `dateModified`). A
price edit updates it; so do stock changes from orders and text edits. One page of 20 to 100 every few
minutes would catch a new sale long before a full pass.

Category tree: `GET /wp-json/wc/store/v1/products/categories?per_page=100` (78 categories, `id`, `slug`,
`parent`, `count`). Its `count` is unreliable (see the table below).

## Prices and stock

- **Almost everything is "on sale"**: 1,044 of the 1,046 in-stock products have a `regular_price` above the
  price, discounts from 4% to 80% (most 10 to 33%: 25% on 115, 20% on 82, 17% on 68). The crossed-out price
  is a permanent anchor, not evidence of a deal; alerts must come from our own history (kind `list` for the
  shop's claim, as on Dream 2000).
- Prices are whole pounds (one product of 4,235 has piastres).
- **75% of the catalogue is sold out** (3,189). Sold-out products keep their price. With all three
  `stock_status` values a product that sells out stays visible with `is_in_stock` false; without them it
  silently disappears, like Jumia and 2B.
- No placeholder prices: nothing under 35 EGP.
- In-stock prices: from 35 to 13,999 EGP, median 750; 629 at 500 or more, 389 at 1,000+, 183 at 2,000+, 93 at
  3,000+, 36 at 5,000+, 10 at 10,000+. The most expensive products overall (up to 34,999) are sold out.

## Catalogue and categories

The shop is about gaming peripherals and accessories. In-stock products by category (a product has 1 to 3):
mouse 249 (wireless 175, wired 57), computer accessories 189 (USB hubs 97), mobile accessories 120, streaming
92 (microphones 49, webcams 23), keyboards (TKL 73, full size 71, 60% 61), headsets 62, gamepads 33, mouse
pads 29. Brands: Redragon 121, Logitech 67, UGREEN 55, LISEN 22, SKYLOONG 21, Bloody 19, FIFINE 16.

| Root (id) | Children | All / in stock (children included) |
| --- | --- | --- |
| Mouse (41) | Wired 69, Wireless 70, Mouse Accessories 4435 (grip tape, bungee, skates, USB receiver) | 795 / 249 |
| Keyboards (15) | 60% 66, FullSize 38, TKL 67, One Handed 68, Keyboard Accessories 4442 (keycaps, switches, coiled cables, wrist rests) | 897 / 234 |
| Headsets (39) | Wired 72, Wireless 73, Headset Stand 4424 | 559 / 62 |
| Streaming (1109) | Microphone 65, Webcam 64, Audio Mixers, Capture Card, Mic Stands, Stream Decks | 352 / 92 |
| Computer Accessories (62) | USB HUB 151, HDMI/DisplayPort/Ethernet cables, enclosures, laptop stands, monitor arms, screwdrivers | 438 / 189 |
| Mobile Accessories (4395) | chargers, cables, car holders, stands, cooling fans, game controllers, power banks | 273 / 120 |
| Hardware (433) | Graphics Cards 576, PSU 174, STORAGE 612, Computer Cases 434 | 63 / **0** |
| Fans & Cooling (120) | CPU coolers (air, liquid/AIO), case fans, thermal paste and pads, laptop cooling pads | 163 / 13 |
| Mouse Pads (37) | | 333 / 29 |
| Speakers (125) | | 78 / 14 |
| Graphic Tablet (63) | | 72 / 1 |
| Gamepad (148) | | 67 / 33 |
| Gaming Accessories (175), Gaming Chairs (4458), Gaming Consoles (4454, retro handhelds), Projectors (4434) | | 33 / 2, 11 / 6, 8 / 5, 17 / 2 |

The categories endpoint's `count` matches neither number (Mouse 259, Keyboards 252, Headsets 71,
Hardware 59): WooCommerce's cached term counts. Use `X-WP-Total` of a products request instead.

Fit with the existing watch groups:

| Group (minimum) | Hardware Market | In stock ≥ minimum |
| --- | --- | --- |
| Headphones (800) | `headsets` (39) | 35 |
| PC components (1,500) | `hardware` (433), `cpu-coolers` | 0 today (all sold out) |
| Gaming consoles (8,000) | `gaming-consoles` is retro handhelds at 450 to 3,699 | 0 (the group's title filter would drop them anyway) |

Most of the shop (mice, keyboards, microphones, webcams, hubs, gamepads) fit no older group, so a new group
**Gaming peripherals** (from 1,000 EGP, 30 to 60 minutes) holds Mouse `41`, Keyboards `15`, Gamepad `148` and
Streaming `1109` plus Dubai Phone's keyboards and mice. Amazon, noon and Jumia are not crawled for it (thousands
of cheap listings, block risk); cross-shop matching still finds the same Logitech or Redragon model there if
another group tracks it.

## How we crawl it

- **Requests**: `GET /wp-json/wc/store/v1/products?_fields=…&per_page=100&page=N&orderby=id&order=asc
  &category=<id>&min_price=<EGP×100>&max_price=<EGP×100+99>&stock_status[]=instock&…outofstock&…onbackorder`.
  Slice keys: `category:<id or slug>` (`category:all` = the whole shop), `search:<words>`, and
  `deals:<id>`, the fast lane: one page of the category's 50 most recently modified products
  (`orderby=modified&order=desc`), no count, never a second page.
- The category list (`/products/categories?per_page=100`) is read once per worker session to reject an unknown
  id or slug (`UnknownTarget`; the products endpoint just answers `[]`) and to list children for
  `deals categories`.
- **Reading**: price = `prices.price` (already piastres); a higher `regular_price` is the crossed-out price (kind
  `list`); `in_stock` = `is_in_stock`; `stock_left` = `low_stock_remaining`; title unescaped; brand from
  `brands[0]`, else the first word of an "ONIKUMA | X92" style SKU (title-cased when shouted). Keyed by id.
- **Answers**: 403/429/503 or a non-JSON body twice (a fresh session in between) = `Blocked`; HTTP 400 =
  `ValueError` with the API's message.
- `max_slice_total` is 1,000 (10 pages): each page costs the shop about 3 s, so bigger roots are split by price.
- **Groups**: Headphones (`39`, `deals:39`), PC components (`433`, `120`), Gaming peripherals (`41`, `15`, `148`,
  `1109`, `deals:41`, `deals:15`), Chargers & power banks (Chargers `4396` 58, Power Banks `4470` 16; from 31 Oct). About 1,550 products above the group minimums (340 in stock; sold-out ones are
  read too, so a sell-out is seen), about 20 pages a pass.
- **Politeness**: 1 to 2 s between requests (`[sites.hardwaremarket_eg]`).

## Running and checking it

```bash
.venv/bin/deals categories hardwaremarket_eg                 # main ids
.venv/bin/deals categories hardwaremarket_eg 15              # count and children
.venv/bin/deals crawl hardwaremarket_eg "category:39|price:800-" --show 5
.venv/bin/deals crawl hardwaremarket_eg "deals:41|price:1000-" --show 5   # the fast lane
```

On the server, `make sql`:

```sql
SELECT grp, count(*) products, sum(in_stock) in_stock FROM products WHERE site = 'hardwaremarket_eg' GROUP BY grp;
SELECT kind, count(*), sum(failures > 0) failing, min(next_due_at) FROM jobs WHERE site = 'hardwaremarket_eg' GROUP BY kind;
```

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `blocked: hardwaremarket_eg: HTTP 403` | Cloudflare's challenge | Normal from a cloud IP (keep it off the droplet); from home it pauses by itself |
| `UnknownTarget: no category 'x'` | Unknown id or slug | `deals categories hardwaremarket_eg`, or the category list URL |
| `the Store API refused …: rest_invalid_param` | A parameter the API rejects (e.g. `per_page` above 100) | Fix the adapter's URL |
| Every product suddenly "new" | Products re-created with new ids (an import) | Expected once; history restarts for those products |

## Re-checking

```bash
curl -s -D - -o /dev/null 'https://hardwaremarket.net/wp-json/wc/store/v1/products?per_page=1' | grep -i x-wp-total
curl -s 'https://hardwaremarket.net/wp-json/wc/store/v1/products?per_page=5&orderby=modified&order=desc&_fields=id,name,prices.price,prices.regular_price,is_in_stock'
ssh root@207.154.206.127 "curl -s -o /dev/null -w '%{http_code}\n' https://hardwaremarket.net/wp-json/wc/store/v1/products?per_page=1"   # 403 from the droplet
```
