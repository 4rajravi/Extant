"""Run the MCP server: ``python -m extant.mcp_server [--transport stdio|streamable-http]``.

Over streamable-http the endpoint is ``/mcp``. A 2026-07-28 request is self-contained (no
session, no handshake); an older client that still performs the handshake is served
statelessly too (``stateless_http``), so nothing is kept between requests on either path.
Readiness is checked before the server accepts anything: a build that may not be served
stops the process.
"""

from __future__ import annotations

import argparse
import sys

from extant.agent.serving import NotReadyError, start
from extant.guards import logs
from extant.mcp_server.backend import PgBackend
from extant.mcp_server.server import build_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m extant.mcp_server")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        serving = start()
    except NotReadyError as exc:
        print(f"not ready: {exc}", file=sys.stderr)
        return 2
    for warning in serving.readiness().warnings:
        print(f"warning: {warning}", file=sys.stderr)  # stderr: stdout is the stdio channel
    # Over stdio, stdout carries JSON-RPC: the request log goes to stderr instead.
    log = logs.emit_stderr if args.transport == "stdio" else logs.emit
    server = build_server(PgBackend(serving, log=log))
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run(
            "streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path="/mcp",
            stateless_http=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
