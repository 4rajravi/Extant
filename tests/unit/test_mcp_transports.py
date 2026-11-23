"""The server as a process, over both transports: the same tools either way, and over HTTP
no session, as protocol 2026-07-28 has none."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = "2026-07-28"
# Every 2026-07-28 request carries its own envelope: there is no handshake to remember it.
META = {
    "io.modelcontextprotocol/protocolVersion": PROTOCOL,
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "extant-tests", "version": "0"},
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def http_url() -> Iterator[str]:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.mcp_fake_server", "streamable-http", str(port)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        else:
            raise RuntimeError("the HTTP server did not start")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _tools(server: Any, mode: str = "auto") -> list[dict[str, Any]]:
    async def go() -> list[dict[str, Any]]:
        async with Client(server, mode=mode) as client:  # type: ignore[arg-type]
            listed = await client.list_tools()
            return [t.model_dump(mode="json") for t in listed.tools]

    return anyio.run(go)


def test_stdio_and_http_list_the_same_tools(http_url: str) -> None:
    stdio = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tests.mcp_fake_server", "stdio"],
        cwd=str(ROOT),
        env=dict(os.environ),
    )
    over_stdio = _tools(stdio)
    over_http = _tools(http_url)
    assert len(over_http) == 6
    assert over_stdio == over_http


def test_an_older_client_with_the_handshake_gets_the_same_tools(http_url: str) -> None:
    assert _tools(http_url, mode="legacy") == _tools(http_url)


def test_an_http_request_is_self_contained_and_opens_no_session(http_url: str) -> None:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"_meta": META},
    }
    headers = {
        "MCP-Protocol-Version": PROTOCOL,
        "Mcp-Method": "tools/list",
        "Accept": "application/json, text/event-stream",
    }
    response = httpx.post(http_url, json=body, headers=headers, timeout=10)
    assert response.status_code == 200
    assert "mcp-session-id" not in response.headers
    result = response.json()["result"]
    assert result["ttlMs"] == 86_400_000
    assert result["cacheScope"] == "public"
    assert len(result["tools"]) == 6


def test_a_header_that_contradicts_the_body_is_refused(http_url: str) -> None:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"_meta": META},
    }
    headers = {
        "MCP-Protocol-Version": PROTOCOL,
        "Mcp-Method": "resources/list",  # the body says tools/list
        "Accept": "application/json, text/event-stream",
    }
    response = httpx.post(http_url, json=body, headers=headers, timeout=10)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020
