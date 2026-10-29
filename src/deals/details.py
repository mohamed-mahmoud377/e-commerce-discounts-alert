"""What a shop's product page says about the product's identity: barcodes, the maker's model or part
numbers, and a few specs. For matching only (docs/MATCHING.md); prices never come from here.

Each page is read once per product, by a worker with nothing else to do (the coordinator hands one out
only when no crawl job is due for that shop), at a slow per-shop pace with a daily cap. Every parser
reads only the shop's own structured block for the product (a spec table, the product's JSON-LD),
never free text: a page also shows other products, the shop's own SKUs and related items, and the
pilot showed free-text scraping picks those up (docs/MATCHING.md, "Product pages: the pilot").

A challenge is never fought: `fetch_details` raises Blocked at the first sign of one, and the
coordinator stops handing out that shop's pages to that IP for a day.
"""

import html as htmllib
import json
import re
from dataclasses import dataclass, field

from deals.models import Blocked, valid_gtin

CHALLENGE = ("cf-challenge", "challenge-platform", "Just a moment...", "captcha", "Robot Check",
             "Enter the characters you see below", "Access Denied", "Request blocked")
BARCODE_LABELS = {"barcode", "ean", "gtin", "gtin barcode", "gtin13", "gtin14", "upc", "upc/ean", "ean/upc",
                  "باركود (ean)", "باركود"}
MPN_LABELS = {"model number", "item model number", "model no", "model no.", "model", "part number", "mpn",
              "mfr part number", "manufacturer part number", "manufacturer reference", "رقم الموديل"}
NAME_LABELS = {"model name"}
MAX_SPECS = 25  # spec pairs kept per product (identity-relevant ones first); values cut to 80 characters


@dataclass
class Details:
    gtins: list[str] = field(default_factory=list)  # valid GTIN-13s
    mpns: list[str] = field(default_factory=list)  # the maker's model / part numbers, as written
    model_name: str | None = None
    specs: dict[str, str] = field(default_factory=dict)


def _clean(text: str) -> str:
    return htmllib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))).strip()


def from_pairs(pairs: list[tuple[str, str]]) -> Details:
    """Label/value pairs of a product's spec block -> Details."""
    d = Details()
    for label, value in pairs:
        key, value = _clean(label).rstrip(":").strip().lower(), _clean(value)
        if not value or len(value) > 200:
            continue
        if key in BARCODE_LABELS:
            for g in re.findall(r"\d{12,14}", value):
                if (g := valid_gtin(g)) and g not in d.gtins:
                    d.gtins.append(g)
        elif key in MPN_LABELS:
            words = value.split()
            # "UI6 F1T S" is one code written with spaces; "CPH2831 (Aurora Gold)" is a code and words
            joined = len(words) <= 3 and all(re.fullmatch(r"[A-Z0-9][A-Z0-9\-./]*", w) for w in words)
            mpn = "".join(words) if joined else words[0].strip(".,;") if len(words) <= 3 else ""
            if mpn and any(c.isdigit() for c in mpn) and any(c.isalpha() for c in mpn) and mpn not in d.mpns:
                d.mpns.append(mpn)
        elif key in NAME_LABELS:
            d.model_name = value[:80]
        if len(d.specs) < MAX_SPECS and key not in d.specs:
            d.specs[key] = value[:80]
    return d


def _jsonld_products(page: str) -> list[dict]:
    out = []
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []:
            if isinstance(item, dict) and item.get("@type") == "Product":
                out.append(item)
    return out


def parse_jumia(page: str) -> Details:
    """The product's JSON-LD: gtin13/14, and its additionalProperty list (SKU, GTIN Barcode, Model...)."""
    products = _jsonld_products(page)
    if not products:
        return Details()
    p = products[0]
    pairs = [(k, str(p[k])) for k in ("gtin13", "gtin14", "gtin12", "gtin", "mpn") if p.get(k)]
    pairs += [(x.get("name", ""), str(x.get("value", ""))) for x in p.get("additionalProperty") or [] if isinstance(x, dict)]
    return from_pairs([("gtin" if k.startswith("gtin") else k, v) for k, v in pairs])


def parse_2b(page: str) -> Details:
    """Magento's product spec table (Barcode, MPN, Part Number, EAN rows)."""
    i = page.find('id="product-attribute-specs-table"')
    if i < 0:
        return Details()
    table = page[i:page.find("</table>", i)]
    return from_pairs(re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", table, re.S))


def parse_amazon(page: str) -> Details:
    """The product details table (Model Number, Mfr Part Number, UPC/EAN, Model Name)."""
    rows = re.findall(r'<th class="a-color-secondary a-size-base prodDetSectionEntry">(.*?)</th>\s*<td[^>]*>(.*?)</td>',
                      page, re.S)
    bullets = re.findall(r'<span class="a-text-bold">\s*(.*?)\s*</span>\s*<span>(.*?)</span>', page, re.S)
    return from_pairs(rows + [(re.sub(r"[‎‏:]", "", a), b) for a, b in bullets])


def parse_btech(page: str) -> Details:
    """The spec key/value pairs in the page data, and "Model Number: X" in the product's description meta."""
    pairs = [(k, v) for k, v in re.findall(r'\\"key\\":\\"([^"\\]{1,60})\\",\\"value\\":\\"([^"\\]{0,200})\\"', page)]
    meta = re.search(r'<meta name="description" content="([^"]*)"', page)
    if meta and (m := re.search(r"Model Number\s*:\s*([A-Za-z0-9][A-Za-z0-9\-./]{2,30})", htmllib.unescape(meta.group(1)))):
        pairs.append(("model number", m.group(1)))
    return from_pairs(pairs)


def parse_cairosales(page: str) -> Details:
    """PrestaShop's data sheet table, and the product's itemprop="mpn"."""
    pairs = []
    i = page.find('class="table-data-sheet"')
    if i >= 0:
        pairs += re.findall(r"<tr[^>]*>\s*<td>(.*?)</td>\s*<td>(.*?)</td>", page[i:page.find("</table>", i)], re.S)
    for m in re.finditer(r'itemprop="(mpn|gtin13|gtin)"[^>]*content="([^"]{3,40})"', page):
        pairs.append(("gtin" if m.group(1).startswith("gtin") else "mpn", m.group(2)))
    return from_pairs(pairs)


PARSERS = {"jumia_eg": parse_jumia, "2b_eg": parse_2b, "amazon_eg": parse_amazon, "btech_eg": parse_btech,
           "cairosales_eg": parse_cairosales}


def fetch_details(site, url: str) -> Details | None:
    """Read one product page with the shop's own crawler session (its TLS fingerprint and cookies).
    None when the product is gone (404/410); Blocked at the first sign of a challenge, never retried."""
    if site.name not in PARSERS:
        raise ValueError(f"no product page parser for {site.name}")
    r = site._session.get(url, timeout=30, headers={"Accept-Language": "en-US,en;q=0.9"})
    if r.status_code in (404, 410):
        return None
    head = r.text[:20000]
    if r.status_code in (403, 429, 503) or any(m in head for m in CHALLENGE) or len(r.text) < 20000:
        raise Blocked(f"{site.name}: product page refused (HTTP {r.status_code}, {len(r.text)} bytes)")
    r.raise_for_status()
    return PARSERS[site.name](r.text)
