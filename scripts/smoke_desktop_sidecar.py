#!/usr/bin/env python3
"""Smoke a frozen desktop sidecar from an unrelated working directory."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import queue
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Sequence


TOKEN_HEADER = "X-Doc2Audiobook-Desktop-Token"
READY_PROTOCOL = 1


class ArtifactSmokeError(RuntimeError):
    """The frozen sidecar did not satisfy its desktop runtime contract."""


@dataclass(frozen=True)
class ArtifactSmokeResult:
    executable: Path
    port: int
    provider_ids: tuple[str, ...]
    log_files: tuple[Path, ...]
    rendered_formats: tuple[str, ...]


def _readline_with_timeout(stream: BinaryIO, timeout: float) -> bytes:
    result: queue.Queue[bytes | BaseException] = queue.Queue(maxsize=1)

    def read() -> None:
        try:
            result.put(stream.readline())
        except BaseException as exc:  # pragma: no cover - defensive pipe failure
            result.put(exc)

    threading.Thread(target=read, daemon=True).start()
    try:
        value = result.get(timeout=timeout)
    except queue.Empty as exc:
        raise ArtifactSmokeError(
            f"Frozen sidecar emitted no readiness frame within {timeout:.0f} seconds."
        ) from exc
    if isinstance(value, BaseException):
        raise ArtifactSmokeError(f"Could not read sidecar readiness: {value}") from value
    return value


def _expect_http_error(
    opener: urllib.request.OpenerDirector,
    request: urllib.request.Request,
    status: int,
) -> None:
    try:
        opener.open(request, timeout=10)
    except urllib.error.HTTPError as exc:
        try:
            if exc.code == status:
                return
            raise ArtifactSmokeError(
                f"{request.full_url} returned HTTP {exc.code}, expected {status}."
            ) from exc
        finally:
            exc.close()
    raise ArtifactSmokeError(
        f"{request.full_url} succeeded, expected HTTP {status}."
    )


def _json_request(
    opener: urllib.request.OpenerDirector,
    request: urllib.request.Request,
    *,
    status: int = 200,
) -> dict:
    try:
        with opener.open(request, timeout=10) as response:
            payload = response.read()
            actual_status = response.status
    except urllib.error.URLError as exc:
        raise ArtifactSmokeError(f"Request failed: {request.full_url}: {exc}") from exc
    if actual_status != status:
        raise ArtifactSmokeError(
            f"{request.full_url} returned HTTP {actual_status}, expected {status}."
        )
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ArtifactSmokeError(
            f"{request.full_url} did not return valid JSON."
        ) from exc
    if not isinstance(value, dict):
        raise ArtifactSmokeError(f"{request.full_url} returned a non-object payload.")
    return value


def _validated_ready_frame(line: bytes) -> dict:
    try:
        frame = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactSmokeError(
            f"Invalid frozen-sidecar readiness frame: {line!r}"
        ) from exc
    expected_keys = {"event", "protocol", "host", "port"}
    if set(frame) != expected_keys:
        raise ArtifactSmokeError(
            f"Readiness keys were {sorted(frame)}, expected {sorted(expected_keys)}."
        )
    if (
        frame["event"] != "ready"
        or frame["protocol"] != READY_PROTOCOL
        or frame["host"] != "127.0.0.1"
        or not isinstance(frame["port"], int)
        or not 1 <= frame["port"] <= 65535
    ):
        raise ArtifactSmokeError(f"Invalid readiness values: {frame!r}")
    return frame


def _stop_process(process: subprocess.Popen[bytes], timeout: float = 15) -> int:
    """Request protocol shutdown, then terminate the exact process tree."""
    if process.poll() is not None:
        return int(process.returncode)
    if process.stdin is not None and not process.stdin.closed:
        try:
            process.stdin.write(b'{"command":"shutdown"}\n')
            process.stdin.flush()
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
            )
        else:
            process.kill()
        return process.wait(timeout=10)


def _render_fixture(
    *,
    opener: urllib.request.OpenerDirector,
    base_url: str,
    fixture: Path,
    output_format: str,
    ffprobe: Path,
    output_dir: Path,
    timeout: float = 120,
) -> None:
    form = urllib.parse.urlencode(
        {
            "desktop_path": str(fixture.resolve()),
            "provider": "edge",
            "voice": "en-US-AndrewNeural",
            "rate": "-4",
            "pitch": "0",
            "output_format": output_format,
            "max_chars": "3000",
            "keep_chunks": "true",
            "rules": "[]",
        }
    ).encode("utf-8")
    created = _json_request(
        opener,
        urllib.request.Request(
            f"{base_url}/api/jobs",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        ),
        status=202,
    )
    job_id = created.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise ArtifactSmokeError("Render submission did not return a job id.")

    deadline = time.monotonic() + timeout
    terminal = created
    while terminal.get("status") not in {"completed", "failed"}:
        if time.monotonic() >= deadline:
            raise ArtifactSmokeError(
                f"Edge {output_format.upper()} render did not finish within "
                f"{timeout:.0f} seconds."
            )
        time.sleep(0.25)
        terminal = _json_request(
            opener,
            urllib.request.Request(f"{base_url}/api/jobs/{job_id}"),
        )
    if terminal.get("status") != "completed":
        raise ArtifactSmokeError(
            f"Edge {output_format.upper()} render failed: "
            f"{terminal.get('error') or 'unknown error'}"
        )

    try:
        with opener.open(f"{base_url}/api/jobs/{job_id}/output", timeout=15) as response:
            audio = response.read()
    except urllib.error.URLError as exc:
        raise ArtifactSmokeError(
            f"Could not download completed {output_format.upper()} output: {exc}"
        ) from exc
    if len(audio) < 1000:
        raise ArtifactSmokeError(
            f"{output_format.upper()} output was unexpectedly small ({len(audio)} bytes)."
        )
    output_path = output_dir / f"artifact-acceptance.{output_format}"
    output_path.write_bytes(audio)
    probe = subprocess.run(
        [
            str(ffprobe.resolve()),
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name",
            "-of",
            "json",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if probe.returncode != 0:
        raise ArtifactSmokeError(
            f"Bundled ffprobe rejected {output_format.upper()} output: "
            f"{probe.stderr.strip()}"
        )
    try:
        probe_format = json.loads(probe.stdout)["format"]
        duration = float(probe_format["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ArtifactSmokeError(
            f"Bundled ffprobe returned invalid metadata for {output_format.upper()}."
        ) from exc
    if duration <= 0:
        raise ArtifactSmokeError(
            f"{output_format.upper()} output has non-positive duration {duration}."
        )


def smoke_sidecar(
    *,
    executable: Path,
    frontend: Path,
    ffmpeg: Path,
    ffprobe: Path,
    startup_timeout: float = 60,
    render_fixture: Path | None = None,
    render_formats: Sequence[str] = (),
) -> ArtifactSmokeResult:
    """Exercise authentication, resources, providers, and clean shutdown."""
    inputs = {
        "sidecar executable": executable,
        "frontend index": frontend / "index.html",
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
    }
    for label, path in inputs.items():
        if not path.resolve().is_file():
            raise ArtifactSmokeError(f"Missing {label}: {path}")
    if render_fixture is not None and not render_fixture.resolve().is_file():
        raise ArtifactSmokeError(f"Missing render fixture: {render_fixture}")
    invalid_formats = set(render_formats) - {"mp3", "m4a", "wav"}
    if invalid_formats:
        raise ArtifactSmokeError(
            f"Unsupported render smoke formats: {sorted(invalid_formats)}"
        )

    token = secrets.token_hex(32)
    with tempfile.TemporaryDirectory(prefix="Doc2Audiobook artifact smoke ") as raw:
        root = Path(raw).resolve()
        launch_dir = root / "unrelated cwd ü"
        data_dir = root / "app data" / "library"
        preview_dir = root / "cache" / "previews"
        log_dir = root / "logs"
        extraction_dir = root / "onefile extraction"
        launch_dir.mkdir(parents=True)
        extraction_dir.mkdir()

        environment = os.environ.copy()
        environment.update(
            {
                "DOC2AUDIOBOOK_WEB_DATA_DIR": str(data_dir),
                "DOC2AUDIOBOOK_WEB_PREVIEW_DIR": str(preview_dir),
                "DOC2AUDIOBOOK_FRONTEND_DIST": str(frontend.resolve()),
                "DOC2AUDIOBOOK_LOG_DIR": str(log_dir),
                "DOC2AUDIOBOOK_DESKTOP_TOKEN": token,
                "DOC2AUDIOBOOK_FFMPEG": str(ffmpeg.resolve()),
                "DOC2AUDIOBOOK_FFPROBE": str(ffprobe.resolve()),
                "TMP": str(extraction_dir),
                "TEMP": str(extraction_dir),
            }
        )
        process = subprocess.Popen(
            [str(executable.resolve()), "--port", "0"],
            cwd=launch_dir,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_tail = b""
        stderr = b""
        try:
            assert process.stdout is not None
            ready_line = _readline_with_timeout(process.stdout, startup_timeout)
            frame = _validated_ready_frame(ready_line)
            if token.encode() in ready_line:
                raise ArtifactSmokeError("Bootstrap token leaked in readiness output.")

            base_url = f"http://127.0.0.1:{frame['port']}"
            cookie_jar = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(cookie_jar)
            )
            _expect_http_error(
                opener,
                urllib.request.Request(f"{base_url}/api/health"),
                401,
            )

            session_request = urllib.request.Request(
                f"{base_url}/api/desktop/session",
                data=b"",
                headers={TOKEN_HEADER: token, "Accept": "application/json"},
                method="POST",
            )
            _json_request(opener, session_request)
            _expect_http_error(opener, session_request, 401)

            health = _json_request(
                opener, urllib.request.Request(f"{base_url}/api/health")
            )
            if health.get("desktop") is not True:
                raise ArtifactSmokeError("Health did not identify desktop mode.")
            if health.get("ffmpeg") is not True or health.get("ffprobe") is not True:
                raise ArtifactSmokeError("Health did not resolve both staged media tools.")

            providers = _json_request(
                opener, urllib.request.Request(f"{base_url}/api/providers")
            )
            provider_ids = tuple(
                row.get("id", "")
                for row in providers.get("providers", [])
                if isinstance(row, dict)
            )
            if provider_ids != ("edge",):
                raise ArtifactSmokeError(
                    f"Frozen provider scope was {provider_ids!r}, expected Edge only."
                )

            with opener.open(f"{base_url}/", timeout=10) as response:
                html = response.read()
            if b"Doc2Audiobook" not in html:
                raise ArtifactSmokeError("Frozen sidecar did not serve the built SPA.")

            rendered_formats: list[str] = []
            if render_fixture is not None:
                render_output_dir = root / "validated outputs"
                render_output_dir.mkdir()
                for output_format in render_formats or ("mp3",):
                    _render_fixture(
                        opener=opener,
                        base_url=base_url,
                        fixture=render_fixture,
                        output_format=output_format,
                        ffprobe=ffprobe,
                        output_dir=render_output_dir,
                    )
                    rendered_formats.append(output_format)

            return_code = _stop_process(process)
            stdout_tail = process.stdout.read()
            assert process.stderr is not None
            stderr = process.stderr.read()
            if return_code != 0:
                raise ArtifactSmokeError(
                    f"Frozen sidecar exited with status {return_code}: "
                    f"{stderr.decode('utf-8', errors='replace')}"
                )

            log_files = tuple(
                sorted(path for path in log_dir.rglob("*") if path.is_file())
            )
            leaked_text = ready_line + stdout_tail + stderr
            for log_file in log_files:
                leaked_text += log_file.read_bytes()
            if token.encode() in leaked_text:
                raise ArtifactSmokeError("Bootstrap token leaked into process output or logs.")
            if any(launch_dir.iterdir()):
                raise ArtifactSmokeError(
                    "Frozen sidecar wrote files into its unrelated working directory."
                )
            for expected_dir in (data_dir, preview_dir, log_dir):
                if not expected_dir.is_dir():
                    raise ArtifactSmokeError(
                        f"Frozen sidecar did not create configured directory: {expected_dir}"
                    )

            return ArtifactSmokeResult(
                executable=executable.resolve(),
                port=frame["port"],
                provider_ids=provider_ids,
                log_files=log_files,
                rendered_formats=tuple(rendered_formats),
            )
        finally:
            _stop_process(process)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Smoke a frozen Phase 4A sidecar outside the source tree."
    )
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--frontend", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    parser.add_argument(
        "--render-fixture",
        type=Path,
        help=(
            "optional non-sensitive document to narrate through the online Edge "
            "provider as an artifact acceptance check"
        ),
    )
    parser.add_argument(
        "--formats",
        default="mp3",
        help="comma-separated render formats used with --render-fixture (default: mp3)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = smoke_sidecar(
            executable=args.sidecar,
            frontend=args.frontend,
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
            render_fixture=args.render_fixture,
            render_formats=tuple(
                item.strip().lower()
                for item in args.formats.split(",")
                if item.strip()
            ),
        )
    except (ArtifactSmokeError, OSError, subprocess.SubprocessError) as exc:
        print(f"desktop sidecar smoke failed: {exc}", file=os.sys.stderr)
        return 1
    print(
        f"Frozen sidecar smoke passed on 127.0.0.1:{result.port}; "
        f"providers={','.join(result.provider_ids)}; logs={len(result.log_files)}; "
        f"renders={','.join(result.rendered_formats) or 'not requested'}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
