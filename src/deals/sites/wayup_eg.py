"""wayupsports.com (WayUp Sports): shoes and sportswear, a Shopify shop. See deals/sites/shopify.py and docs/WAYUP.md.

Variants are sizes, folded into one listing at the cheapest in stock: of 18,568 sized products 12 have sizes at different
prices (5 Nov 2026). Listing each size would make about five times the rows for nothing.
"""

from deals.sites.dream2000_eg import IGNORED_OPTIONS
from deals.sites.shopify import ShopifyShop


class WayUpEG(ShopifyShop):
    name = "wayup_eg"
    base = "https://wayupsports.com"
    ignored = IGNORED_OPTIONS | {"size"}


parse_products = WayUpEG.parse_products
