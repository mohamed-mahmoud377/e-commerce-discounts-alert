"""shop.iravin.com (Ravin): an Egyptian clothes brand. Shopify, see deals/sites/shopify.py and docs/RAVIN.md.

Read whole through `category:all`. Its `/products.json` answered HTTP 429 "local_rate_limited" to the first request of 5 Nov 2026,
while `/collections/all/products.json` lists the same shop and answers: `whole` says which to use. Variants are colours and
sizes; see the doc for what was counted about their prices. Most of the shop is on sale, so its crossed-out prices prove nothing.
"""

from deals.sites.dream2000_eg import IGNORED_OPTIONS
from deals.sites.shopify import ShopifyShop


class RavinEG(ShopifyShop):
    name = "ravin_eg"
    base = "https://shop.iravin.com"
    ignored = IGNORED_OPTIONS | {"size"}
    whole = "/collections/all/products.json"


parse_products = RavinEG.parse_products
