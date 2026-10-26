#!/usr/bin/env bash
# Writes ./worker.env for this Mac using the 'mac' token from the server's config.
set -euo pipefail
HOST="${1:-${DEALS_HOST:-root@207.154.206.127}}"
cd "$(dirname "$0")/.."
token=$(ssh "$HOST" "sed -n 's/^mac = \"\\(.*\\)\"/\\1/p' /etc/deals/config.toml")
[ -n "$token" ] || { echo "no 'mac' token in /etc/deals/config.toml on $HOST"; exit 1; }
cat > worker.env <<ENV
DEALS_COORDINATOR=http://${HOST#*@}:8787
DEALS_TOKEN=$token
DEALS_SITES=noon_eg,amazon_eg,jumia_eg
DEALS_DATA_DIR=$PWD/data
ENV
chmod 600 worker.env
echo "wrote worker.env (sites: noon_eg, amazon_eg, jumia_eg)"
