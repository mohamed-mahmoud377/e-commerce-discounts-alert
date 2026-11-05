# LC Waikiki and DeFacto: first look (5 Nov 2026), not added yet

Both are fashion, where no watch group fits, and both `robots.txt` files forbid the pages we would need. **Two decisions are the user's** (the add-shop skill, stage 1 and stage 6): crawl anyway or not, and which new watch group. One page load each was made to learn the shape, and no more until decided.

| | LC Waikiki (`www.lcwaikiki.eg`) | DeFacto (`www.defacto.com.eg`) |
| --- | --- | --- |
| Platform | React on ASP.NET; own API hosts (`/en/ajax/...`, `opusstream.lcwaikiki.com`) | ASP.NET, server-rendered catalogue; Akamai |
| From the Mac | Chrome's fingerprint 200; plain curl 403 | Chrome's fingerprint 200; plain curl 403 |
| From the droplet | **403 with every client** (Cloudflare): home-only | Chrome's fingerprint 200, plain curl 403 |
| Listing | Rendered in the page (2.8 MB) with no product request seen; not worked out | `data-documents` JSON on each `.product-card`: `Price`, `DiscountedPrice`, `CampaignDiscountedPrice`, `LongCode`, `ProductVariantIndex`, with `<input id="totalCount" value="1543">` and 36 cards a page |
| robots.txt | Allows the bare category pages (`/en/women-clothing-t-10`). **Disallows** `*PageIndex=*`, `*sort=*`, `*MinPrice=*`, `*MaxPrice=*`, `*price=*`, `*filtertype=*` and every filter name: paging and price slices, everything the planner depends on | **Disallows `/app/`** (the category pages themselves), `*catalog*`, `*search*`, `/*Product*`, `/*filter=*`, `/*sortorder*`. Its sitemap answers plain curl with an Akamai 403 |

Other things seen: LC Waikiki has `/ar` as default with Arabic slugs (`/نساء-الملابس-t-10`) and `/en` for English; its menu is `GET /en/ajax/Home/GetMenuData?platformId=1` (249 KB). DeFacto's Arabic is the bare path, English is `/en-eg/`.

Clothes also don't fit the "one key, one price" shape without thought: sizes and colours per style, and permanent seasonal sales.
