"""Command line entry point.

Three modes, matching the three ways this gets used:

    icloud-drive-mcp login    one-time (per ~30 days) Apple sign-in
    icloud-drive-mcp stdio    local transport, for Claude Code / Desktop
    icloud-drive-mcp http     remote transport, for a claude.ai connector
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import Config


def _configure_logging(level: str, stdio: bool) -> None:
    # An stdio server must never write to stdout: that stream is the protocol.
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stderr if stdio else sys.stdout,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="icloud-drive-mcp",
        description="MCP server for Apple iCloud Drive.",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="http",
        choices=["http", "stdio", "login", "status"],
        help=(
            "http: serve remotely for a claude.ai connector (default). "
            "stdio: serve locally over stdin/stdout. "
            "login: sign in to Apple and store the session. "
            "status: print whether the stored session still works."
        ),
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    _configure_logging(args.log_level, stdio=args.mode == "stdio")
    config = Config.from_env()

    if args.mode == "login":
        from .login import run_cli_login

        return run_cli_login(config)

    if args.mode == "status":
        import json

        from .drive import DriveClient

        status = DriveClient(config).session_status()
        print(json.dumps(status, indent=2))
        return 0 if status.get("authenticated") else 1

    if args.mode == "stdio":
        from .server import build_server

        mcp, _client, _provider = build_server(config, with_auth=False)
        mcp.run(transport="stdio")
        return 0

    import uvicorn

    from .http_app import create_app

    try:
        app = create_app(config)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logging.getLogger(__name__).info(
        "Serving MCP at %s/mcp (bind %s:%s)", config.public_url, config.host, config.port
    )
    uvicorn.run(app, host=config.host, port=config.port, log_level=args.log_level.lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
