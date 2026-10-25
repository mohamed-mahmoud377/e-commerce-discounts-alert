import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from deals.config import Config, SiteSettings, Watch

FIXTURES = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def fixture_text(name: str) -> str:
    return gzip.decompress((FIXTURES / name).read_bytes()).decode()


def fixture_json(name: str) -> dict:
    return json.loads(fixture_text(name))


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        data_dir=tmp_path,
        workers={"w1": "token-1", "w2": "token-2"},
        sites={"amazon_eg": SiteSettings(0, 0), "noon_eg": SiteSettings(0, 0)},
        watches=[Watch(name="TVs", targets={"amazon_eg": ["category:111"], "noon_eg": ["category:tvs"]},
                       interval_minutes=30, min_price=1000)],
    )
