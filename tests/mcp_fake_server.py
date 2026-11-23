"""The MCP server on the fake backend, as a process: for the transport tests::

python -m tests.mcp_fake_server stdio
python -m tests.mcp_fake_server streamable-http <port>
"""

from __future__ import annotations

import sys

from extant.mcp_server.server import build_server
from tests.mcp_support import FakeBackend


def main() -> None:
    server = build_server(FakeBackend())
    if sys.argv[1] == "stdio":
        server.run("stdio")
    else:
        server.run(
            "streamable-http",
            host="127.0.0.1",
            port=int(sys.argv[2]),
            streamable_http_path="/mcp",
            stateless_http=True,
        )


if __name__ == "__main__":
    main()
