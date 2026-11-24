"""The page in a real browser: Playwright's Chromium against the real app, served by uvicorn on
a free local port, on a runtime with no database and no network.

These tests are marked ``web`` and left out of a plain ``pytest``: they need a browser
(``uv run playwright install chromium``). CI installs one and runs ``pytest -m web``.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import uvicorn

from extant.api.app import create_app
from extant.api.settings import ApiSettings


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def serve() -> Iterator[Callable[[Any], str]]:
    """Start the app on ``runtime`` and return its base URL; stopped after the test."""
    servers: list[tuple[uvicorn.Server, threading.Thread]] = []

    def start(runtime: Any) -> str:
        port = _free_port()
        app = create_app(runtime, settings=ApiSettings(ask_per_minute=1000, reads_per_minute=10000))
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("the test server did not start")
            time.sleep(0.02)
        servers.append((server, thread))
        return f"http://127.0.0.1:{port}"

    yield start
    for server, thread in servers:
        server.should_exit = True
        thread.join(timeout=5)
