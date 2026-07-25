"""Run the local-only Doc2Audiobook API with ``python -m web_api``."""

from __future__ import annotations

import argparse
import os
import socket
import sys

import uvicorn

from .main import DEFAULT_FRONTEND_DIST


LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m web_api",
        description="Run the Doc2Audiobook local web application.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("DOC2AUDIOBOOK_WEB_HOST", "127.0.0.1"),
        choices=LOCAL_HOSTS,
        help="loopback interface to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        default=os.environ.get("DOC2AUDIOBOOK_WEB_PORT", "8000"),
        type=_port,
        help="local port to use (default: 8000)",
    )
    return parser


def _require_available_port(host: str, port: int) -> None:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(
                    socket.SOL_SOCKET,
                    socket.SO_EXCLUSIVEADDRUSE,
                    1,
                )
            probe.bind((host, port))
    except OSError as exc:
        raise SystemExit(
            f"Cannot start Doc2Audiobook: {host}:{port} is unavailable. "
            f"Stop the process using that port or choose another with "
            f"`--port {port + 1}`. ({exc})"
        ) from exc


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.host not in LOCAL_HOSTS:
        raise SystemExit("Refusing to bind the local app to a non-loopback host.")
    _require_available_port(args.host, args.port)
    if not (DEFAULT_FRONTEND_DIST / "index.html").is_file():
        print(
            "Warning: the React frontend has not been built; the API will still "
            "be available.\nBuild it with: cd web && npm ci && npm run build",
            file=sys.stderr,
        )
    uvicorn.run(
        "web_api.main:app",
        host=args.host,
        port=args.port,
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    main()
