"""Amazon Now on amazon.eg (groceries and essentials in about 20 minutes, from dark stores).

Amazon Now is the ordinary amazon.eg search under its own browse root, so the page
layout, the WAF cookies and the parser are amazon_eg's. But an ASIN sold on Amazon Now
is a separate offer at its own price (Juhayna 6x1 L: EGP 275.95 from Amazon, 289.75 on
Amazon Now, 3 Nov 2026), so it is a shop of its own: one price history per offer. Every
query must carry the root node; the same child node without it lists the regular
offers. See docs/AMAZON_NOW.md.
"""

from deals.models import Blocked, PageResult, Slice, UnknownTarget
from deals.sites.amazon_eg import AmazonEG

ROOT = "211761638031"  # Amazon Now's browse root ("Amazon Now" in the search box is i=amazonyalla)


class AmazonNowEG(AmazonEG):
    name = "amazon_now_eg"
    # Links open the Amazon Now offer instead of the regular one.
    dp_query = "?almBrandId=sAuWWBROaG&fpw=alm"

    def fetch_page(self, slice: Slice, page: int) -> PageResult:
        if slice.kind == "deals":  # the Today's Deals API has nothing under the Amazon Now root
            raise UnknownTarget(f"{self.name}: no deals feed; use category:<node> or search:<keywords>")
        result = super().fetch_page(slice, page)
        if result.total == 0 and slice.value == ROOT and not slice.has_price:
            # The whole store is never empty: this session's delivery location has no dark store
            # (the droplet's, 3 Nov 2026). Pause this worker instead of recording an empty shop.
            raise Blocked(f"{self.name}: Amazon Now lists nothing here (delivery location not served)")
        return result

    def _nodes(self, slice: Slice) -> list[str]:
        if slice.kind == "search":
            return [f"n:{ROOT}"]
        if slice.value.split(",")[0] == ROOT:
            return [f"n:{slice.value}"]
        return [f"n:{ROOT}", f"n:{slice.value}"]
