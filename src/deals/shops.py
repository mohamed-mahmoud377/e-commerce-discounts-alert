"""Every shop's identity in one place: names, badge, kind, access, doc, category roots.

The adapters that fetch pages are in deals/sites (they import HTTP clients; the coordinator only needs
this table). A new shop is one entry here plus its adapter; tests/test_shops.py checks that every shop has
everything else it needs (adapter, screenshot spec, parser fixture, doc, config delays, worker script).
"""
from dataclasses import dataclass

# Kinds, in the order the web UI groups shops.
KINDS = {"marketplace": "Marketplaces", "electronics": "Electronics shops", "grocery": "Supermarkets", "fashion": "Fashion"}


@dataclass(frozen=True)
class Shop:
    name: str  # the key everywhere: products.site, jobs, config, DEALS_SITES
    label: str  # English, for the CLI and the web UI
    arabic: str  # alerts, bot replies, health and the daily report
    badge: str  # one to three letters for tight spaces (watch groups)
    kind: str  # a KINDS key
    url: str  # the shop's home page
    home_only: bool  # datacenter IPs are refused: only home workers can crawl it
    doc: str  # its analysis in docs/
    roots: tuple[tuple[str, str], ...]  # (name, category id) printed by `deals categories <shop>`
    slug_codes: bool = False  # model codes sit in the last words of its product links (matching.slug_codes)


SHOPS: dict[str, Shop] = {s.name: s for s in (
    Shop("amazon_eg", "Amazon", "أمازون مصر", "A", "marketplace", "https://www.amazon.eg", False, "CATEGORIES.md",
         (("Electronics", "18018102031"), ("Home", "18021933031"), ("Appliances", "21863792031"),
          ("Video Games", "18022560031"))),
    Shop("noon_eg", "noon", "نون مصر", "N", "marketplace", "https://www.noon.com/egypt-en/", True, "CATEGORIES.md",
         (("Electronics & Mobiles", "electronics-and-mobiles"), ("Home & Kitchen", "home-and-kitchen"))),
    Shop("jumia_eg", "Jumia", "جوميا مصر", "J", "marketplace", "https://www.jumia.com.eg", True, "JUMIA.md",
         (("Electronics", "electronics"), ("Phones & Tablets", "phones-tablets"), ("Computing", "computing"),
          ("Home & Office", "home-office"), ("Gaming", "video-games"))),
    Shop("cairosales_eg", "Cairo Sales", "كايرو سيلز", "C", "electronics", "https://cairosales.com", True,
         "CAIROSALES.md",
         (("TVs & Screens", "tvs-screens"), ("Air Conditioners", "air-conditioners"), ("Refrigerators", "refrigerators"),
          ("Mobiles", "mobiles-tablets"), ("Small Appliances", "small-appliances"),
          ("Computers", "computer-accessories"), ("Gaming", "gaming-accessories"), ("Audio", "home-theaters-speakers")),
         slug_codes=True),
    Shop("2b_eg", "2B", "توبي (2B)", "2B", "electronics", "https://2b.com.eg", True, "2B.md",
         (("Computer", "118"), ("Mobile & Tablet", "266"), ("Home Appliances", "2370"), ("Televisions", "1932"),
          ("Audio", "2382"), ("Gaming", "120"), ("Electronics", "610"))),
    Shop("btech_eg", "B.TECH", "بي تك (B.TECH)", "BT", "electronics", "https://btech.com", True, "BTECH.md",
         (("TVs & Projectors", "tvs-projectors"), ("Large Home Appliances", "large-home-appliances"),
          ("Small Home Appliances", "small-home-appliances"), ("Mobiles & Tablets", "mobiles-tablets"),
          ("Laptops & PCs", "laptop-pc"), ("Audio & Recording", "audio-recording"), ("Gaming Area", "gaming-area"),
          ("Electronics", "electronics"), ("Wearables", "wearables"), ("Personal Care", "personal-care")),
         slug_codes=True),
    Shop("dream2000_eg", "Dream 2000", "دريم 2000", "D", "electronics", "https://dream2000.com", False, "DREAM2000.md",
         (("TVs", "tvs"), ("Mobiles", "mobiles"), ("Tablets", "tablets"), ("Laptops", "laptop"),
          ("Air Conditioners", "air-conditioners"), ("Refrigerators", "refrigerator-and-deep-freezer"),
          ("Kitchen Appliances", "kitchen-appliances"), ("Gaming", "gaming"), ("Audio", "audio"),
          ("Electronics", "electronics"),
          ("every collection (386)", "https://dream2000.com/en/collections.json?limit=250&page=1"))),
    Shop("carrefour_eg", "Carrefour", "كارفور مصر", "CF", "grocery", "https://www.carrefouregypt.com", False,
         "CARREFOUR.md",
         (("Electronics & Appliances", "NFEGY4000000"), ("Mobiles, Tablets & Wearables", "NFEGY1200000"),
          ("Small Appliances", "NFEGY4040000"), ("Large Appliances", "NFEGY4100000"),
          ("Computers & Office", "NFEGY4070000"), ("TVs & Projectors", "NFEGY4080000")),
         slug_codes=True),
    Shop("dubaiphone_eg", "Dubai Phone", "دبي فون", "DP", "electronics", "https://www.dubaiphone.net", True,
         "DUBAIPHONE.md",
         (("Everything (one listing)", "all"), ("Smart Phone", "mobiles-all-2"), ("Laptops", "all-laptops"),
          ("Tablets", "tablets"), ("TVs", "tvs"), ("Kitchen Appliances", "kitchen-appliances"),
          ("Home Appliances", "home-appliances"),
          ("every category (145)", "https://www.dubaiphone.net/sitemap-categories.xml"))),
    Shop("hardwaremarket_eg", "Hardware Market", "هاردوير ماركت", "HM", "electronics", "https://hardwaremarket.net",
         True, "HARDWAREMARKET.md",
         (("Everything", "all"), ("Mouse", "41"), ("Keyboards", "15"), ("Headsets", "39"), ("Streaming", "1109"),
          ("Gamepad", "148"), ("Hardware", "433"), ("Fans & Cooling", "120"), ("Computer Accessories", "62"),
          ("Mobile Accessories", "4395"))),
    Shop("amazon_now_eg", "Amazon Now", "أمازون ناو", "AN", "grocery", "https://www.amazon.eg", True, "AMAZON_NOW.md",
         (("Everything (the Amazon Now root)", "211761638031"), ("Grocery", "18020637031"),
          ("Health & Household", "18021875031"), ("Beauty", "18017988031"), ("Baby", "18017931031"),
          ("Electronics", "18018102031"))),
    Shop("raya_eg", "Raya", "راية شوب", "RA", "marketplace", "https://www.rayashop.com", False, "RAYA.md",
         (("Mobiles & Tablets", "316"), ("Televisions", "533"), ("Large Appliances", "292"),
          ("Kitchen Appliances", "3116"), ("Small Appliances", "300"), ("Laptops & PCs", "272"), ("Electronics", "263"))),
    Shop("vodafone_eg", "Vodafone", "فودافون", "VF", "electronics", "https://eshop.vodafone.com.eg", False, "VODAFONE.md",
         (("Smart Phones", "smart-phones"), ("TVs", "tv"), ("Laptops", "laptops"), ("Tablets", "tablets"),
          ("Gaming", "gaming"), ("Accessories", "phones-accessories"), ("Home Appliances", "home-appliances"))),
    Shop("dokkantech_eg", "Dokkan Tech", "دكان تك", "DT", "electronics", "https://www.dokkantech.com", False,
         "DOKKANTECH.md",
         (("Everything", "all"), ("Audio", "audio"), ("Gaming", "gaming"), ("Computer Accessories", "computer-accessories"),
          ("Wearables", "wearables"), ("Phone Accessories", "phoneaccessories"))),
    Shop("etisal_eg", "Etisal", "اتصال ستور", "ES", "electronics", "https://etisalstore.com", False, "ETISAL.md",
         (("Mobiles & Tablets", "1081"), ("Mobile Accessories", "1283"), ("Portable Audio", "1334"),
          ("Wearables", "1372"), ("Computing", "1183"), ("Televisions", "1160"), ("Small Appliances", "1570"),
          ("Food Preparation", "1531"))),
    Shop("orange_eg", "Orange", "أورنج", "OR", "electronics", "https://eshop.orange.eg", False, "ORANGE.md",
         (("Accessories", "accessories"), ("Home Appliances", "home-appliances"), ("Smart TVs", "smart-tvs"),
          ("Computer Supplies", "computer-supplies"), ("Smart Phones (not watched, several prices)", "smart-phones"))),
    Shop("lcwaikiki_eg", "LC Waikiki", "إل سي وايكيكي", "LCW", "fashion", "https://www.lcwaikiki.eg", True, "LCWAIKIKI.md",
         (("Women", "women-clothing-t-10"), ("Men", "men-clothing-t-9"), ("Girls", "girls-clothing-t-100046"),
          ("Boys", "boys-clothing-t-200046"), ("Women's shoes", "femei-incaltaminte-t-33"))),
    Shop("defacto_eg", "DeFacto", "ديفاكتو", "DF", "fashion", "https://www.defacto.com.eg", False, "DEFACTO.md",
         (("Women on sale", "woman-discount"), ("Women new season", "woman-new-season"), ("Men on sale", "man-discount"),
          ("Kids on sale", "kids-discount"), ("Women's dresses", "woman-dress"))),
)}


def arabic(name: str) -> str:
    """The shop's Arabic name, or its key for a shop this code does not know (an old row)."""
    return SHOPS[name].arabic if name in SHOPS else name


def label(name: str) -> str:
    return SHOPS[name].label if name in SHOPS else name


def expand(value: str | None) -> list[str] | None:
    """A shop filter from the web UI: '' means every shop, 'kind:electronics' every shop of that kind,
    anything else that one shop (also a name this code does not know: old rows keep their key)."""
    if not value:
        return None
    if value.startswith("kind:"):
        return [s.name for s in SHOPS.values() if s.kind == value[5:]]
    return [value]


def for_ui() -> list[dict]:
    """The shop list the web UI draws its selects, badges and tables from, in SHOPS order."""
    return [{"name": s.name, "label": s.label, "badge": s.badge, "kind": s.kind, "kind_label": KINDS[s.kind],
             "url": s.url, "home_only": s.home_only, "doc": s.doc} for s in SHOPS.values()]
