#!/usr/bin/env bash
# Runs the worker in the background on this Mac, starting at login and restarting
# if it crashes. Remove with:  scripts/install-mac-worker.sh --uninstall
set -euo pipefail
cd "$(dirname "$0")/.."
LABEL=com.deals.worker
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/deals-worker.log"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$PLIST"; echo "worker removed"; exit 0
fi
[ -f worker.env ] || { echo "worker.env missing: run scripts/mac-worker-env.sh first"; exit 1; }

env_xml=""
while IFS='=' read -r key value; do
  [ -n "$key" ] && env_xml+="    <key>$key</key><string>$value</string>"$'\n'
done < worker.env

mkdir -p "$(dirname "$PLIST")"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PWD/.venv/bin/deals</string><string>worker</string></array>
  <key>EnvironmentVariables</key>
  <dict>
$env_xml  </dict>
  <key>WorkingDirectory</key><string>$PWD</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "worker installed; logs: tail -f $LOG"
