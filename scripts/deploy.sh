#!/usr/bin/env bash
# Test, ship and restart everything on the server.  Usage: scripts/deploy.sh [user@host]
set -euo pipefail
HOST="${1:-${DEALS_HOST:-root@207.154.206.127}}"
cd "$(dirname "$0")/.."

if [ "${DEALS_SKIP_TESTS:-0}" = 1 ]; then
  echo "==> tests already run (DEALS_SKIP_TESTS=1)"
else
  echo "==> running tests"
  .venv/bin/pytest -q
fi

echo "==> syncing code to $HOST"
# which commit is live; `make health` prints it
echo "$(git rev-parse --short HEAD 2>/dev/null || echo unknown) $(git log -1 --format=%cI 2>/dev/null) deployed $(date -u +%FT%TZ) by ${GITHUB_ACTOR:-$USER}${GITHUB_RUN_ID:+ (GitHub Actions run $GITHUB_RUN_ID)}" > REVISION
rsync -az --delete \
  --exclude .venv --exclude data --exclude .git --exclude .github --exclude .idea --exclude '*.egg-info' \
  --exclude __pycache__ --exclude .pytest_cache --exclude config.toml --exclude worker.env \
  ./ "$HOST:/opt/deals/"

echo "==> installing and restarting on $HOST"
ssh "$HOST" 'bash /opt/deals/scripts/remote-install.sh'

echo "==> health check over the internet"
curl -fsS --retry 5 --retry-delay 2 "http://${HOST#*@}:8787/health" && echo
echo "deployed."
