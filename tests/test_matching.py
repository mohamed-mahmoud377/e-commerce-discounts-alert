"""Cross-shop matching (matching.py). Titles are real ones from the shops, including the cases
the rules were written for."""

import threading
from datetime import timedelta
from pathlib import Path

import pytest

from deals import db, health, matching
from deals.coordinator import Watchdog
from deals.db import iso, utcnow
from deals.matching import norm_brand, read_line, read_title, same_brand
from deals.models import Listing

NOW = utcnow()


@pytest.mark.parametrize("title, own", [
    ("Sharp Split Air Conditioner 1.5 HP Cool Inverter Digital Plasmacluster White AH-XP12BME", ["AHXP12BME"]),
    ("WH-1000XM5 Wireless Noise-Cancelling Headphones Smoky Pink", ["WH1000XM5"]),
    ("Over-Ear Wireless Noise-Cancelling Headphones WH-1000XM5/LM Midnight Blue", ["WH1000XM5"]),
    ("Sony WH1000XM5 SM Wh 1000Xm5 Wireless Noise Cancelling Headphones, SILVER", ["WH1000XM5"]),
    ("Haier Split Air Conditioner ,1.5 horsepower Cooling and Heating ,White - HSU-12K", ["HSU12K"]),
    ("Tornado Microwave Solo 25 Liter 900 Watt - Silver - TMDS-25EL-S", ["TMDS25ELS"]),
    ("Kenwood Smp01.A0Wh Sandwich Maker with Grill, 2 In 1-700 Watts", ["SMP01A0WH"]),
    ("Jabra Biz 2300 Duo 2399‐823‐109 Wired Headset", []),  # digits only: not a code
    # specs are not models
    ("8GB DDR4 3200MHz Laptop RAM – PC4-25600 SODIMM, 260-Pin, CL22", []),
    ("IdeaPad Slim 3 15IRH10 ( Core i7-13620H-16G Ram DDR5-4800-512GB NVMe ) Lenovo WL310", ["WL310"]),  # 15IRH10: a generation
    ("Haier Air Conditioner 2.25 HP Smart Cool - 18000 BTU/h - Cooling Only - R32", []),
    ("XPG ADATA, ATX3.1 and PCIe5.1 850W 80+ Gold Power Supply", []),
    ("Realme C75 5G-Middle East Version", []),
    ("MSI Prestige13 AI Evo A1MG-261EG Laptop", ["A1MG261EG"]),  # 'Prestige13' is a product line
    # '+' makes a different model
    ("Benq 24.1 Inch Gaming Monitor , 600 Hertz 0.5 Millisecond , Black - XL2586X+", ["XL2586XPLUS"]),
    # a bundled monitor's code is not the PC's
    ("DELL 7020 CORE I3 - 12100 - 8G - 512 SSD + MONITOR DELL - P2425H -3YEARS", []),
    # a product it is compatible with is not this product
    ("PHS-memory 32 GB RAM Memory Compatible with Synology DiskStation DS1621+ DDR4 SO DIMM ECC", []),
    ("DSP capsule coffee machine, 19 Bar Pump, 1450W, Compatible with Nespresso, KA3046", ["KA3046"]),
    # Lenovo generations are shared by many laptops
    ("Lenovo LOQ 15IRX10 83JE00H2ED Gaming Laptop", ["83JE00H2ED"]),
])
def test_codes_read_from_titles(title, own):
    assert read_title(title).own[:len(own) or None] == own


@pytest.mark.parametrize("title, mentions", [
    ("Ear Pads Cushions for Sony WH-1000XM5", ["WH1000XM5"]),
    ("Replacement Keyboard for HP EliteBook 830-G5 with Backlit", ["830G5"]),
    ("Laptop Battery Compatible with N5010, N5110, N4010", ["N5010", "N5110", "N4010"]),
])
def test_accessories_name_the_product_they_fit(title, mentions):
    t = read_title(title)
    assert t.accessory and t.own == [] and t.mentions == mentions


@pytest.mark.parametrize("title", [
    "Sony WH-1000XM5 Wireless Headphones with carrying case for travel",
    "Crucial E100 1TB M.2 NVMe PCIe Gen4 Internal SSD, Laptop & Desktop Compatible - CT1000E100SSD8",
    "Kenwood Stand Mixer for baking - KMX750",
    "Sharp Split Air Conditioner with remote control for cooling - AH-XP12BME",
])
def test_products_that_mention_for_or_compatible_are_not_accessories(title):
    t = read_title(title)
    assert not t.accessory and t.own


def test_used_capacity_and_series_word():
    assert read_title('iPhone 14 Single SIM 5G 128GB With Facetime - Blue "Renewed"').used
    assert read_title("Lenovo ThinkPad T480 (Used) i5").used
    assert not read_title("Kettle that can be used for tea KT1234").used
    assert read_title("HP 15-fd0015ne Laptop - Intel Core i5-1334U - 8GB - 512GB SSD").capacity == "8GB+512GB"
    assert read_title("JBL Tune 660NC, On-ear wireless").before == {"660NC": "TUNE"}


def test_brands():
    assert norm_brand("Black + Decker") == norm_brand("BLACK&DECKER") == norm_brand("BLACK AND DECKER") == "blackdecker"
    assert norm_brand("Generic") is None and norm_brand("Soundcore") == "anker"
    assert same_brand("arzum", "arzumokka") and same_brand("jamaky", "jamakyitaly") and not same_brand("lg", "lgx")


def row(id, site, title, price=100_000, brand=None, seller=None, seen=NOW):
    return (id, site, title, brand, seller, price, iso(seen))


def built(rows):
    r = matching.build(rows, NOW)
    return r.products, r.models


def test_same_code_on_several_shops_is_one_model():
    products, models = built([
        row(1, "jumia_eg", "Sharp Split AC 1.5 HP Cool Inverter AH-XP12BME", 3_100_000, "Sharp"),
        row(2, "amazon_eg", "SHARP Split Air Conditioner 1.5HP Cool Inverter Digital AH-XP12BME", 2_950_000),
        row(3, "noon_eg", "Air Conditioner 1.5 HP Cool Digital Inverter AH-XP12BME white", 2_980_000, seller="x"),
        row(4, "btech_eg", "Sharp Split Air Conditioner Cooling and Heating ,White - AY-XP12BME", 3_300_000, "Sharp"),
    ])
    assert [products[i][:2] for i in (1, 2, 3)] == [("sharp AHXP12BME", "model")] * 3
    assert products[2][2:4] == ("sharp", "title") and products[3][2:4] == ("sharp", "model")  # the model's brand
    m = models["sharp AHXP12BME"]
    assert m[:4] == ("AHXP12BME", None, "sharp", "ok") and m[4:7] == (3, 3, "amazon_eg,jumia_eg,noon_eg")
    assert (m[7], m[9]) == (2_950_000, 3_100_000)
    assert products[4][0] == "sharp AYXP12BME" and models["sharp AYXP12BME"][5] == 1  # cool/heat is another model


def test_price_and_brand_checks_leave_listings_out():
    products, models = built([
        row(1, "jumia_eg", "Sharp AC AH-XP12BME", 3_100_000, "Sharp"),
        row(2, "btech_eg", "Sharp AC AH-XP12BME", 3_000_000, "Sharp"),
        row(3, "noon_eg", "Sharp AC AH-XP12BME", 3_050_000, "Sharp"),
        row(4, "jumia_eg", "Sharp AC AH-XP12BME", 10_000_000, "Sharp"),  # a placeholder price
        row(5, "noon_eg", "Ear pads AH-XP12BME", 30_000, "Sharp"),
        row(6, "amazon_eg", "Tornado AC AH-XP12BME", 3_000_000, "Tornado"),
        row(7, "amazon_eg", "Tornado kettle", 30_000, "Tornado"),
    ])
    assert products[4][1] == products[5][1] == "price_outlier"
    assert products[6][0] == "tornado AHXP12BME"  # brand + code is the key: another maker's code is another model
    assert models["sharp AHXP12BME"][3:6] == ("ok", 3, 3)


def test_unbranded_listings_join_the_one_brand_with_the_code():
    products, _ = built([
        row(1, "jumia_eg", "Sharp AC AH-XP12BME", 3_000_000, "Sharp"),
        row(2, "noon_eg", "AC AH-XP12BME white", 3_000_000),  # no brand anywhere: Sharp is the only owner
        row(3, "jumia_eg", "Mixer KM-500", 100_000, "Sokany"),
        row(4, "btech_eg", "Mixer KM-500", 100_000, "Kenwood"),
        row(5, "noon_eg", "Mixer KM-500 silver", 100_000),  # two brands have the code: left out
        row(6, "jumia_eg", "Arzum Okka Coffee OK-0026", 500_000, "Arzum"),
        row(7, "btech_eg", "Arzum Okka Coffee OK-0026", 500_000, "Arzum Okka"),  # spellings of one brand fold
    ])
    assert products[2][:4] == ("sharp AHXP12BME", "model", "sharp", "model")
    assert products[5][1] == "ambiguous_brand" and products[3][0] != products[4][0]
    assert products[6][0] == products[7][0] == "arzum OK0026"


def test_prices_that_cannot_be_one_product_make_a_conflict():
    _, models = built([row(1, "jumia_eg", "Sokany SK-09031", 140_000), row(2, "noon_eg", "Sokany SK-09031", 639_900)])
    assert models["- SK09031"][3] == "conflict"  # '-': no brand known


def test_stale_prices_dont_count():
    products, models = built([row(1, "jumia_eg", "Sokany SK-09031", 140_000),
                              row(2, "noon_eg", "Sokany SK-09031", 639_900, seen=NOW - timedelta(days=30))])
    assert models["- SK09031"][3] == "ok" and products[2][1] == "model"


def test_a_code_many_brands_use_is_a_spec_word():
    products, models = built([row(i, "noon_eg", f"{b} 27 inch Monitor with ZQ-7741K panel", brand=b, seller=f"s{i}")
                              for i, b in enumerate(["Dell", "HP", "Lenovo", "Asus"])])
    assert all(p[1] == "no_code" and p[0] is None for p in products.values())
    assert models["ZQ7741K"][3] == "spec"


def test_series_words_split_a_short_code():
    titles = ["JBL Tune 660NC Headphones", "JBL Tune 670NC Headphones", "JBL Live 660NC Headphones",
              "JBL Live 670NC Headphones", "JBL 660NC Headphones"]
    products, models = built([row(i, "noon_eg", t, brand="JBL") for i, t in enumerate(titles, start=1)])
    assert [products[i][0] for i in range(1, 5)] == ["jbl TUNE660NC", "jbl TUNE670NC", "jbl LIVE660NC", "jbl LIVE670NC"]
    assert products[5][:2] == (None, "no_code") and "several series" in products[5][5]


def test_tv_size_is_part_of_a_series_code():
    assert read_title("Samsung U8000H TV 43 Inch 4K UHD Smart LED").capacity == "43IN"
    assert read_title("Samsung 55 Inch Crystal UHD TV UA55DU7000").capacity == "55IN"  # dropped at assignment:
    products, _ = built([row(1, "2b_eg", "Samsung 55 Inch Crystal UHD TV UA55DU7000", 2_000_000, "Samsung"),
                         row(2, "jumia_eg", "Samsung U8000H TV 43 Inch 4K Smart LED - UA43U8000H", 1_950_000, "Samsung"),
                         row(3, "noon_eg", "Samsung 43 Inch Crystal UHD 4K Smart TV U8000H", 1_935_600, "Samsung")])
    assert products[1][0] == "samsung UA55DU7000" and products[2][0] == products[3][0] == "samsung U8000H 43IN"
    assert read_title('MSI Pro MP243L 23.8" FHD Monitor').capacity == ""  # monitors: below 32 inch


def test_capacity_is_part_of_the_model():
    products, _ = built([row(1, "btech_eg", "Asus Vivobook 15 X1504VA-NJ001W 8GB 512GB SSD"),
                         row(2, "jumia_eg", "Asus Vivobook 15 X1504VA-NJ001W 16GB 1TB SSD")])
    assert products[1][0] == "- X1504VANJ001W 8GB+512GB" and products[2][0] == "- X1504VANJ001W 16GB+1TB"


def test_used_accessory_seller_brand_and_brand_name_codes():
    products, _ = built([
        row(1, "noon_eg", "Renewed - Latitude 5490 Laptop", brand="Dell"),
        row(2, "noon_eg", "Ear Pads Cushions for Sony WH-1000XM5", brand="Generic"),
        row(3, "noon_eg", "Dell P2425H 24 inch monitor", brand="Lap-Tech", seller="Lap-Tech"),
        row(4, "2b_eg", "V2com 14 inch Triple Laptop Screen Extender S2", brand="V2com"),
        row(5, "amazon_eg", "V2Com S2 14-Inch Portable Monitor"),
    ])
    assert products[1][1] == "used" and products[2][1] == "accessory"
    assert products[3][2] is None  # the seller's name is not a brand
    assert products[4][0] is None and products[5][0] is None  # 'V2COM' is the brand, not a model


@pytest.mark.parametrize("title, line, attrs", [
    ("Samsung Mobile Galaxy A16 , 128GB, 4GB RAM, 4G - Grey", "samsung galaxy a16", {"storage": "128GB", "ram": "4GB", "net": "4G"}),
    ("Apple iPhone 16 Plus (128 GB) - Pink with Face ID", "apple iphone 16 plus", {"storage": "128GB"}),
    ("Sony Playstation 5 Slim Disc Edition 1 TB ,2 Controllers", "sony playstation 5 slim disc", {"storage": "1TB", "bundle": "1"}),
    ("Sony PlayStation 5 Slim Disc Edition Console 1TB - White", "sony playstation 5 slim disc", {"storage": "1TB"}),
    ("PS5 Slim Console With Marvel's Spider-Man 2 Bundle", "ps5 slim", {"bundle": "1"}),
    ("Apple iPhone 18 Pro Max 512 GB (Nano SIM + eSIM) 5G With FaceTime - Burgundy", "apple iphone 18 pro max", {"storage": "512GB", "net": "5G"}),
    ("Xiaomi 17T 5G Smartphone (12GB RAM, 512GB Storage), Leica Camera", "xiaomi 17t", {"storage": "512GB", "ram": "12GB", "net": "5G"}),
    ("Dell G15 5530 Laptop, Intel Core i7-13650HX, 512 GB, 16 GB, NVIDIA GeForce RTX 4050", "dell g15",
     {"storage": "512GB", "ram": "16GB", "cpu": "I713650HX", "gpu": "RTX4050"}),
    ("ThinkPad T490 Laptop With 14-Inch Display, Core i5-8265U/16GB RAM/256GB SSD", "thinkpad t490",
     {"storage": "256GB", "ram": "16GB", "cpu": "I58265U"}),
    ("Carrier 3 Horse Power Inverter Cooling Only", "", {}),  # a spec number is not a name
    ("Fresh Chopper 6 Liter 1500 Watt", "", {}),
    ("Haier 50 Inch Smart TV , 4K UHD QLED", "", {}),
    ("Magic Pad 4 Tablet Gray 12GBRAM 256GB WiFi with Keyboard", "magic pad 4", {"storage": "256GB", "ram": "12GB", "bundle": "1"}),
    ("Honor Magic Pad 4 Tablet Gray 12GBRAM 256GB WiFi", "honor magic pad 4", {"storage": "256GB", "ram": "12GB"}),
])
def test_product_lines(title, line, attrs):
    got_line, got_attrs = read_line(title)
    assert " ".join(w for _, w in got_line) == line and got_attrs == attrs


def test_line_tier_variants_and_ambiguity():
    products, models = built([
        row(1, "jumia_eg", "Samsung Galaxy A16, 128GB, 4GB RAM, 4G - Grey", 950_000, "Samsung"),
        row(2, "btech_eg", "Samsung Galaxy A16 , 128 GB , 4 GB , 4G - Green", 930_000, "Samsung"),
        row(3, "carrefour_eg", "Samsung Mobile Galaxy A16, 256GB, 4G, Grey", 1_260_000, "Samsung"),  # no RAM: joins 8/256
        row(4, "jumia_eg", "Samsung Galaxy A16, 256GB, 8GB RAM, 4G - Black", 1_250_000, "Samsung"),
        row(5, "noon_eg", "Galaxy A16 256GB 4G Black", 1_240_000, "Samsung"),  # no RAM, one 256GB variant: joins it
        row(6, "jumia_eg", "Samsung Galaxy A16, 128GB, 8GB RAM, 4G - Black", 1_100_000, "Samsung"),
        row(7, "noon_eg", "Galaxy A16 128GB 4G Grey", 1_000_000, "Samsung"),  # 128GB has 4GB and 8GB variants: ambiguous
        row(8, "btech_eg", "Apple iPhone 16 , 128 GB , 6 GB , 5G - Teal", 6_440_000, "Apple"),
        row(9, "2b_eg", "Apple iPhone 16 - 128GB - Pink", 7_329_900, "Apple"),  # Apple RAM is ignored
        row(10, "noon_eg", "Galaxy A16 128GB 8GB RAM 4G + Galaxy Buds", 1_300_000, "Samsung"),  # a bundle
    ])
    assert products[1][0] == products[2][0] == "samsung galaxya16 128GB 4GB 4G"
    assert products[3][0] == products[4][0] == products[5][0] == "samsung galaxya16 256GB 8GB 4G"
    assert products[7][1] == "ambiguous_variant" and products[10][1] == "bundle"
    assert products[8][0] == products[9][0] == "apple iphone16 128GB 5G"
    assert models["samsung galaxya16 128GB 4GB 4G"][:2] == ("galaxya16", "128GB 4GB 4G")


def listing(site, sku, title, price, brand=None):
    return Listing(site, sku, title, f"https://x/{sku}", price, None, "EGP", True, brand=brand)


def test_rebuild_writes_only_changes_and_shows_the_other_shops(cfg):
    conn = db.connect(cfg.db_path)
    with conn:
        a = db.record(conn, listing("jumia_eg", "J1", "Sharp AC AH-XP12BME", 3_100_000, "Sharp"), now=NOW).product_id
        db.record(conn, listing("amazon_eg", "A1", "SHARP AC AH-XP12BME", 2_950_000), now=NOW)
        db.record(conn, listing("noon_eg", "N1@s", "Kettle 1.7L", 50_000), now=NOW)
    first = matching.rebuild(conn, NOW)
    assert (first.products, first.extracted, first.in_model, first.cross_shop, first.models) == (3, 3, 2, 2, 1)
    again = matching.rebuild(conn, NOW)
    assert (again.extracted, again.changed) == (0, 0)  # nothing new: no title read, nothing written
    seen = matching.same_model(conn, a)
    assert seen["model"]["model"] == "sharp AHXP12BME" and [o["site"] for o in seen["others"]] == ["amazon_eg"]
    kettle = conn.execute("SELECT id FROM products WHERE sku = 'N1@s'").fetchone()[0]
    assert matching.same_model(conn, kettle) == {"me": matching.same_model(conn, kettle)["me"], "model": None, "others": []}
    with conn:  # the title changes: the next rebuild follows
        conn.execute("UPDATE products SET title = 'SHARP AC AY-XP12BME' WHERE sku = 'A1'")
    third = matching.rebuild(conn, NOW)
    assert (third.extracted, third.cross_shop) == (1, 0)  # only the changed title is read again
    with conn:  # a deleted product takes its row along
        conn.execute("DELETE FROM price_history WHERE product_id = ?", (kettle,))
        conn.execute("DELETE FROM products WHERE id = ?", (kettle,))
    assert conn.execute("SELECT COUNT(*) FROM product_models WHERE product_id = ?", (kettle,)).fetchone()[0] == 0


def test_watchdog_matches_hourly_and_a_failure_stays_contained(cfg, monkeypatch):
    conn = db.connect(cfg.db_path)
    dog = Watchdog(cfg, threading.Event())
    calls = []
    monkeypatch.setattr(matching, "rebuild_in_process", lambda path: calls.append(path) or matching.Summary(0, 0, 0, 0, 0, 0, 0))
    dog.match(conn, NOW)
    dog.match(conn, NOW + timedelta(minutes=30))
    dog.match(conn, NOW + timedelta(minutes=61))
    assert len(calls) == 2

    def boom(path):
        raise RuntimeError("bug")
    monkeypatch.setattr(matching, "rebuild_in_process", boom)
    monkeypatch.setattr(dog, "check", lambda c, now: calls.append("check"))
    monkeypatch.setattr(dog.stop_event, "wait", lambda _: len(calls) > 4)
    dog.next_match = None
    dog.run()  # the matching error is logged; the checks keep running
    assert calls.count("check") >= 2


def test_older_code_on_a_newer_database_is_only_a_warning(cfg):
    conn = db.connect(cfg.db_path)
    conn.execute(f"PRAGMA user_version = {db.schema_version() + 1}")
    got = {c.name: c.level for c in health.run_checks(conn, cfg, NOW)}
    assert got["database"] == "warn"


def test_rebuild_in_its_own_process(cfg):
    conn = db.connect(cfg.db_path)
    with conn:
        db.record(conn, listing("jumia_eg", "J1", "Sharp AC AH-XP12BME", 3_100_000, "Sharp"), now=NOW)
        db.record(conn, listing("amazon_eg", "A1", "SHARP AC AH-XP12BME", 2_950_000), now=NOW)
    s = matching.rebuild_in_process(cfg.db_path)
    assert (s.products, s.cross_shop) == (2, 2)
    assert conn.execute("SELECT site_list FROM models WHERE code = 'AHXP12BME'").fetchone()[0] == "amazon_eg,jumia_eg"
    with pytest.raises(RuntimeError, match="matching exited"):
        matching.rebuild_in_process(cfg.db_path / "not-a-folder.db")  # a crash is reported


GOLD = Path(__file__).parent / "fixtures" / "matching_gold.tsv"


def test_gold_set_precision_and_recall():
    """Real titles labelled by hand (fixtures/matching_gold.tsv): rows with the same group are one
    product, different groups are different products, group 'x' rows belong to no model. Every pair
    the matcher puts together must be labelled together (precision 100%); most labelled pairs must
    be found (recall). Add rows there whenever a wrong or missed match is found."""
    rows, label = [], {}
    for i, line in enumerate(GOLD.read_text().splitlines()[1:], start=1):
        if not line.strip() or line.startswith("#"):
            continue
        group, site, brand, price, title = line.split("\t")
        rows.append(row(i, site, title, int(price) * 100, brand or None))
        label[i] = group
    products, _ = built(rows)
    predicted = {i: p[0] for i, p in products.items() if p[1] == "model"}
    wrong = [(label[a], label[b], a, b) for a in predicted for b in predicted
             if a < b and predicted[a] == predicted[b] and label[a] != label[b]]
    assert not wrong, f"put together but different products (group a, group b, row a, row b): {wrong[:10]}"
    stray = [(i, label[i], products[i]) for i in predicted if label[i] == "x"]
    assert not stray, f"matched but should be in no model: {stray[:10]}"
    same = [(a, b) for a in label for b in label if a < b and label[a] == label[b] != "x"]
    found = sum(1 for a, b in same if predicted.get(a) is not None and predicted.get(a) == predicted.get(b))
    assert found / len(same) >= 0.9, f"recall {found}/{len(same)}: " + str(
        [(a, b, products[a][:2], products[b][:2]) for a, b in same if predicted.get(a) != predicted.get(b)][:10])
