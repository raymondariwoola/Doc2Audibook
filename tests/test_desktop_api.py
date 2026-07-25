import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import providers
from web_api.main import (
    DESKTOP_SESSION_COOKIE,
    DESKTOP_TOKEN_HEADER,
    create_app,
)


class _DesktopDummyProvider(providers.TTSProvider):
    id = "desktop-dummy"
    label = "Desktop dummy"

    def capabilities(self) -> providers.Capabilities:
        return providers.Capabilities(provider_id=self.id)

    async def list_voices(self) -> list[providers.VoiceInfo]:
        return [
            providers.VoiceInfo(
                id="voice", label="Voice", locale="en-US", provider=self.id
            )
        ]

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        out_path: Path,
        *,
        rate: str,
        pitch: str,
    ) -> None:
        out_path.write_bytes(b"audio")


class DesktopSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        frontend = root / "frontend"
        frontend.mkdir()
        (frontend / "index.html").write_text("<html>desktop</html>", encoding="utf-8")
        self.token = "phase4a-" + ("x" * 56)
        self.app = create_app(
            storage_root=root / "library",
            preview_root=root / "previews",
            frontend_dist=frontend,
            desktop_token=self.token,
        )
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.temporary.cleanup()

    def authenticate(self):
        response = self.client.post(
            "/api/desktop/session",
            headers={DESKTOP_TOKEN_HEADER: self.token},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def test_api_is_locked_until_one_time_bootstrap_sets_strict_cookie(self):
        self.assertEqual(self.client.get("/api/health").status_code, 401)
        self.assertEqual(self.client.get("/health").status_code, 401)
        # Static SPA assets can load so the in-memory native credential can
        # establish the session, but API/media routes remain inaccessible.
        self.assertEqual(self.client.get("/").status_code, 200)

        rejected = self.client.post(
            "/api/desktop/session",
            headers={DESKTOP_TOKEN_HEADER: "wrong"},
        )
        self.assertEqual(rejected.status_code, 401)

        accepted = self.authenticate()
        cookie = accepted.headers["set-cookie"]
        self.assertIn(DESKTOP_SESSION_COOKIE, cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)

        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["desktop"])
        self.assertIn("frame-ancestors 'none'", health.headers["content-security-policy"])
        self.assertEqual(health.headers["referrer-policy"], "no-referrer")
        self.assertEqual(health.headers["x-content-type-options"], "nosniff")

        reused = self.client.post(
            "/api/desktop/session",
            headers={DESKTOP_TOKEN_HEADER: self.token},
        )
        self.assertEqual(reused.status_code, 401)

    def test_native_path_is_copied_into_managed_storage_without_disclosure(self):
        self.authenticate()
        source = Path(self.temporary.name) / "Selected document.txt"
        source.write_text("A short desktop document.", encoding="utf-8")
        provider = _DesktopDummyProvider()

        with (
            mock.patch("web_api.main.providers.get_provider", return_value=provider),
            mock.patch("web_api.main._start_job"),
        ):
            response = self.client.post(
                "/api/jobs",
                data={
                    "desktop_path": str(source.resolve()),
                    "provider": provider.id,
                    "voice": "voice",
                    "output_format": "mp3",
                },
            )

        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(body["filename"], source.name)
        self.assertNotIn(str(source.parent), response.text)
        stored = self.app.state.job_store.get(body["id"])
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.input_path.read_text(encoding="utf-8"), source.read_text())
        self.assertNotEqual(stored.input_path, source)

    def test_native_path_is_rejected_outside_desktop_mode(self):
        root = Path(self.temporary.name) / "ordinary"
        source = Path(self.temporary.name) / "ordinary.txt"
        source.write_text("ordinary", encoding="utf-8")
        ordinary = create_app(
            storage_root=root / "jobs",
            preview_root=root / "previews",
            frontend_dist=root / "missing-frontend",
            desktop_token="",
        )
        provider = _DesktopDummyProvider()
        with (
            TestClient(ordinary) as client,
            mock.patch("web_api.main.providers.get_provider", return_value=provider),
        ):
            response = client.post(
                "/api/jobs",
                data={
                    "desktop_path": str(source.resolve()),
                    "provider": provider.id,
                    "voice": "voice",
                },
            )
        self.assertEqual(response.status_code, 422)
        self.assertIn("desktop app", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
