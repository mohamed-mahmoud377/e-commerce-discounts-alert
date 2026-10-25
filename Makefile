HOST ?= root@207.154.206.127
HOST_IP = $(lastword $(subst @, ,$(HOST)))
SSH_DEALS = ssh $(HOST) runuser -u deals -- /opt/deals/.venv/bin/deals

.PHONY: test deploy save-logs ui ui-token ssh restart-server restart-mac-worker health devices dashboard events worker-log report telegram db sql dbweb status alerts movers logs mac-worker-env mac-worker install-mac-worker uninstall-mac-worker

test:                 ## run the test suite
	.venv/bin/pytest -q

deploy:               ## test, ship to the server, restart, health-check
	scripts/deploy.sh $(HOST)

ui:                   ## open the web UI in your browser, already logged in
	@open "http://$(HOST_IP):8787/ui/login?token=$$(ssh $(HOST) "sed -n 's/^admin_token = \"\(.*\)\"/\1/p' /etc/deals/config.toml")"

ui-token:             ## copy the web UI token to the clipboard (to log in from a phone or another computer)
	@ssh $(HOST) "sed -n 's/^admin_token = \"\(.*\)\"/\1/p' /etc/deals/config.toml" | tr -d '\n' | pbcopy && echo "token copied; open http://$(HOST_IP):8787/ui and paste it"

ssh:                  ## log in to the server
	ssh $(HOST)

restart-server:       ## restart both services on the server (e.g. after editing /etc/deals/config.toml)
	ssh $(HOST) systemctl restart deals-coordinator deals-worker && ssh $(HOST) systemctl is-active deals-coordinator deals-worker

restart-mac-worker:   ## restart this Mac's worker (after git pull or changing worker code)
	launchctl kickstart -k gui/$$(id -u)/com.deals.worker && echo "mac worker restarted"

health:               ## is everything working? server, logs, code, Mac worker, database, crawling, alerts
	@scripts/health.sh $(HOST)

devices:              ## every connected device and how its fetching is going, per shop
	@set -a && . ./worker.env && set +a && .venv/bin/deals devices

dashboard:            ## the same in your browser (auto-refreshes), with everyone's logs
	@set -a && . ./worker.env && set +a && open "$$DEALS_COORDINATOR/dashboard?token=$$DEALS_TOKEN"

events:               ## live log lines from all devices + the server (Ctrl+C to stop); SOURCE=mac LEVEL=warning to filter
	@set -a && . ./worker.env && set +a && .venv/bin/deals logs --follow $(if $(SOURCE),--source $(SOURCE)) $(if $(LEVEL),--level $(LEVEL))

save-logs:            ## download every log (all devices, server journal, daily archives, Mac) into ./logs/ for analysis
	@scripts/save-logs.sh $(HOST)

worker-log:           ## this Mac's worker log, live
	tail -f ~/Library/Logs/deals-worker.log

report:               ## send the daily report to your Telegram now
	$(SSH_DEALS) report --send --config /etc/deals/config.toml

telegram:             ## connect Telegram alerts: make telegram TOKEN=<token from @BotFather>
	scripts/telegram-setup.sh "$(TOKEN)" $(HOST)

db:                   ## download a snapshot of the server database to data/server.db (open in IntelliJ)
	scripts/pull-db.sh $(HOST)

sql:                  ## live read-only SQL shell on the server database
	ssh -t $(HOST) runuser -u deals -- sqlite3 -readonly -box -cmd "'.headers on'" /var/lib/deals/deals.db

dbweb:                ## live database in your browser at http://localhost:8001 (Ctrl+C to stop)
	@echo "==> open http://localhost:8001  (Ctrl+C here to stop)"
	ssh -t -L 8001:127.0.0.1:8001 $(HOST) "pkill -u deals -f bin/datasette; python3 /opt/deals/scripts/datasette_metadata.py /opt/deals/docs/queries.sql > /tmp/deals-datasette.json && runuser -u deals -- /opt/deals/.venv/bin/datasette /var/lib/deals/deals.db --metadata /tmp/deals-datasette.json -h 127.0.0.1 -p 8001 --setting sql_time_limit_ms 15000"

status:               ## workers, blocks, backlog, freshness (on the server)
	$(SSH_DEALS) status --config /etc/deals/config.toml

alerts:               ## deal alerts from the last 24h
	$(SSH_DEALS) alerts --config /etc/deals/config.toml --system

movers:               ## biggest price moves in the last 24h
	$(SSH_DEALS) movers --config /etc/deals/config.toml --min-price 1000

logs:                 ## follow server logs
	ssh $(HOST) journalctl -u deals-coordinator -u deals-worker -f -n 100

mac-worker-env:       ## write worker.env for this Mac from the server's token
	scripts/mac-worker-env.sh $(HOST)

mac-worker:           ## run a worker in the foreground on this Mac
	set -a && . ./worker.env && set +a && .venv/bin/deals worker

install-mac-worker:   ## run the worker in the background on this Mac (launchd)
	scripts/install-mac-worker.sh

uninstall-mac-worker:
	scripts/install-mac-worker.sh --uninstall
