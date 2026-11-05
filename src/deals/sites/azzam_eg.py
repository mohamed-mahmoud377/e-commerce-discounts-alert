"""azzamwatches.com (Azzam Watches): watches from Casio to Richard Mille. Shopify, see deals/sites/shopify.py and docs/AZZAM.md.

Read whole through `category:all` (7,287 products; its collections count up to 21,227). Nothing is folded but the SKU
option: 4 of the 10 products with colour or movement options price them differently (5 Nov 2026), so each is its own listing.
"""

from deals.sites.shopify import ShopifyShop


class AzzamEG(ShopifyShop):
    name = "azzam_eg"
    base = "https://azzamwatches.com"
    ignored = {"sku", "title"}


parse_products = AzzamEG.parse_products
