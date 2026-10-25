#!/usr/bin/env bash
# Connect Telegram alerts on the server in one go.
#   1. create a bot with @BotFather and copy its token
#   2. open the bot in Telegram and press Start
#   3. scripts/telegram-setup.sh <bot token> [user@host]
# Saves the token, finds your chat id, sends a test message, restarts the coordinator.
set -euo pipefail
TOKEN="${1:?usage: scripts/telegram-setup.sh <bot token from @BotFather> [user@host]}"
HOST="${2:-${DEALS_HOST:-root@207.154.206.127}}"

ssh "$HOST" bash -s -- "$TOKEN" <<'REMOTE'
set -euo pipefail
TOKEN="$1"
CFG=/etc/deals/config.toml
DEALS="runuser -u deals -- /opt/deals/.venv/bin/deals"

sed -i "s|^bot_token = .*|bot_token = \"$TOKEN\"|" "$CFG"
if ! out=$($DEALS telegram --config "$CFG" 2>&1); then
  echo "Telegram rejected the token. Copy it again from @BotFather (looks like 123456789:AA...)."
  exit 1
fi
chat=$(echo "$out" | sed -n 's/^chat_id = "\(-\{0,1\}[0-9]*\)".*/\1/p' | head -1)
if [ -z "$chat" ]; then
  echo "Token saved, but the bot has no messages yet."
  echo "Open your bot in Telegram, press Start (or send 'hi'), then run this again."
  exit 1
fi
sed -i "s|^chat_id = .*|chat_id = \"$chat\"|" "$CFG"
$DEALS telegram --test --config "$CFG"
systemctl restart deals-coordinator
echo "Telegram connected (chat $chat). Alerts will arrive in your bot chat."
REMOTE
