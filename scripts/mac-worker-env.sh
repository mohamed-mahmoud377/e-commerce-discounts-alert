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
# Every shop (deals/shops.py; tests/test_shops.py checks this line lists them all).
DEALS_SITES=noon_eg,amazon_eg,jumia_eg,cairosales_eg,2b_eg,btech_eg,dream2000_eg,carrefour_eg,dubaiphone_eg,hardwaremarket_eg,amazon_now_eg
DEALS_DATA_DIR=$PWD/data
DEALS_SCREENSHOTS=1
ENV
chmod 600 worker.env
echo "wrote worker.env (sites: $(sed -n 's/^DEALS_SITES=//p' worker.env))"
