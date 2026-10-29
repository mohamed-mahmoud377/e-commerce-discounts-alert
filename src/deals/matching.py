"""The same product on different shops, found by the manufacturer's model code.

No shop gives a barcode (GTIN) or part number on its listing pages, but most titles carry the
manufacturer's model code ("Sharp ... - AH-XP12BME", "Sony WH-1000XM5 ..."). Brand plus that
code is the identifier Google Shopping asks for when there is no barcode. This module reads it
from titles and groups the listings that share it into a *model*. docs/MATCHING.md has the
design, the rules and how well they do on the real data.

It never runs while crawling. `rebuild` recomputes every assignment from the products table in
one pass (the watchdog runs it every hour; `deals models --rebuild` runs it now) and writes only
what changed, in short transactions. Crawling and alert rules don't read its tables, so a bug
here can show a wrong match in the web UI or CLI, never stop crawling or change an alert.

Precision comes first: a wrong "same product" is worse than none. Each rule only removes:
  1. code shape: letters and digits, 5+ characters, not a spec ("3200MHz", "i7-13620H", "R32");
  2. accessories: codes after "for" / "compatible with" in a title that names an accessory
     ("Ear Pads for WH-1000XM5"), or after "+ monitor" in a bundle, belong to another product;
  3. spec words learned from the data: a code used by 3+ brands is not a model ("1355U");
  4. series: a short code two series share ("Tune 660NC", "Live 660NC") keeps its series word;
  5. capacity: "16GB 512GB" in the title is part of the model (one laptop code, many configs), and
     so is a TV's size when the code doesn't hold it ("Samsung U8000H" is sold in four sizes);
  6. condition: renewed / refurbished / used listings are not the new product;
  7. brand: a listing whose brand disagrees with the model's brand is left out;
  8. price: a listing priced far from the others (a 100,000 EGP placeholder, a case at 300 EGP)
     is left out, and a model whose prices can't be one product is marked 'conflict'.
"""

import json
import os
import re
import sqlite3
import statistics
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from deals import db
from deals.db import iso

VERSION = 1  # bump when the rules change (kept on each row, to tell old assignments from new)

MIN_CODE = 5  # shorter codes ("A16", "G502", "M5") are too often shared by unrelated products
SPEC_BRANDS = 3  # a code seen under this many brands ...
DOMINANT = 0.7  # ... none of which has this share of its listings, is a spec word, not a model
OUTLIER_LOW, OUTLIER_HIGH = 0.5, 2.0  # a listing this far from the model's median price is left out
FRESH = timedelta(days=14)  # prices older than this don't count in a model's price range
BATCH = 1000  # rows per write transaction: the coordinator's page uploads wait at most this long

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−－"), "-")
_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)*")
_UNITS = ("GB|TB|MB|KB|MAH|WH|MHZ|GHZ|HZ|KW|W|WATT|WATTS|V|VOLT|A|AMP|L|LTR|LITER|LITERS|LITRE|LITRES|ML|KG|G|"
          "GM|GRAM|CM|MM|M|IN|INCH|INCHE|INCHES|HP|BTU|BTUH|BTUHR|K|P|FPS|MS|DB|PIN|PINS|CORE|CORES|THREAD|THREADS|"
          "YEAR|YEARS|YR|YRS|MONTH|MONTHS|BURNER|BURNERS|PIECE|PIECES|PCS|PC|SPEED|SPEEDS|LEVEL|LEVELS|LAYER|LAYERS|"
          "DOOR|DOORS|DRAWER|DRAWERS|SLICE|SLICES|CUP|CUPS|PERSON|PERSONS|PLACE|STAGE|STAGES|MP|X|TH|ST|ND|RD|D|GEN|"
          "NM|RPM|BAR|PSI|LB|LBS|OZ|C|F|MIN|MINS|MINUTE|MINUTES|H|HR|HRS|HOUR|HOURS|S|SEC|NIT|NITS|DPI|CH|KEY|KEYS|"
          "PORT|PORTS|BIT|T|FT|FEET|FOOT|PACK|SET|SETS|TIMES|MODE|MODES|PROGRAM|PROGRAMS|FUNCTIONS|IN1|BAND|FAN|FANS|"
          "BLADE|BLADES|STAR|STARS|SERIES|CC|KCAL|NOZZLE|NOZZLES|HEAD|HEADS|EDITION|DEGREE|DEGREES|KPA|PA|VA|"
          "GALLON|GALLONS|PIXEL|PIXELS|LUMEN|LUMENS|WAY|STEP|STEPS|MT|MTS|CELL|CELLS|KHZ|TIER|TIERS")
_SPEC = re.compile(
    r"^(?:"
    rf"\d+(?:{_UNITS})"  # 3200MHZ, 260PIN, 10CORE, 2YEAR, 5BURNER, 15HP (from 1.5HP)
    r"|\d+(?:GB|TB|MB)[A-Z]*"  # 8GBRAM, 256GBMIDDLE
    r"|\d+X\d+[A-Z]*"  # 2X8GB, 1920X1080, 90X60CM
    r"|\d+IN\d+[A-Z]*"  # 4IN1
    r"|I[3579]\d{3,5}[A-Z]{0,3}\d?"  # I71355U, I513420H, I71165G7, I713650HX
    r"|\d{4,5}(?:H|HX|HS|HK|U|G[1-7]|KF|F|X3D|XT)"  # bare CPU numbers: 1355U, 14650HX, 5600X3D
    r"|R[3579]\d{4}[A-Z]{0,3}"  # R74800H (Ryzen)
    r"|(?:CORE|ULTRA|RYZEN|CELERON|PENTIUM|ATHLON|SNAPDRAGON|HELIO|DIMENSITY|EXYNOS|TENSOR)[A-Z0-9]*\d[A-Z0-9]*"
    r"|(?:RTX|GTX|RX|MX|GT|ARC|QUADRO|RADEON|IRIS|UHD)\d{2,5}[A-Z]{0,3}"
    r"|(?:LP)?G?DDR\d[A-Z]?\d*[A-Z]*"  # DDR5, DDR3L, GDDR6, LPDDR5X
    r"|PC\d{1,2}L?\d{4,5}[A-Z]?"  # PC425600
    r"|\d+RX\d+"  # 1RX16
    r"|\d{3,4}R"  # monitor curvature: 1500R
    r"|[A-Z]{4,}\d{1,2}[A-Z]?"  # product lines, not models: PRESTIGE13, VIVOBOOK15, NOTE14, BUDS3
    r"|\d{2}[IA][A-Z]{1,3}\d{1,2}"  # Lenovo generations, shared by many laptops: 15IRH10, 15ARP9, 16IAX10
    r"|[A-Z]{4,}\d+(?:MM|CM|W|V|H|HZ|MAH|L|ML|IN|INCH)"  # a word glued to a spec: EARBUDS10MM
    r"|(?:USB|HDMI|WIFI|BT|BLUETOOTH|IPX|IP|CAT|HDR|WIN|WINDOWS|ANDROID|IOS|PCIE|NVME|SATA|QI|DOLBY|DTS|LGA|AM|"
    r"ATX|EATX|MATX|ITX|PD|QC|"
    r"VER|VERSION|GEN|MODEL|TYPE|CLASS|GRADE|SIZE|NO|NUMBER)\d+[A-Z]?\d*"  # HDR10, WIN11, IPX4, PCIE4, LGA1700
    r"|R\d{2,3}A?"  # refrigerants: R32, R410A
    r"|(?:19|20)\d{2}[A-Z]{0,8}"  # years: 2026EDITION
    r"|\d{3,4}[PK]|[48]K\d+.*"  # 1080P, 4K120
    r")$"
)
# A title about an accessory names it ("Ear Pads for ...", "Replacement Keyboard for ..."); the
# codes after its "for" / "compatible with" belong to the product it fits.
_ACCESSORY = re.compile(
    r"\b(?:case|cases|cover|covers|pads?|earpads?|cushions?|ear ?tips|strap|straps|protector|film|skin|skins|"
    r"sleeve|pouch|charger|chargers|cable|cables|adapter|adaptor|battery|batteries|keyboard|remote|belt|"
    r"cartridge|gasket|seal|spare|replacement|hinge|motherboard|sticker|decal|shell|housing|grips?|silicone|"
    r"tempered|cooling pad|cooling fan|laptop fan|cpu fan|holder|mount|bracket)\b", re.I)
_FITS = re.compile(r"\b(?:compatible|replacement|for|fits)\b", re.I)
# "RAM ... Compatible with Synology DS1621+, PC4-2666V": the clause names another product.
_FITS_CLAUSE = re.compile(r"\b(?:compatible\s+(?:with|for)|replacement\s+for|for\s+use\s+with|fits)\b"
                          r"(?:(?!\s-\s|[,|()\u2013;]).)*", re.I)
# "PC ... + MONITOR DELL P2425H": the monitor's code is not the PC's.
_EXTRA = re.compile(r"\+\s*(?:[A-Za-z]+\s+){0,2}?(?:monitor|screen|mouse|keyboard|headset|headphones?|bag|"
                    r"backpack|gift|speaker|webcam|printer)\b", re.I)
_BUNDLE = re.compile(r"\s(?:\+|with|plus|&|and|bundle|free|gift)\s", re.I)
_USED = re.compile(r"\b(?:renewed|refurbished|refurb|pre-?owned|open[ -]box|like new|second[ -]?hand)\b"
                   r"|\(used\)|\bused\s*[-|]|[-|]\s*used\b", re.I)
_CAPACITY = re.compile(r"(\d+(?:\.\d+)?)\s*(GB|TB)\b", re.I)
_SCREEN = re.compile(r"\b(\d{2,3})(?:\.\d)?\s*-?\s*(?:inch(?:es)?\b|in\b|\"|\u201d|'')", re.I)  # 55 Inch, 55", 55-inch
_WEAK = re.compile(r"^\d[A-Z0-9]{0,5}$")  # '660NC', '255XF': a number a maker may reuse across series
# Words shops put in the brand field that are not brands.
_NOT_BRANDS = {"generic", "other", "others", "nobrand", "unbranded", "oem", "none", "na", "brand", "nobrandname",
               "universal", "original", "new", "high", "smart", "electric", "wireless", "portable", "mini", "pro",
               "premium", "gaming", "the", "for", "kitchen", "home", "multi"}
# Same company, different names (only where shops really disagree).
_ALIASES = {"wd": "westerndigital", "soundcore": "anker", "ankersoundcore": "anker", "hewlettpackard": "hp",
            "lgelectronics": "lg", "redmi": "xiaomi", "poco": "xiaomi", "mi": "xiaomi", "jblbyharman": "jbl",
            "samsungelectronics": "samsung", "hyperxgaming": "hyperx", "philipsdomesticappliances": "philips",
            "sonyinteractive": "sony", "playstation": "sony"}


@lru_cache(maxsize=100_000)
def norm_brand(text: str | None) -> str | None:
    """'Black + Decker' / 'BLACK and DECKER' -> 'blackdecker'; None for non-brands ('Generic')."""
    if not text:
        return None
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]", "", re.sub(r"\band\b", "", s))
    s = _ALIASES.get(s, s)
    return None if not s or s in _NOT_BRANDS or s.isdigit() else s


def same_brand(a: str, b: str) -> bool:
    """One brand written two ways: 'arzum' / 'arzumokka', 'jamaky' / 'jamakyitaly', 'alaska' / 'walaska'."""
    return a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a))


def brand_groups(brands: Counter) -> Counter:
    """Listings per brand, with the spellings of one brand counted together (under the shortest)."""
    groups: Counter = Counter()
    for b in sorted(brands, key=len):
        groups[next((g for g in groups if same_brand(g, b)), b)] += brands[b]
    return groups


def guess_brand(title: str, known: set[str]) -> str | None:
    """The known brand a title starts with ('SHARP Split Air ...' -> 'sharp')."""
    words = re.findall(r"[A-Za-z0-9&+'.]+", unicodedata.normalize("NFKD", title))[:4]
    for k in range(len(words), 0, -1):
        if (b := norm_brand(" ".join(words[:k]))) in known:
            return b
    return None


def norm_code(token: str) -> str:
    """'WH-1000XM5' / 'wh1000xm5' / 'Wh-1000.XM5' -> 'WH1000XM5'."""
    return re.sub(r"[-.]", "", token).upper()


def is_code(code: str) -> bool:
    """Could this normalised word be a model code? (Its shape only: spec words that many brands
    use are found from the data.)"""
    return (MIN_CODE <= len(code) <= 24 and not code.isdigit() and not code.isalpha()
            and not _SPEC.match(code))


def _pieces(token: str) -> list[str]:
    """The parts of a hyphenated token that can be a code: 'WH-1000XM5' and 'HSU-12K' stay whole,
    plain words glued on are cut off ('5G-Middle-East' -> '5G'), and glued specs are dropped
    ('i7-13620H-16G', 'DDR5-4800-512GB')."""
    parts = token.split("-")
    if len(parts) == 1:
        return [token]
    if sum(1 for p in parts if len(p) >= 2 and _SPEC.match(p.upper())) >= 2:
        return []
    out, run = [], []
    for p in parts + [""]:
        if p == "" or (len(p) >= 5 and p.isalpha()):
            if run:
                out.append("-".join(run))
            run = []
        else:
            run.append(p)
    return out


@lru_cache(maxsize=300_000)
def _token_code(token: str) -> str | None:
    """The code a title word holds, if any (cached: the same words fill thousands of titles)."""
    if not any(ch.isdigit() for ch in token):
        return None
    return next((c for c in map(norm_code, _pieces(token)) if is_code(c)), None)


@dataclass(slots=True)
class TitleCodes:
    own: list[str]  # model codes of this product, best first
    mentions: list[str]  # codes of other products: the one an accessory fits, a bundled monitor
    accessory: bool  # the title is about an accessory for another product
    used: bool
    capacity: str  # '16GB+512GB' when the title states capacities, '55IN' a TV size the code lacks ('' otherwise)
    before: dict[str, str] = field(default_factory=dict)  # short code -> the word just before it ('TUNE')


@lru_cache(maxsize=200_000)
def read_title(title: str) -> TitleCodes:
    """Model codes in a title (needs no brand or database). Cached (noon lists one item under many
    sellers with the same title), so the result is shared: don't change it."""
    t = title.translate(_DASHES)
    cut, accessory = len(t), False
    if fits := _FITS.search(t):
        # the accessory is named in the first words ("Ear Pads Cushions for"), or right before a
        # "replacement" / "compatible" ("Cooling Fan Replacement for"); not "AC with remote for cooling"
        head = t[:fits.start()].split()
        strong = fits.group(0).lower() in ("replacement", "compatible")
        if _ACCESSORY.search(" ".join(head[:4])) or strong and _ACCESSORY.search(" ".join(head + [fits.group(0)])):
            cut, accessory = fits.start(), True
    if extra := _EXTRA.search(t, 12):
        cut = min(cut, extra.start())
    clauses = [m.span() for m in _FITS_CLAUSE.finditer(t)]
    bundle = _BUNDLE.search(t, 12)  # (not at the very start: "Black + Decker")
    mentions, scored, before = [], [], {}
    for m in _TOKEN.finditer(t):
        code = _token_code(m.group(0))
        if code is None:
            continue
        if t[m.end():m.end() + 1] == "+":
            code += "PLUS"  # 'XL2586X+' is a newer model than 'XL2586X'
        if m.start() >= cut or any(a <= m.start() < b for a, b in clauses):
            if code not in mentions:
                mentions.append(code)
            continue
        if any(code == c for _, _, c in scored):
            continue
        if _WEAK.match(code) and (word := re.search(r"([A-Za-z]{2,})\s+$", t[:m.start()])):
            before[code] = word.group(1).upper()
        near = t[max(0, m.start() - 8):m.start()].lower()
        score = (min(len(code), 12)
                 + (4 if re.search(r"(model|mpn|-|\(|,)\s*$", near) else 0)
                 + (2 if m.start() > len(t) * 0.6 else 0)
                 - (10 if bundle and m.start() > bundle.start() else 0))
        scored.append((score, -m.start(), code))
    own = [c for _, _, c in sorted(scored, reverse=True)]
    caps = sorted({f"{float(n):g}{u.upper()}" for n, u in _CAPACITY.findall(t)},
                  key=lambda c: (c[-2:], float(c[:-2])))
    # A TV series code ("Samsung U8000H") covers every screen size; a full code ("UA43U8000H") names one.
    if own and (size := _SCREEN.search(t)) and int(size.group(1)) >= 32 and size.group(1) not in own[0]:
        caps.append(f"{size.group(1)}IN")
    return TitleCodes(own, mentions, accessory and not own, bool(_USED.search(t)), "+".join(caps), before)


# --- the rebuild -------------------------------------------------------------------------------

@dataclass(slots=True)
class _Product:
    id: int
    site: str
    price: int | None
    fresh: bool
    brand: str | None
    brand_from: str  # 'title', 'shop', or 'model' (taken from the model's other listings)
    codes: TitleCodes
    code: str | None = None
    model: str | None = None
    status: str = "no_code"
    note: str = ""


@dataclass
class Result:
    products: dict[int, tuple] = field(default_factory=dict)  # product_id -> product_models columns
    models: dict[str, tuple] = field(default_factory=dict)  # model -> models columns


def build(rows, now: datetime, brand_sites: list[tuple[str, str]] | None = None) -> Result:
    """Every product's model, or why it has none. Pure: product rows in, table rows out.

    `rows` are (id, site, title, brand, seller, price, last_seen_at), read once (a cursor will do
    when `brand_sites`, the distinct (brand, site) pairs, is given; otherwise they come from `rows`).
    """
    fresh_after = iso(now - FRESH)
    if brand_sites is None:
        rows = list(rows)
        brand_sites = {(r[3], r[1]) for r in rows}
    shops_of = defaultdict(set)
    for brand, site in brand_sites:
        if b := norm_brand(brand):
            shops_of[b].add(site)
    known = {b for b, sites in shops_of.items() if len(sites) >= 2}  # brands 2+ shops use

    products = []
    for pid, site, title, brand, seller, price, last_seen in rows:
        shop_brand = norm_brand(brand)
        if shop_brand and shop_brand == norm_brand(seller):
            shop_brand = None  # the seller's own name, not the maker's
        # A known brand the title starts with beats the brand field, which sellers sometimes
        # fill with their shop's name ("Lap-Tech" on a Dell monitor).
        title_brand = guess_brand(title, known)
        brand, src = (title_brand, "title") if title_brand else (shop_brand, "shop") if shop_brand else (None, "")
        products.append(_Product(pid, site, price, last_seen >= fresh_after, brand, src, read_title(title)))

    # Spec words, learned: a code that many brands use is not a model ("1355U", "DCIP3"); nor is a
    # brand's name ("V2COM" is the brand V2Com).
    code_brands: dict[str, Counter] = defaultdict(Counter)
    code_sites: dict[str, set] = defaultdict(set)
    for p in products:
        for c in p.codes.own:
            code_sites[c].add(p.site)
            if p.brand:
                code_brands[c][p.brand] += 1
    spec = {}
    for c, counts in code_brands.items():
        groups = brand_groups(counts)
        if len(groups) >= SPEC_BRANDS and groups.most_common(1)[0][1] < DOMINANT * sum(groups.values()):
            spec[c] = f"used by {len(groups)} brands: " + ", ".join(b for b, _ in groups.most_common(4))

    # Each product's code: its best one that is not a spec word, preferring a code another shop
    # also has (title order alone can't tell a laptop's code from the mouse bundled with it).
    for p in products:
        if p.codes.used:
            p.status, p.note = "used", "renewed / refurbished / used listing"
        elif p.codes.accessory:
            p.status, p.note = "accessory", "accessory for " + (" ".join(p.codes.mentions[:3]) or "another product")
        elif candidates := [c for c in p.codes.own if c not in spec and c.lower() not in shops_of]:
            p.code = next((c for c in candidates if code_sites[c] - {p.site}), candidates[0])
        elif p.codes.own:
            p.note = "only spec words: " + " ".join(p.codes.own[:3])
        elif p.codes.mentions:
            p.note = "bundle with " + " ".join(p.codes.mentions[:3])

    # A short code two series share ("JBL Tune 660NC", "JBL Live 660NC") keeps the series word. A series
    # word is one brand's and comes before several of its codes (Tune, Live, LOQ); words many brands
    # put there ("Model", "Receiver") are not. A listing that doesn't say which series is left out.
    def series_word(p: _Product) -> str:
        w = p.codes.before.get(p.code, "")
        return "" if not w or (b := norm_brand(w)) and p.brand and same_brand(b, p.brand) else w
    word_codes, word_brands = defaultdict(set), defaultdict(Counter)
    for p in products:
        if p.code and _WEAK.match(p.code) and (w := series_word(p)):
            word_codes[w].add(p.code)
            word_brands[w][p.brand] += bool(p.brand)
    series_words = {w for w, codes in word_codes.items()
                    if len(codes) >= 2 and len(brand_groups(+word_brands[w])) == 1}
    series = defaultdict(set)
    for p in products:
        if p.code and (w := series_word(p)) in series_words:
            series[p.code].add(w)
    for p in products:
        if not p.code:
            continue
        code = p.code
        if len(words := series.get(code, set())) > 1:
            if series_word(p) not in words:
                p.note = f"{code} is used by several series ({', '.join(sorted(words))}); the title doesn't say which"
                continue
            code = series_word(p) + code
        p.model = f"{code} {p.codes.capacity}" if p.codes.capacity else code
        p.status = "model"

    # Brand and price checks within each model.
    members: dict[str, list[_Product]] = defaultdict(list)
    for p in products:
        if p.model:
            members[p.model].append(p)
    models = {}
    for key, ps in members.items():
        brands = brand_groups(Counter(p.brand for p in ps if p.brand))
        brand, status, note = None, "ok", ""
        if brands:
            brand, n = brands.most_common(1)[0]
            if len(brands) > 1 and n < DOMINANT * sum(brands.values()):
                status = "conflict"
                note = "brands disagree: " + ", ".join(f"{b} ({k})" for b, k in brands.most_common(4))
            for p in ps:
                if not p.brand:
                    p.brand, p.brand_from = brand, "model"
                elif status == "ok" and not same_brand(p.brand, brand):
                    p.status, p.note = "brand_conflict", f"brand {p.brand}, the model's is {brand}"
        priced = [p for p in ps if p.status == "model" and p.fresh and p.price]
        if len(priced) >= 3:
            median = statistics.median(p.price for p in priced)
            for p in priced:
                if not OUTLIER_LOW * median <= p.price <= OUTLIER_HIGH * median:
                    p.status, p.note = "price_outlier", f"{p.price / median:.1f}x the model's median price"
        kept = sorted(p.price for p in ps if p.status == "model" and p.fresh and p.price)
        if status == "ok" and len(kept) >= 2 and kept[-1] > OUTLIER_HIGH * kept[0]:
            status, note = "conflict", f"prices {kept[0] // 100:,}-{kept[-1] // 100:,} EGP can't be one product"
        listed = [p for p in ps if p.status == "model"]
        sites = sorted({p.site for p in listed})
        code, _, capacity = key.partition(" ")
        models[key] = (code, capacity or None, brand, status, len(listed), len(sites), ",".join(sites),
                       kept[0] if kept else None, int(statistics.median(kept)) if kept else None,
                       kept[-1] if kept else None, note or None)
    for c, why in spec.items():
        models.setdefault(c, (c, None, None, "spec", 0, 0, "", None, None, None, why))

    return Result(
        products={p.id: (p.model, p.status, p.brand, p.brand_from or None, " ".join(p.codes.own[:6]) or None,
                         p.note or None) for p in products},
        models=models)


@dataclass
class Summary:
    products: int
    in_model: int  # products in a model
    cross_shop: int  # ... whose model ('ok') is on 2+ shops
    models: int
    changed: int  # rows written or deleted
    seconds: float


def rebuild(conn: sqlite3.Connection, now: datetime) -> Summary:
    """Recompute every product's model and write only what changed (in short transactions)."""
    started = time.monotonic()
    # Plain tuples, streamed: this runs on the 1 GB droplet next to the coordinator.
    read = conn.cursor()
    read.row_factory = None
    result = build(read.execute("SELECT id, site, title, brand, seller, price, last_seen_at FROM products"), now,
                   conn.execute("SELECT DISTINCT brand, site FROM products WHERE brand IS NOT NULL").fetchall())
    # Only a hash of each stored row, to tell what changed.
    old_p = {r[0]: hash(tuple(r[1:])) for r in read.execute(
        "SELECT product_id, model, status, brand, brand_from, codes, note FROM product_models")}
    old_m = {r[0]: hash(tuple(r[1:])) for r in read.execute(
        "SELECT model, code, capacity, brand, status, products, sites, site_list, low, median, high, note FROM models")}
    now_s = iso(now)
    writes = [
        ("""INSERT OR REPLACE INTO product_models (product_id, model, status, brand, brand_from, codes, note,
                                                   version, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
         [(k, *v, VERSION, now_s) for k, v in result.products.items() if old_p.get(k) != hash(v)]),
        ("DELETE FROM product_models WHERE product_id = ?", [(k,) for k in old_p.keys() - result.products.keys()]),
        ("""INSERT OR REPLACE INTO models (model, code, capacity, brand, status, products, sites, site_list, low,
                                           median, high, note, version, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
         [(k, *v, VERSION, now_s) for k, v in result.models.items() if old_m.get(k) != hash(v)]),
        ("DELETE FROM models WHERE model = ?", [(k,) for k in old_m.keys() - result.models.keys()]),
    ]
    for sql, items in writes:
        for i in range(0, len(items), BATCH):
            with conn:
                conn.executemany(sql, items[i:i + BATCH])
    cross = {k for k, m in result.models.items() if m[3] == "ok" and m[5] >= 2}
    return Summary(
        products=len(result.products),
        in_model=sum(1 for v in result.products.values() if v[1] == "model"),
        cross_shop=sum(1 for v in result.products.values() if v[1] == "model" and v[0] in cross),
        models=sum(1 for m in result.models.values() if m[3] != "spec"),
        changed=sum(len(items) for _, items in writes),
        seconds=round(time.monotonic() - started, 2))


def rebuild_in_process(db_path: Path, timeout: float = 1800) -> Summary:
    """`rebuild` in a separate process at the lowest CPU priority, as the coordinator runs it:
    crawl uploads always get the CPU first (a full pass is ~16 s of CPU on the 1-CPU droplet), the
    ~140 MB it needs is returned when it exits, a crash in it can't touch the coordinator, and if
    the service's memory limit is reached the kernel kills it rather than the coordinator."""
    run = subprocess.run([sys.executable, "-m", "deals.matching", str(db_path)],
                         capture_output=True, text=True, timeout=timeout)
    if run.returncode:
        raise RuntimeError(f"matching exited with {run.returncode}: {run.stderr.strip()[-800:]}")
    return Summary(**json.loads(run.stdout.strip().splitlines()[-1]))


def same_model(conn: sqlite3.Connection, product_id: int) -> dict | None:
    """A product's model and the model's other listings (all shops; usable ones first, cheapest first)."""
    me = conn.execute("SELECT * FROM product_models WHERE product_id = ?", (product_id,)).fetchone()
    if me is None:
        return None
    if me["model"] is None:
        return {"me": dict(me), "model": None, "others": []}
    model = conn.execute("SELECT * FROM models WHERE model = ?", (me["model"],)).fetchone()
    others = conn.execute(
        """SELECT p.id, p.site, p.title, p.url, p.seller, p.price, p.in_stock, p.last_seen_at, pm.status, pm.note
           FROM product_models pm JOIN products p ON p.id = pm.product_id
           WHERE pm.model = ? AND pm.product_id != ?
           ORDER BY pm.status != 'model', p.in_stock DESC, p.price IS NULL, p.price""",
        (me["model"], product_id)).fetchall()
    return {"me": dict(me), "model": dict(model) if model else None, "others": [dict(o) for o in others]}


if __name__ == "__main__":  # python -m deals.matching <database>: one rebuild, summary as JSON
    os.nice(19)
    try:  # it shares the coordinator's memory limit (systemd MemoryMax): if memory runs out, kill this first
        Path("/proc/self/oom_score_adj").write_text("1000")
    except OSError:
        pass  # not Linux
    print(json.dumps(asdict(rebuild(db.connect(Path(sys.argv[1])), db.utcnow()))))
