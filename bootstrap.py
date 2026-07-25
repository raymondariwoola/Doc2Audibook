#!/usr/bin/env python3
"""
One-command setup for Doc2Audiobook on any machine (macOS / Windows / Linux).

    python bootstrap.py

What it does (idempotent — safe to re-run any time, e.g. after `git pull`):
  1. Creates a local virtual environment in .venv (if missing).
  2. Installs pinned Python dependencies (core + both UIs + local providers).
  3. Installs and builds the Phase 3 React frontend when Node.js is available.
  4. Checks for ffmpeg and prints OS-specific install instructions if missing.
  5. Downloads the offline voice models listed in voices.manifest.json
     (Piper + Kokoro) via sync_voices.py — skipping any already present.

This is the command to run whenever you switch machines: pull the repo, run it,
and you're on parity with wherever you left off (code + dependencies + voices).
Generated audiobooks and the resumable chunk cache are machine-local and are NOT
synced. Frontend dependencies/build output are machine-local too; bootstrap
recreates them from `web/package-lock.json`.

Flags:
  --minimal     core CLI dependencies only (no UI, no local providers, no models)
  --no-ui       skip the Gradio UI dependency
  --no-web      skip the FastAPI backend and React frontend
  --no-local    skip the offline local-provider packages (Piper/Kokoro)
  --no-voices   skip downloading voice models
  --desktop     additionally install Phase 4A build dependencies and check the
                native toolchain (does not build or install system prerequisites)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PIP_VERSION = "26.1.2"
SETUPTOOLS_VERSION = "83.0.0"
MIN_NODE_VERSION = (22, 13, 0)
DESKTOP_REQUIREMENTS = ROOT / "requirements-desktop.txt"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(cmd: list[str], **kw) -> None:
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, check=True, **kw)


def step(msg: str) -> None:
    print(f"\n=== {msg} ===")


def ensure_venv() -> None:
    step("1/5  Virtual environment")
    if venv_python().exists():
        check = subprocess.run(
            [str(venv_python()), "-m", "pip", "--version"],
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            print("  .venv exists but pip is missing; repairing interrupted setup...")
            run([str(venv_python()), "-m", "ensurepip", "--upgrade"])
        else:
            print(f"  .venv already exists ({venv_python()})")
        return
    print(f"  creating {VENV} …")
    venv.EnvBuilder(with_pip=True, upgrade_deps=True).create(str(VENV))


def install_deps(
    minimal: bool,
    no_ui: bool,
    no_web: bool,
    no_local: bool,
) -> None:
    step("2/5  Python dependencies")
    py = str(venv_python())
    run([py, "-m", "pip", "install", f"pip=={PIP_VERSION}"])
    run([py, "-m", "pip", "install", f"setuptools=={SETUPTOOLS_VERSION}"])
    req_files = [ROOT / "requirements.txt"]
    if not minimal:
        if not no_ui:
            req_files.append(ROOT / "requirements-ui.txt")
        if not no_web:
            req_files.append(ROOT / "requirements-web.txt")
        if not no_local:
            req_files.append(ROOT / "requirements-local.txt")
    args = [py, "-m", "pip", "install"]
    for rf in req_files:
        args += ["-r", str(rf)]
    run(args)


def build_web_frontend(minimal: bool, no_web: bool) -> None:
    step("3/5  Phase 3 web frontend")
    if minimal or no_web:
        print("  skipped (per flags)")
        return

    node = shutil.which("node")
    npm = shutil.which("npm")
    if node is None or npm is None:
        print("  [warning] Node.js/npm was not found; the Gradio prototype still works.")
        print("  Install Node.js 22.13+ and re-run bootstrap to build the React app.")
        return

    raw_version = subprocess.check_output([node, "--version"], text=True).strip()
    try:
        node_version = tuple(
            int(part) for part in raw_version.removeprefix("v").split(".")[:3]
        )
    except ValueError:
        node_version = (0, 0, 0)
    if node_version < MIN_NODE_VERSION:
        print(
            f"  [warning] Node.js {raw_version} is too old; "
            "the Gradio prototype still works."
        )
        print("  Install Node.js 22.13+ and re-run bootstrap to build the React app.")
        return

    web_root = ROOT / "web"
    run([npm, "ci"], cwd=web_root)
    run([npm, "run", "build"], cwd=web_root)


def check_ffmpeg() -> None:
    step("4/5  ffmpeg (external dependency)")
    if shutil.which("ffmpeg"):
        print("  [ok] ffmpeg found")
        return
    instructions = {
        "darwin": "brew install ffmpeg",
        "win32": "winget install Gyan.FFmpeg   (or: choco install ffmpeg)",
        "linux": "sudo apt install ffmpeg   (Debian/Ubuntu)  |  sudo dnf install ffmpeg (Fedora)",
    }
    plat = "win32" if os.name == "nt" else sys.platform
    hint = instructions.get(plat, "See https://ffmpeg.org/download.html")
    print("  [warning] ffmpeg NOT found on PATH. Install it, then re-run this script:")
    print(f"     {hint}")


def sync_voices(minimal: bool, no_local: bool, no_voices: bool) -> None:
    step("5/5  Offline voice models")
    if minimal or no_local or no_voices:
        print("  skipped (per flags). Run `python sync_voices.py` later to fetch models.")
        return
    run([str(venv_python()), str(ROOT / "sync_voices.py")])


def install_desktop_deps() -> None:
    """Install the pinned, Python-side desktop build dependencies."""
    step("Desktop 1/2  Python build dependencies")
    if not DESKTOP_REQUIREMENTS.is_file():
        raise FileNotFoundError(
            f"Desktop requirements file is missing: {DESKTOP_REQUIREMENTS}"
        )
    run(
        [
            str(venv_python()),
            "-m",
            "pip",
            "install",
            "-r",
            str(DESKTOP_REQUIREMENTS),
        ]
    )


def _windows_webview2_available(
    environment: dict[str, str] | None = None,
) -> bool:
    """Return whether an Evergreen WebView2 runtime is installed.

    WebView2 is not normally placed on PATH. Check the documented machine and
    per-user installation roots without changing the host.
    """
    env = os.environ if environment is None else environment
    roots = []
    for variable in ("ProgramFiles(x86)", "PROGRAMFILES", "LOCALAPPDATA"):
        value = env.get(variable)
        if value:
            roots.append(Path(value) / "Microsoft" / "EdgeWebView" / "Application")
    for root in roots:
        try:
            if any(
                executable.is_file()
                for executable in root.glob("*/msedgewebview2.exe")
            ):
                return True
        except OSError:
            # A locked-down machine may deny one installation root. Continue
            # checking the remaining machine/per-user locations.
            continue
    return False


def _macos_xcode_cli_available() -> bool:
    """Return whether macOS has an active Xcode Command Line Tools directory."""
    xcode_select = shutil.which("xcode-select")
    if xcode_select is None:
        return False
    try:
        result = subprocess.run(
            [xcode_select, "-p"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def check_desktop_prerequisites(platform_name: str | None = None) -> bool:
    """Report native Phase 4A prerequisites without installing any of them.

    Returns True when every prerequisite checked for the current platform is
    present. A missing tool is a warning rather than a bootstrap failure so the
    regular CLI and web interfaces remain usable.
    """
    step("Desktop 2/2  Native prerequisites")
    platform_id = platform_name or sys.platform
    missing_rust = [
        name for name in ("rustc", "cargo") if shutil.which(name) is None
    ]
    ready = not missing_rust
    if missing_rust:
        print(f"  [warning] Missing Rust tools: {', '.join(missing_rust)}")
        if platform_id == "win32":
            print("  Install Rust, then reopen the terminal:")
            print("     winget install --id Rustlang.Rustup --exact")
            print("     rustup default stable-msvc")
        else:
            print("  Install Rust, then reopen the terminal:")
            print(
                "     curl --proto '=https' --tlsv1.2 -sSf "
                "https://sh.rustup.rs | sh"
            )
    else:
        print("  [ok] Rust compiler and Cargo found")

    if platform_id == "win32":
        if _windows_webview2_available():
            print("  [ok] Microsoft Edge WebView2 Runtime found")
        else:
            ready = False
            print("  [warning] Microsoft Edge WebView2 Runtime was not found.")
            print("  Install it, then rerun bootstrap:")
            print(
                "     winget install --id Microsoft.EdgeWebView2Runtime --exact"
            )
    elif platform_id == "darwin":
        if _macos_xcode_cli_available():
            print("  [ok] Xcode Command Line Tools found")
        else:
            ready = False
            print("  [warning] Xcode Command Line Tools were not found.")
            print("  Install them, then rerun bootstrap:")
            print("     xcode-select --install")
    else:
        ready = False
        print(
            "  [warning] Phase 4A packaging is currently supported only on "
            "Windows x64 and macOS."
        )

    if ready:
        print("  [ok] Native desktop prerequisites are ready")
    else:
        print(
            "  Desktop packaging is not ready yet; the regular application "
            "setup completed normally."
        )
    return ready


def main(argv: list[str]) -> int:
    minimal = "--minimal" in argv
    no_ui = "--no-ui" in argv
    no_web = "--no-web" in argv
    no_local = "--no-local" in argv
    no_voices = "--no-voices" in argv
    desktop = "--desktop" in argv

    print("Doc2Audiobook - bootstrap")
    print(f"Python: {sys.version.split()[0]}  |  Platform: {sys.platform}  |  Root: {ROOT}")

    ensure_venv()
    install_deps(minimal, no_ui, no_web, no_local)
    build_web_frontend(minimal, no_web)
    check_ffmpeg()
    sync_voices(minimal, no_local, no_voices)
    if desktop:
        install_desktop_deps()
        check_desktop_prerequisites()

    py = venv_python()
    step("Ready")
    print("Activate the environment:")
    if os.name == "nt":
        print(r"  .venv\Scripts\activate")
    else:
        print("  source .venv/bin/activate")
    print("\nThen try:")
    print(f"  {py} narrate.py --list-voices            # free online voices")
    print(f"  {py} narrate.py mydoc.docx               # narrate a document")
    print(f"  {py} app.py                              # Gradio prototype")
    print(f"  {py} -m web_api                          # Phase 3 local web app")
    if desktop:
        print("\nDesktop build checks:")
        print(f"  {py} scripts/build_desktop.py --help")
        print("  npm --prefix web run tauri -- --version")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except subprocess.CalledProcessError as exc:
        print(f"\nbootstrap failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
