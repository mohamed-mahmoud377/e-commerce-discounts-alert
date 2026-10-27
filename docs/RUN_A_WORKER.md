# Run a worker: exact steps

This page is written to be followed literally, by a person or by an AI assistant acting for them. It sets up
one computer as a **worker** for an existing deals server: the computer downloads shop pages (amazon.eg,
noon.com, jumia.com.eg, cairosales.com, 2b.com.eg, btech.com and dream2000.com) and sends the products it finds to the server. It keeps no data, sends no messages, and changes
nothing on the server. Background explanation is in [GETTING_STARTED.md](GETTING_STARTED.md), path A.

## 0. Inputs you must have before starting

| Input | Example | Where it comes from |
| --- | --- | --- |
| `SERVER` | `http://207.154.206.127:8787` | The owner. Always `http://` + IP + `:8787` |
| `TOKEN` | a 43-character random string | The owner, privately. One token per computer; never reuse someone else's |
| `NAME` | `ahmed` | The owner chose it when creating the token; it only matters for recognising the device |

Do not continue without `SERVER` and `TOKEN`. Do not generate a token yourself: it must be in the server's
config or it is rejected.

Rules that apply to every step:

- Never paste `TOKEN` into a chat, an issue, a commit or a screenshot. It goes only into `worker.env` (or the
  service file), which is gitignored.
- Never run `make deploy`, `make restart-server`, `make ssh` or anything that touches the server; those
  need the owner's SSH key and are not part of running a worker.
- Never edit files under `src/` to "fix" something; report the problem instead (section 6).
- Run exactly one worker per computer. Two workers with the same token on one computer gain nothing (the
  server allows one job per shop per internet connection) and confuse the device view.

## 1. Check the computer

Run these and compare with the expected result.

| Command | Expected | If not |
| --- | --- | --- |
| `python3 --version` | `Python 3.12.x` or newer | macOS: `brew install python@3.13` then use `python3.13` instead of `python3` below. Ubuntu/Debian: `sudo apt install python3 python3-venv`. Windows: install WSL (Ubuntu) and do everything inside it |
| `git --version` | any version | macOS: `xcode-select --install`. Linux: `sudo apt install git` |
| `df -h .` | at least 1 GB free | Free space; Chromium alone is about 400 MB |
| `curl -s SERVER/health` | `{"ok":true,"version":"0.1.0"}` | The address is wrong, or port 8787 is not reachable from this network (some office networks block it). Ask the owner |
| `curl -s https://www.noon.com/egypt-en/ -o /dev/null -w '%{http_code}\n'` | `200` | `403` means this is a cloud/datacenter or VPN connection and noon will refuse it. The worker still helps with Amazon: set `DEALS_SITES=amazon_eg` in step 3 |

## 2. Install

Run from the folder where the project should live (for example the home folder). Every command must finish
without an error before the next one.

```bash
git clone https://github.com/mohamed-mahmoud377/e-commerce-discounts-alert.git
cd e-commerce-discounts-alert
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m playwright install chromium
```

On Linux the last line is `.venv/bin/python -m playwright install --with-deps chromium` (it will ask for
sudo to install system libraries; that is expected).

Check: `.venv/bin/deals --help` prints a list of subcommands including `worker`. Optional but recommended:
`.venv/bin/pytest -q` prints `61 passed` (or more); a failure here means the environment is broken, not the
server.

## 3. First run, in the foreground

Replace the two values and run (one command, four lines):

```bash
DEALS_COORDINATOR=SERVER \
DEALS_TOKEN=TOKEN \
DEALS_SITES=noon_eg,amazon_eg,jumia_eg,cairosales_eg,2b_eg,btech_eg,dream2000_eg \
.venv/bin/deals worker
```

Read the output for 2 minutes. What the lines mean:

| Line | Meaning | Action |
| --- | --- | --- |
| `crawl noon_eg category:...` / `plan amazon_eg ...` | It received a job | None: working |
| `done noon_eg ...: 103 products, 3 pages, 0 changes, 5s` | It finished a job | None: working |
| `amazon_eg: solving WAF challenge in headless browser` | Passing Amazon's bot check (invisible Chromium) | None: happens on first run and occasionally later |
| `coordinator rejected the worker token; check DEALS_TOKEN` and the program exits | Token wrong or not added on the server yet | Check `TOKEN` for typos (copy again). If it matches, ask the owner to add it and restart the server |
| `coordinator unreachable (ConnectionError) ...; retrying in 2s`, repeating | Cannot reach `SERVER` | Redo the `curl SERVER/health` check from section 1 |
| `blocked: amazon_eg ... challenged` | Amazon refused this connection for now | None: the worker pauses that shop (15 min, doubling) and retries alone. If it never stops, the connection is probably a datacenter/VPN; drop `amazon_eg` from `DEALS_SITES` |
| `blocked: noon_eg ... 403` | noon refuses this connection | Set `DEALS_SITES=amazon_eg` (see section 1) |
| `blocked: jumia_eg: Cloudflare challenge` | Jumia (Cloudflare) refuses this connection | It pauses and retries alone. If every Jumia job is blocked, the connection is a datacenter/VPN: drop `jumia_eg` from `DEALS_SITES`. (A plain `curl` of jumia.com.eg always gets 403, even from home: only the worker's Safari fingerprint passes, so test with `.venv/bin/deals crawl jumia_eg "category:televisions\|price:3000-"`, which should print 40 products) |
| `blocked: cairosales_eg: Cloudflare challenge` / `blocked: 2b_eg: Cloudflare challenge` | Cloudflare refuses this connection on Cairo Sales or 2B | Same as Jumia: expected from datacenter/VPN connections, drop the shop from `DEALS_SITES` there |
| nothing at all for 5 minutes after `worker 0.1.0 started for noon_eg, amazon_eg, jumia_eg, cairosales_eg, 2b_eg` | Every job is taken by other devices on the same network, or nothing is due yet | Normal if another worker runs on the same Wi-Fi. Wait 10 minutes |

Stop it with Ctrl+C when you have seen `done` lines (or a clear error). Ctrl+C is safe: an unfinished job
goes back to the queue within 5 minutes.

Second check from the server's point of view (works with the worker token):

```bash
DEALS_COORDINATOR=SERVER DEALS_TOKEN=TOKEN .venv/bin/deals devices
```

Expected output like:

```
NAME  41.36.x.x  v0.1.0  online, seen 2s ago, 120 pages, 6 jobs
   🔵 noon_eg    crawling  crawl category:...
   🔵 amazon_eg  crawling  crawl category:...
```

(🔵 crawling, 🟢 ok/idle, 🟠 blocked, 🔴 failing or offline; "seen" under a minute.) If `NAME` is missing, the token in use is not the one the owner created under
that name.

## 4. Run it permanently

### macOS

1. Create `worker.env` in the project folder with exactly these four lines (replace the values; the last line
   must be the absolute path of the project folder plus `/data`):

   ```
   DEALS_COORDINATOR=SERVER
   DEALS_TOKEN=TOKEN
   DEALS_SITES=noon_eg,amazon_eg,jumia_eg,cairosales_eg,2b_eg,btech_eg,dream2000_eg
   DEALS_DATA_DIR=/Users/<you>/e-commerce-discounts-alert/data
   ```

   `chmod 600 worker.env` so only this user can read it.
2. `make install-mac-worker`. This writes `~/Library/LaunchAgents/com.deals.worker.plist` and starts it; it
   runs at every login and restarts itself after a crash.
3. Check: `launchctl list | grep com.deals.worker` prints a line with a number (the process id) as the
   first field, and within a minute `tail -5 ~/Library/Logs/deals-worker.log` shows `crawl`/`done` lines.
4. Sleep: a sleeping Mac does no work. `make keep-awake` stops it sleeping while plugged in (the screen
   still turns off; closing the lid still sleeps unless an external display is attached). `make allow-sleep`
   undoes it.

Everyday commands on macOS: `make worker-log` (follow the log), `make restart-mac-worker` (after
`git pull`), `make uninstall-mac-worker` (remove it).

### Linux (systemd user service)

1. Write `~/.config/systemd/user/deals-worker.service` (create the folder if needed):

   ```ini
   [Unit]
   Description=deals worker
   After=network-online.target

   [Service]
   WorkingDirectory=%h/e-commerce-discounts-alert
   Environment=DEALS_COORDINATOR=SERVER
   Environment=DEALS_TOKEN=TOKEN
   Environment=DEALS_SITES=noon_eg,amazon_eg,jumia_eg,cairosales_eg,2b_eg,btech_eg,dream2000_eg
   Environment=DEALS_DATA_DIR=%h/e-commerce-discounts-alert/data
   ExecStart=%h/e-commerce-discounts-alert/.venv/bin/deals worker
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=default.target
   ```

   (`%h` is the home folder; adjust `WorkingDirectory` and paths if the clone lives elsewhere.)
2. `chmod 600 ~/.config/systemd/user/deals-worker.service`, then
   `systemctl --user daemon-reload && systemctl --user enable --now deals-worker`.
3. `loginctl enable-linger $USER` so it keeps running after logout and starts at boot.
4. Check: `systemctl --user status deals-worker` says `active (running)`; `journalctl --user -u deals-worker -f`
   shows `crawl`/`done` lines.

Everyday commands on Linux: `journalctl --user -u deals-worker -f` (log), `systemctl --user restart
deals-worker` (after `git pull`), `systemctl --user disable --now deals-worker` (remove).

## 5. Updating

When the owner says the code changed: `cd` into the project, `git pull`, `.venv/bin/pip install -e ".[dev]"`
(only needed if `pyproject.toml` changed, harmless otherwise), then restart the worker with the command for
your OS above. Nothing else; configuration never changes on the worker side unless the owner gives a new
token or address.

## 6. When to tell the owner, and what to send

Tell the owner (and stop guessing) when:

- `coordinator rejected the worker token` after re-checking the token;
- `coordinator unreachable` continues for more than 10 minutes while `curl SERVER/health` works;
- the worker exits repeatedly (macOS: `launchctl list | grep com.deals.worker` shows a non-zero second
  field; Linux: `systemctl --user status deals-worker` shows restarts) and the log shows a Python traceback;
- `blocked:` lines for the same shop keep coming for more than a day.

Send: the last 50 lines of the log (`tail -50 ~/Library/Logs/deals-worker.log` or
`journalctl --user -u deals-worker -n 50`), the OS and Python version, and whether the connection is home,
office, mobile hotspot or VPN. Remove the token if it appears in what you send (it normally does not).

## 7. What the worker does and does not do (for the person whose computer it is)

- Fetches one shop page every 1 to 4 seconds while it has work, about 2 to 5 MB per minute, in two threads
  (one per shop). Starts an invisible Chromium now and then for Amazon's bot check.
- Writes only to `data/` (an Amazon cookie file) and the log file. Does not read anything else on the
  computer, does not open ports, does not run as root.
- The token lets it ask the server for jobs and send products back. It cannot read the database, send
  Telegram messages or change settings. The owner can see the device's name, pages fetched and blocks; not
  anything else about the computer.
- Work is shared through the server's queue, so several devices never crawl the same thing twice. Two
  devices on the same Wi-Fi take turns per shop; a device on a different connection adds full extra
  capacity. See GETTING_STARTED.md, "What your device actually does".
- To stop helping: the "remove" command in section 4, then tell the owner so they can delete the token.
