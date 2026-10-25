#!/usr/bin/env bash
# Download a consistent snapshot of the server database to data/server.db,
# e.g. to browse it in IntelliJ's Database tool. Safe while the server is running.
set -euo pipefail
HOST="${1:-${DEALS_HOST:-root@207.154.206.127}}"
cd "$(dirname "$0")/.."
mkdir -p data

ssh "$HOST" 'set -e
rm -f /tmp/deals-snapshot.db
runuser -u deals -- /opt/deals/.venv/bin/python - <<PY
import sqlite3
src = sqlite3.connect("/var/lib/deals/deals.db", timeout=30)
dst = sqlite3.connect("/tmp/deals-snapshot.db")
src.backup(dst)                          # consistent copy, even mid-write
dst.execute("PRAGMA journal_mode=DELETE")  # a single self-contained file
dst.close()
PY'
scp -q -C "$HOST:/tmp/deals-snapshot.db" data/server.db.tmp
ssh "$HOST" rm -f /tmp/deals-snapshot.db
rm -f data/server.db-wal data/server.db-shm data/server.db-journal
mv data/server.db.tmp data/server.db
echo "data/server.db updated ($(du -h data/server.db | cut -f1)) — in IntelliJ: Database window → Refresh"
