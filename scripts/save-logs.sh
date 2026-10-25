#!/usr/bin/env bash
# Download every log for later analysis into ./logs/<UTC time>/  (make save-logs)
#   events.jsonl          all log lines still in the database (every device + coordinator, last 7 days)
#   archive/*.jsonl.gz    daily archives the server keeps for 90 days
#   journal.log           the server's systemd journal for both services (last 7 days)
#   mac-worker.log        this Mac's worker log file
#   summary.txt           counts by device and level, and every warning/error
set -euo pipefail
HOST="${1:-${DEALS_HOST:-root@207.154.206.127}}"
cd "$(dirname "$0")/.."
out="logs/$(date -u +%Y-%m-%d_%H%M)"
mkdir -p "$out/archive"

ssh "$HOST" 'set -e; tmp=$(mktemp -d); chown deals "$tmp"
runuser -u deals -- /opt/deals/.venv/bin/python - "$tmp/events.jsonl" <<PY
import json, sqlite3, sys
conn = sqlite3.connect("file:/var/lib/deals/deals.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
with open(sys.argv[1], "w") as out:
    for r in conn.execute("SELECT id, at, source, level, message FROM events ORDER BY id"):
        out.write(json.dumps(dict(r), ensure_ascii=False) + "\n")
PY
journalctl -u deals-coordinator -u deals-worker --since -7d --no-pager -o short-iso > "$tmp/journal.log"
cp /var/lib/deals/logs/*.jsonl.gz "$tmp/" 2>/dev/null || true
tar -C "$tmp" -czf - . ; rm -rf "$tmp"' | tar -C "$out" -xzf -
mv "$out"/*.jsonl.gz "$out/archive/" 2>/dev/null || true
cp ~/Library/Logs/deals-worker.log "$out/mac-worker.log" 2>/dev/null || true

python3 - "$out" <<'PY'
import collections, json, sys
from pathlib import Path
out = Path(sys.argv[1])
rows = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines() if line.strip()]
by = collections.Counter((r["source"], r["level"]) for r in rows)
lines = [f"{len(rows)} log lines from {rows[0]['at'][:16] if rows else '-'} to {rows[-1]['at'][:16] if rows else '-'} UTC", ""]
lines += [f"{n:6}  {src:<12} {lvl}" for (src, lvl), n in sorted(by.items())]
problems = [r for r in rows if r["level"] in ("WARNING", "ERROR")]
lines += ["", f"{len(problems)} warnings/errors:"] + [f"{r['at'][:19]}  {r['source']:<12} {r['level']:<7} {r['message']}" for r in problems]
(out / "summary.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines[:12]))
PY
echo
echo "saved to $out:"; ls -1 "$out" "$out/archive" | sed 's/^/  /'
