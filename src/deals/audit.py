"""The error book: every problem a device or the server meets, grouped by kind, with its likely
cause and what to do about it.

Fed from two places: every WARNING/ERROR log line (each worker ships its log to the coordinator, and
the coordinator's own lines go the same way, `scheduler.add_events`), and the workers' failed/blocked
job reports, which carry the real reason. Read in the web UI (Errors), `deals errors`, `make errors`.

One row per fingerprint: who (device or 'coordinator'), shop, category, and the message with its
numbers, addresses and links blanked, so "page 3 attempt 2 failed after 30002 ms" and "page 5 attempt
1 failed after 30001 ms" count as one problem. The row keeps the latest message in full.
"""

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta

from deals import shops
from deals.db import iso
from deals.shops import SHOPS

# category: (title, likely cause, what to do). English: the web UI and the CLI show them.
CATEGORIES: dict[str, tuple[str, str, str]] = {
    "blocked": ("Blocked by the shop",
                "The shop's bot protection (Cloudflare, Akamai, a captcha or a decoy page) refused this device's IP.",
                "Automatic: that device leaves the shop alone for 15 min, doubling up to 24 h, while others carry on. "
                "If it keeps coming back, crawl the shop slower (sites.<shop> delays) or from another device."),
    "pages_refused": ("Product page refused",
                      "The shop refused a product-page read (the slow background reading used for matching).",
                      "Automatic: page reading pauses for that shop and device. Crawling is not affected."),
    "parser": ("Could not read the page",
               "The page came back but held no products where we look for them: the shop changed its page or "
               "API, or answered with an error page.",
               "Open the shop's page in a browser. If it changed, save it to tests/fixtures and fix the parser "
               "(docs/<SHOP>.md). If it happens only now and then, it is the shop's own error page."),
    "config": ("Category not found",
               "The category, collection or search in config.toml no longer exists on the shop (moved, renamed, "
               "or the id is wrong).",
               "Find the new id with `deals categories <shop>` and fix the watch in /etc/deals/config.toml "
               "(docs/CATEGORIES.md)."),
    "timeout": ("Timed out",
                "The shop, or the device's internet, did not answer within 30 s.",
                "Retried automatically. Many timeouts on one shop from one device are often quiet throttling: "
                "slow that shop down."),
    "network": ("Network error",
                "The device's internet failed (no DNS, connection reset or refused) before the shop answered.",
                "Retried automatically. If it is one device only, check its internet or whether it slept."),
    "server_unreachable": ("Server unreachable",
                           "The device could not reach the coordinator for over a minute: the server was restarting "
                           "or overloaded, or the device's internet was down.",
                           "Nothing is lost: jobs are offered again. If it lasts, run `make health` and check the "
                           "droplet's memory (`make logs`)."),
    "http": ("Shop answered with an error",
             "The shop answered with an HTTP error status (4xx/5xx) that is not a known block.",
             "Usually the shop's own trouble and passes; if it lasts, open the URL in a browser."),
    "telegram": ("Telegram",
                 "Sending a message or photo to Telegram failed.",
                 "Retried automatically. If it lasts, check the bot token and that the bot is not blocked."),
    "screenshot": ("Screenshot",
                   "An alert's screenshot could not be taken or delivered.",
                   "The alert goes out as text without it. If every shot fails, run `deals shot <shop> <url>` on "
                   "the device."),
    "login": ("Failed web UI login",
              "Someone typed a wrong username, password or token at /ui/login or /api/login.",
              "One now and then is a typo. Many in a row is someone guessing (10 in 15 minutes lock that address out "
              "for a while): change the admins' passwords (deals passwd) and [server] admin_token."),
    "bug": ("Bug",
            "The code raised an error it did not expect.",
            "Read the full line (Logs tab, same time) and fix the code."),
    "other": ("Other", "A warning that matches none of the known kinds.", "Read the message."),
}

# For the daily report (Arabic).
CATEGORY_AR = {"blocked": "حظر", "pages_refused": "رفض صفحات المنتجات", "parser": "قراءة الصفحة",
               "config": "قسم غير موجود", "timeout": "انتهاء المهلة", "network": "الشبكة",
               "server_unreachable": "تعذّر الوصول للخادم", "http": "خطأ من المتجر", "telegram": "تيليجرام",
               "screenshot": "لقطة الشاشة", "login": "دخول خاطئ", "bug": "خطأ برمجي", "other": "أخرى"}

# First match wins, so the specific kinds come before the general ones.
RULES: list[tuple[str, re.Pattern]] = [(cat, re.compile(rx, re.I)) for cat, rx in [
    ("server_unreachable", r"coordinator (unreachable|HTTP)"),
    ("telegram", r"telegram|alert delivery"),
    ("screenshot", r"screenshot|\bshot\b"),
    ("login", r"failed login"),
    ("pages_refused", r"product page refused"),
    ("config", r"UnknownTarget|no such page|no category|no collection|redirected to|unknown category|"
               r"no server-side search|no deals feed|category ids are"),
    ("blocked", r"\bBlocked\b|blocked:|challenge|captcha|decoy page|guest token|lists nothing here|"
                r"HTTP 40[13]|HTTP 429|access denied"),
    ("parser", r"layout|no shopData|no __STORE__|no listing data|products parsed|API changed|GraphQL error|"
               r"JSONDecodeError|unbalanced|Store API refused"),
    ("timeout", r"timed out|timeout|curl: \(28\)"),
    ("network", r"resolve host|\bDNS\b|curl: \((6|7|16|35|52|55|56)\)|connection (reset|refused|aborted|closed)|"
                r"recv failure|ConnectionError|network is unreachable|\bSSL\b"),
    ("http", r"HTTP (Error )?[45]\d\d"),
    ("bug", r"Error\b|Exception|Traceback"),
]]

# Worker log lines that repeat a job report the coordinator records itself, with the reason
# (`job 935 failed`, `blocked: ...`): counted once, from the report.
_REPORTED = re.compile(r"^(job \d+ failed|blocked: )")
_PREFIX = re.compile(r"^\[(\w+)\] ")
_CURL_HINT = re.compile(r"\s*See https://curl\.se/\S+ first for more details\.")


def categorize(message: str) -> str:
    return next((cat for cat, rx in RULES if rx.search(message)), "other")


def shape(message: str) -> str:
    """The message with what changes between occurrences blanked: the part that names the problem."""
    m = re.sub(r"https?://\S+", "<url>", message)
    m = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<ip>", m)
    m = re.sub(r"\d{4}-\d\d-\d\d[T ][\d:.]+(?:[+-]\d\d:\d\d|Z)?", "<time>", m)
    m = re.sub(r"\d+", "#", m)
    return m[:200]


def find_site(message: str) -> str | None:
    return next((s for s in SHOPS if s in message), None)


def record(conn: sqlite3.Connection, source: str, level: str, message: str, at: str, *,
           site: str | None = None, category: str | None = None) -> None:
    """Count one problem (the caller commits)."""
    message = _CURL_HINT.sub("", message).strip()
    if m := _PREFIX.match(message):  # a worker's per-shop thread: '[jumia_eg] ...'
        site, message = site or (m.group(1) if m.group(1) in SHOPS else None), message[m.end():]
    category = category or categorize(message)
    # the server is the same for every shop thread of a device: one row per device
    site = None if category == "server_unreachable" else site or find_site(message)
    level = "ERROR" if level.upper() in ("ERROR", "CRITICAL") else "WARNING"
    key = hashlib.sha1(f"{source}|{site}|{category}|{shape(message)}".encode()).hexdigest()[:20]
    conn.execute(
        """INSERT INTO errors (fingerprint, category, source, site, level, message, count, first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
           ON CONFLICT (fingerprint) DO UPDATE SET count = count + 1,
               level = CASE WHEN excluded.level = 'ERROR' THEN 'ERROR' ELSE level END,
               message = CASE WHEN excluded.last_seen_at >= last_seen_at THEN excluded.message ELSE message END,
               first_seen_at = MIN(first_seen_at, excluded.first_seen_at),
               last_seen_at = MAX(last_seen_at, excluded.last_seen_at)""",
        (key, category, source, site, level, message[:1000], at, at))


def record_events(conn: sqlite3.Connection, source: str, events: list[tuple[str, str, str]]) -> None:
    """The WARNING and ERROR lines of a batch of log lines: (at, level, message)."""
    for at, level, message in events:
        if level.upper() not in ("WARNING", "ERROR", "CRITICAL"):
            continue
        if source != "coordinator" and _REPORTED.match(_PREFIX.sub("", message)):
            continue
        record(conn, source, level, message, at)


def backfill(conn: sqlite3.Connection) -> int:
    """A new (empty) error book starts from the problems already in the logs (the last 7 days).
    Old lines have no job reports beside them, so the workers' own lines count here."""
    if conn.execute("SELECT 1 FROM errors LIMIT 1").fetchone():
        return 0
    n = 0
    with conn:
        for r in conn.execute("""SELECT at, source, level, message FROM events
                                 WHERE level IN ('WARNING', 'ERROR', 'CRITICAL') ORDER BY id""").fetchall():
            if r["source"] == "coordinator" and " blocked on " in r["message"]:
                continue  # the coordinator's echo of a worker's block
            record(conn, r["source"], r["level"], r["message"], r["at"])
            n += 1
    return n


def resolve(conn: sqlite3.Connection, error_id: int, now: datetime, *, reopen: bool = False) -> bool:
    with conn:
        return conn.execute("UPDATE errors SET resolved_at = ? WHERE id = ?",
                            (None if reopen else iso(now), error_id)).rowcount > 0


def listing(conn: sqlite3.Connection, now: datetime, *, days: float = 7, site: str | None = None,
            category: str | None = None, source: str | None = None, open_only: bool = False,
            limit: int = 500) -> list[dict]:
    """Problems seen in the last `days`, the most recent first, each with its cause and fix. `site` is a
    shop or 'kind:<kind>' (shops.expand)."""
    sites = shops.expand(site)
    site = json.dumps(sites) if sites is not None else None
    rows = conn.execute(
        """SELECT *, (resolved_at IS NULL OR last_seen_at > resolved_at) AS open FROM errors
           WHERE last_seen_at >= ? AND (? IS NULL OR site IN (SELECT value FROM json_each(?)))
             AND (? IS NULL OR category = ?)
             AND (? IS NULL OR source = ?) AND (NOT ? OR resolved_at IS NULL OR last_seen_at > resolved_at)
           ORDER BY last_seen_at DESC LIMIT ?""",
        (iso(now - timedelta(days=days)), site, site, category, category, source, source, int(open_only),
         limit)).fetchall()
    out = []
    for r in rows:
        title, cause, fix = CATEGORIES.get(r["category"], CATEGORIES["other"])
        out.append({**dict(r), "open": bool(r["open"]), "title": title, "cause": cause, "fix": fix})
    return out


def prune(conn: sqlite3.Connection, now: datetime, keep_days: int = 90) -> None:
    conn.execute("DELETE FROM errors WHERE last_seen_at < ?", (iso(now - timedelta(days=keep_days)),))
