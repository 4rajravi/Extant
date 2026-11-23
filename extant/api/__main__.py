"""Run the HTTP API: ``python -m extant.api [--host 0.0.0.0] [--port 8080]``.

The process starts whether or not the database is reachable: ``GET /health`` says whether it
may receive traffic (point the startup probe there), and ``GET /health/live`` only that the
process answers (point the liveness probe there). Uvicorn's own proxy header handling is off:
whom to trust for the client address is ``EXTANT_API_TRUSTED_PROXY_HOPS``, and only that.
"""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from extant.agent.serving import start
from extant.api.app import create_app
from extant.api.runtime import PgRuntime
from extant.api.settings import load_api_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m extant.api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    args = parser.parse_args(argv)
    app = create_app(PgRuntime(start(require_ready=False)), settings=load_api_settings())
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=False, access_log=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
