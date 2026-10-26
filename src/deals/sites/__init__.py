from deals.sites.amazon_eg import AmazonEG
from deals.sites.cairosales_eg import CairoSalesEG
from deals.sites.jumia_eg import JumiaEG
from deals.sites.noon_eg import NoonEG
from deals.sites.twob_eg import TwoBEG

SITES = {
    AmazonEG.name: AmazonEG,
    NoonEG.name: NoonEG,
    JumiaEG.name: JumiaEG,
    CairoSalesEG.name: CairoSalesEG,
    TwoBEG.name: TwoBEG,
}
