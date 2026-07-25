# Switching between Windows and macOS

The repository is the cross-machine source of truth. Virtual environments,
Node modules, Rust build output, frozen sidecars, installers, voice-model
binaries, FFmpeg binaries, caches, generated audio, logs, and signing material
are machine-local.

The desktop architecture is intentionally native-build-first: switching
machines means restoring dependencies and rebuilding, not copying a Windows
artifact to a Mac or a macOS artifact to Windows.

## Before leaving a machine

1. Stop the desktop app and confirm that its sidecar and media processes exited.
2. Let any render finish, or record that it was interrupted. Render chunks and
   library state do not travel through Git.
3. Run `git status --short`.
4. Commit/push source only if that is the chosen workflow. The project does not
   automatically commit or push.
5. Record meaningful test evidence and the next unfinished gate in
   [STATUS.md](STATUS.md).
6. Do not commit generated artifacts, models, logs, audio, credentials,
   certificates, or signing keys.

If there are uncommitted source changes, transfer them deliberately before
continuing elsewhere. Do not assume a generated installer contains recoverable
source.

## Restore the new machine

After cloning or pulling the source:

```bash
python bootstrap.py --desktop
```

This idempotently creates/repairs `.venv`, installs pinned application and
desktop build dependencies, installs/builds the React client when Node is
available, checks for FFmpeg and native desktop prerequisites, and downloads the
voice files listed in
[`voices.manifest.json`](../../voices.manifest.json) with SHA-256 verification.
It reports missing Rust/WebView2/Xcode components but does not silently install
native toolchains or build an installer.

Then restore the desktop-native prerequisites for that operating system and
open a new terminal so `PATH` changes are visible.

Read:

1. [STATUS.md](STATUS.md) for the exact resume marker;
2. [BUILDING.md](BUILDING.md) for native prerequisites;
3. [TESTING.md](TESTING.md) for the platform's acceptance gates; and
4. [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) before staging resources.

## Windows resume

Verify the MSVC Rust toolchain and project environment:

```powershell
cargo --version
rustc --version
rustup show active-toolchain
node --version
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe scripts\build_desktop.py --help
```

If `cargo` is not recognized immediately after installation, restart the
terminal/editor. Rust normally adds `%USERPROFILE%\.cargo\bin` to the user
`PATH`.

Run the source gates before trusting older build output:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
npm --prefix web test
cargo test --manifest-path web\src-tauri\Cargo.toml
```

The current Phase 4A Windows target is:

```text
x86_64-pc-windows-msvc
```

Rebuild the Python sidecar and Tauri bundle on Windows. Do not reuse either one
from macOS.

## macOS resume

Verify Xcode command-line tools, Rust, architecture, and the project
environment:

```bash
xcode-select -p
cargo --version
rustc --version
rustup show active-toolchain
node --version
.venv/bin/python --version
uname -m
.venv/bin/python scripts/build_desktop.py --help
```

Run the source gates:

```bash
.venv/bin/python -m unittest discover -s tests -v
npm --prefix web test
cargo test --manifest-path web/src-tauri/Cargo.toml
```

Choose the target that matches the physical Mac:

```text
Apple silicon: aarch64-apple-darwin
Intel:         x86_64-apple-darwin
```

Rebuild the sidecar and `.app` on the Mac. Phase 4A's frozen sidecar is
Edge-only, so run its Edge/media/process-shutdown acceptance tests before
changing macOS status from “designed” to “verified.” Apple `say` remains a
source-interface capability until local-provider desktop packaging is approved.

## What Git restores

| Restored through Git | Restored or generated per machine |
|---|---|
| Python, React, Rust, and build-script source | `.venv/` |
| lockfiles and pinned requirements | `web/node_modules/` and `web/dist/` |
| Tauri configuration | Rust `target/` output |
| architecture/test/build documentation | PyInstaller sidecar |
| `voices.manifest.json` and checksums | Piper/Kokoro model binaries |
| resource metadata and approved notices | FFmpeg/ffprobe binaries |
| roadmap and risk decisions | app data, previews, logs, generated audio |
| test fixtures | installers, signatures, notarization tickets |

`python bootstrap.py --desktop` handles ordinary project parity plus the pinned
Python-side desktop build requirements and native-prerequisite checks. The
desktop build script handles native staging. Neither should copy credentials or
signing identities between machines.

## User data does not roam

Desktop library data is intentionally under the operating system's app-local
data directory. It is not synced through the repository:

- source document copies;
- job metadata;
- resumable chunks;
- completed audiobooks;
- downloaded model overlays;
- preview cache; and
- diagnostic logs.

If the same in-progress audiobook must continue on another computer, transfer
that library explicitly using a future supported export/backup workflow. Until
such a workflow exists, rerun the render on the destination machine. Do not
place private library data in Git.

## Credentials and signing material

Provider API keys, Windows code-signing certificates, Apple Developer ID
identities, notarization credentials, and updater secrets never belong in the
repository or frontend `VITE_` variables.

Environment variables can restore development provider keys manually. Secure
OS keychain integration remains a later release gate; document a provider path
as key-gated/unverified when the key is unavailable.

## Resume-note template

Add a concise dated entry to [STATUS.md](STATUS.md) when changing machines:

```text
Date:
Leaving: Windows x64 | macOS arm64 | macOS x64
Commit/worktree:
Last passing gates:
Artifact tested:
Provider/format checks:
Pending blocker:
Next command:
```

This distinguishes source progress from machine-local validation and prevents a
Windows-only pass from being mistaken for macOS acceptance.
