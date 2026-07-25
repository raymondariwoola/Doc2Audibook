"""Authenticated ephemeral FastAPI sidecar for the Tauri desktop shell."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

DESKTOP_PROTOCOL_VERSION = 1
DATA_DIR_ENV = "DOC2AUDIOBOOK_WEB_DATA_DIR"
PREVIEW_DIR_ENV = "DOC2AUDIOBOOK_WEB_PREVIEW_DIR"
FRONTEND_DIR_ENV = "DOC2AUDIOBOOK_FRONTEND_DIST"
LOG_DIR_ENV = "DOC2AUDIOBOOK_LOG_DIR"
TOKEN_ENV = "DOC2AUDIOBOOK_DESKTOP_TOKEN"


def _required_absolute_dir(environment: dict[str, str], name: str) -> Path:
    raw = environment.get(name, "")
    if not raw:
        raise ValueError(f"{name} is required for the desktop sidecar.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path.")
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


@dataclass(frozen=True)
class DesktopRuntimeConfig:
    data_dir: Path
    preview_dir: Path
    frontend_dir: Path
    log_dir: Path
    token: str

    @classmethod
    def from_environment(
        cls, environment: dict[str, str] | None = None
    ) -> "DesktopRuntimeConfig":
        values = dict(os.environ if environment is None else environment)
        token = values.get(TOKEN_ENV, "")
        if len(token) < 32:
            raise ValueError(f"{TOKEN_ENV} must contain at least 32 characters.")
        frontend = _required_absolute_dir(values, FRONTEND_DIR_ENV)
        if not (frontend / "index.html").is_file():
            raise ValueError(
                f"{FRONTEND_DIR_ENV} does not contain a built index.html: {frontend}"
            )
        return cls(
            data_dir=_required_absolute_dir(values, DATA_DIR_ENV),
            preview_dir=_required_absolute_dir(values, PREVIEW_DIR_ENV),
            frontend_dir=frontend,
            log_dir=_required_absolute_dir(values, LOG_DIR_ENV),
            token=token,
        )


def bind_loopback_socket(port: int = 0) -> socket.socket:
    """Bind once and hand the already-owned socket to Uvicorn (no port race)."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(2048)
    return listener


def readiness_payload(port: int) -> dict[str, int | str]:
    return {
        "event": "ready",
        "protocol": DESKTOP_PROTOCOL_VERSION,
        "host": "127.0.0.1",
        "port": port,
    }


def _monitor_control(server, stream) -> None:
    """Treat a parent shutdown frame or a closed stdin pipe as app exit."""
    try:
        raw = stream.readline()
    except Exception:
        raw = b""
    if not raw:
        server.should_exit = True
        return
    try:
        frame = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    if frame.get("command") == "shutdown":
        server.should_exit = True


async def serve_desktop(
    runtime: DesktopRuntimeConfig,
    *,
    port: int = 0,
    output: TextIO = sys.stdout,
    control_stream=None,
) -> None:
    """Start Uvicorn on an owned socket and emit readiness after startup."""
    import uvicorn

    # Imports happen only after runtime paths have been validated. Provider model
    # directories and the default app therefore see the desktop environment.
    from web_api.main import create_app

    app = create_app(
        storage_root=runtime.data_dir,
        preview_root=runtime.preview_dir,
        frontend_dist=runtime.frontend_dir,
        desktop_token=runtime.token,
    )
    listener = bind_loopback_socket(port)
    selected_port = int(listener.getsockname()[1])
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=selected_port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    if control_stream is not None:
        threading.Thread(
            target=_monitor_control,
            args=(server, control_stream),
            name="desktop-parent-control",
            daemon=True,
        ).start()
    try:
        while not server.started:
            if server_task.done():
                await server_task
                raise RuntimeError("Desktop sidecar stopped before it became ready.")
            await asyncio.sleep(0.01)
        output.write(json.dumps(readiness_payload(selected_port), separators=(",", ":")))
        output.write("\n")
        output.flush()
        await server_task
    finally:
        server.should_exit = True
        listener.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doc2audiobook-sidecar",
        description="Run the private Doc2Audiobook desktop sidecar.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="loopback port; 0 selects an ephemeral port (default)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 <= args.port <= 65535:
        raise SystemExit("--port must be between 0 and 65535")
    try:
        runtime = DesktopRuntimeConfig.from_environment()
    except ValueError as exc:
        print(f"desktop sidecar configuration error: {exc}", file=sys.stderr)
        return 2
    logging.basicConfig(
        filename=runtime.log_dir / "python-sidecar.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(
        serve_desktop(
            runtime,
            port=args.port,
            control_stream=getattr(sys.stdin, "buffer", sys.stdin),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
