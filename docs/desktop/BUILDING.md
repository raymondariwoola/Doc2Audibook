# Building the Phase 4A desktop application

Phase 4A development is currently on **Windows x64**. Its target output is an
unsigned internal development artifact, not a public release. Windows MSI and
NSIS debug artifacts have been built and validated as recorded in
[STATUS.md](STATUS.md). macOS commands are documented now so the same source can
be resumed on a Mac, but macOS artifacts must be built and tested there.

Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing packaging or resource
paths, and read [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) before adding any
third-party binary or model to an installer.

## Build entry point

Use [`scripts/build_desktop.py`](../../scripts/build_desktop.py) as the
canonical desktop staging entry point. Do not manually copy the React build,
sidecar, FFmpeg, models, or notices into `web/src-tauri`; manual staging is
difficult to reproduce and can silently package stale files.

From the repository root, first inspect the script's current contract:

```powershell
.\.venv\Scripts\python.exe scripts\build_desktop.py --help
```

On macOS:

```bash
.venv/bin/python scripts/build_desktop.py --help
```

For a fresh clone or machine switch, the desktop-aware bootstrap installs the
pinned Python build dependencies and reports native prerequisites:

```powershell
python bootstrap.py --desktop
```

Use the same command with `python3` on a Mac when that is the platform's Python
launcher. It does not install Rust, WebView2, Xcode, FFmpeg, or signing
identities, and it does not build an artifact.

The script and this document must be updated together if its interface or
output layout changes.

Prepare the native payload on Windows:

```powershell
.\.venv\Scripts\python.exe scripts\build_desktop.py --ffmpeg-dir "C:\path\to\ffmpeg\bin" --allow-unreviewed-ffmpeg
```

Prepare it and build the unsigned native bundle:

```powershell
.\.venv\Scripts\python.exe scripts\build_desktop.py --ffmpeg-dir "C:\path\to\ffmpeg\bin" --allow-unreviewed-ffmpeg --bundle
```

On macOS, use the same interface with POSIX paths:

```bash
.venv/bin/python scripts/build_desktop.py \
  --ffmpeg-dir /path/to/ffmpeg/bin \
  --allow-unreviewed-ffmpeg \
  --bundle
```

`--ffmpeg-dir` must contain both `ffmpeg` and `ffprobe` (with `.exe` on
Windows). The script can also resolve explicit media-tool environment variables
or `PATH`, but an explicit directory makes build provenance easier to review.

`--allow-unreviewed-ffmpeg` is required for an unknown or GPL-classified build.
It never permits an `--enable-nonfree` build. The flag records an
internal-development-only exception in provenance; it is not licensing
approval. Omit it only when the script can classify the supplied build as an
LGPL-compatible candidate, and still complete the release review separately.

Preparation is the default action. `--bundle` additionally invokes Tauri.
`--debug` is valid only together with `--bundle`.

## Common prerequisites

- Git
- Python 3.10 or newer
- Node.js 22.13 or newer and npm
- Rust stable and Cargo
- native compiler/linker prerequisites for Tauri
- the repository `.venv`, React dependencies, and voice assets installed by
  `python bootstrap.py`
- PyInstaller from the project-pinned desktop build requirements

Run the ordinary cross-machine bootstrap first:

```powershell
python bootstrap.py
```

Bootstrap restores the Python environment, React client, and
manifest-managed voice downloads for source workflows. The current desktop
shell reserves native app-local model directories, but Phase 4A does not copy
models there or freeze local-provider dependencies. Bootstrap does not make an
arbitrary FFmpeg download redistributable and does not replace native
Rust/compiler setup.

## Windows x64 setup

Required native components:

- 64-bit Windows;
- Microsoft C++ Build Tools with the MSVC toolchain and Windows SDK;
- the WebView2 runtime; and
- Rust's `stable-x86_64-pc-windows-msvc` toolchain.

Verify from a **new terminal** after installing Rust:

```powershell
cargo --version
rustc --version
rustup show active-toolchain
node --version
.\.venv\Scripts\python.exe --version
```

Rust is installed on the current Windows machine under
`%USERPROFILE%\.cargo\bin`. A terminal opened before installation may not see
the updated `PATH`; close and reopen the terminal or editor rather than adding
repository-specific path workarounds. The repository's
`web/src-tauri/rust-toolchain.toml` selects the exact Rust toolchain used by the
desktop scaffold.

If the desktop bootstrap was not used, install the pinned desktop-only Python
build dependencies directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
```

Do not add PyInstaller to the application's ordinary runtime requirements.

## macOS setup

Install Apple's command-line developer tools and verify the selected SDK:

```bash
xcode-select --install
xcode-select -p
```

Install Rust with the official `rustup` installer, open a new terminal, then
verify:

```bash
cargo --version
rustc --version
rustup show active-toolchain
node --version
.venv/bin/python --version
uname -m
```

Use an Apple-silicon Mac for `aarch64-apple-darwin` and an Intel Mac for
`x86_64-apple-darwin`. Do not treat a Windows build or a Python virtual
environment copied from another machine as reusable.

Then restore machine-local dependencies:

```bash
python3 bootstrap.py --desktop
```

Apple `say` needs no bundled executable in source workflows, but it is outside
the Edge-only Phase 4A frozen scope. Reopen its desktop acceptance gate only
when local-provider packaging is approved.

## Development checks before packaging

Windows:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
npm --prefix web test
cargo fmt --manifest-path web\src-tauri\Cargo.toml -- --check
cargo clippy --manifest-path web\src-tauri\Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path web\src-tauri\Cargo.toml
```

macOS:

```bash
.venv/bin/python -m unittest discover -s tests -v
npm --prefix web test
cargo fmt --manifest-path web/src-tauri/Cargo.toml -- --check
cargo clippy --manifest-path web/src-tauri/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path web/src-tauri/Cargo.toml
```

See [TESTING.md](TESTING.md) for the full gates and physical acceptance steps.

## Staging and building

The desktop build has four ordered inputs:

1. `npm --prefix web run build` produces a current `web/dist`.
2. PyInstaller freezes the sidecar natively for the current target.
3. The build script stages the frontend, sidecar, and policy-classified media
   resources with checksums and explicit provenance.
4. Tauri compiles the Rust shell and creates the native bundle.

Use the build script's help output for its supported modes. A complete build
must run through the script rather than invoking only `tauri build`, because
Tauri cannot detect that a manually staged sidecar or frontend is stale.

Preparation writes:

```text
build/desktop/pyinstaller/                       # transient freeze output
web/src-tauri/binaries/<sidecar>-<target>[.exe]
web/src-tauri/resources/frontend/
web/src-tauri/resources/tools/
web/src-tauri/resources/licenses/desktop-build-provenance.json
web/src-tauri/tauri.bundle.conf.json
```

The generated provenance records the native target, Edge-only provider scope,
sidecar and frontend digests, FFmpeg/ffprobe digests, FFmpeg build
configuration/classification, unsigned status, and any unreviewed override.

Tauri's release output is under:

```text
web/src-tauri/target/release/bundle/
```

Debug bundles use `web/src-tauri/target/debug/bundle/`. The exact child
directory and installer extension are platform-specific.
Intermediate sidecars and resources are generated below
`web/src-tauri/binaries/` and `web/src-tauri/resources/`; they are not source
artifacts and must not be committed.

## Frozen-artifact smoke

Run the staged one-file sidecar from an unrelated temporary working directory
through the dedicated smoke harness. On Windows x64:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_desktop_sidecar.py `
  --sidecar web\src-tauri\binaries\doc2audiobook-sidecar-x86_64-pc-windows-msvc.exe `
  --frontend web\src-tauri\resources\frontend `
  --ffmpeg web\src-tauri\resources\tools\ffmpeg.exe `
  --ffprobe web\src-tauri\resources\tools\ffprobe.exe
```

Add a real, non-sensitive Edge render across all supported Phase 4A formats:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_desktop_sidecar.py `
  --sidecar web\src-tauri\binaries\doc2audiobook-sidecar-x86_64-pc-windows-msvc.exe `
  --frontend web\src-tauri\resources\frontend `
  --ffmpeg web\src-tauri\resources\tools\ffmpeg.exe `
  --ffprobe web\src-tauri\resources\tools\ffprobe.exe `
  --render-fixture tests\fixtures\sample.txt `
  --formats mp3,m4a,wav
```

Use the matching native target-suffixed sidecar and extension-free media tools
on macOS. The harness creates an unrelated path containing spaces and Unicode,
checks token-free readiness and one-use authentication, serves the staged SPA,
confirms Edge-only provider scope and exact tools, shuts down cleanly, and
rejects writes to its working directory. See [TESTING.md](TESTING.md) for the
acceptance boundary.

## Sidecar naming

Tauri external binaries are target-specific. The staged sidecar basename must
match the configured `externalBin` name and carry the native target suffix.
Examples:

```text
doc2audiobook-sidecar-x86_64-pc-windows-msvc.exe
doc2audiobook-sidecar-aarch64-apple-darwin
doc2audiobook-sidecar-x86_64-apple-darwin
```

Never rename a Windows sidecar to look like a macOS sidecar, or vice versa.

## Resource policy

The following are build inputs but are intentionally absent from Git:

- PyInstaller output;
- Tauri `target/` output and installers;
- FFmpeg/ffprobe binaries;
- Piper and Kokoro model files;
- downloaded frontend dependencies; and
- signing identities, certificates, and credentials.

Before staging FFmpeg, record its source, version, SHA-256 digest, build
configuration, applicable license, notices, and any source-code obligations.
The Gyan “full” FFmpeg installation used for local development is not
automatically approved for redistribution.

Before staging a model, verify its digest against
[`voices.manifest.json`](../../voices.manifest.json) and complete its
redistribution provenance and notice record. A matching checksum proves
integrity, not redistribution permission.

Until those gates pass, source workflows may use machine-local tools/models.
Phase 4A leaves voice models and local-provider runtime dependencies out of the
frozen sidecar and supports Edge only.
The current Rust scaffold requires `resourceDir/tools/ffmpeg[.exe]` and
`ffprobe[.exe]` before it will open its main window, so a Tauri artifact cannot
silently fall back to `PATH`. Do not describe a development artifact as
self-contained or distributable until its staged resources pass the gates.

## Troubleshooting

### `cargo` is not recognized after installation

Open a new terminal and retry. On the current Windows machine, confirm that
`%USERPROFILE%\.cargo\bin\cargo.exe` exists. Do not hard-code that user-specific
path into project scripts.

### PyInstaller is missing

Install the pinned desktop requirements into `.venv`, then invoke it through
that environment. Do not use a global PyInstaller because its Python version
and hooks may differ from the project.

### FFmpeg works in one terminal but not another

Restart the terminal/editor so `PATH` is refreshed. Source workflows may use
`PATH`; packaged desktop runs must receive exact absolute
`DOC2AUDIOBOOK_FFMPEG` and `DOC2AUDIOBOOK_FFPROBE` paths from Tauri.

### The desktop window is blank

Inspect both the Rust log and `python-sidecar.log`. A production build must show
an actionable startup view for sidecar timeout, malformed readiness, protocol
mismatch, or unexpected exit; a blank window is a defect.

### A build works only from the repository root

Treat that as a packaging failure. The frozen sidecar must use Tauri-supplied
absolute app/resource paths and must pass an isolated-artifact smoke test from
a temporary working directory.
