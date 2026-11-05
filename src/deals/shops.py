"""Every shop's identity in one place: names, badge, kind, access, doc, category roots.

The adapters that fetch pages are in deals/sites (they import HTTP clients; the coordinator only needs
this table). A new shop is one entry here plus its adapter; tests/test_shops.py checks that every shop has
everything else it needs (adapter, screenshot spec, parser fixture, doc, config delays, worker script).
"""
from dataclasses import dataclass

# Kinds, in the order the web UI groups shops.
KINDS = {"marketplace": "Marketplaces", "electronics": "Electronics shops", "grocery": "Supermarkets", "fashion": "Fashion", "watches": "Watches", "beauty": "Beauty"}


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
    Shop("elaraby_eg", "El Araby", "العربي جروب", "EA", "electronics", "https://www.elarabygroup.com", False, "ELARABY.md",
         (("Home Appliances", "60"), ("TVs and Audio", "329"), ("Air Conditioners", "62"), ("Watches", "64"),
          ("Lighting", "65"), ("Spare Parts", "217"), ("Accessories", "1966"), ("Business Products", "66")),
         slug_codes=True),
    Shop("miami_eg", "Miami Center", "ميامي سنتر", "MI", "electronics", "https://miamicenters.com", False, "MIAMI.md",
         (("Mobiles & Tablets", "52"), ("Apple", "822"), ("Buds", "685"), ("Smart Watches", "1627"), ("Audio", "701"),
          ("Home Appliances", "1639"), ("Accessories", "107"), ("Consoles", "513"))),
    Shop("sigma_eg", "Sigma Computer", "سيجما", "SG", "electronics", "https://www.sigma-computer.com/en", False, "SIGMA.md",
         (("Hardware Components", "9f5039af-f5d2-4396-9ba2-8ac40277c373"), ("Laptops", "9f5039de-5c80-46f3-9fe4-6e8f94189b8c"),
          ("Storage", "9f5039ed-8dd4-4a9b-a8ce-caf20ed29436"), ("Monitors", "9f503a01-79d7-4a25-b3f3-63b0fd5b3094"),
          ("Accessories", "9f503a0f-5b3e-4efe-8722-ffbafc51114c"), ("Desktops", "9f503a1d-8d45-4d97-ad76-c068afaa39db"))),
    Shop("elnekhely_eg", "Elnekhely", "النخيلي تكنولوجي", "EN", "electronics", "https://www.elnekhelytechnology.com", True,
         "ELNEKHELY.md",
         (("Graphics Cards", "graphics-card"), ("Processors", "processors"), ("Motherboards", "motherboards"),
          ("RAM", "ram"), ("SSD", "ssd"), ("Laptops", "laptop"), ("Monitors", "monitors"), ("Accessories", "accessories"))),
    Shop("wayup_eg", "WayUp Sports", "واي أب سبورتس", "WU", "fashion", "https://wayupsports.com", False, "WAYUP.md",
         (("On sale", "all-sale"), ("New in", "all-new-in"), ("Footwear", "all-footwear"), ("Apparel", "all-apparel"))),
    Shop("gcshop_eg", "GC Shop", "جي سي شوب", "GC", "watches", "https://gcshopeg.com", False, "GCSHOP.md",
         (("Everything", "all"), ("Casio", "casio"), ("Citizen", "citizen"), ("Hot deals", "gc-stores-hotdeals"))),
    Shop("azzam_eg", "Azzam Watches", "عزام للساعات", "AZ", "watches", "https://azzamwatches.com", False, "AZZAM.md",
         (("Everything", "all"), ("Luxury", "luxury"), ("Fashion brands", "fashion-brands"))),
    Shop("mazaya_eg", "Mazaya", "مزايا", "MZ", "beauty", "https://mazaya.eg/en", False, "MAZAYA.md",
         (("Perfumes", "4"), ("Makeup", "81"), ("Skin Care", "42"), ("Bundles & sets", "862"), ("Gifts", "542"))),
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
