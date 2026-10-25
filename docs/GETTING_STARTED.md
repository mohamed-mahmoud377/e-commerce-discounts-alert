# Getting started

This repository tracks prices on amazon.eg and noon.com (Egypt) and sends Arabic Telegram alerts when a
price is genuinely low compared with that product's own history. Pick the path that matches you:

| You want to… | Path | Time |
| --- | --- | --- |
| **Get the deal alerts** | Open the Telegram bot and press Start; the owner approves you | 1 minute, no install |
| **Help crawl for an existing server** (you were given a worker token) | [A. Run a worker](#a-run-a-worker-for-an-existing-server) | 10 minutes |
| **Try the whole thing on one computer** | [B. Everything on one computer](#b-everything-on-one-computer) | 10 minutes |
| **Run your own server** (like the original setup) | [C. Your own server](#c-your-own-server) | 30 minutes |

Background reading once it runs: [GUIDE.md](GUIDE.md) (how it works), [COMMANDS.md](COMMANDS.md) (every
command), [MONITORING.md](MONITORING.md) (health and troubleshooting).

## What you need

- **Python 3.12 or newer**: `python3 --version`. On macOS: `brew install python@3.13`; on Ubuntu 24.04 it is
  already there (`sudo apt install python3-venv` if `venv` is missing).
- **git**, and about 500 MB of disk (most of it is the headless Chrome used for Amazon).
- macOS or Linux. On Windows, use WSL (Ubuntu) and follow the Linux steps.
- A **home or office internet connection** for crawling: both shops refuse most cloud-server traffic
  (noon always, Amazon often).

Install once, in any folder:

```bash
git clone https://github.com/mohamed-mahmoud377/e-commerce-discounts-alert.git
cd e-commerce-discounts-alert
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m playwright install chromium     # Linux: add --with-deps
.venv/bin/pytest -q                                   # optional: everything should pass
```

## A. Run a worker for an existing server

A worker downloads shop pages and sends the products to the server; it keeps no data itself. The server
owner gives you two things: the server address (for example `http://207.154.206.127:8787`) and **your own
worker token** (they add it under `[workers]` in their config, named after you).

1. Try it in the foreground first:

   ```bash
   DEALS_COORDINATOR=http://207.154.206.127:8787 \
   DEALS_TOKEN=<your token> \
   DEALS_SITES=noon_eg,amazon_eg \
   .venv/bin/deals worker
   ```

   Within a minute you should see `crawl ...` and `done ...: N products` lines. Ctrl+C stops it cleanly.
   "coordinator rejected the worker token" means the token is wrong or not added yet.
2. Keep it running in the background:
   - **macOS:** put the three settings in a file named `worker.env` in this folder (one `NAME=value` per
     line, plus `DEALS_DATA_DIR=<this folder>/data`), then `make install-mac-worker`. It starts at login and
     restarts if it crashes; logs in `~/Library/Logs/deals-worker.log`. Optional: `make keep-awake` so the
     Mac doesn't sleep while plugged in.
   - **Linux:** a systemd user service, `~/.config/systemd/user/deals-worker.service`:

     ```ini
     [Unit]
     Description=deals worker
     After=network-online.target

     [Service]
     WorkingDirectory=%h/e-commerce-discounts-alert
     Environment=DEALS_COORDINATOR=http://207.154.206.127:8787
     Environment=DEALS_TOKEN=<your token>
     Environment=DEALS_SITES=noon_eg,amazon_eg
     ExecStart=%h/e-commerce-discounts-alert/.venv/bin/deals worker
     Restart=always
     RestartSec=10

     [Install]
     WantedBy=default.target
     ```

     then `systemctl --user enable --now deals-worker` and `loginctl enable-linger $USER` (keeps it running
     when you log out). Logs: `journalctl --user -u deals-worker -f`.
3. Tell the owner; they will see your device in `make devices` or the web UI.

To update later: `git pull`, then restart the worker (`make restart-mac-worker` on macOS,
`systemctl --user restart deals-worker` on Linux). To stop for good: `make uninstall-mac-worker` /
`systemctl --user disable --now deals-worker`.

## B. Everything on one computer

No server: the coordinator (database, scheduler, alerts) and a worker run in one process on your machine.

```bash
cp config.example.toml config.toml
.venv/bin/deals local
```

`config.example.toml` already tracks TVs, fridges, air conditioners, phones, laptops, desktops, monitors, PC
components, headphones and kitchen appliances. The first minutes plan the categories, then crawling starts.
In a second terminal:

```bash
.venv/bin/deals status      # what is being crawled, how fresh
.venv/bin/deals alerts      # deals found (there are few until products have 3 days of history)
.venv/bin/deals movers      # biggest price moves
.venv/bin/deals health      # pass/warn/fail checks
```

The web UI runs too: open http://127.0.0.1:8787/ui (on the same computer no login is needed).

Telegram alerts (optional): create a bot with @BotFather, put its token in `[telegram] bot_token`, send
the bot a message, run `.venv/bin/deals telegram` to get your `chat_id`, put that in the config, restart.

Data lives in `data/deals.db` (SQLite). Everything stops when you close the terminal; run it again to
continue where it left off.

## C. Your own server

The original setup: an Ubuntu 24.04 server (the smallest DigitalOcean droplet is enough) runs the
coordinator, and your own computers run workers. Everything is automated from your computer with `make`.

1. Create the server with **your SSH key**, and check `ssh root@<server ip>` works without a password
   (if your key has a passphrase: `ssh-add ~/.ssh/id_ed25519` first).
2. Point the tooling at it: in the `Makefile` change `HOST ?= root@207.154.206.127` to your server, or
   pass `HOST=root@<ip>` to every `make` command (scripts also read `DEALS_HOST`).
3. Deploy: `make deploy`. On a fresh server this creates a `deals` user, installs Python packages and
   Chromium, writes `/etc/deals/config.toml` from the example with two new worker tokens (`droplet` and
   `mac`) and a web UI secret, installs two systemd services, opens port 8787 and checks it answers.
4. Open the web UI with `make ui` (the install generated its secret `admin_token`; `make ui-token`
   copies it for other devices).
5. Telegram: create a bot with @BotFather, press Start in it, then `make telegram TOKEN=<bot token>`.
6. Add your computer as a worker: `make mac-worker-env` (writes `worker.env` with the server's `mac` token),
   then `make install-mac-worker` (macOS) or the Linux service from path A. Optional: `make keep-awake`.
7. Check: `make health` should end with "healthy" after a few minutes.

**Automatic deploys from GitHub (optional):** if you fork the repo, edit `.github/workflows/deploy.yml`:
the server address in `HOST` and the pinned host key line (get it with `ssh-keyscan -t ed25519 <ip>`).
Create a deploy key (`ssh-keygen -t ed25519 -N "" -f ~/.ssh/deals_deploy`), append the `.pub` to the
server's `/root/.ssh/authorized_keys`, and store the private key as the repository secret
`DEPLOY_SSH_KEY`. Without the secret the workflow just skips deploying.

## If something doesn't work

| Symptom | Fix |
| --- | --- |
| `python3: command not found` or version below 3.12 | Install a newer Python (see "What you need") |
| `playwright` error about a missing browser | `.venv/bin/python -m playwright install chromium` (Linux: `--with-deps`) |
| Worker says `coordinator unreachable` for minutes | Check the address and that port 8787 is open; `curl <address>/health` should print `{"ok":true...}` |
| `coordinator rejected the worker token` | The token is wrong or not in the server's `[workers]` yet; the server needs a restart after adding it |
| `blocked: amazon_eg ... challenged` | Amazon is refusing this connection for now; the worker pauses that shop and retries later by itself |
| noon never works | You are on a cloud/datacenter connection; noon only answers home/office connections |
| No alerts yet | Normal for the first days: real price-drop alerts need 3 days of history per product |

More in [MONITORING.md](MONITORING.md).
