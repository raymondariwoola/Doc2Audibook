from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "build_desktop.py"
SPEC = importlib.util.spec_from_file_location("desktop_build", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
desktop_build = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = desktop_build
SPEC.loader.exec_module(desktop_build)


class NativeHostTests(unittest.TestCase):
    def test_windows_x64_host_is_accepted_and_cross_architecture_is_rejected(self):
        verbose = "rustc 1.97.0\nhost: x86_64-pc-windows-msvc\n"
        target = desktop_build.parse_native_host(
            verbose, system_name="Windows", machine_name="AMD64"
        )
        self.assertEqual(target.triple, "x86_64-pc-windows-msvc")
        self.assertEqual(target.sidecar_name, "doc2audiobook-sidecar-x86_64-pc-windows-msvc.exe")

        with self.assertRaisesRegex(
            desktop_build.DesktopBuildError, "Cross-compilation"
        ):
            desktop_build.parse_native_host(
                verbose, system_name="Windows", machine_name="ARM64"
            )

    def test_native_apple_silicon_and_intel_hosts_are_supported(self):
        for triple, machine in (
            ("aarch64-apple-darwin", "arm64"),
            ("x86_64-apple-darwin", "x86_64"),
        ):
            with self.subTest(triple=triple):
                target = desktop_build.parse_native_host(
                    f"host: {triple}\n",
                    system_name="Darwin",
                    machine_name=machine,
                )
                self.assertEqual(target.triple, triple)
                self.assertEqual(target.executable_suffix, "")

    def test_unsupported_host_is_rejected(self):
        with self.assertRaisesRegex(
            desktop_build.DesktopBuildError, "Unsupported native Rust host"
        ):
            desktop_build.parse_native_host(
                "host: x86_64-unknown-linux-gnu\n",
                system_name="Linux",
                machine_name="x86_64",
            )


class FFmpegPolicyTests(unittest.TestCase):
    def test_nonfree_build_is_always_rejected(self):
        for allowed in (False, True):
            with self.subTest(allowed=allowed), self.assertRaisesRegex(
                desktop_build.DesktopBuildError, "enable-nonfree"
            ):
                desktop_build.classify_ffmpeg_build(
                    "configuration: --enable-gpl --enable-nonfree",
                    command_succeeded=True,
                    allow_unreviewed=allowed,
                )

    def test_gpl_and_unknown_builds_require_explicit_override(self):
        cases = (
            ("configuration: --enable-gpl --enable-libx264", True),
            ("configuration unavailable", False),
        )
        for configuration, succeeded in cases:
            with self.subTest(configuration=configuration):
                with self.assertRaisesRegex(
                    desktop_build.DesktopBuildError,
                    "allow-unreviewed-ffmpeg",
                ):
                    desktop_build.classify_ffmpeg_build(
                        configuration,
                        command_succeeded=succeeded,
                        allow_unreviewed=False,
                    )
                classification, override = desktop_build.classify_ffmpeg_build(
                    configuration,
                    command_succeeded=succeeded,
                    allow_unreviewed=True,
                )
                self.assertTrue(classification.endswith("-unreviewed"))
                self.assertTrue(override)

    def test_candidate_lgpl_build_does_not_need_override(self):
        classification, override = desktop_build.classify_ffmpeg_build(
            "configuration: --enable-shared --disable-static",
            command_succeeded=True,
            allow_unreviewed=False,
        )
        self.assertEqual(classification, "lgpl-compatible-candidate")
        self.assertFalse(override)


class StagingTests(unittest.TestCase):
    def test_staging_is_complete_deterministic_and_edge_only(self):
        target = desktop_build.SUPPORTED_HOSTS["x86_64-pc-windows-msvc"]
        audit = desktop_build.FFmpegAudit(
            version_output="ffmpeg version test",
            build_configuration="configuration: --enable-gpl",
            classification="gpl-unreviewed",
            override_used=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tauri = root / "web" / "src-tauri"
            frontend = root / "dist"
            frontend.joinpath("assets").mkdir(parents=True)
            frontend.joinpath("index.html").write_text(
                "<html>test</html>", encoding="utf-8"
            )
            frontend.joinpath("assets", "app.js").write_text(
                "console.log('test')", encoding="utf-8"
            )
            sidecar = root / "doc2audiobook-sidecar.exe"
            ffmpeg = root / "ffmpeg.exe"
            ffprobe = root / "ffprobe.exe"
            sidecar.write_bytes(b"sidecar")
            ffmpeg.write_bytes(b"ffmpeg")
            ffprobe.write_bytes(b"ffprobe")

            provenance_path, overlay_path = desktop_build.stage_payload(
                tauri_dir=tauri,
                target=target,
                sidecar_source=sidecar,
                frontend_source=frontend,
                ffmpeg_source=ffmpeg,
                ffprobe_source=ffprobe,
                ffmpeg_audit=audit,
            )
            first_provenance = provenance_path.read_bytes()
            first_overlay = overlay_path.read_bytes()

            # A stale staged file is removed and rerunning with the same inputs
            # writes byte-identical JSON manifests.
            tauri.joinpath("resources", "frontend", "stale.js").write_text(
                "stale", encoding="utf-8"
            )
            desktop_build.stage_payload(
                tauri_dir=tauri,
                target=target,
                sidecar_source=sidecar,
                frontend_source=frontend,
                ffmpeg_source=ffmpeg,
                ffprobe_source=ffprobe,
                ffmpeg_audit=audit,
            )
            self.assertFalse(
                tauri.joinpath("resources", "frontend", "stale.js").exists()
            )
            self.assertEqual(first_provenance, provenance_path.read_bytes())
            self.assertEqual(first_overlay, overlay_path.read_bytes())

            provenance = json.loads(first_provenance)
            self.assertEqual(provenance["provider_scope"]["supported"], ["edge"])
            self.assertFalse(provenance["provider_scope"]["local_voice_models_bundled"])
            self.assertEqual(
                provenance["artifact_policy"]["distribution"],
                "internal-development-only",
            )
            self.assertFalse(
                provenance["artifact_policy"]["public_distribution_approved"]
            )
            self.assertEqual(
                provenance["sidecar"]["sha256"],
                desktop_build.sha256_file(sidecar),
            )
            self.assertIn("assets/app.js", provenance["frontend"]["files"])

            overlay = json.loads(first_overlay)
            self.assertEqual(
                overlay["bundle"]["externalBin"],
                ["binaries/doc2audiobook-sidecar"],
            )
            resources = overlay["bundle"]["resources"]
            self.assertEqual(
                resources["resources/frontend/index.html"],
                "frontend/index.html",
            )
            self.assertEqual(
                resources["resources/tools/ffmpeg.exe"], "tools/ffmpeg.exe"
            )
            self.assertEqual(
                resources[
                    "resources/licenses/desktop-build-provenance.json"
                ],
                "licenses/desktop-build-provenance.json",
            )

    def test_default_cli_action_is_prepare(self):
        args = desktop_build.build_parser().parse_args([])
        self.assertFalse(args.bundle)
        self.assertFalse(args.debug)
        self.assertIsNone(args.ffmpeg_dir)

    def test_pyinstaller_command_is_one_spec_driven_native_build(self):
        command = desktop_build.pyinstaller_command()
        self.assertEqual(command[:3], [desktop_build.sys.executable, "-m", "PyInstaller"])
        self.assertIn("--clean", command)
        self.assertEqual(Path(command[-1]), desktop_build.SPEC_PATH)
        recipe = desktop_build.SPEC_PATH.read_text(encoding="utf-8")
        self.assertIn("doc2audiobook-edge-only-hook.py", recipe)
        self.assertIn("_providers._REGISTRY.clear()", recipe)
        self.assertIn('"kokoro_onnx"', recipe)
        self.assertIn('"piper"', recipe)


if __name__ == "__main__":
    unittest.main()
