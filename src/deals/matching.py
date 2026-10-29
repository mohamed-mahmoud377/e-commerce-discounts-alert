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
import math
import os
import re
import sqlite3
import statistics
import subprocess
import sys
import time
import unicodedata
import zlib
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from itertools import groupby
from pathlib import Path

from deals import db
from deals.db import iso
from deals.models import valid_gtin

VERSION = 6  # bump when the title rules change: every title is then read again at the next pass

MIN_CODE = 5  # shorter codes ("A16", "G502", "M5") are too often shared by unrelated products ...
_SHORT = re.compile(r"^(?:[A-Z]\d{3}|[A-Z]{2}\d{2}|[A-Z]\d{2}[A-Z]|\d[A-Z]\d{2}|[A-Z]\d[A-Z]\d)$")  # ... but these 4-char shapes are codes when brand-scoped: C75X, H390, Q20I
SPEC_BRANDS = 3  # a code seen under this many brands ...
DOMINANT = 0.7  # ... none of which has this share of its listings, is a spec word, not a model
OUTLIER_LOW, OUTLIER_HIGH = 0.5, 2.0  # a listing this far from the model's median price is left out
FRESH = timedelta(days=14)  # prices older than this don't count in a model's price range
BATCH = 1000  # rows per write transaction: the coordinator's page uploads wait at most this long

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−－"), "-")
_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)*")
# A code written with spaces, "CFB 9433 XFE", "MQ 5200", "Fp 3132 Bk": joined before tokenising. Not a
# unit after a number ("HP 1500 W", "2.25 HP 18000 BTU") and not a product line ("iPhone 16", "Note 14").
_SPACED = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,4})(?:\s?-\s?|\s)(\d{2,5}(?:-\d{2,4})?)"
                     r"(?:(\s?-\s?|\s)([A-Za-z]{1,3})(?![A-Za-z]))?(?![A-Za-z0-9.])")
# words that come before a number without being part of a code ("TV 32", "RAM 16", "HP 15-fd...")
_PREFIX_STOP = {"AC", "DC", "USB", "RAM", "ROM", "SSD", "HDD", "TV", "PS", "LED", "LCD", "HD", "UHD", "FHD", "QHD", "OLED",
                "QLED", "IPS", "VA", "TN", "HZ", "BTU", "RPM", "DPI", "HP", "EU", "UK", "US", "AND", "OR", "OF", "THE",
                "FOR", "TO", "BY", "PRO", "MAX", "AIR", "PAD", "TAB", "NOTE", "GEN", "SIM", "TYPE", "WIFI", "CAT", "WIN",
                "IOS", "PCS", "SET", "PACK", "BAR", "CUP", "PIN", "CORE", "ULTRA", "IPAD", "PLUS", "LITE", "MINI", "NEO",
                "XL", "XXL", "NO", "CODE", "MODEL", "ITEM", "YEAR", "ONE", "TWO", "ZOOM", "VOL", "VER", "REV", "SIZE",
                "AGE", "X", "IN", "ON", "AT", "UP", "M", "A", "G", "S", "V", "W", "L", "OS", "GPU", "CPU", "NEW", "BIG",
                "EGP", "LE", "TOP", "LOW", "HOT", "BOX", "KIT", "DUO", "ANC", "ENC", "ECO", "FAN", "GAS", "OIL", "LTE",
                "DELL", "ACER", "ASUS", "SONY", "MSI", "LG", "JBL", "ZTE", "OPPO", "VIVO", "IBM",  # a brand, then its number
                "RGB", "ARGB", "TWS", "BT", "IPX", "CL", "MS", "NM", "SN"}
# units after a number ("1500 W"): the tail is not part of the code
_UNIT_TAIL = {"W", "L", "ML", "KG", "G", "MM", "CM", "V", "A", "HZ", "K", "P", "M", "IN", "GB", "TB", "MAH", "BTU", "HP",
              "RPM", "PCS", "PC", "LTR", "LT", "MB", "KW", "DB", "FT", "MP", "X", "BAR", "CC", "OZ"}
_NUMBER_CODE = re.compile(r"(?<![\d.,/\w-])(\d{5,9}|\d{4,6}-\d{2,3})(?![\d.,/]|-\d)"
                          r"(?!\s*\(?\s*(?:w|watts?|v|volts?|mah|btu|rpm|l|ltr|liters?|litres?|ml|g|kg|mm|cm|hz|k|p|egp|le|"
                          r"pcs|pieces|x|bar|cc|sq|m2|lumens?)\b)", re.I)  # plain-number model codes: 40566, 9710-24
_GTIN = re.compile(r"(?<![\d.,])(\d{12,14})(?![\d.,])")
_NUMBERED = re.compile(r"\b(?:model|code|item|no|number|mpn|art|ref|sku)\.?\s*:?\s*#?\s*(\d{4,8})\b", re.I)  # "Code 20605"
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
    r"|(?:RTX|GTX|RX|GT|ARC|QUADRO|RADEON|IRIS|UHD)\d{2,5}[A-Z]{0,3}|MX\d{3}[A-Z]?"  # not MX5200 (a Tornado blender)
    r"|(?:LP)?G?DDR\d[A-Z]?\d*[A-Z]*"  # DDR5, DDR3L, GDDR6, LPDDR5X
    r"|PC\d{1,2}L?\d{4,5}[A-Z]?"  # PC425600
    r"|\d+RX\d+"  # 1RX16
    r"|\d{3,4}R"  # monitor curvature: 1500R
    r"|(?:[A-Z]{6,}|NOTE|BUDS|PADS|TUNE|LIVE|FLIP|FOLD|MATE|NOVA|SPARK|CAMON|RENO|NARZO|ENCO|OMEN|ENVY|YOGA|SLIM|"
    r"THIN|SWIFT|TUF|ROG|LOQ|MAG|PRO|MAX|PLUS|AIR|PAD|TAB|WATCH|BAND|GEN|SERIES|ZOOM|EDGE|HOT|SMART|POWER|PLAY|"
    r"ULTRA|MINI|NEO|LITE|CORE|VIVO|OPPO|PIXEL|IPHONE|IPAD|REDMI|POCO|GALAXY|PRESTIGE|VIVOBOOK|ZENBOOK|NITRO|"
    r"ASPIRE|LEGION|IDEAPAD|THINKPAD|VICTUS|STRIX|KATANA|CYBORG|LATITUDE|INSPIRON|VOSTRO|XPS|ELITEBOOK|PROBOOK|"
    r"OMNIBOOK|MATEBOOK|MAGICBOOK|AIRPODS|FREEBUDS|PLAYSTATION|SWITCH|XBOX)\d{1,2}[A-Z]?"  # product lines, not models: PRESTIGE13, NOTE14, BUDS3
    r"|\d{2}[IA][A-Z]{1,3}\d{1,2}"  # Lenovo generations, shared by many laptops: 15IRH10, 15ARP9, 16IAX10
    r"|[A-Z]{6,}\d+(?:MM|CM|HZ|MAH|ML|INCH)"  # a word glued to a spec: EARBUDS10MM (not SIFW24H)
    r"|(?:USB|HDMI|WIFI|BT|BLUETOOTH|IPX|IP|CAT|HDR|WIN|WINDOWS|ANDROID|IOS|PCIE|NVME|SATA|QI|DOLBY|DTS|LGA|AM|"
    r"ATX|EATX|MATX|ITX|PD|QC|"
    r"VER|VERSION|GEN|MODEL|TYPE|CLASS|GRADE|SIZE|NO|NUMBER|AOSP|WEBOS|TIZEN|VIDAA|EMUI|HYPEROS|MIUI|ONEUI|"
    r"COLOROS|FUNTOUCH|XOS|HIOS|MAGICOS)\d+[A-Z]?\d*"  # HDR10, WIN11, IPX4, PCIE4, LGA1700, AOSP12
    r"|R\d{2,3}A?"  # refrigerants: R32, R410A
    r"|CL\d{2}|[A-Z]\d|X360|2IN1"  # memory latency CL22; a letter and a digit; laptop lines
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
_SCREEN = re.compile(r"(?<![\d.])(\d{2,3})(?:\.\d)?\s*-?\s*(?:inch(?:es)?\b|in\b|\"|\u201d|'')", re.I)  # 55 Inch, 55", 55-inch; not 6.83"
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


def title_heads(title: str) -> list[tuple[int, str]]:
    """The title's first 1-4 words as brand candidates, longest first, with how many words each
    uses: 'Black + Decker Air Fryer' -> [(4, 'blackdeckerairfryer'), (3, 'blackdeckerair'),
    (2, 'blackdecker'), (1, 'black')]. The one that is a known brand is the title's brand; which
    brands are known changes as shops are added, so the candidates are kept, not the answer."""
    words = re.findall(r"[A-Za-z0-9&+'.]+", unicodedata.normalize("NFKD", title))[:4]
    return [(k, b) for k in range(len(words), 0, -1) if (b := norm_brand(" ".join(words[:k])))]


def guess_brand(title: str, known: set[str]) -> str | None:
    """The known brand a title starts with ('SHARP Split Air ...' -> 'sharp')."""
    return next((h for _, h in title_heads(title) if h in known), None)


def norm_code(token: str) -> str:
    """'WH-1000XM5' / 'wh1000xm5' / 'Wh-1000.XM5' -> 'WH1000XM5'."""
    return re.sub(r"[-.]", "", token).upper()


def is_code(code: str) -> bool:
    """Could this normalised word be a model code? (Its shape only: spec words that many brands
    use are found from the data.)"""
    return ((MIN_CODE <= len(code) <= 24 or _SHORT.match(code)) and not code.isdigit() and not code.isalpha()
            and not _SPEC.match(code))


def _join_spaced(t: str) -> str:
    """'Beko Hood CFB 9433 XFE' -> 'Beko Hood CFB9433XFE', 'ELIO 75-545' -> 'ELIO75-545', 'MM 5371' ->
    'MM5371' (a code the shop wrote with spaces). Only an upper-case word (or one of 1-2 letters)
    before the number, never a stop word ('TV 32', 'HP 15'), a year, or a 2-digit number after a
    one-letter or mixed-case word."""
    def join(m: re.Match) -> str:
        a, digits, sep, b = m.group(1), m.group(2), m.group(3) or "", m.group(4) or ""
        if a.upper() in _PREFIX_STOP or not (a.isupper() or len(a) <= 2):
            return m.group(0)
        if a.upper() in _UNIT_TAIL | {"GB", "TB", "MB", "CM", "MM", "KG", "HZ"} and re.search(r"\d\s*$", t[:m.start()]):
            return m.group(0)  # '12 GB 512 GB', '90 cm 19722': a unit after a number, then the next number
        if len(digits) == 4 and digits[:2] in ("19", "20") and not b:
            return m.group(0)  # a year
        if len(digits) == 2 and (len(a) < 2 or not a.isupper()):
            return m.group(0)  # 'Note 14', 'X 15'
        if not b or b.upper() in _UNIT_TAIL or not (b.isupper() or len(b) <= 2):
            return a + digits + sep + b  # 'WKM 4215 W': the code is WKM4215, the W stays
        return a + digits + b
    return _SPACED.sub(join, t)


def gtin(title: str) -> str:
    """A barcode (GTIN-12/13/14, checksum verified, not a store's own number) in the title, or ''."""
    return next((g for d in _GTIN.findall(title) if (g := valid_gtin(d))), "")


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


@lru_cache(maxsize=100_000)
def _token_code(token: str) -> str | None:
    """The code a title word holds, if any (cached: the same words fill thousands of titles)."""
    if not any(ch.isdigit() for ch in token):
        return None
    return next((c for c in map(norm_code, _pieces(token)) if is_code(c)), None)


# The product line: the words after the brand up to the first spec, minus filler and colours.
# "Apple iPhone 16 Plus (128 GB) - Pink with Face ID" -> ['iphone', '16', 'plus']
_FILLER = {"mobile", "smartphone", "phone", "phones", "cellphone", "tablet", "laptop", "notebook", "console", "gaming",
           "new", "the", "a", "an", "official", "warranty", "international", "version", "middle", "east", "global",
           "tax", "paid", "years", "year", "yr", "and", "by", "facetime", "ai", "face", "id", "edition", "model", "sim",
           "dual", "single", "esim", "nano", "only", "device", "unlocked", "sealed", "original", "genuine", "brand",
           "inch", "inches", "display", "screen", "wireless", "wired", "bluetooth", "headphones", "headphone", "earbuds",
           "earphones", "headset", "tws", "anc", "in", "ear", "open", "over", "on", "true", "stereo", "wi", "fi", "lte",
           "dual", "band", "cellular", "black", "white", "blue",
           "red", "green", "pink", "purple", "grey", "gray", "silver", "gold", "golden", "yellow", "orange", "brown",
           "beige", "cream", "ivory", "navy", "teal", "cyan", "violet", "lilac", "lavender", "mint", "olive", "graphite",
           "titanium", "midnight", "starlight", "ultramarine", "coral", "rose", "sand", "sky", "ocean", "forest",
           "charcoal", "platinum", "bronze", "copper", "space", "natural", "desert", "cosmic", "awesome", "phantom",
           "mystic", "lunar", "stellar", "shadow", "mist", "aqua", "sage", "lemon", "peach", "denim", "marble", "icyblue",
           "iceblue", "lightblue", "darkblue", "skyblue", "lightgreen", "darkgreen", "cypress", "jade", "ruby", "plum",
           "burgundy", "glacier", "obsidian", "pistachio", "blueberry", "mocha", "latte", "amber", "emerald", "sapphire",
           "onyx", "pearl", "slate", "steel", "crystal", "frost", "snow", "ice", "dawn", "dusk", "sunset", "sunrise",
           "colour", "color", "colors", "colours", "matte", "glossy", "transparent", "clear"}
_UNITS_AFTER = {"gb", "tb", "mb", "mah", "w", "watt", "watts", "hz", "mm", "cm", "m", "l", "liter", "liters", "litre",
                "litres", "ltr", "kg", "g", "in", "inch", "inches", "hp", "k", "fps", "ms", "nit", "nits", "btu", "cups",
                "cup", "pieces", "pcs", "burner", "burners", "feet", "ft", "speed", "speeds", "door", "doors", "slice",
                "slices", "place", "persons", "person", "programs", "in1", "x", "core", "cores", "th", "st", "nd", "rd",
                "gen", "generation", "years", "year", "v", "a", "amp", "bar", "rpm", "db", "kw", "mp", "horsepower", "ton",
                "tons", "tier", "tiers", "stage", "stages", "level", "levels", "mode", "modes", "function", "functions",
                "piece", "seater", "seats", "sets", "set", "pack", "packs", "channel", "channels", "zone", "zones",
                "drawer", "drawers", "shelf", "shelves", "bottle", "bottles", "kcal", "ml", "oz", "lb", "lbs", "qt", "horse",
                "horses", "hours", "hour", "h", "hr", "hrs", "minutes", "min", "days", "months", "degrees", "degree"}
_LINE_STOP = re.compile(r"^(?:\d+(?:\.\d+)?(?:gb|tb|mb|mah|w|hz|mm|cm|l|kg|g|in|inch|hp|k|fps|ms|nits?)\w*|\d{2,3}(?:\.\d)?\"|"
                        r"[45]g|lte|wifi|wi-fi|cellular|ram|rom|ssd|hdd|storage|memory|core|cpu|gpu|intel|amd|ryzen|"
                        r"snapdragon|dimensity|helio|exynos|tensor|mediatek|unisoc|camera|battery|charger|case|cover|"
                        r"price|offer|deal|free|gift|bundle|with|for|incl|includes|including|plus\+|\+)$", re.I)
_RAM = re.compile(r"(\d+)\s*GB\s*(?:RAM|Ram|ram|memory|DDR\d\w*)", re.I)
_NET = re.compile(r"\b([45])G\b", re.I)
_CPU = re.compile(r"\b(?:i[3579][\s-]?\d{4,5}[A-Z]{0,3}|ultra\s?[5-9]\s?-?\d{3}[A-Z]{0,2}|ryzen\s?(?:ai\s?)?[3579]\s?-?\d{3,4}[A-Z]{0,3}|"
                  r"m[1-5](?:\s?(?:pro|max|ultra))?\b(?=.*(?:mac|ipad|imac))|snapdragon\s?\w+|dimensity\s?\d+\w*|helio\s?\w+)", re.I)
_BUNDLE_WORD = re.compile(r"\bbundle\b|\+\s*(?!(?:e?sim|5g|4g|lte|facetime|warranty|tax|decker|esim)\b)[A-Za-z]{3,}"
                          r"|\bwith\b[^,(|]{0,25}\b(?:games?|controllers?|headsets?|gift|keyboard|stylus|pen)\b"
                          r"|\b[2-9]\s+controllers\b|\bextra\s+\w*\s*controller", re.I)
_GPU = re.compile(r"\b(?:RTX|GTX|RX|ARC|MX)\s?-?\s?(\d{3,4})\s?(TI|XT|S)?\b", re.I)


def read_line(title: str) -> tuple[list[tuple[int, str]], dict[str, str]]:
    """The product line words (with their word index, so the brand's words can be dropped later) and
    the variant attributes: 'Samsung Galaxy A16, 128GB, 4GB RAM, 4G - Grey' ->
    ([(0,'samsung'), (1,'galaxy'), (2,'a16')], {'storage': '128GB', 'ram': '4GB', 'net': '4G'})."""
    t = title.translate(_DASHES)
    head = re.split(r"\s[-|/]\s|[,()\[\]|/:;]|\s[-–]\s*|\s{2,}", t, maxsplit=1)[0]  # up to the first separator
    words, line, named = re.findall(r"[A-Za-z0-9+]+(?:\.\d+[A-Za-z]*)?", head), [], False  # '2.5L' stays one word
    for i, w in enumerate(words):
        lw, nxt = w.lower(), words[i + 1].lower() if i + 1 < len(words) else ""
        num = re.fullmatch(r"\d+(?:\.\d+)?", lw) is not None
        if _LINE_STOP.match(lw) or is_code(norm_code(w)) and len(w) >= 5 or len(line) >= 5 or lw in _UNITS_AFTER:
            break
        if num and (nxt in _UNITS_AFTER or nxt.isdigit() or len(lw) >= 4 and "." not in lw):
            break  # '6 liter', '1.5 hp' (split as 1, 5), a year
        if lw in _FILLER:
            continue
        # the line is a name when a word mixes letters and digits ('a16', 'x9d', 's11') or a short
        # number follows a word ('iphone 16', 'momentum 4'); digits from specs don't count
        if num and line or any(c.isdigit() for c in lw) and any(c.isalpha() for c in lw):
            named = True
        line.append((i, lw.replace("+", "plus")))
    if not named:
        line = []
    attrs = {}
    caps = [(float(n), u.upper()) for n, u in _CAPACITY.findall(t)]
    gbs = sorted({int(n * (1024 if u == "TB" else 1)) for n, u in caps})
    ram = _RAM.search(t)
    if ram:
        attrs["ram"] = f"{ram.group(1)}GB"
        gbs = [g for g in gbs if g != int(ram.group(1))]
    if gbs and gbs[-1] >= 32:
        attrs["storage"] = f"{gbs[-1] // 1024}TB" if gbs[-1] >= 1024 else f"{gbs[-1]}GB"
        if "ram" not in attrs and len(gbs) > 1 and gbs[0] <= 64:
            attrs["ram"] = f"{gbs[0]}GB"
    if net := _NET.search(t):
        attrs["net"] = net.group(1) + "G"
    if cpu := _CPU.search(t):
        attrs["cpu"] = re.sub(r"[\s-]", "", cpu.group(0)).upper()
    if gpu := _GPU.search(t):
        attrs["gpu"] = re.sub(r"[\s-]", "", gpu.group(0)).upper()
    if _BUNDLE_WORD.search(t):
        attrs["bundle"] = "1"
    return line, attrs


# The spec signature: for products with neither a code nor a named line ("Fresh Air Fryer 5.5 L
# 1800 W"). The product type (most specific first), every number with its unit, and the features
# that make two otherwise equal products different (inverter, cooling only, no frost, built-in...).
_TYPES = [(c, re.compile(p, re.I)) for c, p in [
    ("chest freezer", r"chest (?:deep )?freezer|horizontal (?:deep )?freezer"),
    ("upright freezer", r"upright (?:deep )?freezer|vertical (?:deep )?freezer"),
    ("deep freezer", r"freezer"),
    ("mini bar", r"mini ?bar|mini fridge|compact refrigerator"),
    ("refrigerator", r"refrigerator|fridge"),
    ("air conditioner", r"air ?conditioner|split (?:ac|air)|\bac\b"),
    ("hob", r"\bhob\b|cooktop|cook top"),
    ("built-in oven", r"built[- ]?in\b.{0,20}\boven"),
    ("rice cooker", r"rice cooker"),
    ("cooker", r"\bcooker\b|\bstove\b|gas range"),
    ("hood", r"\bhood\b|chimney"),
    ("dishwasher", r"dish ?washer"),
    ("washing machine", r"washing machine|washer dryer"),
    ("microwave", r"micro ?wave"),
    ("air fryer oven", r"air fryer oven|oven air fryer"),
    ("air fryer", r"air ?fryer|oil[- ]?free fryer|\bfryer\b"),
    ("electric oven", r"electric oven|toaster oven|\boven\b"),
    ("stand mixer", r"stand mixer|kitchen machine|dough mixer"),
    ("hand mixer", r"hand mixer|egg beater"),
    ("hand blender", r"hand blender|stick blender"),
    ("food processor", r"food processor"),
    ("chopper", r"chopper|meat grinder|mincer"),
    ("blender", r"blender"),
    ("juicer", r"juicer|juice extractor|citrus press"),
    ("kettle", r"kettle"),
    ("toaster", r"toaster"),
    ("sandwich maker", r"sandwich|panini|waffle"),
    ("grill", r"\bgrill\b|griddle"),
    ("coffee machine", r"espresso|coffee (?:machine|maker)|cappuccino|barista"),
    ("coffee grinder", r"grinder|coffee mill"),
    ("hot plate", r"hot ?plate|cooking plate"),
    ("garment steamer", r"steamer"),
    ("iron", r"\biron\b"),
    ("water dispenser", r"water dispenser|water cooler"),
    ("vacuum", r"vacuum"),
    ("heater", r"heater"),
    ("fan", r"\bfan\b"),
]]
_NUM = r"(\d+(?:[.,]\d+)?)"
_SPECS = [(re.compile(_NUM + rx, re.I), fmt) for rx, fmt in [
    (r"\s*(?:hp|h\.p|horse ?power|horse)\b", "{:g}HP"),
    (r"\s*k?\s*btu", "BTU"),
    (r"\s*(?:l|liter|liters|litre|litres|ltr|lt)\b", "{:g}L"),
    (r"\s*(?:w|watt|watts)\b", "{:.0f}W"),
    (r"\s*(?:cm|centimeters?)\b", "{:.0f}CM"),
    (r"\s*(?:burners?)\b", "{:.0f}BURNERS"),
    (r"\s*speeds?\b", "{:.0f}SPEEDS"),
    (r"\s*cups?\b", "{:.0f}CUPS"),
    (r"\s*(?:feet|ft|foot)\b", "{:g}FT"),
    (r"\s*kg\b", "{:g}KG"),
    (r"\s*(?:place settings?|persons?)\b", "{:.0f}PLACES"),
    (r"\s*drawers?\b", "{:.0f}DRAWERS"),
    (r"\s*doors?\b", "{:.0f}DOORS"),
    (r"\s*(?:bars?)\b", "{:.0f}BAR"),
]]
_FLAGS = [(n, re.compile(rx, re.I)) for n, rx in [
    ("inverter", r"inverter"),
    ("heat", r"\bheat|hot\s*(?:and|&|/)\s*cold|cool\s*(?:and|&|/|-)\s*h"),
    ("coolonly", r"cool(?:ing)? only|cold only"),
    ("nofrost", r"no[- ]?frost"),
    ("defrost", r"\bdefrost"),
    ("digital", r"digital"),
    ("builtin", r"built[- ]?in"),
    ("glass", r"\bglass\b"),
    ("fan", r"\bwith fan\b|\bfan\b"),
    ("grill", r"\bgrill\b"),
    ("steam", r"\bsteam\b"),
    ("smart", r"\bsmart\b|wi-?fi"),
]]
_SIM_STOP = set("the a an and with for of in on to by from new original warranty year years official egypt version middle "
                "east international black white silver grey gray blue red green pink gold golden beige cream stainless "
                "steel inox titanium navy purple brown orange yellow".split())


def read_signature(title: str) -> tuple[str, list[str], list[str], list[str]]:
    """(type, specs, flags, words) of a title: 'Fresh Air Fryer without oil, 5.5 litres, 1800 watt'
    -> ('air fryer', ['1800W', '5.5L'], [], ['fresh', 'air', 'fryer', 'without', 'oil', ...])."""
    t = title.translate(_DASHES)
    ptype = next((c for c, rx in _TYPES if rx.search(t)), "")
    specs = set()
    for rx, fmt in _SPECS:
        for m in rx.finditer(t):
            n = float(m.group(1).replace(",", "."))
            if fmt == "BTU":
                specs.add(f"{n * 1000 if n < 100 else n:.0f}BTU")
            else:
                specs.add(fmt.format(n))
    flags = {n for n, rx in _FLAGS if rx.search(t)}
    if "coolonly" in flags:
        flags.discard("heat")
    words = [w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in _SIM_STOP and len(w) > 1]
    return ptype, sorted(specs), sorted(flags), words


@dataclass(slots=True)
class TitleCodes:
    own: list[str]  # model codes of this product, best first
    mentions: list[str]  # codes of other products: the one an accessory fits, a bundled monitor
    accessory: bool  # the title is about an accessory for another product
    used: bool
    capacity: str  # '16GB+512GB' when the title states capacities, '55IN' a TV size the code lacks ('' otherwise)
    before: dict[str, str] = field(default_factory=dict)  # short code -> the word just before it ('TUNE')


@lru_cache(maxsize=20_000)
def read_title(title: str) -> TitleCodes:
    """Model codes in a title (needs no brand or database). Cached (noon lists one item under many
    sellers with the same title), so the result is shared: don't change it."""
    t = _join_spaced(title.translate(_DASHES))
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
    for m in _NUMBERED.finditer(t):  # "Model 20605": a plain number is a code when the title says so
        if m.start() < cut and m.group(1) not in own:
            own.append(m.group(1))
    # plain-number codes (Egyptian brands: "Touch Elzenouki 40566", "Hans 9710-24"): weaker than any
    # lettered code, so last, and only ever used with the listing's brand (see the assignment)
    for m in _NUMBER_CODE.finditer(t):
        code = m.group(1).replace("-", "")
        if re.search(r"(?:\bi[3579]|core|ryzen|ultra|celeron|pentium|athlon|gen|intel|amd)\W{0,4}$", t[:m.start()], re.I):
            continue  # "Core i3 - 12100": a CPU number
        if m.start() < cut and not any(a <= m.start() < b for a, b in clauses) and code not in own \
                and not (len(code) == 8 and code[:2] in ("19", "20")) and not gtin(m.group(1)):
            own.append(code)
    if barcode := gtin(t[:cut]):  # a barcode names exactly one product: the last resort when nothing else does
        own.append("GTIN" + barcode)
    caps = sorted({f"{float(n):g}{u.upper()}" for n, u in _CAPACITY.findall(t)},
                  key=lambda c: (c[-2:], float(c[:-2])))
    # A TV series code ("Samsung U8000H") covers every screen size, so the size is part of the model;
    # a full code ("UA43U8000H") already names it (dropped at assignment when the chosen code has it).
    if own and (size := _SCREEN.search(t)) and int(size.group(1)) >= 32:
        caps.append(f"{size.group(1)}IN")
    return TitleCodes(own, mentions, accessory and not own, bool(_USED.search(t)), "+".join(caps), before)


# --- the rebuild -------------------------------------------------------------------------------
#
# Built to stay small on the 1 GB droplet however many products there are: codes are read from a
# title once (product_codes, incremental), every count over the whole catalogue is a SQL GROUP BY,
# the per-product and per-model decisions stream through temporary tables in product or model order,
# and the result is diffed against the stored tables in SQL. Python holds only the small global
# lookups (spec words, shared codes, series words, which brand owns a code).

CHUNK = 2000  # rows per write transaction: the coordinator's uploads wait at most this long


def _hash(title: str, brand: str | None, seller: str | None) -> int:
    return zlib.crc32(f"{title}\0{brand or ''}\0{seller or ''}".encode())


def _chunked(conn: sqlite3.Connection, sql: str, rows) -> int:
    """executemany in short transactions; returns the row count."""
    n, batch = 0, []
    for row in rows:
        batch.append(row)
        if len(batch) >= CHUNK:
            with conn:
                conn.executemany(sql, batch)
            n, batch = n + len(batch), []
    if batch:
        with conn:
            conn.executemany(sql, batch)
    return n + len(batch)


def extract(conn: sqlite3.Connection) -> int:
    """Read the codes of every product whose title, brand or seller changed (or that is new, or
    was read with older rules) into product_codes / product_code_index. Returns how many."""
    conn.create_function("title_hash", 3, _hash, deterministic=True)
    todo = [r[0] for r in conn.execute(  # ids only: 300k titles would be 100 MB
        """SELECT p.id FROM products p LEFT JOIN product_codes pc ON pc.product_id = p.id
           WHERE pc.product_id IS NULL OR pc.version != ?
              OR pc.title_hash != title_hash(p.title, p.brand, p.seller)""", (VERSION,))]
    n = 0
    for i in range(0, len(todo), CHUNK):
        ids = todo[i:i + CHUNK]
        codes, index = [], []
        for pid, title, brand, seller in conn.execute(
                f"SELECT id, title, brand, seller FROM products WHERE id IN ({','.join('?' * len(ids))})", ids):
            t = read_title(title)
            shop_brand = norm_brand(brand)
            if shop_brand and shop_brand == norm_brand(seller):
                shop_brand = None  # the seller's own name, not the maker's
            line, attrs = read_line(title)
            ptype, specs, flags, words = read_signature(title)
            codes.append((pid, _hash(title, brand, seller), VERSION, "|".join(f"{k}:{b}" for k, b in title_heads(title)),
                          shop_brand, " ".join(t.own), " ".join(t.mentions),
                          " ".join(f"{c}={w}" for c, w in t.before.items()), t.capacity, int(t.used), int(t.accessory),
                          " ".join(f"{i}:{w}" for i, w in line), " ".join(f"{k}={v}" for k, v in sorted(attrs.items())),
                          ptype, " ".join(specs), " ".join(flags), " ".join(words)))
            index += [(pid, c, rank) for rank, c in enumerate(t.own)]
        with conn:
            conn.executemany("DELETE FROM product_code_index WHERE product_id = ?", [(c[0],) for c in codes])
            conn.executemany(
                """INSERT OR REPLACE INTO product_codes (product_id, title_hash, version, heads, shop_brand, own, mentions,
                       before, capacity, used, accessory, line, attrs, ptype, specs, flags, toks)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", codes)
            conn.executemany("INSERT OR REPLACE INTO product_code_index VALUES (?, ?, ?)", index)
        n += len(codes)
    return n


def _fold(brands: Counter) -> dict[str, str]:
    """Each brand spelling -> the group's canonical (shortest) spelling."""
    groups = brand_groups(brands)
    return {b: next(g for g in groups if same_brand(g, b)) for b in brands}


def _group(conn: sqlite3.Connection, now: datetime) -> None:
    """From product_codes to the temp tables new_pm / new_m (the next product_models / models)."""
    fresh_after = iso(now - FRESH)
    shops_of = defaultdict(set)
    for brand, site in conn.execute("SELECT DISTINCT brand, site FROM products WHERE brand IS NOT NULL"):
        if b := norm_brand(brand):
            shops_of[b].add(site)
    known = {b for b, sites in shops_of.items() if len(sites) >= 2}  # brands 2+ shops use
    conn.executescript("""
        CREATE TEMP TABLE pb (product_id INTEGER PRIMARY KEY, brand TEXT, brand_from TEXT, words INTEGER);
        CREATE TEMP TABLE lines (product_id INTEGER PRIMARY KEY, brand TEXT NOT NULL, line TEXT NOT NULL, attrs TEXT NOT NULL,
                                 site TEXT, price INTEGER, fresh INTEGER, codes TEXT);
        CREATE INDEX lines_block ON lines (brand, line);
        CREATE TEMP TABLE pick (product_id INTEGER PRIMARY KEY, code TEXT, word TEXT NOT NULL, status TEXT, note TEXT);
        CREATE TEMP TABLE assign (product_id INTEGER PRIMARY KEY, model TEXT, status TEXT NOT NULL, brand TEXT,
                                  brand_from TEXT, codes TEXT, note TEXT, site TEXT, price INTEGER, fresh INTEGER,
                                  kind TEXT NOT NULL DEFAULT 'mpn');
        CREATE INDEX assign_model ON assign (model);
        CREATE TEMP TABLE new_pm (product_id INTEGER PRIMARY KEY, model TEXT, status TEXT NOT NULL, brand TEXT,
                                  brand_from TEXT, codes TEXT, note TEXT);
        CREATE TEMP TABLE new_m (model TEXT PRIMARY KEY, kind TEXT, code TEXT, capacity TEXT, brand TEXT, status TEXT,
                                 products INTEGER, sites INTEGER, site_list TEXT, low INTEGER, median INTEGER,
                                 high INTEGER, note TEXT);
    """)

    # 1. Each product's brand: a known brand the title starts with beats the shop's brand field,
    #    which sellers sometimes fill with their own name ("Lap-Tech" on a Dell monitor).
    def brands():
        for pid, heads, shop_brand in conn.execute("SELECT product_id, heads, shop_brand FROM product_codes"):
            found = next(((int(k), b) for k, b in (h.split(":", 1) for h in heads.split("|") if h) if b in known), None)
            if found:
                yield pid, found[1], "title", found[0]
            else:
                yield pid, shop_brand, "shop" if shop_brand else None, 0
    _chunked(conn, "INSERT INTO pb VALUES (?, ?, ?, ?)", brands())

    # Codes: the title's (product_code_index) and those the product's own page gave (product_details),
    # which rank after the title's.
    conn.executescript("""
        CREATE TEMP TABLE idx AS SELECT product_id, code, rank FROM product_code_index;
        CREATE INDEX idx_code ON idx (code);""")
    page_codes = {}
    for pid, codes in conn.execute("SELECT product_id, codes FROM product_details WHERE status = 'ok' AND codes != ''"):
        page_codes[pid] = codes.split()
    _chunked(conn, "INSERT INTO idx VALUES (?, ?, ?)",
             ((pid, c, 100 + n) for pid, cs in page_codes.items() for n, c in enumerate(cs)))

    # 2. Spec words, learned: a code that many brands use is not a model ("1355U", "DCIP3").
    spec = {}
    pairs = conn.execute("""SELECT i.code, pb.brand, COUNT(*) FROM idx i JOIN pb USING (product_id)
                            WHERE pb.brand IS NOT NULL GROUP BY i.code, pb.brand ORDER BY i.code""")
    for code, rows in groupby(pairs, key=lambda r: r[0]):
        counts = Counter({b: n for _, b, n in rows})
        if len(counts) >= SPEC_BRANDS:
            groups = brand_groups(counts)
            if len(groups) >= SPEC_BRANDS and groups.most_common(1)[0][1] < DOMINANT * sum(groups.values()):
                spec[code] = f"used by {len(groups)} brands: " + ", ".join(b for b, _ in groups.most_common(4))
    shared = {r[0] for r in conn.execute(
        """SELECT i.code FROM idx i JOIN products p ON p.id = i.product_id
           GROUP BY i.code HAVING COUNT(DISTINCT p.site) >= 2""")}

    # 3. Each product's code: its best one that is not a spec word or a brand's name ("V2COM" is the
    #    brand V2Com), preferring a code another shop also has (title order alone can't tell a
    #    laptop's code from the mouse bundled with it); and the word before a short code.
    def picks():
        for pid, own, mentions, before, used, accessory, brand in conn.execute(
                """SELECT pc.product_id, pc.own, pc.mentions, pc.before, pc.used, pc.accessory, pb.brand
                   FROM product_codes pc JOIN pb USING (product_id)"""):
            own, mentions = own.split(), mentions.split()
            own += [c for c in page_codes.get(pid, ()) if c not in own]  # the page's model numbers, after the title's
            if used:
                yield pid, None, "", "used", "renewed / refurbished / used listing"
            elif accessory:
                yield pid, None, "", "accessory", "accessory for " + (" ".join(mentions[:3]) or "another product")
            elif candidates := [c for c in own if c not in spec and c.lower() not in shops_of]:
                code = next((c for c in candidates if c in shared), candidates[0])
                word = dict(kv.split("=", 1) for kv in before.split()).get(code, "") if _WEAK.match(code) else ""
                if word and (b := norm_brand(word)) and brand and same_brand(b, brand):
                    word = ""  # "JBL 520BT": the brand, not a series
                yield pid, code, word, None, None
            elif own:
                yield pid, None, "", "no_code", "only spec words: " + " ".join(own[:3])
            elif mentions:
                yield pid, None, "", "no_code", "bundle with " + " ".join(mentions[:3])
            else:
                yield pid, None, "", "no_code", None
    _chunked(conn, "INSERT INTO pick VALUES (?, ?, ?, ?, ?)", picks())

    # 4. Series: a short code two series share ("JBL Tune 660NC", "JBL Live 660NC") keeps the series
    #    word. A series word is one brand's and comes before several of its codes (Tune, Live, LOQ);
    #    words many brands put there ("Model", "Receiver") are not.
    word_codes, word_brands = defaultdict(set), defaultdict(Counter)
    for code, word, brand, n in conn.execute(
            """SELECT pick.code, pick.word, pb.brand, COUNT(*) FROM pick JOIN pb USING (product_id)
               WHERE pick.word != '' GROUP BY pick.code, pick.word, pb.brand"""):
        word_codes[word].add(code)
        if brand:
            word_brands[word][brand] += n
    series_words = {w for w, codes in word_codes.items() if len(codes) >= 2 and len(brand_groups(word_brands[w])) == 1}
    series = defaultdict(set)
    for word in series_words:
        for code in word_codes[word]:
            series[code].add(word)

    # 5. Brand-scoped keys (brand + model code, the identifier Google Shopping asks for when there is
    #    no barcode): the same code from two makers stays two models. Spellings of one brand on a code
    #    fold together; a listing with no brand joins the one brand that has its code, or is left out
    #    as ambiguous when several do.
    canon: dict[str, dict[str, str]] = {}  # code -> spelling -> canonical, for codes with 2+ spellings
    owner: dict[str, str | None] = {}  # code -> the one brand that has it (None: several), for unbranded listings
    rows = conn.execute("""SELECT pick.code, pb.brand, COUNT(*) FROM pick JOIN pb USING (product_id)
                           WHERE pick.code IS NOT NULL AND pb.brand IS NOT NULL
                           GROUP BY pick.code, pb.brand ORDER BY pick.code""")
    unbranded = {r[0] for r in conn.execute(
        "SELECT DISTINCT pick.code FROM pick JOIN pb USING (product_id) WHERE pick.code IS NOT NULL AND pb.brand IS NULL")}
    for code, group in groupby(rows, key=lambda r: r[0]):
        counts = Counter({b: n for _, b, n in group})
        folded = _fold(counts) if len(counts) > 1 else {}
        if folded:
            canon[code] = folded
        if code in unbranded:
            owners = set(folded.values()) if folded else set(counts)
            owner[code] = next(iter(owners)) if len(owners) == 1 else None

    def assignments():
        for pid, code, word, status, note, brand, brand_from, capacity, codes, site, price, fresh in conn.execute(
                """SELECT pick.product_id, pick.code, pick.word, pick.status, pick.note, pb.brand, pb.brand_from,
                          pc.capacity, pc.own, p.site, p.price, p.last_seen_at >= ?
                   FROM pick JOIN pb USING (product_id) JOIN product_codes pc USING (product_id)
                   JOIN products p ON p.id = pick.product_id""", (fresh_after,)):
            codes = codes or None
            if status:
                yield pid, None, status, brand, brand_from, codes, note, site, price, fresh
                continue
            if len(words := series.get(code, ())) > 1:
                if word not in words:
                    yield (pid, None, "no_code", brand, brand_from, codes,
                           f"{code} is used by several series ({', '.join(sorted(words))}); the title doesn't say which",
                           site, price, fresh)
                    continue
                code = word + code
            if capacity.endswith("IN") and capacity.rsplit("+", 1)[-1][:-2] in code:
                capacity = capacity.rsplit("+", 1)[0] if "+" in capacity else ""  # 'UA43U8000H' holds the 43
            if brand:
                brand = canon.get(code, {}).get(brand, brand)
            elif len(code) == 4 or code.isdigit():
                yield (pid, None, "no_code", None, None, codes,
                       f"{code} is too {'plain' if code.isdigit() else 'short'} a code to trust without a brand",
                       site, price, fresh)
                continue
            elif code in owner:
                if owner[code] is None:
                    yield (pid, None, "ambiguous_brand", None, None, codes,
                           f"{code} is sold by several brands and the title names none", site, price, fresh)
                    continue
                brand, brand_from = owner[code], "model"
            model = f"{brand or '-'} {code}" + (f" {capacity}" if capacity else "")
            yield pid, model, "model", brand, brand_from, codes, None, site, price, fresh
    _chunked(conn, "INSERT INTO assign VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
             (r + ("gtin" if r[1] and " GTIN" in r[1] else "mpn",) for r in assignments()))
    _lines(conn, fresh_after)
    _similar(conn)
    _barcodes(conn)
    _overrides(conn)

    # 7. Price checks per model (its listings come in model order): a listing far from the model's
    #    median price is left out (a 100,000 EGP placeholder, a case at 300 EGP); a model whose
    #    prices can't be one product is 'conflict'.
    def finish():
        rows = conn.execute("""SELECT product_id, model, status, brand, brand_from, codes, note, site, price, fresh, kind
                               FROM assign ORDER BY model, product_id""")
        for model, group in groupby(rows, key=lambda r: r[1]):
            members = [list(r) for r in group]
            if model is None:
                for m in members:
                    yield "pm", tuple(m[:7])
                continue
            priced = [m for m in members if m[9] and m[8]]
            if len(priced) >= 3:
                median = statistics.median(m[8] for m in priced)
                for m in priced:
                    if not OUTLIER_LOW * median <= m[8] <= OUTLIER_HIGH * median:
                        m[2], m[6] = "price_outlier", f"{m[8] / median:.1f}x the model's median price"
            kept = sorted(m[8] for m in members if m[2] == "model" and m[9] and m[8])
            status, note = "probable" if members[0][10] == "similar" else "ok", None
            if len(kept) >= 2 and kept[-1] > OUTLIER_HIGH * kept[0]:
                status, note = "conflict", f"prices {kept[0] // 100:,}-{kept[-1] // 100:,} EGP can't be one product"
            listed = [m for m in members if m[2] == "model"]
            sites = sorted({m[7] for m in listed})
            brand, code, capacity = (model.split(" ", 2) + [None])[:3]
            yield "m", (model, members[0][10], code, capacity, None if brand == "-" else brand, status, len(listed), len(sites),
                        ",".join(sites), kept[0] if kept else None, int(statistics.median(kept)) if kept else None,
                        kept[-1] if kept else None, note)
            for m in members:
                yield "pm", tuple(m[:7])
    pm_rows, m_rows = [], []
    for kind, row in finish():
        (pm_rows if kind == "pm" else m_rows).append(row)
        if len(pm_rows) >= CHUNK:
            _chunked(conn, "INSERT INTO new_pm VALUES (?, ?, ?, ?, ?, ?, ?)", pm_rows)
            pm_rows = []
        if len(m_rows) >= CHUNK:
            _chunked(conn, "INSERT INTO new_m VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", m_rows)
            m_rows = []
    _chunked(conn, "INSERT INTO new_pm VALUES (?, ?, ?, ?, ?, ?, ?)", pm_rows)
    _chunked(conn, "INSERT INTO new_m VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
             m_rows + [(c, "mpn", c, None, None, "spec", 0, 0, "", None, None, None, why) for c, why in spec.items()])


_SUB_BRANDS = {"xiaomi": ("xiaomi", "redmi", "poco", "mi"), "huawei": ("huawei",), "samsung": ("samsung",),
               "apple": ("apple",), "sony": ("sony",), "anker": ("anker", "soundcore"),
               "oppo": ("oppo",), "realme": ("realme",), "vivo": ("vivo", "iqoo"), "lenovo": ("lenovo", "motorola", "moto")}


def _lines(conn: sqlite3.Connection, fresh_after: str) -> None:
    """6. The second identity, for listings with no usable code: brand + product line + variant
    attributes ("apple iphone16 128GB", "samsung galaxytabs11 128GB 12GB 5G"). The line must have a
    digit. Listings of one (brand, line) are one block: a listing that omits an attribute joins the
    one fuller variant consistent with it, or is left out as ambiguous when several are."""
    def rows():
        for pid, brand, words, line, attrs, codes, site, price, fresh in conn.execute(
                """SELECT pc.product_id, pb.brand, pb.words, pc.line, pc.attrs, pc.own, p.site, p.price, p.last_seen_at >= ?
                   FROM assign a JOIN product_codes pc USING (product_id) JOIN pb USING (product_id)
                   JOIN products p ON p.id = a.product_id
                   WHERE a.status = 'no_code' AND pb.brand IS NOT NULL AND pc.line != ''""", (fresh_after,)):
            toks = [(int(i), w) for i, w in (x.split(":", 1) for x in line.split())]
            toks = [w for i, w in toks if i >= words]  # drop the brand's own words
            subs = _SUB_BRANDS.get(brand, (brand,))
            while toks and (toks[0] in subs or norm_brand(toks[0]) and brand.startswith(norm_brand(toks[0]))):
                toks = toks[1:]
            key = "".join(toks)
            if not any(ch.isdigit() for ch in key) or len(key) < 3:
                continue
            a = dict(kv.split("=", 1) for kv in attrs.split())
            if brand == "apple":
                a.pop("ram", None)  # Apple never states it; shops guess it wrong
            if a.pop("bundle", None):
                bundles.append((pid, None, "bundle", brand, None, codes or None,
                                "a bundle (console + game, phone + earbuds): its price is not the product's", site, price, fresh))
                continue
            yield pid, brand, key, " ".join(f"{k}={v}" for k, v in sorted(a.items())), site, price, fresh, codes or None
    bundles: list[tuple] = []
    _chunked(conn, "INSERT INTO lines VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows())
    _chunked(conn, "INSERT OR REPLACE INTO assign VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'line')", bundles)

    def compatible(a: dict, b: dict) -> bool:
        return all(b.get(k, v) == v for k, v in a.items())

    def assignments():
        block_rows = conn.execute("SELECT * FROM lines ORDER BY brand, line, product_id")
        for (brand, line), group in groupby(block_rows, key=lambda r: (r[1], r[2])):
            members = [(r[0], dict(kv.split("=", 1) for kv in r[3].split()), r[4], r[5], r[6], r[7]) for r in group]
            keys = {tuple(sorted(a.items())) for _, a, *_ in members}
            # the fullest variants: keys no other compatible key adds to
            maximal = sorted(k for k in keys if not any(len(o) > len(k) and compatible(dict(k), dict(o)) for o in keys))
            for pid, a, site, price, fresh, codes in members:
                fits = [k for k in maximal if compatible(a, dict(k))]
                if len(fits) > 1:
                    yield (pid, None, "ambiguous_variant", brand, None, codes,
                           f"{line}: several variants fit ({'; '.join(' '.join(v for _, v in k) or 'base' for k in fits[:3])})",
                           site, price, fresh)
                    continue
                full = dict(fits[0]) if fits else a
                model = f"{brand} {line}" + "".join(f" {full[k]}" for k in ("storage", "ram", "net", "cpu", "gpu") if k in full)
                yield pid, model, "model", brand, None, codes, None, site, price, fresh
    _chunked(conn, "INSERT OR REPLACE INTO assign VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'line')", assignments())


SIM_TYPES_EXCLUDED = {""}  # every listing needs a product type
SIM_MIN_SPECS = 2  # "1800W 5.5L": one number alone ("1500W") names too many products
SIM_SAME_SHOP = 0.6  # one shop's listings of a signature must be this alike (else the shop sells several such products)
SIM_CROSS_SHOP = 0.35  # listings on different shops must be at least this alike ...
SIM_PRICE = 1.4  # ... and priced within this factor


def _cosine(a: list[str], b: list[str], idf: dict[str, float]) -> float:
    va, vb = Counter(a), Counter(b)
    dot = sum(va[w] * vb[w] * idf.get(w, 1.0) ** 2 for w in va.keys() & vb.keys())
    na = math.sqrt(sum((n * idf.get(w, 1.0)) ** 2 for w, n in va.items()))
    nb = math.sqrt(sum((n * idf.get(w, 1.0)) ** 2 for w, n in vb.items()))
    return dot / (na * nb) if na and nb else 0.0


def _similar(conn: sqlite3.Connection) -> None:
    """6b. The 'probable' tier, for listings with a brand but neither a code nor a named line: same
    brand, product type, exact set of specs and decisive features (blocking, so only near-identical
    candidates are compared). A signature must be one product on each shop (all of one shop's listings
    alike and priced alike: the uniqueness constraint), and the shops' listings must look alike (TF-IDF
    cosine of the title words) and be priced alike. Shown as 'probable', never used by alerts."""
    query = """SELECT a.product_id, pb.brand, pc.ptype, pc.specs, pc.flags, pc.toks, a.site, a.price, a.codes
               FROM assign a JOIN pb USING (product_id) JOIN product_codes pc USING (product_id)
               WHERE a.status = 'no_code' AND pb.brand IS NOT NULL AND pc.ptype != '' AND a.price IS NOT NULL
                 AND a.fresh AND length(pc.specs) - length(replace(pc.specs, ' ', '')) + 1 >= ?"""
    df, n = Counter(), 0
    for row in conn.execute(query, (SIM_MIN_SPECS,)):
        df.update(set(row[5].split()))
        n += 1
    if not n:
        return
    idf = {w: math.log(n / k) for w, k in df.items()}
    del df

    def assignments():
        rows = conn.execute(query + " ORDER BY pb.brand, pc.ptype, pc.specs, pc.flags", (SIM_MIN_SPECS,))
        for (brand, ptype, specs, flags), group in groupby(rows, key=lambda r: r[1:5]):
            members = [(r[0], r[5].split(), r[6], r[7], r[8]) for r in group]
            by_site = defaultdict(list)
            for m in members:
                by_site[m[2]].append(m)
            if len(by_site) < 2:
                continue
            unique = all(_cosine(a[1], b[1], idf) >= SIM_SAME_SHOP and max(a[3], b[3]) <= 1.15 * min(a[3], b[3])
                         for ms in by_site.values() for i, a in enumerate(ms) for b in ms[i + 1:])
            reps = [ms[0] for ms in by_site.values()]
            alike = all(_cosine(a[1], b[1], idf) >= SIM_CROSS_SHOP for i, a in enumerate(reps) for b in reps[i + 1:])
            prices = [m[3] for m in members]
            if not unique or not alike or max(prices) > SIM_PRICE * min(prices):
                continue
            model = f"{brand} ~{ptype.replace(' ', '')} {specs.replace(' ', '+')}" + (f" {flags.replace(' ', '+')}" if flags else "")
            for pid, _, site, price, codes in members:
                yield pid, model, "model", brand, None, codes, None, site, price, 1, "similar"
    _chunked(conn, "INSERT OR REPLACE INTO assign VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", assignments())


# How much each identity is trusted, strongest first: when one barcode links listings that carry
# different identities, the strongest one wins; two different ones of the same rank disagree.
RANK = {"manual": 4, "gtin": 3, "mpn": 2, "line": 1}  # 'similar' (probable) is below all of them
KEEP_OUT = ("used", "accessory", "bundle")  # a barcode never brings these into a model


def _barcodes(conn: sqlite3.Connection) -> None:
    """6c. Barcodes rank above every other identity. Listings that share a barcode (from the shop's
    data, product_barcodes, or from the title) are one product, connected through any chain of shared
    barcodes. A group joins the one strong model its listings already have (a Dream 2000 listing
    with no code in its title joins the code model its barcode partner is in); with none it becomes a
    barcode model. When its listings carry different identities the most trusted one wins (by hand >
    barcode > model code > product line); two different identities of that same rank mean the
    evidence disagrees, so nothing is merged and the listings are noted for review."""
    pairs = conn.execute(
        """WITH b AS (SELECT product_id, gtin FROM product_barcodes
                      UNION SELECT product_id, substr(code, 5) FROM product_code_index WHERE code GLOB 'GTIN*')
           SELECT gtin, product_id FROM b WHERE gtin IN (SELECT gtin FROM b GROUP BY gtin HAVING COUNT(*) >= 2)
           ORDER BY gtin""").fetchall()
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    label: dict[int, str] = {}
    for g, group in groupby(pairs, key=lambda r: r[0]):
        ids = [pid for _, pid in group]
        for pid in ids[1:]:
            parent[find(pid)] = find(ids[0])
        label.setdefault(find(ids[0]), g)
    components: dict[int, list[int]] = defaultdict(list)
    for pid in parent:
        components[find(pid)].append(pid)
    for root, ids in components.items():
        if len(ids) < 2:
            continue
        rows = conn.execute(f"""SELECT a.product_id, a.model, a.kind, a.status, pb.brand FROM assign a JOIN pb USING (product_id)
                                WHERE a.product_id IN ({','.join('?' * len(ids))})""", ids).fetchall()
        held = {(m, k) for _, m, k, st, _ in rows if st == "model" and m and k in RANK}
        top = max((RANK[k] for _, k in held), default=0)
        strong = {(m, k) for m, k in held if RANK[k] == top}
        movable = [r for r in rows if r[3] not in KEEP_OUT]
        gtin_of = min(g for g, pid in pairs if pid in ids)
        with conn:
            if len(strong) > 1:
                conn.executemany("UPDATE assign SET note = ? WHERE product_id = ?",
                                 [(f"barcode {gtin_of} also links models {', '.join(sorted(m for m, _ in strong))}: not merged",
                                   r[0]) for r in rows])
                continue
            if strong:
                model, kind = next(iter(strong))
            else:
                brands = Counter(r[4] for r in movable if r[4])
                model, kind = f"{brands.most_common(1)[0][0] if brands else '-'} GTIN{gtin_of}", "gtin"
            conn.executemany(
                "UPDATE assign SET model = ?, kind = ?, status = 'model', note = ? WHERE product_id = ?",
                [(model, kind, None if r[1] == model else f"joined by barcode {gtin_of}", r[0]) for r in movable])


def _overrides(conn: sqlite3.Connection) -> None:
    """Matches set by hand (match_overrides), applied last. 'same' (a, b): b joins a's model, or both
    form a new hand-made model when a has none. 'different' (a, b): b leaves a's model."""
    for a, b, verdict in conn.execute("SELECT a, b, verdict FROM match_overrides ORDER BY verdict DESC, created_at").fetchall():
        ra = conn.execute("SELECT model, kind, brand FROM assign WHERE product_id = ?", (a,)).fetchone()
        rb = conn.execute("SELECT model FROM assign WHERE product_id = ?", (b,)).fetchone()
        if ra is None or rb is None:
            continue
        with conn:
            if verdict == "same":
                model, kind = (ra[0], ra[1]) if ra[0] else (f"{ra[2] or '-'} #{min(a, b)}", "manual")
                conn.execute("UPDATE assign SET model = ?, kind = ?, status = 'model', note = NULL WHERE product_id = ?",
                             (model, kind, a))
                conn.execute("UPDATE assign SET model = ?, kind = ?, status = 'model', note = ? WHERE product_id = ?",
                             (model, kind, f"linked by hand to #{a}", b))
            elif ra[0] is not None and ra[0] == rb[0]:
                conn.execute("UPDATE assign SET model = NULL, status = 'split', note = ? WHERE product_id = ?",
                             (f"not the same as #{a} (set by hand)", b))


def set_override(conn: sqlite3.Connection, a: int, b: int, verdict: str | None, note: str | None, now: datetime) -> None:
    """Record (or with verdict None, forget) a hand-made match between two products; it applies from
    the next rebuild."""
    if a == b:
        raise ValueError("a product is always the same as itself")
    with conn:
        conn.execute("DELETE FROM match_overrides WHERE (a = ? AND b = ?) OR (a = ? AND b = ?)", (a, b, b, a))
        if verdict is not None:
            if verdict not in ("same", "different"):
                raise ValueError(f"verdict must be 'same' or 'different', not {verdict!r}")
            conn.execute("INSERT INTO match_overrides (a, b, verdict, note, created_at) VALUES (?, ?, ?, ?, ?)",
                         (a, b, verdict, note, iso(now)))


def _write(conn: sqlite3.Connection, now: datetime) -> int:
    """product_models / models := new_pm / new_m, touching only rows that differ. Returns the row count."""
    now_s, before = iso(now), conn.total_changes
    last = conn.execute("SELECT MAX(rowid) FROM new_pm").fetchone()[0] or 0
    for lo in range(1, last + 1, 10 * CHUNK):
        with conn:
            conn.execute(
                """INSERT OR REPLACE INTO product_models
                   SELECT n.product_id, n.model, n.status, n.brand, n.brand_from, n.codes, n.note, ?, ?
                   FROM new_pm n LEFT JOIN product_models o USING (product_id)
                   WHERE n.rowid BETWEEN ? AND ? AND (o.product_id IS NULL OR o.model IS NOT n.model
                      OR o.status IS NOT n.status OR o.brand IS NOT n.brand OR o.brand_from IS NOT n.brand_from
                      OR o.codes IS NOT n.codes OR o.note IS NOT n.note)""", (VERSION, now_s, lo, lo + 10 * CHUNK - 1))
    with conn:
        conn.execute("DELETE FROM product_models WHERE product_id NOT IN (SELECT product_id FROM new_pm)")
        conn.execute(
            """INSERT OR REPLACE INTO models
               SELECT n.*, ?, ? FROM new_m n LEFT JOIN models o USING (model)
               WHERE o.model IS NULL OR o.kind IS NOT n.kind OR o.status IS NOT n.status OR o.brand IS NOT n.brand
                  OR o.products IS NOT n.products OR o.sites IS NOT n.sites OR o.site_list IS NOT n.site_list
                  OR o.low IS NOT n.low OR o.median IS NOT n.median OR o.high IS NOT n.high OR o.note IS NOT n.note""",
            (VERSION, now_s))
        conn.execute("DELETE FROM models WHERE model NOT IN (SELECT model FROM new_m)")
    return conn.total_changes - before


@dataclass
class Summary:
    products: int
    extracted: int  # titles read this pass (new, changed, or read with older rules)
    in_model: int  # products in a model
    cross_shop: int  # ... whose model ('ok') is on 2+ shops
    models: int
    changed: int  # rows written or deleted
    seconds: float


def rebuild(conn: sqlite3.Connection, now: datetime) -> Summary:
    """Read new titles, recompute every product's model, write what changed."""
    started = time.monotonic()
    extracted = extract(conn)
    _group(conn, now)
    changed = _write(conn, now)
    conn.executescript("DROP TABLE pb; DROP TABLE pick; DROP TABLE lines; DROP TABLE assign; DROP TABLE new_pm; "
                       "DROP TABLE new_m; DROP TABLE idx")
    try:  # the next product pages to read (details.py); never fails the matching pass
        from deals import detailq
        detailq.refill(conn, now)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("product page queue")
    n = conn.execute(
        """SELECT COUNT(*), SUM(pm.status = 'model'), SUM(pm.status = 'model' AND m.status = 'ok' AND m.sites >= 2),
                  (SELECT COUNT(*) FROM models WHERE status != 'spec')
           FROM product_models pm LEFT JOIN models m USING (model)""").fetchone()
    return Summary(n[0], extracted, n[1] or 0, n[2] or 0, n[3], changed, round(time.monotonic() - started, 2))


@dataclass
class Result:
    products: dict[int, tuple]  # product_id -> (model, status, brand, brand_from, codes, note)
    models: dict[str, tuple]  # model -> (code, capacity, brand, status, products, sites, site_list, low, median, high, note)


def build(rows, now: datetime, barcodes: dict[int, list[str]] | None = None) -> Result:
    """The matching of a list of (id, site, title, brand, seller, price, last_seen_at) rows, in
    memory, through the same pipeline as the server (for tests and experiments); `barcodes` are
    the shops' own barcodes per product id."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    db._migrate(conn)
    conn.executemany(
        """INSERT INTO products (id, site, title, brand, seller, price, last_seen_at, sku, url, currency, in_stock,
                                 first_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', 'EGP', 1, ?)""",
        [(*r, f"sku{r[0]}", r[6]) for r in rows])
    for pid, gs in (barcodes or {}).items():
        db.record_barcodes(conn, pid, gs, "feed", now)
    rebuild(conn, now)
    return Result(
        products={r[0]: tuple(r[1:]) for r in conn.execute(
            "SELECT product_id, model, status, brand, brand_from, codes, note FROM product_models")},
        models={r[0]: tuple(r[1:]) for r in conn.execute(
            "SELECT model, code, capacity, brand, status, products, sites, site_list, low, median, high, note FROM models")})


def rebuild_in_process(db_path: Path, timeout: float = 1800) -> Summary:
    """`rebuild` in a separate process at the lowest CPU priority, as the coordinator runs it:
    crawl uploads always get the CPU first, the memory it needs is returned when it exits, a crash
    in it can't touch the coordinator, and if the service's memory limit is reached the kernel
    kills it rather than the coordinator."""
    run = subprocess.run([sys.executable, "-m", "deals.matching", str(db_path)],
                         capture_output=True, text=True, timeout=timeout)
    if run.returncode:
        raise RuntimeError(f"matching exited with {run.returncode}: {run.stderr.strip()[-800:]}")
    return Summary(**json.loads(run.stdout.strip().splitlines()[-1]))


OTHER_SHOPS_FRESH = timedelta(hours=24)


def other_shops(conn: sqlite3.Connection, product_id: int, now: datetime) -> list[sqlite3.Row]:
    """The same product's cheapest in-stock listing on each *other* shop, seen in the last day, for
    the end of an alert: (site, price, url, seller, last_seen_at), cheapest shop first. Empty when the
    product is not in an 'ok' model. Read-only, and only called after the alert decision is made."""
    me = conn.execute(
        """SELECT pm.model, p.site FROM product_models pm JOIN products p ON p.id = pm.product_id
           JOIN models m ON m.model = pm.model
           WHERE pm.product_id = ? AND pm.status = 'model' AND m.status = 'ok'""", (product_id,)).fetchone()
    if me is None:
        return []
    return conn.execute(
        """SELECT p.site, MIN(p.price) AS price, p.url, p.seller, MAX(p.last_seen_at) AS last_seen_at
           FROM product_models pm JOIN products p ON p.id = pm.product_id
           WHERE pm.model = ? AND pm.status = 'model' AND p.site != ? AND p.in_stock = 1
                 AND p.price IS NOT NULL AND p.last_seen_at >= ?
           GROUP BY p.site ORDER BY price""",
        (me["model"], me["site"], iso(now - OTHER_SHOPS_FRESH))).fetchall()


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
