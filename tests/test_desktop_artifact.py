from __future__ import annotations

import importlib.util
import platform
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "smoke_desktop_sidecar.py"
SPEC = importlib.util.spec_from_file_location("desktop_artifact_smoke", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
desktop_smoke = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = desktop_smoke
SPEC.loader.exec_module(desktop_smoke)


def _target_triple() -> str | None:
    machine = platform.machine().lower()
    if sys.platform == "win32" and machine in {"amd64", "x86_64"}:
        return "x86_64-pc-windows-msvc"
    if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        return "aarch64-apple-darwin"
    if sys.platform == "darwin" and machine in {"x86_64", "amd64"}:
        return "x86_64-apple-darwin"
    return None


TARGET = _target_triple()
SUFFIX = ".exe" if sys.platform == "win32" else ""
TAURI = ROOT / "web" / "src-tauri"
SIDECAR = TAURI / "binaries" / f"doc2audiobook-sidecar-{TARGET}{SUFFIX}"
FRONTEND = TAURI / "resources" / "frontend"
FFMPEG = TAURI / "resources" / "tools" / f"ffmpeg{SUFFIX}"
FFPROBE = TAURI / "resources" / "tools" / f"ffprobe{SUFFIX}"
ARTIFACT_READY = TARGET is not None and all(
    path.is_file() for path in (SIDECAR, FRONTEND / "index.html", FFMPEG, FFPROBE)
)


class FrozenDesktopArtifactTests(unittest.TestCase):
    @unittest.skipUnless(
        ARTIFACT_READY,
        "run scripts/build_desktop.py first to stage the native desktop payload",
    )
    def test_frozen_sidecar_from_unrelated_unicode_working_directory(self):
        result = desktop_smoke.smoke_sidecar(
            executable=SIDECAR,
            frontend=FRONTEND,
            ffmpeg=FFMPEG,
            ffprobe=FFPROBE,
        )
        self.assertEqual(result.provider_ids, ("edge",))
        self.assertTrue(result.log_files)


if __name__ == "__main__":
    unittest.main()
