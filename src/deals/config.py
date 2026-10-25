"""Coordinator configuration (TOML). See config.example.toml for every option."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from deals.models import Slice


@dataclass(frozen=True)
class AlertRules:
    min_drop: float = 0.30  # below the product's typical price (time-weighted 90-day median)
    atl_min_drop: float = 0.20  # a new all-time low only needs this much
    atl_min_history_days: float = 14
    price_error_drop: float = 0.60  # flagged as a possible pricing mistake
    min_history_days: float = 3  # less in-stock history than this = "new product" rules
    new_product_min_drop: float = 0.30  # site-claimed discount needed for new products
    realert_after_days: float = 7
    realert_extra_drop: float = 0.05  # re-alert sooner if the price falls this much further


@dataclass(frozen=True)
class Schedule:
    min_interval_minutes: int = 10  # hottest slices never get crawled more often than this
    max_interval_factor: float = 3.0  # quiet slices back off up to watch interval x this
    replan_days: float = 7
    lease_minutes: float = 5  # extended by every page a worker reports


@dataclass(frozen=True)
class SiteSettings:
    min_delay: float = 2.0  # seconds between requests, per worker
    max_delay: float = 4.0


@dataclass(frozen=True)
class Watch:
    """A product group to track, e.g. TVs, with one root target per site."""

    name: str
    targets: dict[str, list[str]]  # site -> root slice keys
    interval_minutes: int = 60
    min_price: int | None = None  # EGP; cheaper products are neither crawled nor alerted
    min_drop: float | None = None  # overrides alerts.min_drop for this group
    label: str | None = None  # display name in alerts (e.g. Arabic); defaults to name

    def roots(self) -> list[tuple[str, Slice]]:
        out = []
        for site, keys in self.targets.items():
            for key in keys:
                root = Slice.from_key(key)
                if self.min_price and root.price_min is None:
                    root = root.with_price(self.min_price, root.price_max)
                out.append((site, root))
        return out


@dataclass(frozen=True)
class Config:
    host: str = "127.0.0.1"
    port: int = 8787
    data_dir: Path = Path("data")
    workers: dict[str, str] = field(default_factory=dict)  # name -> token
    telegram_token: str = ""
    telegram_chat_id: str = ""
    admin_token: str = ""  # logs in to the web UI (/ui); worker tokens work too
    alerts: AlertRules = AlertRules()
    schedule: Schedule = Schedule()
    sites: dict[str, SiteSettings] = field(default_factory=dict)
    watches: list[Watch] = field(default_factory=list)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "deals.db"

    def site(self, name: str) -> SiteSettings:
        return self.sites.get(name, SiteSettings())

    def watch(self, name: str) -> Watch | None:
        return next((w for w in self.watches if w.name == name), None)


def load(path: Path) -> Config:
    from deals.sites import SITES

    raw = tomllib.loads(path.read_text())
    server = raw.get("server", {})
    telegram = raw.get("telegram", {})

    watches = []
    for w in raw.get("watch", []):
        targets = {}
        for site in SITES:
            value = w.get(site)
            if value:
                targets[site] = [value] if isinstance(value, str) else list(value)
        unknown = set(w) - set(SITES) - {"name", "label", "interval_minutes", "min_price", "min_drop"}
        if unknown:
            raise ValueError(f"watch {w.get('name')!r}: unknown keys {sorted(unknown)}")
        watches.append(Watch(
            name=w["name"],
            targets=targets,
            interval_minutes=w.get("interval_minutes", 60),
            min_price=w.get("min_price"),
            min_drop=w.get("min_drop"),
            label=w.get("label"),
        ))
        watches[-1].roots()  # fail fast on malformed target keys
    names = [w.name for w in watches]
    if len(names) != len(set(names)):
        raise ValueError("watch names must be unique")

    data_dir = Path(server.get("data_dir", "data"))
    if not data_dir.is_absolute():
        data_dir = path.resolve().parent / data_dir
    return Config(
        host=server.get("host", "127.0.0.1"),
        port=server.get("port", 8787),
        data_dir=data_dir,
        workers=dict(raw.get("workers", {})),
        telegram_token=telegram.get("bot_token", ""),
        telegram_chat_id=str(telegram.get("chat_id", "")),
        admin_token=server.get("admin_token", ""),
        alerts=AlertRules(**raw.get("alerts", {})),
        schedule=Schedule(**raw.get("schedule", {})),
        sites={name: SiteSettings(**s) for name, s in raw.get("sites", {}).items()},
        watches=watches,
    )
