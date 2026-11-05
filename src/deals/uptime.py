"""What is up and what is down: every shop, every device, and each device on each of its shops,
checked every minute by the watchdog.

`status` holds the current state of each; every change goes to `status_changes` (when, why, and how
long the old state lasted), so outages can be read back later (web UI → Uptime, `deals uptime`).
Telegram tells the admins when a shop stalls or a device goes silent, and tells them again when it is
back. A device on a shop is announced only when it comes back from a block the admins were warned
about (3 strikes in a row, coordinator.blocked). A gap in the watchdog's own rounds means the server
itself was down; it is recorded and announced when it is back.
"""

import html
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from deals import alerts, scheduler, shop_settings
from deals.alerts import SITE_NAME
from deals.db import iso, parse_iso

OFFLINE_AFTER = timedelta(minutes=30)  # a device silent this long is offline
SHOP_OVERDUE = timedelta(minutes=45)   # a shop is down when its work is this late ...
SHOP_IDLE = timedelta(hours=1)         # ... and nothing finished for this long
FAILING_FOR = timedelta(hours=1)       # a device's last page on a shop failed within this
SERVER_GAP = timedelta(minutes=10)     # no watchdog round for this long: the server was down
SETTLE = timedelta(minutes=10)         # after a start, workers need a moment to reconnect
DOWN_COOLDOWN = timedelta(hours=1)     # a flapping shop or device is announced at most hourly
GOOD = {"up", "online", "ok", "paused"}
KEEP = timedelta(days=180)


@dataclass(frozen=True)
class Seen:
    subject: str
    kind: str      # shop | device | device_shop
    name: str
    state: str
    reason: str    # English, for the web UI and the CLI
    down_key: str | None = None   # system alert key; with down_ar, going bad is told on Telegram
    down_ar: str | None = None


def observe(conn: sqlite3.Connection, now: datetime) -> list[Seen]:
    """The state of everything right now."""
    out: list[Seen] = []
    for row in conn.execute("""SELECT site, MIN(next_due_at) oldest_due, MAX(last_done_at) last_done
                               FROM jobs GROUP BY site""").fetchall():
        site, name_ar = row["site"], SITE_NAME.get(row["site"], row["site"])
        overdue = now - parse_iso(row["oldest_due"])
        last_done = parse_iso(row["last_done"]) if row["last_done"] else None
        done = f"last slice finished {_ago(last_done, now)}" if last_done else "no slice finished yet"
        subject = f"shop:{site}"
        if hold := scheduler.site_paused(conn, site, now):
            out.append(Seen(subject, "shop", site, "paused", scheduler.hold_text(hold)))
        elif overdue > SHOP_OVERDUE and (last_done is None or now - last_done > SHOP_IDLE):
            blocks = conn.execute("SELECT ip, until FROM blocks WHERE site = ? AND until > ?",
                                  (site, iso(now))).fetchall()
            blocked_en = ", ".join(f"{b['ip']} until {b['until'][11:16]}" for b in blocks)
            blocked_ar = "، ".join(f"{b['ip']} حتى {b['until'][11:16]}" for b in blocks) or "لا يوجد"
            out.append(Seen(
                subject, "shop", site, "down",
                f"{done}; work {overdue.total_seconds() / 3600:.1f} h overdue"
                + (f"; blocked: {blocked_en}" if blocked_en else "; no device is crawling it"),
                f"stalled:{site}",
                f"{name_ar} متوقف: العمل متأخر {overdue.total_seconds() / 3600:.1f} ساعة ولم يكتمل شيء خلال "
                f"الساعة الأخيرة. الأجهزة المحظورة: {blocked_ar}. هل يوجد جهاز (worker) شغّال لهذا الموقع؟ "
                f"(مثلًا الماك نائم)"))
        else:
            out.append(Seen(subject, "shop", site, "up", done))

    for w in conn.execute("SELECT * FROM workers ORDER BY name").fetchall():
        name, seen = w["name"], parse_iso(w["last_seen_at"])
        online = now - seen < OFFLINE_AFTER
        out.append(Seen(f"device:{name}", "device", name, "online" if online else "offline",
                        f"last seen {_ago(seen, now)} from {w['last_ip']}", f"offline:{name}",
                        f"الجهاز {name} لم يتواصل منذ {ar_duration((now - seen).total_seconds())} "
                        f"(نائم، مغلق، أو بلا إنترنت؟)"))
        for site in filter(None, (w["sites"] or "").split(",")):
            subject = f"device_shop:{name}/{site}"
            if not online:
                out.append(Seen(subject, "device_shop", f"{name}/{site}", "offline", "the device is offline"))
                continue
            if reason := shop_settings.stopped(conn, name, site):
                out.append(Seen(subject, "device_shop", f"{name}/{site}", "paused", reason))
                continue
            # A block lasts until a page from that IP succeeds (scheduler.unblock), not until its pause ends.
            block = conn.execute("SELECT * FROM blocks WHERE site = ? AND ip = ?", (site, w["last_ip"])).fetchone()
            ws = conn.execute("SELECT * FROM worker_sites WHERE worker = ? AND site = ?", (name, site)).fetchone()
            last_ok = parse_iso(ws["last_ok_at"]) if ws and ws["last_ok_at"] else None
            last_err = parse_iso(ws["last_error_at"]) if ws and ws["last_error_at"] else None
            if block:
                waiting = (f"paused until {block['until'][11:16]} UTC" if block["until"] > iso(now)
                           else "waiting for its next try")
                out.append(Seen(subject, "device_shop", f"{name}/{site}", "blocked",
                                f"strike {block['strikes']}, {waiting}: {block['reason']}"))
            elif last_err and (not last_ok or last_err > last_ok) and now - last_err < FAILING_FOR:
                out.append(Seen(subject, "device_shop", f"{name}/{site}", "failing", ws["last_error"] or "failing"))
            else:
                out.append(Seen(subject, "device_shop", f"{name}/{site}", "ok",
                                f"last page {_ago(last_ok, now)}" if last_ok else "no page yet"))
    return out


def track(conn: sqlite3.Connection, now: datetime, *, settling: bool = False) -> list[str]:
    """One watchdog round: compare with the last known states, record and announce the changes.
    While `settling` (just after a start) nothing is marked bad yet. Returns the changed subjects."""
    changed = []
    with conn:
        _server_gap(conn, now)
        known = {r["subject"]: r for r in conn.execute("SELECT * FROM status").fetchall()}
        seen = observe(conn, now)
        for s in seen:
            old = known.get(s.subject)
            if old is None:  # first sight: recorded quietly (the old watchdog already told about it)
                conn.execute("""INSERT INTO status (subject, kind, name, state, since, reason, checked_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                             (s.subject, s.kind, s.name, s.state, iso(now), s.reason, iso(now)))
                continue
            if old["state"] == s.state or (settling and s.state not in GOOD):
                conn.execute("UPDATE status SET reason = ?, checked_at = ? WHERE subject = ?",
                             (s.reason if old["state"] == s.state else old["reason"], iso(now), s.subject))
                continue
            changed.append(s.subject)
            notified = _announce(conn, old, s, now)
            conn.execute("""INSERT INTO status_changes (subject, kind, name, old_state, new_state, at, lasted_s,
                                                        reason, notified) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (s.subject, s.kind, s.name, old["state"], s.state, iso(now),
                          int((now - parse_iso(old["since"])).total_seconds()), s.reason, int(notified)))
            # bad -> bad (blocked -> offline) keeps the promise to say when it is back
            keep = bool(old["notified"]) and old["state"] not in GOOD and s.state not in GOOD
            conn.execute("""UPDATE status SET state = ?, since = ?, reason = ?, notified = ?, checked_at = ?
                            WHERE subject = ?""",
                         (s.state, iso(now), s.reason, int(notified or keep), iso(now), s.subject))
        gone = set(known) - {s.subject for s in seen}  # a shop left the config, a device dropped a shop
        conn.executemany("DELETE FROM status WHERE subject = ?", [(g,) for g in gone])
    return changed


def _announce(conn: sqlite3.Connection, old: sqlite3.Row, s: Seen, now: datetime) -> bool:
    """Tell Telegram about a change if it should know. True when the message is queued."""
    if s.state not in GOOD:  # going bad (or from one bad state to another)
        if old["state"] in GOOD and s.down_key and s.down_ar:
            return alerts.system_alert(conn, s.down_key, f"🔴 {_e(s.down_ar)}", now, DOWN_COOLDOWN, raw_html=True)
        return False
    if old["state"] in GOOD or not old["notified"] or s.state == "paused":
        return False  # nobody was told it went bad (or it was paused by hand meanwhile)
    took = ar_duration((now - parse_iso(old["since"])).total_seconds())
    if s.kind == "shop":
        text = f"{SITE_NAME.get(s.name, s.name)} عاد للعمل: اكتمل جمع جديد بعد توقف دام {took}."
    elif s.kind == "device":
        text = f"الجهاز {s.name} عاد للاتصال بعد انقطاع دام {took}."
    else:
        worker, site = s.name.split("/", 1)
        text = f"{SITE_NAME.get(site, site)} لم يعد يحظر الجهاز {worker}: نجح الجمع مرة أخرى بعد {took}."
    return alerts.system_alert(conn, f"back:{s.subject}:{iso(now)}", f"✅ {_e(text)}", now, timedelta(0), raw_html=True)


def mark_notified(conn: sqlite3.Connection, subject: str) -> None:
    """The admins were warned about this subject elsewhere (a 3-strike block): tell them when it is back."""
    conn.execute("UPDATE status SET notified = 1 WHERE subject = ? AND state NOT IN ('up', 'online', 'ok', 'paused')",
                 (subject,))


def _server_gap(conn: sqlite3.Connection, now: datetime) -> None:
    """A long gap since the last round means the coordinator itself was down: record it, say it is back."""
    row = conn.execute("SELECT value FROM kv WHERE key = 'uptime:last_round'").fetchone()
    conn.execute("INSERT INTO kv (key, value) VALUES ('uptime:last_round', ?) "
                 "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (iso(now),))
    if row is None or now - parse_iso(row["value"]) < SERVER_GAP:
        return
    gap = (now - parse_iso(row["value"])).total_seconds()
    notified = alerts.system_alert(
        conn, f"back:server:{iso(now)}",
        f"✅ {_e(f'الخادم عاد للعمل بعد توقف دام {ar_duration(gap)}. لم يُراقَب أي شيء ولم تُرسل عروض خلال هذه المدة.')}",
        now, timedelta(0), raw_html=True)
    conn.execute("""INSERT INTO status_changes (subject, kind, name, old_state, new_state, at, lasted_s, reason, notified)
                    VALUES ('server', 'server', 'coordinator', 'down', 'up', ?, ?, ?, ?)""",
                 (iso(now), int(gap), f"no watchdog round since {row['value'][:16]} UTC: the server was stopped "
                                      f"or crashed", int(notified)))


def current(conn: sqlite3.Connection, now: datetime) -> list[dict]:
    """Everything, what is bad first."""
    rows = conn.execute("""SELECT * FROM status ORDER BY state IN ('up', 'online', 'ok', 'paused'),
                           CASE kind WHEN 'shop' THEN 0 WHEN 'device' THEN 1 ELSE 2 END, name""").fetchall()
    return [{**dict(r), "good": r["state"] in GOOD, "for_s": int((now - parse_iso(r["since"])).total_seconds())}
            for r in rows]


def changes(conn: sqlite3.Connection, now: datetime, *, days: float = 7, kind: str | None = None,
            name: str | None = None, limit: int = 500) -> list[dict]:
    rows = conn.execute("""SELECT * FROM status_changes WHERE at >= ? AND (? IS NULL OR kind = ?)
                           AND (? IS NULL OR name = ? OR name LIKE ? || '/%' OR name LIKE '%/' || ?)
                           ORDER BY at DESC, id DESC LIMIT ?""",
                        (iso(now - timedelta(days=days)), kind, kind, name, name, name, name, limit)).fetchall()
    return [{**dict(r), "good": r["new_state"] in GOOD} for r in rows]


def outages(conn: sqlite3.Connection, now: datetime, hours: float = 24) -> list[dict]:
    """Per shop, device or server: how often it went down in the window and for how long in total
    (an outage still going on counts up to now). Device-on-shop blocks are left out: they are routine."""
    since = now - timedelta(hours=hours)
    out: dict[str, dict] = {}
    for c in conn.execute("""SELECT * FROM status_changes WHERE at >= ? AND kind IN ('shop', 'device', 'server')
                             ORDER BY at""", (iso(since),)).fetchall():
        o = out.setdefault(c["subject"], {"subject": c["subject"], "kind": c["kind"], "name": c["name"],
                                          "times": 0, "down_s": 0, "ongoing": False})
        if c["kind"] == "server":
            o["times"] += 1
            o["down_s"] += c["lasted_s"] or 0
        elif c["new_state"] not in GOOD and c["old_state"] in GOOD:
            o["times"] += 1
        elif c["old_state"] not in GOOD and c["new_state"] in GOOD:
            o["down_s"] += min(c["lasted_s"] or 0, int((parse_iso(c["at"]) - since).total_seconds()))
    for r in conn.execute("SELECT * FROM status WHERE kind IN ('shop', 'device') AND state NOT IN "
                          "('up', 'online', 'ok', 'paused')").fetchall():
        o = out.setdefault(r["subject"], {"subject": r["subject"], "kind": r["kind"], "name": r["name"],
                                          "times": 0, "down_s": 0, "ongoing": False})
        o["ongoing"], o["times"] = True, max(o["times"], 1)  # (down since before the window)
        o["down_s"] += int((now - max(parse_iso(r["since"]), since)).total_seconds())
    return sorted(out.values(), key=lambda o: -o["down_s"])


def prune(conn: sqlite3.Connection, now: datetime) -> None:
    conn.execute("DELETE FROM status_changes WHERE at < ?", (iso(now - KEEP),))


def _e(text: str) -> str:
    return html.escape(text, quote=False)


def _ago(t: datetime | None, now: datetime) -> str:
    if t is None:
        return "never"
    s = (now - t).total_seconds()
    return f"{s:.0f} s ago" if s < 120 else f"{s / 60:.0f} min ago" if s < 7200 else f"{s / 3600:.1f} h ago"


def ar_duration(seconds: float) -> str:
    m = seconds / 60
    if m < 90:
        return f"{max(1, round(m))} دقيقة"
    h = m / 60
    return f"{h:.1f} ساعة" if h < 48 else f"{h / 24:.1f} يوم"

