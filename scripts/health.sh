#!/usr/bin/env bash
# One command to know whether everything works:  make health
# Server services + logs, deployed code vs this checkout, the Mac worker, then
# `deals health` on the server (database, workers, crawling, alerts, disk, backups).
set -uo pipefail
HOST="${1:-${DEALS_HOST:-root@207.154.206.127}}"
cd "$(dirname "$0")/.."
problems=0
ok()   { echo "✅ $*"; }
warn() { echo "⚠️  $*"; }
bad()  { echo "❌ $*"; problems=$((problems + 1)); }

echo "== server ($HOST)"
remote=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" '
  for s in deals-coordinator deals-worker; do
    echo "svc $s $(systemctl is-active $s) $(systemctl show $s -p NRestarts --value)"
  done
  echo "errors $(journalctl -u deals-coordinator -u deals-worker --since -24h --no-pager -o cat | grep -cE "ERROR|Traceback")"
  cd /opt/deals && echo "code $(find src scripts deploy pyproject.toml -type f \( -name "*.py" -o -name "*.sh" -o -name "*.service" -o -name "*.toml" \) | sort | xargs sha1sum | sha1sum | cut -c1-12)"
' 2>&1) || { bad "cannot reach the server: $remote"; echo; exit 1; }
while read -r kind a b c; do
  case "$kind" in
    svc) [ "$b" = active ] && ok "$a running (restarted $c times)" || bad "$a is $b" ;;
    errors) [ "$a" = 0 ] && ok "no errors in server logs (24h)" || warn "$a error lines in server logs (24h): make logs" ;;
    code) local_hash=$(find src scripts deploy pyproject.toml -type f \( -name "*.py" -o -name "*.sh" -o -name "*.service" -o -name "*.toml" \) | sort | xargs shasum | shasum | cut -c1-12)
          [ "$a" = "$local_hash" ] && ok "server runs the same code as this checkout" || warn "server code differs from this checkout: make deploy" ;;
  esac
done <<< "$remote"
[ -z "$(git status --porcelain)" ] && ok "git: everything committed" || warn "git: uncommitted changes"

echo
echo "== this Mac's worker"
if launchctl print "gui/$(id -u)/com.deals.worker" 2>/dev/null | grep -q "state = running"; then
  ok "worker running (launchd)"
  errors=$(tail -n 2000 ~/Library/Logs/deals-worker.log 2>/dev/null | grep -cE "ERROR|Traceback")
  [ "$errors" = 0 ] && ok "no errors in its recent log" || warn "$errors error lines in ~/Library/Logs/deals-worker.log"
else
  warn "worker not running on this Mac (make install-mac-worker), so noon has no worker unless another machine runs one"
fi

echo
echo "== system checks (on the server)"
ssh -o BatchMode=yes "$HOST" runuser -u deals -- /opt/deals/.venv/bin/deals health --config /etc/deals/config.toml || problems=$((problems + 1))

echo
[ "$problems" = 0 ] && echo "🟢 healthy" || echo "🔴 $problems problem(s) above"
exit $(( problems > 0 ))
