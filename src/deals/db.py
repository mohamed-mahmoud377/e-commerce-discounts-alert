"""SQLite storage.

products holds the latest known state of every product; price_history gets a
row only when price or stock actually changes, which keeps the table small even
when thousands of products are re-checked every few minutes. Each history row
is a step: the price held from its observed_at until the next row.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from deals.models import Listing

# Append-only: each entry upgrades the schema by one version (PRAGMA user_version).
MIGRATIONS = [
    """
    CREATE TABLE products (
        id              INTEGER PRIMARY KEY,
        site            TEXT NOT NULL,
        sku             TEXT NOT NULL,
        title           TEXT NOT NULL,
        url             TEXT NOT NULL,
        currency        TEXT NOT NULL,
        price           INTEGER,
        list_price      INTEGER,
        in_stock        INTEGER NOT NULL,
        first_seen_at   TEXT NOT NULL,
        last_seen_at    TEXT NOT NULL,
        UNIQUE (site, sku)
    );
    CREATE TABLE price_history (
        id              INTEGER PRIMARY KEY,
        product_id      INTEGER NOT NULL REFERENCES products(id),
        price           INTEGER,
        list_price      INTEGER,
        in_stock        INTEGER NOT NULL,
        observed_at     TEXT NOT NULL
    );
    CREATE INDEX price_history_product ON price_history (product_id, observed_at);
    """,
    """
    CREATE TABLE crawl_slices (
        id              INTEGER PRIMARY KEY,
        site            TEXT NOT NULL,
        root            TEXT NOT NULL,
        slice           TEXT NOT NULL,
        expected_total  INTEGER,
        oversized       INTEGER NOT NULL DEFAULT 0,
        planned_at      TEXT NOT NULL,
        last_crawled_at TEXT,
        UNIQUE (site, slice)
    );
    """,
    """
    DROP TABLE crawl_slices;

    ALTER TABLE products ADD COLUMN grp TEXT;  -- watch group, e.g. 'TVs'
    ALTER TABLE products ADD COLUMN list_price_kind TEXT;
    ALTER TABLE products ADD COLUMN brand TEXT;
    ALTER TABLE products ADD COLUMN stock_left INTEGER;
    ALTER TABLE products ADD COLUMN recent_sales INTEGER;
    ALTER TABLE products ADD COLUMN rating REAL;
    ALTER TABLE products ADD COLUMN reviews INTEGER;
    ALTER TABLE products ADD COLUMN badges TEXT NOT NULL DEFAULT '';
    ALTER TABLE products ADD COLUMN last_change_at TEXT;
    UPDATE products SET last_change_at = first_seen_at;

    -- Work for workers. 'plan' jobs split a watch root into slices; 'crawl'
    -- jobs walk one slice. A job is leased to one worker at a time.
    CREATE TABLE jobs (
        id               INTEGER PRIMARY KEY,
        kind             TEXT NOT NULL CHECK (kind IN ('plan', 'crawl')),
        site             TEXT NOT NULL,
        target           TEXT NOT NULL,  -- slice key
        root             TEXT NOT NULL,  -- root slice key it was planned from
        grp              TEXT NOT NULL,
        expected_total   INTEGER,
        oversized        INTEGER NOT NULL DEFAULT 0,
        base_interval_s  INTEGER NOT NULL,
        interval_s       INTEGER NOT NULL,
        next_due_at      TEXT NOT NULL,
        lease_worker     TEXT,
        lease_ip         TEXT,
        lease_token      TEXT,
        lease_expires_at TEXT,
        run_count        INTEGER NOT NULL DEFAULT 0,  -- products seen in the current run
        run_changes      INTEGER NOT NULL DEFAULT 0,
        failures         INTEGER NOT NULL DEFAULT 0,
        last_error       TEXT,
        last_done_at     TEXT,
        last_count       INTEGER,
        last_changes     INTEGER,
        created_at       TEXT NOT NULL,
        UNIQUE (kind, site, target)
    );
    CREATE INDEX jobs_due ON jobs (site, next_due_at);

    CREATE TABLE workers (
        name         TEXT PRIMARY KEY,
        last_seen_at TEXT NOT NULL,
        last_ip      TEXT,
        sites        TEXT,
        version      TEXT,
        pages        INTEGER NOT NULL DEFAULT 0,
        jobs_done    INTEGER NOT NULL DEFAULT 0
    );

    -- A site that blocked an IP: no jobs for that site go to that IP until 'until'.
    CREATE TABLE blocks (
        site       TEXT NOT NULL,
        ip         TEXT NOT NULL,
        strikes    INTEGER NOT NULL,
        until      TEXT NOT NULL,
        reason     TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (site, ip)
    );

    -- Outbox: alerts are written here and sent by a background loop with retries.
    CREATE TABLE alerts (
        id              INTEGER PRIMARY KEY,
        product_id      INTEGER REFERENCES products(id),  -- NULL for system alerts
        kind            TEXT NOT NULL,
        dedupe_key      TEXT,
        price           INTEGER,
        reference_price INTEGER,
        drop_pct        REAL,
        score           INTEGER,
        message         TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        sent_at         TEXT,
        attempts        INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT NOT NULL,
        error           TEXT
    );
    CREATE INDEX alerts_pending ON alerts (sent_at, next_attempt_at);
    CREATE INDEX alerts_product ON alerts (product_id, created_at);
    CREATE INDEX alerts_dedupe ON alerts (dedupe_key, created_at);
    """,
    """
    ALTER TABLE products ADD COLUMN seller TEXT;

    -- noon products used to be keyed by product only, so offers from different
    -- sellers were mixed into one history (fake ups and downs). They are now
    -- keyed per seller offer; drop the mixed data and crawl noon afresh, as a
    -- first crawl (so new items don't count as "new listings").
    DELETE FROM alerts WHERE product_id IN (SELECT id FROM products WHERE site = 'noon_eg');
    DELETE FROM price_history WHERE product_id IN (SELECT id FROM products WHERE site = 'noon_eg');
    DELETE FROM products WHERE site = 'noon_eg';
    UPDATE jobs SET last_done_at = NULL, next_due_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
    WHERE site = 'noon_eg' AND kind = 'crawl';
    """,
    """
    -- Telegram users who receive alerts. The admin (config chat_id) approves others.
    CREATE TABLE subscribers (
        chat_id     INTEGER PRIMARY KEY,
        name        TEXT,
        username    TEXT,
        status      TEXT NOT NULL,  -- pending | active | rejected | stopped | blocked
        is_admin    INTEGER NOT NULL DEFAULT 0,
        approved_at TEXT,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );

    -- One row per (alert, recipient): each person's delivery is retried on its own.
    -- alerts.sent_at now means "handed out to recipients".
    CREATE TABLE deliveries (
        id              INTEGER PRIMARY KEY,
        alert_id        INTEGER NOT NULL REFERENCES alerts(id),
        chat_id         INTEGER NOT NULL,
        sent_at         TEXT,
        attempts        INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT NOT NULL,
        error           TEXT,
        UNIQUE (alert_id, chat_id)
    );
    CREATE INDEX deliveries_pending ON deliveries (sent_at, next_attempt_at);

    CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- e.g. telegram update offset
    """,
]


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        with conn:
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version = {i}")


def iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


@dataclass(frozen=True)
class Recorded:
    product_id: int
    status: str  # 'new', 'changed' or 'same'
    old_price: int | None
    old_in_stock: bool | None

    @property
    def price_moved(self) -> bool:
        """New product, or its price / availability changed (list-price-only edits don't count)."""
        return self.status == "new" or self.status == "changed"


def record(conn: sqlite3.Connection, listing: Listing, grp: str | None = None,
           now: datetime | None = None) -> Recorded:
    """Upsert a listing; append a history row if price, list price or stock changed."""
    now_s = iso(now or utcnow())
    row = conn.execute(
        "SELECT id, price, list_price, in_stock, grp FROM products WHERE site = ? AND sku = ?",
        (listing.site, listing.sku),
    ).fetchone()
    details = (listing.title, listing.url, listing.price, listing.list_price, listing.in_stock,
               listing.list_price_kind, listing.brand, listing.stock_left, listing.recent_sales,
               listing.rating, listing.reviews, ",".join(listing.badges), listing.seller)

    if row is None:
        product_id = conn.execute(
            """INSERT INTO products (title, url, price, list_price, in_stock, list_price_kind, brand,
                                     stock_left, recent_sales, rating, reviews, badges, seller,
                                     site, sku, currency, grp, first_seen_at, last_seen_at, last_change_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (*details, listing.site, listing.sku, listing.currency, grp, now_s, now_s, now_s),
        ).lastrowid
        result = Recorded(product_id, "new", None, None)
    else:
        product_id = row["id"]
        price_changed = (row["price"], bool(row["in_stock"])) != (listing.price, listing.in_stock)
        changed = price_changed or row["list_price"] != listing.list_price
        conn.execute(
            f"""UPDATE products SET title = ?, url = ?, price = ?, list_price = ?, in_stock = ?,
                                    list_price_kind = ?, brand = ?, stock_left = ?, recent_sales = ?,
                                    rating = ?, reviews = ?, badges = ?, seller = ?, grp = COALESCE(?, grp),
                                    last_seen_at = ?{", last_change_at = ?" if price_changed else ""}
                WHERE id = ?""",
            (*details, grp, now_s, *([now_s] if price_changed else []), product_id),
        )
        if not changed:
            return Recorded(product_id, "same", row["price"], bool(row["in_stock"]))
        result = Recorded(product_id, "changed" if price_changed else "same",
                          row["price"], bool(row["in_stock"]))

    conn.execute(
        """INSERT INTO price_history (product_id, price, list_price, in_stock, observed_at)
           VALUES (?, ?, ?, ?, ?)""",
        (product_id, listing.price, listing.list_price, listing.in_stock, now_s),
    )
    return result


def history(conn: sqlite3.Connection, product_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT price, list_price, in_stock, observed_at FROM price_history "
        "WHERE product_id = ? ORDER BY observed_at, id",
        (product_id,),
    ).fetchall()


def product(conn: sqlite3.Connection, product_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()


def find_product(conn: sqlite3.Connection, site: str, sku: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM products WHERE site = ? AND sku = ?", (site, sku)).fetchone()


def backup(conn: sqlite3.Connection, dest: Path) -> None:
    """Consistent online copy (safe while the coordinator is writing)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(dest)
    with target:
        conn.backup(target)
    target.close()
