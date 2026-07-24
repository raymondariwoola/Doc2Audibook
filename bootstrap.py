#!/usr/bin/env python3
"""
One-command setup for Doc2Audiobook on any machine (macOS / Windows / Linux).

    python bootstrap.py

What it does (idempotent — safe to re-run any time, e.g. after `git pull`):
  1. Creates a local virtual environment in .venv (if missing).
  2. Installs pinned Python dependencies (core + UI + local providers).
  3. Checks for ffmpeg and prints OS-specific install instructions if missing.
  4. Downloads the offline voice models listed in voices.manifest.json
     (Piper + Kokoro) via sync_voices.py — skipping any already present.

This is the command to run whenever you switch machines: pull the repo, run it,
and you're on parity with wherever you left off (code + dependencies + voices).
Generated audiobooks and the resumable chunk cache are machine-local and are NOT
synced (they're gitignored and cheap to regenerate).

Flags:
  --minimal     core CLI dependencies only (no UI, no local providers, no models)
  --no-ui       skip the Gradio UI dependency
  --no-local    skip the offline local-provider packages (Piper/Kokoro)
  --no-voices   skip downloading voice models
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


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(cmd: list[str], **kw) -> None:
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, check=True, **kw)


def step(msg: str) -> None:
    print(f"\n=== {msg} ===")


def ensure_venv() -> None:
    step("1/4  Virtual environment")
    if venv_python().exists():
        print(f"  .venv already exists ({venv_python()})")
        return
    print(f"  creating {VENV} …")
    venv.EnvBuilder(with_pip=True, upgrade_deps=True).create(str(VENV))


def install_deps(minimal: bool, no_ui: bool, no_local: bool) -> None:
    step("2/4  Python dependencies")
    py = str(venv_python())
    run([py, "-m", "pip", "install", "--upgrade", "pip"])
    req_files = [ROOT / "requirements.txt"]
    if not minimal:
        if not no_ui:
            req_files.append(ROOT / "requirements-ui.txt")
        if not no_local:
            req_files.append(ROOT / "requirements-local.txt")
    args = [py, "-m", "pip", "install"]
    for rf in req_files:
        args += ["-r", str(rf)]
    run(args)


def check_ffmpeg() -> None:
    step("3/4  ffmpeg (external dependency)")
    if shutil.which("ffmpeg"):
        print("  ffmpeg found ✓")
        return
    instructions = {
        "darwin": "brew install ffmpeg",
        "win32": "winget install Gyan.FFmpeg   (or: choco install ffmpeg)",
        "linux": "sudo apt install ffmpeg   (Debian/Ubuntu)  |  sudo dnf install ffmpeg (Fedora)",
    }
    plat = "win32" if os.name == "nt" else sys.platform
    hint = instructions.get(plat, "See https://ffmpeg.org/download.html")
    print("  ⚠️ ffmpeg NOT found on PATH. Install it, then re-run this script:")
    print(f"     {hint}")


def sync_voices(minimal: bool, no_local: bool, no_voices: bool) -> None:
    step("4/4  Offline voice models")
    if minimal or no_local or no_voices:
        print("  skipped (per flags). Run `python sync_voices.py` later to fetch models.")
        return
    run([str(venv_python()), str(ROOT / "sync_voices.py")])


def main(argv: list[str]) -> int:
    minimal = "--minimal" in argv
    no_ui = "--no-ui" in argv
    no_local = "--no-local" in argv
    no_voices = "--no-voices" in argv

    print("Doc2Audiobook — bootstrap")
    print(f"Python: {sys.version.split()[0]}  |  Platform: {sys.platform}  |  Root: {ROOT}")

    ensure_venv()
    install_deps(minimal, no_ui, no_local)
    check_ffmpeg()
    sync_voices(minimal, no_local, no_voices)

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
    print(f"  {py} app.py                              # browser UI (if installed)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except subprocess.CalledProcessError as exc:
        print(f"\nbootstrap failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
