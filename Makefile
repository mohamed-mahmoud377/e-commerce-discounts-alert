HOST ?= root@207.154.206.127
SSH_DEALS = ssh $(HOST) runuser -u deals -- /opt/deals/.venv/bin/deals

.PHONY: test deploy telegram status alerts movers logs mac-worker-env mac-worker install-mac-worker uninstall-mac-worker

test:                 ## run the test suite
	.venv/bin/pytest -q

deploy:               ## test, ship to the server, restart, health-check
	scripts/deploy.sh $(HOST)

telegram:             ## connect Telegram alerts: make telegram TOKEN=<token from @BotFather>
	scripts/telegram-setup.sh "$(TOKEN)" $(HOST)

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
