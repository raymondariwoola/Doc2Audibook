import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path

import httpx

from web_api.desktop import (
    DATA_DIR_ENV,
    DESKTOP_PROTOCOL_VERSION,
    FRONTEND_DIR_ENV,
    LOG_DIR_ENV,
    PREVIEW_DIR_ENV,
    TOKEN_ENV,
    DesktopRuntimeConfig,
    bind_loopback_socket,
    readiness_payload,
)
from web_api.main import DESKTOP_TOKEN_HEADER


class DesktopSidecarContractTests(unittest.TestCase):
    def test_runtime_paths_must_be_absolute_and_frontend_must_be_built(self):
        environment = {
            DATA_DIR_ENV: "relative/data",
            PREVIEW_DIR_ENV: "relative/previews",
            FRONTEND_DIR_ENV: "relative/frontend",
            LOG_DIR_ENV: "relative/logs",
            TOKEN_ENV: "x" * 64,
        }
        with self.assertRaisesRegex(ValueError, "must be an absolute path"):
            DesktopRuntimeConfig.from_environment(environment)

    def test_loopback_port_is_owned_and_readiness_contains_no_secret(self):
        listener = bind_loopback_socket(0)
        try:
            host, port = listener.getsockname()
            self.assertEqual(host, "127.0.0.1")
            self.assertGreater(port, 0)
            payload = readiness_payload(port)
            self.assertEqual(payload["protocol"], DESKTOP_PROTOCOL_VERSION)
            self.assertNotIn("token", payload)
        finally:
            listener.close()

    def test_source_sidecar_bootstrap_health_and_shutdown(self):
        with tempfile.TemporaryDirectory(prefix="phase 4a ü ") as tmp:
            root = Path(tmp)
            frontend = root / "frontend build"
            frontend.mkdir()
            (frontend / "index.html").write_text(
                "<!doctype html><title>Desktop smoke</title>", encoding="utf-8"
            )
            token = "sidecar-" + ("s" * 56)
            environment = os.environ.copy()
            environment.update(
                {
                    DATA_DIR_ENV: str((root / "app data" / "library").resolve()),
                    PREVIEW_DIR_ENV: str((root / "app cache" / "previews").resolve()),
                    FRONTEND_DIR_ENV: str(frontend.resolve()),
                    LOG_DIR_ENV: str((root / "logs").resolve()),
                    TOKEN_ENV: token,
                    "PYTHONUNBUFFERED": "1",
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                }
            )
            process = subprocess.Popen(
                [sys.executable, "-m", "web_api.desktop"],
                cwd=root,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                assert process.stdout is not None
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(process.stdout.readline)
                    try:
                        line = future.result(timeout=20)
                    except TimeoutError as exc:
                        raise AssertionError("Sidecar did not emit readiness") from exc
                if not line:
                    assert process.stderr is not None
                    raise AssertionError(
                        "Sidecar exited before readiness: "
                        + process.stderr.read().decode("utf-8", errors="replace")
                    )
                payload = json.loads(line.decode("utf-8"))
                self.assertEqual(payload["event"], "ready")
                self.assertEqual(payload["protocol"], DESKTOP_PROTOCOL_VERSION)
                self.assertNotIn("token", payload)
                base = f"http://127.0.0.1:{payload['port']}"

                with httpx.Client(base_url=base, timeout=10) as client:
                    self.assertEqual(client.get("/api/health").status_code, 401)
                    session = client.post(
                        "/api/desktop/session",
                        headers={DESKTOP_TOKEN_HEADER: token},
                    )
                    self.assertEqual(session.status_code, 200, session.text)
                    health = client.get("/api/health")
                    self.assertEqual(health.status_code, 200)
                    self.assertTrue(health.json()["desktop"])
                    self.assertEqual(client.get("/").status_code, 200)

                assert process.stdin is not None
                process.stdin.write(b'{"command":"shutdown"}\n')
                process.stdin.flush()
                self.assertEqual(process.wait(timeout=15), 0)
                self.assertTrue((root / "app data" / "library").is_dir())
                self.assertTrue((root / "app cache" / "previews").is_dir())
                self.assertTrue((root / "logs" / "python-sidecar.log").is_file())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()


if __name__ == "__main__":
    unittest.main()
