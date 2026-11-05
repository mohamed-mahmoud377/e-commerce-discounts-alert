"""gcshopeg.com (GC Shop): watches, mostly, with some lighters and bags. Shopify, see deals/sites/shopify.py and docs/GCSHOP.md.

Read whole through `category:all` (8,500 products, the same as its sitemap; its `all` collection counts 3,630). Variants
are sizes and band colours: none of the 41 products that have them price them differently (5 Nov 2026).
"""

from deals.sites.dream2000_eg import IGNORED_OPTIONS
from deals.sites.shopify import ShopifyShop


class GcShopEG(ShopifyShop):
    name = "gcshop_eg"
    base = "https://gcshopeg.com"
    ignored = IGNORED_OPTIONS | {"size", "band color"}


parse_products = GcShopEG.parse_products
