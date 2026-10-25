"""A real coordinator (HTTP on localhost) and a real worker crawling a fake site."""

import socket
import threading
import time
from dataclasses import replace

import uvicorn
from fakesite import FakeSite, make_catalog

from deals.config import Watch
from deals.coordinator import create_app
from deals.worker import Worker


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_worker_plans_and_crawls_everything(cfg, tmp_path):
    cfg = replace(cfg, watches=[Watch(name="All", targets={"amazon_eg": ["category:root"]})])
    app = create_app(cfg, background=False)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)

    catalog = make_catalog(600)
    site = FakeSite(catalog)
    worker = Worker(f"http://127.0.0.1:{port}", "token-1", ["amazon_eg"], tmp_path, site_factory=lambda _: site)
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()

    conn = app.state.conn
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE last_done_at IS NULL").fetchone()[0]
            if pending == 0:
                break
            time.sleep(0.2)
        assert pending == 0, "worker did not finish all jobs"
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == len(catalog)
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE kind = 'crawl'").fetchone()[0] > 1
    finally:
        worker.stop.set()
        thread.join(timeout=10)
        server.should_exit = True
    assert not thread.is_alive()
