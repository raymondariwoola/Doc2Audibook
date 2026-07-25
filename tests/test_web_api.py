import asyncio
import contextlib
import io
import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import narrate
import providers
from web_api.main import (
    DEFAULT_STORAGE_ROOT,
    MAX_RULES,
    REPO_ROOT,
    create_app,
)
from web_api import __main__ as web_cli


class DummyProvider(providers.TTSProvider):
    id = "dummy"
    label = "Test narrator"

    def __init__(self, *, available=True, supports_pitch=True, supports_marks=False):
        self.available = available
        self.supports_pitch = supports_pitch
        self.supports_marks = supports_marks

    def is_available(self):
        return self.available

    def cache_identity(self):
        return "dummy:test-model"

    def capabilities(self):
        return providers.Capabilities(
            provider_id=self.id,
            supports_pitch=self.supports_pitch,
            supports_word_marks=self.supports_marks,
            notes="Safe test provider",
        )

    async def synthesize_with_marks(self, text, voice_id, out_path, *, rate, pitch):
        out_path.write_bytes(b"marked-audio")
        return [
            {"text": "Hello", "start": 0.0, "end": 0.4},
            {"text": "world", "start": 0.4, "end": 0.9},
        ]

    async def list_voices(self):
        return [
            providers.VoiceInfo(
                id="en-test-one",
                label="One",
                gender="Female",
                locale="en-US",
                provider=self.id,
            ),
            providers.VoiceInfo(
                id="fr-test-two",
                label="Two",
                gender="Male",
                locale="fr-FR",
                provider=self.id,
            ),
        ]

    async def synthesize(self, text, voice_id, out_path, *, rate, pitch):
        out_path.write_bytes(b"audio")


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.storage_root = root / "jobs"
        self.preview_root = root / "previews"
        self.provider = DummyProvider()
        self.provider_patches = [
            mock.patch("web_api.main.providers.all_provider_ids", return_value=["dummy"]),
            mock.patch(
                "web_api.main.providers.get_provider",
                side_effect=lambda provider_id="dummy": (
                    self.provider
                    if provider_id == "dummy"
                    else (_ for _ in ()).throw(ValueError(f"Unknown provider '{provider_id}'"))
                ),
            ),
        ]
        for patcher in self.provider_patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = create_app(
            storage_root=self.storage_root,
            preview_root=self.preview_root,
        )
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.temporary.cleanup()

    def test_health_provider_and_filtered_voice_discovery(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")

        provider_response = self.client.get("/api/providers")
        self.assertEqual(provider_response.status_code, 200)
        provider = provider_response.json()["providers"][0]
        self.assertEqual(provider["id"], "dummy")
        self.assertTrue(provider["available"])
        self.assertTrue(provider["capabilities"]["supports_pitch"])

        voices = self.client.get(
            "/api/providers/dummy/voices",
            params={"locale": "en-US", "q": "one"},
        )
        self.assertEqual(voices.status_code, 200)
        self.assertEqual(
            voices.json()["voices"],
            [{
                "id": "en-test-one",
                "label": "One",
                "gender": "Female",
                "locale": "en-US",
                "provider": "dummy",
            }],
        )

    def test_preview_returns_audio_and_reuses_machine_local_cache(self):
        calls = 0

        async def fake_preview(text, voice, rate, pitch, out_path, provider=None):
            nonlocal calls
            calls += 1
            self.assertEqual((voice, rate, pitch), ("en-test-one", "-5%", "+3Hz"))
            out_path.write_bytes(b"preview-audio")

        payload = {
            "provider": "dummy",
            "voice": "en-test-one",
            "rate": -5,
            "pitch": 3,
        }
        with mock.patch(
            "web_api.main.narrate.synthesize_to_file", side_effect=fake_preview
        ):
            first = self.client.post("/api/preview", json=payload)
            second = self.client.post("/api/preview", json=payload)

        self.assertEqual(first.status_code, 200)
        body = first.json()
        self.assertTrue(body["audio_url"].startswith("/api/preview/file/"))
        self.assertTrue(body["audio_url"].endswith(".mp3"))
        # A provider without word timings reports no marks.
        self.assertIsNone(body["marks"])
        self.assertEqual(second.json()["audio_url"], body["audio_url"])
        self.assertEqual(calls, 1)

        audio = self.client.get(body["audio_url"])
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(audio.headers["content-type"], "audio/mpeg")
        self.assertEqual(audio.content, b"preview-audio")

    def test_preview_returns_word_marks_when_the_provider_supports_them(self):
        self.provider.supports_marks = True
        response = self.client.post(
            "/api/preview",
            json={"provider": "dummy", "voice": "en-test-one", "text": "Hello world"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([mark["text"] for mark in body["marks"]], ["Hello", "world"])
        self.assertEqual(body["text"], "Hello world")

        audio = self.client.get(body["audio_url"])
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(audio.content, b"marked-audio")

        # Marks persist beside the cached audio, so a cache hit still returns them.
        cached = self.client.post(
            "/api/preview",
            json={"provider": "dummy", "voice": "en-test-one", "text": "Hello world"},
        )
        self.assertEqual([mark["text"] for mark in cached.json()["marks"]], ["Hello", "world"])

    def test_preview_file_route_rejects_path_traversal(self):
        for name in ["../secret.mp3", "not-a-digest.mp3", "%2e%2e/x.mp3"]:
            self.assertEqual(
                self.client.get(f"/api/preview/file/{name}").status_code,
                404,
            )

    def test_preview_hides_local_exception_details_but_logs_them(self):
        secret_path = r"C:\Users\private\models\voice.onnx"

        async def fail_preview(*args, **kwargs):
            raise RuntimeError(f"Could not read {secret_path}")

        with mock.patch(
            "web_api.main.narrate.synthesize_to_file", side_effect=fail_preview
        ), self.assertLogs("web_api.main", level="ERROR") as logs:
            response = self.client.post(
                "/api/preview",
                json={"provider": "dummy", "voice": "en-test-one"},
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn("Check that the selected provider", response.json()["detail"])
        self.assertNotIn(secret_path, response.text)
        self.assertIn(secret_path, "\n".join(logs.output))

    def test_job_lifecycle_sse_and_download(self):
        async def fake_build(input_path, output, **kwargs):
            self.assertEqual(Path(input_path).read_text(encoding="utf-8"), "Book text")
            kwargs["on_stage"]("extracted", {"chunks": 2})
            kwargs["on_chunk"](1, 2, 4, "done")
            await asyncio.sleep(0)
            kwargs["on_chunk"](2, 2, 5, "done")
            kwargs["on_stage"]("assembling", {})
            Path(output).write_bytes(b"finished-audio")
            kwargs["on_stage"]("done", {})
            return narrate.AudiobookResult(
                output=Path(output),
                fmt=kwargs["fmt"],
                duration=12.5,
                size=14,
                n_blocks=1,
                n_paragraphs=1,
                n_chunks=2,
                total_chars=9,
                work_dir=Path(kwargs["work_dir"]),
            )

        with mock.patch(
            "web_api.main.narrate.build_audiobook_async", side_effect=fake_build
        ):
            created = self.client.post(
                "/api/jobs",
                files={"document": ("chapter.txt", b"Book text", "text/plain")},
                data={
                    "provider": "dummy",
                    "voice": "en-test-one",
                    "rate": "-4",
                    "pitch": "2",
                    "output_format": "mp3",
                    "rules": json.dumps([{"from": "Book", "to": "Story"}]),
                },
            )
            self.assertEqual(created.status_code, 202)
            job_id = created.json()["id"]
            terminal = self._wait_for_terminal(job_id)

            self.assertEqual(terminal["status"], "completed")
            self.assertEqual(terminal["progress"], 1.0)
            self.assertEqual(terminal["result"]["chunks"], 2)
            self.assertEqual(
                terminal["result"]["download_url"],
                f"/api/jobs/{job_id}/output",
            )

            with self.client.stream("GET", f"/api/jobs/{job_id}/events") as events:
                event_text = "".join(events.iter_text())
            self.assertIn("event: complete", event_text)
            self.assertIn(f'"id":"{job_id}"', event_text)

            output = self.client.get(f"/api/jobs/{job_id}/output")
            self.assertEqual(output.status_code, 200)
            self.assertEqual(output.content, b"finished-audio")
            self.assertIn("chapter.mp3", output.headers["content-disposition"])

    def test_equivalent_requests_reuse_the_same_job(self):
        release = asyncio.Event()

        async def slow_build(input_path, output, **kwargs):
            await release.wait()
            Path(output).write_bytes(b"audio")
            return narrate.AudiobookResult(
                output=Path(output),
                fmt="mp3",
                duration=None,
                size=5,
                n_blocks=1,
                n_paragraphs=1,
                n_chunks=1,
                total_chars=4,
                work_dir=Path(kwargs["work_dir"]),
            )

        form = {
            "provider": "dummy",
            "voice": "en-test-one",
            "output_format": "mp3",
        }
        with mock.patch(
            "web_api.main.narrate.build_audiobook_async", side_effect=slow_build
        ) as build:
            first = self.client.post(
                "/api/jobs",
                files={"document": ("same.txt", b"Same", "text/plain")},
                data=form,
            )
            second = self.client.post(
                "/api/jobs",
                files={"document": ("same.txt", b"Same", "text/plain")},
                data=form,
            )
            self.assertEqual(first.json()["id"], second.json()["id"])
            self.assertEqual(build.call_count, 1)
            self.client.portal.call(release.set)
            self._wait_for_terminal(first.json()["id"])

    def test_validation_and_failed_jobs_are_reported_without_output(self):
        unsupported = self.client.post(
            "/api/jobs",
            files={"document": ("archive.zip", b"x", "application/zip")},
            data={"provider": "dummy", "voice": "voice"},
        )
        self.assertEqual(unsupported.status_code, 415)

        secret_path = r"D:\private\unfinished\chunk.mp3"

        async def fail(*args, **kwargs):
            raise RuntimeError(f"synthetic failure at {secret_path}")

        with mock.patch(
            "web_api.main.narrate.build_audiobook_async", side_effect=fail
        ), self.assertLogs("web_api.main", level="ERROR") as logs:
            created = self.client.post(
                "/api/jobs",
                files={"document": ("book.md", b"# Book", "text/markdown")},
                data={"provider": "dummy", "voice": "voice"},
            )
            job_id = created.json()["id"]
            terminal = self._wait_for_terminal(job_id)
        self.assertEqual(terminal["status"], "failed")
        self.assertIn("Check the selected provider and ffmpeg", terminal["error"])
        self.assertNotIn(secret_path, json.dumps(terminal))
        self.assertIn(secret_path, "\n".join(logs.output))
        output = self.client.get(f"/api/jobs/{job_id}/output")
        self.assertEqual(output.status_code, 409)

    def test_rule_payloads_are_bounded_and_require_string_items(self):
        cases = [
            (
                [{"from": str(index), "to": "x"} for index in range(MAX_RULES + 1)],
                "at most",
            ),
            ([{"from": "", "to": "x"}], "empty"),
            ([{"from": "x" * 501, "to": "y"}], "500"),
            ([{"from": "x", "to": "y" * 2001}], "2000"),
            ([{"from": "x", "to": 3}], "strings"),
            ("x" * (64 * 1024 + 1), "65536"),
        ]
        for index, (rules, expected) in enumerate(cases):
            with self.subTest(index=index):
                encoded = rules if isinstance(rules, str) else json.dumps(rules)
                response = self.client.post(
                    "/api/jobs",
                    files={"document": ("book.txt", b"Book", "text/plain")},
                    data={
                        "provider": "dummy",
                        "voice": "voice",
                        "rules": encoded,
                    },
                )
                self.assertEqual(response.status_code, 422)
                self.assertIn(expected, response.json()["detail"])

    def test_only_trusted_hosts_and_browser_origins_reach_post_endpoints(self):
        untrusted_host = self.client.get(
            "/api/health", headers={"Host": "evil.example"}
        )
        self.assertEqual(untrusted_host.status_code, 400)
        self.assertEqual(
            self.client.get(
                "/api/health", headers={"Host": "localhost:8000"}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                "/api/health", headers={"Host": "[::1]:8000"}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                "/api/health", headers={"Host": "[2001:db8::1]:8000"}
            ).status_code,
            400,
        )

        with mock.patch(
            "web_api.main.narrate.synthesize_to_file"
        ) as synthesis:
            rejected = self.client.post(
                "/api/preview",
                json={"provider": "dummy", "voice": "voice"},
                headers={
                    "Host": "127.0.0.1:8000",
                    "Origin": "https://evil.example",
                },
            )
        self.assertEqual(rejected.status_code, 403)
        synthesis.assert_not_called()

        with mock.patch("web_api.main._save_upload") as save_upload:
            rejected_job = self.client.post(
                "/api/jobs",
                files={"document": ("book.txt", b"Book", "text/plain")},
                data={"provider": "dummy", "voice": "voice"},
                headers={
                    "Host": "127.0.0.1:8000",
                    "Origin": "https://evil.example",
                },
            )
        self.assertEqual(rejected_job.status_code, 403)
        save_upload.assert_not_called()

        async def preview_ok(text, voice, rate, pitch, out_path, provider=None):
            out_path.write_bytes(b"preview")

        with mock.patch(
            "web_api.main.narrate.synthesize_to_file", side_effect=preview_ok
        ):
            vite = self.client.post(
                "/api/preview",
                json={"provider": "dummy", "voice": "voice", "text": "Vite"},
                headers={
                    "Host": "127.0.0.1:8000",
                    "Origin": "http://localhost:5173",
                },
            )
            same_origin = self.client.post(
                "/api/preview",
                json={"provider": "dummy", "voice": "voice", "text": "Same origin"},
                headers={
                    "Host": "localhost:8000",
                    "Origin": "http://localhost:8000",
                },
            )
        self.assertEqual(vite.status_code, 200)
        self.assertEqual(same_origin.status_code, 200)

    def test_launcher_warns_when_frontend_build_is_missing(self):
        missing = Path(self.temporary.name) / "missing-dist"
        stderr = io.StringIO()
        with mock.patch.object(web_cli, "DEFAULT_FRONTEND_DIST", missing), mock.patch(
            "web_api.__main__.uvicorn.run"
        ) as run, mock.patch.object(
            web_cli, "_require_available_port"
        ) as require_port, contextlib.redirect_stderr(stderr):
            web_cli.main(["--host", "127.0.0.1", "--port", "8123"])
        self.assertIn("API will still be available", stderr.getvalue())
        self.assertIn("npm ci && npm run build", stderr.getvalue())
        run.assert_called_once_with(
            "web_api.main:app",
            host="127.0.0.1",
            port=8123,
            reload=False,
            workers=1,
        )
        require_port.assert_called_once_with("127.0.0.1", 8123)

    def test_launcher_reports_an_occupied_port_before_starting_uvicorn(self):
        probe = mock.MagicMock()
        probe.__enter__.return_value = probe
        probe.bind.side_effect = OSError("busy")
        with mock.patch("web_api.__main__.socket.socket", return_value=probe):
            with self.assertRaisesRegex(
                SystemExit,
                r"127\.0\.0\.1:8000 is unavailable.*--port 8001",
            ):
                web_cli._require_available_port("127.0.0.1", 8000)

    def test_built_frontend_has_spa_fallback_without_swallowing_api(self):
        root = Path(self.temporary.name)
        frontend = root / "dist"
        frontend.mkdir()
        frontend.joinpath("index.html").write_text(
            "<html>phase-three-spa</html>", encoding="utf-8"
        )
        frontend.joinpath("asset.txt").write_text("asset", encoding="utf-8")
        mounted = create_app(
            storage_root=root / "static-jobs",
            preview_root=root / "static-previews",
            frontend_dist=frontend,
        )
        with TestClient(mounted) as client:
            self.assertIn("phase-three-spa", client.get("/").text)
            self.assertIn("phase-three-spa", client.get("/library/book").text)
            self.assertEqual(client.get("/asset.txt").text, "asset")
            unknown_api = client.get("/api/does-not-exist")
            self.assertEqual(unknown_api.status_code, 404)
            self.assertNotIn("phase-three-spa", unknown_api.text)

    def test_default_storage_is_anchored_to_repository(self):
        self.assertTrue(DEFAULT_STORAGE_ROOT.is_absolute())
        self.assertEqual(DEFAULT_STORAGE_ROOT, REPO_ROOT / "outputs" / "web_jobs")

    def _hanging_build(self, release, payload=b"audio"):
        async def hanging(input_path, output, **kwargs):
            await release.wait()
            Path(output).write_bytes(payload)
            return narrate.AudiobookResult(
                output=Path(output),
                fmt=kwargs["fmt"],
                duration=None,
                size=len(payload),
                n_blocks=1,
                n_paragraphs=1,
                n_chunks=1,
                total_chars=4,
                work_dir=Path(kwargs["work_dir"]),
            )

        return hanging

    def test_restart_recovers_persisted_jobs_and_marks_interrupted(self):
        release = asyncio.Event()
        with mock.patch(
            "web_api.main.narrate.build_audiobook_async",
            side_effect=self._hanging_build(release),
        ):
            created = self.client.post(
                "/api/jobs",
                files={"document": ("live.txt", b"Live text", "text/plain")},
                data={"provider": "dummy", "voice": "en-test-one"},
            )
            self.assertEqual(created.status_code, 202)
            job_id = created.json()["id"]
            self._wait_for_status(job_id, "running")

            # A fresh server process over the same storage reloads persisted jobs
            # and recovers the one left running by the "restart".
            recovered = create_app(
                storage_root=self.storage_root, preview_root=self.preview_root
            )
            reloaded = recovered.state.job_store.get(job_id)
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded.status, "failed")
            self.assertIn("restarted", (reloaded.error or "").lower())

            # Once the original job finishes, a later restart reloads it as-is.
            self.client.portal.call(release.set)
            terminal = self._wait_for_terminal(job_id)
            self.assertEqual(terminal["status"], "completed")

        reopened = create_app(
            storage_root=self.storage_root, preview_root=self.preview_root
        )
        self.assertEqual(reopened.state.job_store.get(job_id).status, "completed")

    def test_active_job_limit_returns_429_for_new_submissions(self):
        release = asyncio.Event()
        with mock.patch("web_api.main.MAX_ACTIVE_JOBS", 1), mock.patch(
            "web_api.main.narrate.build_audiobook_async",
            side_effect=self._hanging_build(release),
        ):
            first = self.client.post(
                "/api/jobs",
                files={"document": ("one.txt", b"One", "text/plain")},
                data={"provider": "dummy", "voice": "en-test-one"},
            )
            self.assertEqual(first.status_code, 202)
            self._wait_for_status(first.json()["id"], "running")

            second = self.client.post(
                "/api/jobs",
                files={"document": ("two.txt", b"Two distinct", "text/plain")},
                data={"provider": "dummy", "voice": "en-test-one"},
            )
            self.assertEqual(second.status_code, 429)
            self.assertIn("active jobs", second.json()["detail"])

            self.client.portal.call(release.set)
            self._wait_for_terminal(first.json()["id"])

    def test_jobs_list_and_sse_event_ids_support_resume(self):
        async def fake_build(input_path, output, **kwargs):
            kwargs["on_chunk"](1, 1, 4, "done")
            Path(output).write_bytes(b"done-audio")
            return narrate.AudiobookResult(
                output=Path(output),
                fmt=kwargs["fmt"],
                duration=1.0,
                size=10,
                n_blocks=1,
                n_paragraphs=1,
                n_chunks=1,
                total_chars=4,
                work_dir=Path(kwargs["work_dir"]),
            )

        with mock.patch(
            "web_api.main.narrate.build_audiobook_async", side_effect=fake_build
        ):
            created = self.client.post(
                "/api/jobs",
                files={"document": ("chapter.txt", b"Book text", "text/plain")},
                data={"provider": "dummy", "voice": "en-test-one"},
            )
            job_id = created.json()["id"]
            self._wait_for_terminal(job_id)

        listing = self.client.get("/api/jobs")
        self.assertEqual(listing.status_code, 200)
        self.assertIn(job_id, [job["id"] for job in listing.json()["jobs"]])

        with self.client.stream("GET", f"/api/jobs/{job_id}/events") as events:
            text = "".join(events.iter_text())
        self.assertIn("event: complete", text)
        event_ids = re.findall(r"id: (\d+)", text)
        self.assertTrue(event_ids)

        # Reconnecting at the terminal event id closes without re-delivering it.
        with self.client.stream(
            "GET",
            f"/api/jobs/{job_id}/events",
            headers={"Last-Event-ID": event_ids[-1]},
        ) as again:
            replay = "".join(again.iter_text())
        self.assertNotIn("event: complete", replay)

    def _wait_for_status(self, job_id, status, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/jobs/{job_id}")
            if response.status_code == 200 and response.json()["status"] == status:
                return response.json()
            time.sleep(0.01)
        self.fail(f"Job {job_id} did not reach status {status!r}")

    def _wait_for_terminal(self, job_id, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/jobs/{job_id}")
            self.assertEqual(response.status_code, 200)
            job = response.json()
            if job["status"] in {"completed", "failed"}:
                return job
            time.sleep(0.01)
        self.fail(f"Job {job_id} did not finish")


if __name__ == "__main__":
    unittest.main()
