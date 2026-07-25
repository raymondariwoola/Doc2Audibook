import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bootstrap


class BootstrapWebTests(unittest.TestCase):
    def test_web_frontend_is_skipped_for_minimal_or_no_web(self):
        with mock.patch("bootstrap.shutil.which") as which:
            bootstrap.build_web_frontend(minimal=True, no_web=False)
            bootstrap.build_web_frontend(minimal=False, no_web=True)
        which.assert_not_called()

    def test_web_frontend_uses_clean_install_and_build(self):
        def executable(name):
            return f"C:/tools/{name}"

        with (
            mock.patch("bootstrap.shutil.which", side_effect=executable),
            mock.patch(
                "bootstrap.subprocess.check_output",
                return_value="v22.13.0\n",
            ),
            mock.patch("bootstrap.run") as run,
        ):
            bootstrap.build_web_frontend(minimal=False, no_web=False)

        self.assertEqual(
            run.call_args_list,
            [
                mock.call(["C:/tools/npm", "ci"], cwd=bootstrap.ROOT / "web"),
                mock.call(
                    ["C:/tools/npm", "run", "build"],
                    cwd=bootstrap.ROOT / "web",
                ),
            ],
        )

    def test_old_node_skips_frontend_with_clear_boundary(self):
        def executable(name):
            return f"C:/tools/{name}"

        with (
            mock.patch("bootstrap.shutil.which", side_effect=executable),
            mock.patch(
                "bootstrap.subprocess.check_output",
                return_value="v20.18.0\n",
            ),
            mock.patch("bootstrap.run") as run,
        ):
            bootstrap.build_web_frontend(minimal=False, no_web=False)

        run.assert_not_called()


class BootstrapDesktopTests(unittest.TestCase):
    def test_desktop_dependencies_use_the_pinned_requirements_file(self):
        self.assertTrue(bootstrap.DESKTOP_REQUIREMENTS.is_file())
        with mock.patch("bootstrap.run") as run:
            bootstrap.install_desktop_deps()

        run.assert_called_once_with(
            [
                str(bootstrap.venv_python()),
                "-m",
                "pip",
                "install",
                "-r",
                str(bootstrap.DESKTOP_REQUIREMENTS),
            ]
        )

    def test_regular_bootstrap_does_not_run_desktop_setup(self):
        with (
            mock.patch("bootstrap.ensure_venv"),
            mock.patch("bootstrap.install_deps"),
            mock.patch("bootstrap.build_web_frontend"),
            mock.patch("bootstrap.check_ffmpeg"),
            mock.patch("bootstrap.sync_voices"),
            mock.patch("bootstrap.install_desktop_deps") as install_desktop,
            mock.patch(
                "bootstrap.check_desktop_prerequisites"
            ) as check_prerequisites,
        ):
            self.assertEqual(bootstrap.main([]), 0)

        install_desktop.assert_not_called()
        check_prerequisites.assert_not_called()

    def test_desktop_setup_runs_after_the_normal_bootstrap(self):
        calls = []

        def record(name):
            return lambda *args, **kwargs: calls.append(name)

        with (
            mock.patch("bootstrap.ensure_venv", side_effect=record("venv")),
            mock.patch(
                "bootstrap.install_deps",
                side_effect=record("dependencies"),
            ),
            mock.patch(
                "bootstrap.build_web_frontend",
                side_effect=record("frontend"),
            ),
            mock.patch("bootstrap.check_ffmpeg", side_effect=record("ffmpeg")),
            mock.patch("bootstrap.sync_voices", side_effect=record("voices")),
            mock.patch(
                "bootstrap.install_desktop_deps",
                side_effect=record("desktop-dependencies"),
            ),
            mock.patch(
                "bootstrap.check_desktop_prerequisites",
                side_effect=record("desktop-prerequisites"),
            ),
        ):
            self.assertEqual(bootstrap.main(["--desktop"]), 0)

        self.assertEqual(
            calls,
            [
                "venv",
                "dependencies",
                "frontend",
                "ffmpeg",
                "voices",
                "desktop-dependencies",
                "desktop-prerequisites",
            ],
        )

    def test_windows_prerequisite_check_is_non_mutating_and_actionable(self):
        found = {
            "rustc": None,
            "cargo": None,
        }
        stdout = io.StringIO()
        with (
            mock.patch(
                "bootstrap.shutil.which",
                side_effect=lambda name: found.get(name),
            ),
            mock.patch("bootstrap.subprocess.run") as subprocess_run,
            mock.patch(
                "bootstrap._windows_webview2_available",
                return_value=False,
            ),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertFalse(
                bootstrap.check_desktop_prerequisites(platform_name="win32")
            )

        message = stdout.getvalue()
        self.assertIn("Rustlang.Rustup", message)
        self.assertIn("Microsoft.EdgeWebView2Runtime", message)
        subprocess_run.assert_not_called()

    def test_macos_prerequisite_check_reports_ready(self):
        stdout = io.StringIO()
        with (
            mock.patch(
                "bootstrap.shutil.which",
                side_effect=lambda name: f"/tools/{name}",
            ),
            mock.patch(
                "bootstrap._macos_xcode_cli_available",
                return_value=True,
            ),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertTrue(
                bootstrap.check_desktop_prerequisites(platform_name="darwin")
            )

        self.assertIn(
            "Native desktop prerequisites are ready",
            stdout.getvalue(),
        )

    def test_unsupported_platform_skips_platform_specific_probes(self):
        with (
            mock.patch(
                "bootstrap.shutil.which",
                side_effect=lambda name: f"/tools/{name}",
            ),
            mock.patch(
                "bootstrap._windows_webview2_available"
            ) as check_webview,
            mock.patch(
                "bootstrap._macos_xcode_cli_available"
            ) as check_xcode,
        ):
            self.assertFalse(
                bootstrap.check_desktop_prerequisites(platform_name="linux")
            )

        check_webview.assert_not_called()
        check_xcode.assert_not_called()

    def test_webview2_detection_checks_machine_and_per_user_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            local_app_data = Path(temporary) / "Local App Data"
            runtime = (
                local_app_data
                / "Microsoft"
                / "EdgeWebView"
                / "Application"
                / "123.0.0.0"
            )
            runtime.mkdir(parents=True)
            (runtime / "msedgewebview2.exe").touch()

            self.assertTrue(
                bootstrap._windows_webview2_available(
                    {"LOCALAPPDATA": str(local_app_data)}
                )
            )

    def test_macos_xcode_check_uses_xcode_select_read_only(self):
        completed = mock.Mock(returncode=0, stdout="/Library/Developer\n")
        with (
            mock.patch(
                "bootstrap.shutil.which",
                return_value="/usr/bin/xcode-select",
            ),
            mock.patch(
                "bootstrap.subprocess.run",
                return_value=completed,
            ) as run,
        ):
            self.assertTrue(bootstrap._macos_xcode_cli_available())

        run.assert_called_once_with(
            ["/usr/bin/xcode-select", "-p"],
            check=False,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
