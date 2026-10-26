from dataclasses import dataclass, field, replace

# Normalised site badges (Listing.badges). Sites word these differently; the
# deal rules only look at these names.
LIMITED_DEAL = "limited_deal"  # amazon "Limited time deal", noon "Deal"
MEGA_DEAL = "mega_deal"  # noon "Mega Deal"
SELLING_FAST = "selling_fast"  # noon "Selling out fast"
LOWEST_30D = "lowest_30d"  # the site's own history: lowest price in 30 days
LOWEST_365D = "lowest_365d"  # ... lowest price in a year
BESTSELLER = "bestseller"


@dataclass(frozen=True)
class Listing:
    """One product as seen on a listing page at a point in time.

    Prices are stored in minor units (piastres for EGP) to avoid float rounding.
    """

    site: str
    sku: str
    title: str
    url: str
    price: int | None  # None when the product shows no price (unavailable)
    list_price: int | None  # the site's crossed-out price; checked against our own history
    currency: str
    in_stock: bool
    list_price_kind: str | None = None  # "was" (site's recent price) or "list" (RRP, often inflated)
    brand: str | None = None
    stock_left: int | None = None  # "Only 3 left in stock"
    recent_sales: int | None = None  # "300+ bought in past month" -> 300
    rating: float | None = None
    reviews: int | None = None
    badges: tuple[str, ...] = ()
    seller: str | None = None  # noon: one product can have several sellers at very different prices


@dataclass(frozen=True)
class Category:
    id: str  # amazon browse node id / noon category path
    name: str


@dataclass(frozen=True)
class Slice:
    """A crawlable piece of a site: a search or category, optionally narrowed to a
    price range (whole EGP, both ends inclusive, None = open-ended).

    Its key is the string form used in config, on the command line and in the
    database: 'category:21832868031' or 'search:laptop|price:1000-2000'.
    """

    kind: str  # "search" or "category"
    value: str  # keywords, or the site's category id/path
    price_min: int | None = None
    price_max: int | None = None

    @property
    def has_price(self) -> bool:
        return self.price_min is not None or self.price_max is not None

    def with_price(self, low: int | None, high: int | None) -> "Slice":
        return replace(self, price_min=low, price_max=high)

    def key(self) -> str:
        key = f"{self.kind}:{self.value}"
        if self.has_price:
            low = "" if self.price_min is None else self.price_min
            high = "" if self.price_max is None else self.price_max
            key += f"|price:{low}-{high}"
        return key

    @classmethod
    def from_key(cls, key: str) -> "Slice":
        target, _, price = key.partition("|price:")
        kind, sep, value = target.partition(":")
        if not sep or kind not in ("search", "category") or not value:
            raise ValueError(f"expected 'search:<keywords>' or 'category:<id>', got {key!r}")
        low = high = None
        if price:
            low_s, _, high_s = price.partition("-")
            low = int(low_s) if low_s else None
            high = int(high_s) if high_s else None
        return cls(kind, value, low, high)

    def __str__(self) -> str:
        return self.key()


@dataclass(frozen=True)
class PageResult:
    listings: list[Listing]
    has_next: bool
    total: int | None = None  # exact result count for the slice, None if the site only says "over N"
    subcategories: list[Category] = field(default_factory=list)  # children (for `deals categories`)
    # True when the page has product cards but no result-count bar: amazon.eg serves
    # such decoy listings (random groceries) to sessions it distrusts.
    decoy: bool = False


class Blocked(Exception):
    """The site served a captcha / bot challenge instead of content."""


class UnknownTarget(ValueError):
    """The site doesn't recognise the category / search (e.g. a typo in config)."""
