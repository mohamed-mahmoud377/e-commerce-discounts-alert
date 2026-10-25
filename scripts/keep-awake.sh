#!/usr/bin/env bash
# Keep this Mac from sleeping while it is plugged in, so the worker keeps crawling.
# The screen still turns off; on battery the Mac sleeps normally. Closing the lid
# still sleeps (unless an external display is attached).
#   scripts/keep-awake.sh            turn on (starts now and at every login)
#   scripts/keep-awake.sh --off      turn off
set -euo pipefail
LABEL=com.deals.awake
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--off" ]; then
  rm -f "$PLIST"; echo "keep-awake off: the Mac sleeps normally again"; exit 0
fi
mkdir -p "$(dirname "$PLIST")"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/usr/bin/caffeinate</string><string>-s</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
PLIST
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "keep-awake on: no sleep while plugged in (screen can still turn off). Undo: make allow-sleep"
