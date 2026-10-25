#!/usr/bin/env bash
# Test, ship and restart everything on the server.  Usage: scripts/deploy.sh [user@host]
set -euo pipefail
HOST="${1:-${DEALS_HOST:-root@207.154.206.127}}"
cd "$(dirname "$0")/.."

echo "==> running tests"
.venv/bin/pytest -q

echo "==> syncing code to $HOST"
rsync -az --delete \
  --exclude .venv --exclude data --exclude .git --exclude .idea --exclude '*.egg-info' \
  --exclude __pycache__ --exclude .pytest_cache --exclude config.toml --exclude worker.env \
  ./ "$HOST:/opt/deals/"

echo "==> installing and restarting on $HOST"
ssh "$HOST" 'bash /opt/deals/scripts/remote-install.sh'

echo "==> health check over the internet"
curl -fsS --retry 5 --retry-delay 2 "http://${HOST#*@}:8787/health" && echo
echo "deployed."
