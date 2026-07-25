# Phase 4A desktop testing

Desktop verification is layered. Passing Python unit tests is necessary but
does not prove that a frozen sidecar starts, that Tauri controls it correctly,
or that an installer works on a physical machine.

The current acceptance platform is **Windows x64**. macOS checks remain required
and must be run natively after switching to a Mac.

## Gate summary

| Gate | Purpose | Phase 4A requirement |
|---|---|---|
| Python regression | protect engine, providers, CLI, Gradio, API, and sidecar contracts | required |
| React tests/build | protect shared web/desktop UI and native adapter | required |
| Rust format/lint/unit | protect shell protocol, paths, navigation, and lifecycle | required once scaffold is present |
| frozen-sidecar smoke | prove the PyInstaller artifact works outside the source tree | required |
| Tauri artifact smoke | prove native shell startup, authentication, and shutdown | required |
| real-provider acceptance | prove actual Edge narration and media tooling | Windows required; macOS pending |
| signed-release acceptance | prove signing, notarization, update, and clean-machine install | not Phase 4A |

## Automated checks

Run from the repository root on Windows:

```powershell
.\.venv\Scripts\python.exe -m compileall -q narrate.py providers.py media_tools.py app.py bootstrap.py sync_voices.py web_api scripts\build_desktop.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
npm --prefix web test
cargo fmt --manifest-path web\src-tauri\Cargo.toml -- --check
cargo clippy --manifest-path web\src-tauri\Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path web\src-tauri\Cargo.toml
```

Run the equivalent commands on macOS:

```bash
.venv/bin/python -m compileall -q narrate.py providers.py media_tools.py app.py bootstrap.py sync_voices.py web_api scripts/build_desktop.py
.venv/bin/python -m unittest discover -s tests -v
npm --prefix web test
cargo fmt --manifest-path web/src-tauri/Cargo.toml -- --check
cargo clippy --manifest-path web/src-tauri/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path web/src-tauri/Cargo.toml
```

## Contract coverage

Python tests must cover:

- all existing engine/provider/CLI/Gradio/web regressions;
- explicit media-tool override precedence;
- rejection of relative and missing explicit media-tool paths;
- ordinary source-workflow `PATH` fallback;
- absolute desktop data, preview, frontend, and log paths;
- rejection of a missing/short token and missing frontend;
- race-free loopback socket ownership and an ephemeral nonzero port;
- protocol-versioned, token-free readiness output;
- unauthenticated API/media rejection;
- wrong and reused bootstrap-token rejection;
- strict `HttpOnly` session cookie attributes;
- desktop security headers;
- native-path rejection outside desktop mode;
- copying a selected native document into managed storage without disclosing
  its original directory;
- source-sidecar health after authentication; and
- graceful shutdown through the stdin control frame.

React tests must cover:

- one-use credential retrieval through Tauri IPC rather than a URL;
- rejection of malformed native-command credentials;
- session exchange through the expected header;
- reload behavior after the credential is consumed;
- a useful error when session establishment fails;
- Windows and POSIX path filename handling;
- accepted document extensions;
- native-dialog selection and cancellation; and
- continued browser upload behavior outside Tauri.

The current Rust unit tests cover:

- readiness-frame parsing and protocol mismatch;
- rejection of non-loopback or invalid navigation;
- strong independent token generation;
- a sidecar URL with no secret-bearing query or fragment;
- exact-origin, one-use in-memory credential consumption;
- startup-shell navigation restrictions; and
- native app-data/resource path derivation.

Startup timeout, early sidecar exit, graceful/bounded shutdown, forced-kill
fallback, and single-instance behavior live in the Rust integration layer and
are not claimed as unit-tested by those six pure-function tests. Build,
frozen-artifact, and physical gates must exercise them. Add focused Rust or
native integration coverage if that layer is refactored behind a testable child
process abstraction.

## Frozen-sidecar smoke

Run the frozen executable from a temporary directory that is not the repository.
Supply only explicit absolute paths and the same environment contract Tauri
uses.

The smoke test passes only if it:

1. creates its configured data, preview, and log directories;
2. emits exactly one valid readiness line without a token;
3. rejects unauthenticated `/api/health`;
4. accepts the one-use bootstrap token once;
5. returns desktop health and the built SPA after authentication;
6. lists providers;
7. starts and observes a test job or deterministic fixture path;
8. responds to the shutdown frame; and
9. exits without leaving Python, provider, or FFmpeg child processes.

Run [`scripts/smoke_desktop_sidecar.py`](../../scripts/smoke_desktop_sidecar.py)
against the staged payload. Exact Windows and macOS command shapes are in
[BUILDING.md](BUILDING.md). Add
`--render-fixture tests/fixtures/sample.txt --formats mp3,m4a,wav` for the
online Edge/media acceptance gate.

`tests/test_desktop_artifact.py` runs this smoke automatically when a staged
payload exists, but skips on a clean clone. A green unit suite with that skip is
not artifact evidence; a candidate build must also run the explicit harness,
which fails when its required artifact inputs are missing.

On 2026-07-25 the Windows one-file sidecar passed five consecutive non-network
smokes. After replacing the persisted fragment design and rebuilding, the new
payload passed another isolated smoke; a separate explicit smoke completed real
Edge MP3, M4A, and WAV renders through the staged FFmpeg/ffprobe tools.

## Windows x64 physical acceptance

Use the generated unsigned internal artifact on the current Windows machine.
Record the artifact filename, SHA-256, commit, date, and tester in
[STATUS.md](STATUS.md).

- [ ] The installer or bundle clearly identifies itself as unsigned/internal.
- [x] The direct bundle executable launches without a terminal or repository
      working directory. Installer/Explorer acceptance remains separate.
- [x] Only one application instance owns the library.
- [x] The native dialog opens with `.docx`, `.pdf`, `.txt`, and `.md` filters.
- [ ] Select a safe fixture of each supported type through the native dialog.
- [ ] Canceling the dialog does not clear an existing valid selection.
- [ ] A selected path with spaces and Unicode characters works.
- [x] Health reports the intended bundled media tools, not an accidental
      machine-wide executable.
- [x] Edge preview completes in the direct native UI when online.
- [x] A real frozen-artifact Edge render completes for MP3, M4A, and WAV.
- [x] Piper, Kokoro, Apple, and paid providers are not presented as bundled
      Phase 4A capabilities.
- [x] Source CLI/Gradio/web regression tests still protect the existing local
      provider paths when their optional dependencies are installed.
- [ ] MP3 output plays and downloads/saves correctly through the full native UI.
- [x] MP3, M4A, and WAV complete through the bundled FFmpeg path in artifact
      smoke.
- [ ] Progress reconnects and the completed job appears in Library.
- [x] Closing while idle leaves no sidecar process.
- [ ] Closing during an active render leaves no Python/provider/FFmpeg process.
- [ ] Relaunch reconciles an interrupted job and retains valid resumable chunks.
- [ ] Relaunch preserves completed library entries and playback.
- [x] Invalid/missing resources produce an actionable error view and log.
- [x] Readiness, termination, and smoke logs contain no bootstrap/session token.
- [x] A corrected native launch adds a clean origin to WebView2 history without
      adding a token-shaped URL; the HttpOnly session cookie is absent after
      shutdown.
- [ ] Recheck provider keys, document text, and original native paths after the
      full native selection/render flow.

Edge is an online provider, so this artifact does not provide offline narration.
Piper/Kokoro artifact acceptance starts only after their dependencies and
models have approved packaging/provenance.

## macOS physical acceptance

Repeat the Edge-only Windows workflow natively on each supported Mac
architecture, plus:

- [ ] app close and forced quit leave no Python or FFmpeg process.
- [ ] file dialog, app-data, cache, log, and resource paths follow macOS
      conventions.
- [ ] the `.app` launches from Finder, not only a terminal.
- [ ] spaces, Unicode, and decomposed Unicode paths work.
- [ ] microphone, accessibility, or broad filesystem permissions are not
      requested unnecessarily.
- [ ] signing/notarization behavior is recorded separately; an unsigned local
      smoke does not pass the release gate.

Apple `say` remains supported by source interfaces but is outside the
Phase 4A frozen-sidecar provider scope. Until the listed checks run on actual
macOS, report macOS as **designed, not verified**.

## Test data and privacy

Use `tests/fixtures/sample.txt` or purpose-built non-sensitive fixtures for
automated and online-provider tests. Do not send a private document to Edge,
OpenAI, or another network provider merely to validate packaging.

Generated test audio, model files, installer output, logs, and temporary
libraries are machine-local artifacts and must remain gitignored.

## Reporting results

For every candidate artifact, record:

- source commit and whether the worktree was clean;
- operating system and architecture;
- Python, Node/npm, Rust/Cargo, Tauri, and PyInstaller versions;
- artifact path, type, size, and SHA-256;
- automated command results;
- which real providers/formats ran;
- whether active-job shutdown was exercised;
- failures or warnings; and
- which gates remain pending.

The 2026-07-25 Windows evidence is recorded in
[STATUS.md](STATUS.md). It is correctly labeled an unsigned internal debug
build: source-verified, frozen-artifact-smoked, and partially physically
verified. It is not installer-verified, macOS-verified, self-contained for
public redistribution, signed, or release-ready.

Use precise labels:

- **unit-tested** for deterministic source tests;
- **artifact-smoked** for a frozen sidecar/Tauri bundle;
- **physically verified** only after the native checklist; and
- **release-ready** only after [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).
