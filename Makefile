HOST ?= root@207.154.206.127
SSH_DEALS = ssh $(HOST) runuser -u deals -- /opt/deals/.venv/bin/deals

.PHONY: test deploy telegram db sql dbweb status alerts movers logs mac-worker-env mac-worker install-mac-worker uninstall-mac-worker

test:                 ## run the test suite
	.venv/bin/pytest -q

deploy:               ## test, ship to the server, restart, health-check
	scripts/deploy.sh $(HOST)

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
