"""Every shop has everything it needs. A new shop in deals/shops.py fails here until it has an adapter, a
screenshot spec, a saved real listing page that parses, its doc, config delays and a place in the worker
script and the setup docs (docs/INTERNALS.md, "Add a shop")."""
import re
from pathlib import Path

from conftest import fixture_json, fixture_text
import pytest

from deals import alerts, shots
from deals.shops import KINDS, SHOPS, expand
from deals.sites import SITES

ROOT = Path(__file__).parent.parent


def _amazon(name, fixture):
    from deals.sites.amazon_eg import parse_search_page
    from deals.sites.amazon_now_eg import AmazonNowEG
    dp = AmazonNowEG.dp_query if name == "amazon_now_eg" else ""
    return parse_search_page(fixture_text(fixture), site=name, dp_query=dp)


def _parse(module, func):
    return getattr(__import__(f"deals.sites.{module}", fromlist=[func]), func)


# One saved real listing page per shop and how its adapter reads it.
LISTING_PAGES = {
    "amazon_eg": lambda: _amazon("amazon_eg", "amazon_search.html.gz"),
    "amazon_now_eg": lambda: _amazon("amazon_now_eg", "amazon_now_node.html.gz"),
    "noon_eg": lambda: _parse("noon_eg", "parse_catalog")(fixture_json("noon_category.json.gz"), 1),
    "jumia_eg": lambda: _parse("jumia_eg", "parse_catalog_page")(fixture_text("jumia_category.html.gz"), "/televisions/", 1),
    "cairosales_eg": lambda: _parse("cairosales_eg", "parse_listing_page")(fixture_text("cairosales_category.html.gz")),
    "2b_eg": lambda: _parse("twob_eg", "parse_products")(fixture_json("2b_products.json.gz"), 1),
    "btech_eg": lambda: _parse("btech_eg", "parse_products")(fixture_json("btech_products.json.gz"), 1),
    "dream2000_eg": lambda: _parse("dream2000_eg", "parse_products")(fixture_json("dream2000_products.json.gz")),
    "carrefour_eg": lambda: _parse("carrefour_eg", "parse_listing_page")(fixture_text("carrefour_category.html.gz")),
    "dubaiphone_eg": lambda: _parse("dubaiphone_eg", "parse_listing")(fixture_text("dubaiphone_mobiles.rsc.gz")),
    "hardwaremarket_eg": lambda: _parse("hardwaremarket_eg", "parse_products")(
        fixture_json("hardwaremarket_headsets.json.gz"), None, None, 1),
    "raya_eg": lambda: _parse("raya_eg", "parse_products")(fixture_json("raya_tvs.json.gz")["data"], 1),
    "vodafone_eg": lambda: _parse("vodafone_eg", "parse_products")(fixture_json("vodafone_phones.json.gz")),
    "dokkantech_eg": lambda: _parse("dokkantech_eg", "parse_products")(fixture_json("dokkantech_keyboards.json.gz")),
    "etisal_eg": lambda: _parse("etisal_eg", "parse_products")(fixture_json("etisal_wearables.json.gz"), 1),
    "orange_eg": lambda: _parse("orange_eg", "parse_products")(fixture_json("orange_tablets.json.gz"), 1),
    "lcwaikiki_eg": lambda: _parse("lcwaikiki_eg", "parse_listing")(fixture_text("lcwaikiki_shoes.html.gz")),
    "defacto_eg": lambda: _parse("defacto_eg", "parse_listing")(fixture_text("defacto_woman_discount.html.gz")),
    "elaraby_eg": lambda: _parse("elaraby_eg", "parse_products")(fixture_json("elaraby_televisions.json.gz")["data"], 1),
    "miami_eg": lambda: _parse("miami_eg", "parse_products")(fixture_json("miami_tablets.json.gz"), None, None, 1),
    "sigma_eg": lambda: _parse("sigma_eg", "parse_listing")(fixture_text("sigma_laptops.html.gz")),
    "elnekhely_eg": lambda: _parse("elnekhely_eg", "parse_listing")(fixture_text("elnekhely_gpu.html.gz")),
    "wayup_eg": lambda: _parse("wayup_eg", "parse_products")(fixture_json("wayup_sale.json.gz")),
    "gcshop_eg": lambda: _parse("gcshop_eg", "parse_products")(fixture_json("gcshop_products.json.gz")),
    "azzam_eg": lambda: _parse("azzam_eg", "parse_products")(fixture_json("azzam_products.json.gz")),
    "mazaya_eg": lambda: _parse("mazaya_eg", "parse_products")(fixture_json("mazaya_perfumes.json.gz")["data"]),
}


def test_the_registry_and_the_adapters_agree():
    assert list(SITES) == list(SHOPS)  # same shops, same order
    for name, adapter in SITES.items():
        assert adapter.name == name and isinstance(adapter.max_slice_total, int) and adapter.max_slice_total > 0


def test_names_badges_and_kinds():
    assert len({s.label for s in SHOPS.values()}) == len({s.badge for s in SHOPS.values()}) == len(SHOPS)
    for s in SHOPS.values():
        assert re.search("[؀-ۿ]", s.arabic), s.name  # alerts and the bot are Arabic
        assert alerts.SITE_NAME[s.name] == s.arabic
        assert s.kind in KINDS and s.url.startswith("https://") and s.roots, s.name
        assert 1 <= len(s.badge) <= 3, s.name


@pytest.mark.parametrize("name", list(SHOPS))
def test_every_shop_has_a_screenshot_spec_doc_and_config(name):
    assert name in shots.SPECS, "add a Spec to shots.SPECS (GUIDE.md, 'Alert screenshots')"
    doc = SHOPS[name].doc
    assert (ROOT / "docs" / doc).exists(), f"write docs/{doc}"
    assert doc in (ROOT / "CLAUDE.md").read_text(), f"link docs/{doc} from CLAUDE.md"
    assert f"[sites.{name}]" in (ROOT / "config.example.toml").read_text(), "add its delays to config.example.toml"


@pytest.mark.parametrize("name", list(SHOPS))
def test_every_shop_parses_its_saved_listing_page(name):
    assert name in LISTING_PAGES, "save a real listing page in tests/fixtures/ and add it to LISTING_PAGES"
    result = LISTING_PAGES[name]()
    assert result.listings, "no products parsed from the saved page"
    assert len({x.sku for x in result.listings}) == len(result.listings), "duplicate SKUs on one page"
    priced = [x for x in result.listings if x.price is not None]
    assert priced, "no prices parsed"
    for x in result.listings:
        assert x.site == name and x.currency == "EGP" and x.title.strip() and x.url.startswith("https://"), x
        # prices are whole piastres; a crossed-out price is above the price
        assert x.price is None or (isinstance(x.price, int) and x.price > 0), x
        assert x.list_price is None or (isinstance(x.list_price, int) and x.price is not None and x.list_price > x.price), x
        assert x.list_price_kind in (None, "was", "list"), x


def test_every_shop_is_in_the_worker_script_and_setup_docs():
    """The lines that list every shop for a home worker must list all of them (Amazon Now was left out of
    the setup docs until 4 Nov 2026)."""
    files = ["scripts/mac-worker-env.sh", "README.md", "docs/RUN_A_WORKER.md", "docs/GETTING_STARTED.md", "docs/GUIDE.md"]
    for f in files:
        lists = re.findall(r"DEALS_SITES=(noon_eg[\w,]*)", (ROOT / f).read_text())
        assert lists, f
        for found in lists:
            assert set(found.split(",")) == set(SHOPS), f"{f}: DEALS_SITES={found}"


def test_a_shop_filter_can_be_a_kind():
    assert expand("") is None and expand(None) is None and expand("noon_eg") == ["noon_eg"]
    assert expand("kind:marketplace") == ["amazon_eg", "noon_eg", "jumia_eg", "raya_eg"]
    assert set(expand("kind:grocery")) == {"carrefour_eg", "amazon_now_eg"}
    assert expand("kind:nope") == []
