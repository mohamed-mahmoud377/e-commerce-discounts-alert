#!/usr/bin/env bash
# Runs ON the server (called by scripts/deploy.sh). Safe to run any number of times.
set -euo pipefail
APP=/opt/deals
export PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
export DEBIAN_FRONTEND=noninteractive

id deals >/dev/null 2>&1 || useradd --system --home-dir /var/lib/deals --shell /usr/sbin/nologin deals
install -d -o deals -g deals /var/lib/deals /var/lib/deals/worker
install -d -m 750 -o root -g deals /etc/deals

for pkg in python3-venv sqlite3; do
  dpkg -s "$pkg" >/dev/null 2>&1 || apt-get install -y -qq "$pkg" >/dev/null
done
[ -x "$APP/.venv/bin/python" ] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q -e "$APP[tools]" 2>&1 | grep -v "notice" || true
# Chromium solves amazon.eg's WAF challenge; the install is skipped when already present.
"$APP/.venv/bin/python" -m playwright install --with-deps --only-shell chromium >/dev/null

if [ ! -f /etc/deals/config.toml ]; then
  echo "first install: creating /etc/deals/config.toml with fresh worker tokens"
  droplet_token=$("$APP/.venv/bin/deals" token)
  mac_token=$("$APP/.venv/bin/deals" token)
  sed -e 's|^host = .*|host = "0.0.0.0"|' \
      -e 's|^data_dir = .*|data_dir = "/var/lib/deals"|' \
      -e "s|^local = \"change-me\"|droplet = \"$droplet_token\"\nmac = \"$mac_token\"|" \
      "$APP/config.example.toml" > /etc/deals/config.toml
  cat > /etc/deals/worker.env <<ENV
DEALS_COORDINATOR=http://127.0.0.1:8787
DEALS_TOKEN=$droplet_token
DEALS_SITES=amazon_eg
DEALS_DATA_DIR=/var/lib/deals/worker
ENV
fi
chown root:deals /etc/deals/config.toml /etc/deals/worker.env
chmod 640 /etc/deals/config.toml /etc/deals/worker.env

install -m 644 "$APP"/deploy/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --quiet deals-coordinator deals-worker
# Stop the worker first so it never sees the coordinator restart (no reconnect warnings),
# then start it once the coordinator answers again.
systemctl stop deals-worker
systemctl restart deals-coordinator
for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:8787/health >/dev/null 2>&1 && break; sleep 1; done
systemctl start deals-worker

if command -v ufw >/dev/null; then
  ufw allow OpenSSH >/dev/null
  ufw allow 8787/tcp >/dev/null
  ufw --force enable >/dev/null
fi

for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8787/health >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS http://127.0.0.1:8787/health && echo
systemctl is-active deals-coordinator deals-worker
