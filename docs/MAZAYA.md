# Mazaya (mazaya.eg): site analysis and how we crawl it

Added 5 November 2026. The adapter is `src/deals/sites/mazaya_eg.py` (site name `mazaya_eg`); tests are `test_mazaya_*` in `tests/test_parsers.py` against `tests/fixtures/mazaya_perfumes.json.gz` and `mazaya_skincare.json.gz`.

## Summary

| Question | Answer |
| --- | --- |
| Name in alerts | مزايا |
| What it sells | Perfumes, makeup, skin care, gift sets. Lattafa, Ajmal, BOSS, Nishane, Dolce & Gabbana |
| Platform | Nuxt in front, **Magento** behind (`/graphql` on its own host, `Store: en`) |
| What we fetch | `GET /graphql?query=...&variables=...` |
| Products per page | 100 (about 35 KB, 1 to 1.5 s) |
| Exact count | `total_count` |
| Price filter | `price: {from, to}` on the current price, inclusive (23,500 finds it, 23,501 doesn't) |
| Who can crawl it | **Anyone** so far: the Mac and the droplet pass `/graphql` with Chrome's fingerprint |
| Keyed by | Magento's `id`, plus the size or shade when they cost differently: `31391:50-ml` |
| Catalogue | Perfumes 1,513 (833 in stock), Makeup 1,076, Skin Care 97, Bundles 63, Gifts about 126 |

## robots.txt: read this first

**`robots.txt` disallows `/graphql`, and that is the only place the listings are:** category pages arrive without products and the browser fetches them there. The user decided to crawl it anyway on 5 Nov 2026, the same call as Dubai Phone, on these terms: slow (Pace 2x, so 4 to 8 s) and alerts on "Kept, not sent" until they look right. Both are in the web UI's Shop settings.

Other refusals seen, all left alone: `/sitemap.xml` 403 and `backend.mazaya.eg/robots.txt` 403 (nginx). Cloudflare in front. No block yet. **If it blocks us it means this rule is being enforced: switch it off rather than fight it.**

## Traps

- **The listing shows only the cheapest size.** Of 1,513 perfumes, 358 are configurable, all by `size`, and 132 have sizes at different prices. Every size is read from `variants` and is its own listing, or a sold-out small bottle would look like a change in price.
- **Shades usually cost the same:** 79 of 87 makeup products. They fold into one listing, and are separate only when priced differently. Their options are named `color`, `colour` and `flormar_color`.
- **A page can't select a variant.** A link opens on the shop's default size or shade, so **an alert's screenshot of a sized product may show another size's price**. Measured: `All Hours Foundation. (MN1)`, listed at 3,151.80, opens at 2,975 on another shade. The alert's own number is the right one; the admins see both in the review group.
- **A page past the end is a GraphQL error**, an unknown category an empty answer (the adapter then asks for the category). Price 0 on 5 perfumes: no price.
- **Sorted by name**, as nothing repeats: 1,513 of 1,513.

## Prices and stock

Crossed-out price on 537 of 833 perfumes in stock (64%), median 15%, none at 60%, by campaigns with names like `deals 15%`, `LATTAFA 25`, `Black Friday`: kind `list`, and a `no_claim_alerts` rule, so its products alert only from our own history. Sold-out products stay listed. Sells its own stock. The page shows "ONLY 1 LEFT" (not read).

## How we crawl it

A group of its own, **Beauty** ("عطور وتجميل"), minimum 300 EGP, every 30 minutes, cap 45, since 5 Nov 2026: categories 4, 81, 42, 862, 542. About 30 pages a pass, 2 to 4 minutes at 4 to 8 s: **about 40 pages an hour**, shared by the droplet and the Mac. Whatever overlaps is in the same group, so no label flips. No fast lane yet; `/en/deals` has a "Has Deal" filter worth trying later.

## Running and checking it

```bash
.venv/bin/deals crawl mazaya_eg "category:862" --pages 1 --show 5
```

## Troubleshooting

`GraphQL error` means a field changed. `HTTP 403` or a non-JSON answer: see robots.txt above.

## Alert screenshots

`mazaya.eg/ar/` for `mazaya.eg/en/`. Price `.text-primary-800:has(.currency)`, context `h1.product-name` and `.zoom-container img`, 1 s to settle. About 8 s. Saved page: `tests/fixtures/mazaya_product_rendered.html.gz`.
