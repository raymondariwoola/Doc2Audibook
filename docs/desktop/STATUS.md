# Phase 4A desktop status

- Last updated: 2026-07-25
- Current machine: Windows x64
- Target: unsigned internal Windows x64 development build

This is a handoff/status page, not a claim of release readiness. Update it after
meaningful implementation, validation, or a machine switch.

## Executive status

Phase 4A is **in progress with a working Windows x64 internal build**. Tauri v2,
the authenticated ephemeral FastAPI sidecar, desktop React adapter, exact
media-path contract, Edge-only PyInstaller freeze, deterministic staging, and
Rust lifecycle are implemented. The frozen artifact passed repeated isolated
smokes and real Edge MP3/M4A/WAV renders. Unsigned MSI and NSIS debug installers
were produced, and the direct native bundle passed partial physical Windows
acceptance.

The remaining Windows 4A work is deliberately narrower: exercise a safe
spaces/Unicode fixture through native selection, run a complete GUI
render/library/restart/recovery flow, close during an active render, and test the
actual installer lifecycle. Licensing/notices and signing remain public-release
gates rather than internal-build claims.

macOS is designed into the path and protocol contracts but has not been
physically verified in this phase. It must remain labeled **pending** until the
user switches to a Mac and the native gates run.

## Capability status

| Area | State | Evidence or next gate |
|---|---|---|
| Shell decision | done | Tauri v2 reuses the React interface |
| Engine/UI separation | preserved | desktop remains a client of `web_api`, `narrate.py`, and `providers.py` |
| Ephemeral loopback sidecar | artifact-smoked | owned `127.0.0.1:0` socket, versioned token-free readiness, source and frozen smokes |
| Desktop session authentication | source and physically tested | one-use origin-bound Rust-memory handoff, strict HttpOnly cookie, API/media lock; no secret in navigation |
| Security headers | tested | CSP, no-referrer, nosniff |
| Absolute runtime roots | artifact-smoked | explicit data, preview, frontend, and log roots from unrelated Unicode/space working directory |
| Media-tool selection | artifact-smoked | exact staged overrides with source-only `PATH` fallback |
| Native document selection | partially physical | native dialog/filter opens; managed-copy contract tested; safe Unicode fixture still pending |
| React desktop tests | tested | Tauri IPC/session/reload and path/dialog adapter tests |
| Tauri Rust shell | tested and partially physical | format, Clippy, six unit tests, clean exact-origin launch, single instance, idle shutdown |
| Frozen Python sidecar | artifact-smoked | pinned Edge-only one-file executable; repeated isolated smokes including the corrected rebuild |
| Resource staging | built | deterministic frontend/tool staging, hashes, and provenance generated |
| Startup error UI | physically verified | missing bundled FFmpeg fault injection retained an actionable local error window |
| Unsigned Windows x64 artifact | built, internal only | debug MSI and NSIS recorded below; installer lifecycle still pending |
| macOS native build | pending | build on the physical target Mac |
| Signed distribution | out of Phase 4A | Authenticode and Apple signing/notarization |
| Auto-update | deferred | later Phase 4 decision |

## Current Windows environment

Observed on 2026-07-25:

- operating-system and process architecture: x64;
- Python: 3.14.6 in `.venv`;
- Node.js: 24.18.0;
- npm: 11.0.0;
- Rust installed under `%USERPROFILE%\.cargo\bin`;
- Cargo: 1.97.1;
- rustc: 1.97.1;
- PyInstaller: 6.21.0 with hooks 2026.6;
- Tauri CLI: 2.11.4;
- Microsoft Edge WebView2 Runtime: present; and
- FFmpeg/ffprobe: Gyan 8.1.2 full GPL build, supplied through explicit absolute
  build inputs and recorded as an unreviewed internal-only override.

The Rust version is selected by `web/src-tauri/rust-toolchain.toml`; the other
observed tool versions in this section are environment facts, not new pins.
Re-run the version and preflight commands in [BUILDING.md](BUILDING.md) before
building.

## Implemented source contracts

- [`web_api/desktop.py`](../../web_api/desktop.py) validates absolute runtime
  paths and token strength, binds an owned ephemeral loopback socket, emits a
  versioned token-free readiness frame, and handles shutdown/EOF.
- [`web_api/main.py`](../../web_api/main.py) can construct an isolated desktop
  app with explicit paths and an authenticated session while preserving the
  ordinary Phase 3 auth-free mode.
- [`media_tools.py`](../../media_tools.py) centralizes `ffmpeg`/`ffprobe`
  discovery and makes explicit packaged paths fail closed.
- [`web/app/desktop.ts`](../../web/app/desktop.ts) consumes the origin-bound
  Rust-memory credential through one-use Tauri IPC, establishes/reuses the
  HttpOnly session, detects Tauri, selects native documents, and displays
  cross-platform paths.
- Desktop selection posts an absolute path only in authenticated desktop mode;
  FastAPI copies its content into managed library storage before rendering.
- The Phase 4A frozen provider scope is Edge only. Local-provider dependencies
  and voice models are deliberately excluded pending packaging and
  redistribution review; source workflows retain them.
- [`scripts/build_desktop.py`](../../scripts/build_desktop.py) detects the
  native host, builds React and the Edge-only sidecar, rejects nonfree FFmpeg,
  stages deterministic resources/provenance, and optionally invokes Tauri.
- [`requirements-desktop.txt`](../../requirements-desktop.txt) pins PyInstaller
  and its hooks separately from runtime dependencies.

## Phase 4A completion gates

- [x] Select Tauri and document the architecture.
- [x] Define an authenticated ephemeral sidecar protocol.
- [x] Separate writable runtime paths from installed resources.
- [x] Centralize explicit packaged media-tool selection.
- [x] Add source-level sidecar, API/auth, media-tool, and React adapter tests.
- [x] Land the initial `web/src-tauri` scaffold.
- [x] Validate Rust formatting, warnings-denied Clippy, and six unit tests.
- [x] Land `scripts/build_desktop.py` and its deterministic policy tests.
- [x] Run the real preparation/bundle path with explicit FFmpeg inputs.
- [x] Pin desktop-only PyInstaller dependencies.
- [x] Install the pinned desktop build dependencies on this machine.
- [x] Pass the full Python, React, and Rust source gates.
- [x] Pass repeated frozen-sidecar smokes outside the repository, then re-smoke
      the corrected rebuild.
- [x] Stage FFmpeg only through the explicit internal-review override and
      record its unresolved redistribution status.
- [x] Exclude local-provider dependencies and models from the Phase 4A frozen
      artifact.
- [x] Produce unsigned Windows x64 MSI and NSIS internal artifacts.
- [x] Physically verify direct native launch, Edge-only readiness and preview,
      single instance, native dialog/filter, idle shutdown, and actionable
      missing-resource failure.
- [x] Complete real Edge MP3, M4A, and WAV renders through the frozen artifact
      and bundled media tools.
- [ ] Complete safe Unicode native selection, full GUI render/library recovery,
      active-render shutdown, and MSI/NSIS installer lifecycle.
- [x] Record artifact hashes and evidence below.
- [x] Keep macOS gates visibly pending for the next Mac session.

## Release blockers

These do not prevent architecture work, but they prevent calling the result a
self-contained distributable release:

- The current Gyan FFmpeg build is identified, hashed, and classified GPL, but
  notices/source obligations and public redistribution approval are incomplete.
- Piper/Kokoro model provenance and redistribution notices are not fully
  represented in the manifest/staging record, so they are excluded.
- Frozen Python dependency license/notice collection is not complete.
- Windows signing is not configured.
- macOS signing and notarization are not configured or tested.
- Secure OS keychain storage for paid-provider credentials remains open.
- Active Edge/FFmpeg process-tree cleanup still needs physical Windows and macOS
  evidence; frozen shutdown and physical Windows idle-close already pass.
- macOS Edge-provider, GUI, packaging, and path behavior are unverified.

See [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).

## Latest validation evidence

Build identity:

- date/platform/target: 2026-07-25, Windows x64,
  `x86_64-pc-windows-msvc`;
- source: uncommitted development worktree on `main`, based on `c559a99`;
- build type: Tauri debug bundle, unsigned, internal development only;
- direct Tauri debug executable SHA-256:
  `24AD8779651DEA05557AA3DAFDDB12564344FA07203193B1ED48E8927EE5651A`;
- frozen sidecar SHA-256:
  `99E65384065CA876A42361E29F30B5AF142513E76DFCE35D922E0E180D06B41E`;
- staged FFmpeg SHA-256:
  `AD8F211BC894755E0061C55AB280AE00E8D3D4F15A8CC4372B24CFA247B5942E`;
- staged ffprobe SHA-256:
  `9DF3B0B5275E830961DF6D94E1F7A71121A7ABD5FF708E9FEC8A0B6084A55015`.

| Artifact | Size | SHA-256 |
|---|---:|---|
| `Doc2Audiobook_1.0.0_x64_en-US.msi` | 222,449,664 bytes | `78395257C8F01E9E719F1C776192E0534178AEBFC51936107951EC4F75207DC3` |
| `Doc2Audiobook_1.0.0_x64-setup.exe` | 157,113,864 bytes | `702FADD5324327D51BF5EAFCD60F4E01249CEF9B673F72818111E5E04D605FC8` |

Artifact evidence:

- Python compile plus all 73 unittest cases passed;
- React passed 6 Vitest cases, TypeScript checking, and the Vite production
  build;
- Rust formatting, warnings-denied Clippy, and all 6 unit tests passed;
- `npm audit` reported zero vulnerabilities and `pip check` reported no broken
  requirements;
- five consecutive non-network frozen-sidecar smokes passed from unrelated
  Unicode/space working directories before the security rebuild, and the
  corrected payload passed both isolated and real-render smokes afterward;
- a real Edge artifact smoke completed MP3, M4A, and WAV with the staged tools;
- the native window launched from a non-repository working directory, reached
  Engine ready, presented only Edge, and completed an Edge preview;
- a second launch was suppressed, and idle close left no
  `doc2audiobook` process;
- readiness/termination logs contained no bootstrap token; and
- moving the staged FFmpeg resource aside produced a visible actionable startup
  error, after which the exact resource was restored.

### Bootstrap persistence correction

The first development build passed the token in a URL fragment. A read-only
audit then found that WebView2 had persisted those navigation URLs even though
React immediately called `history.replaceState`. Those pre-fix values were
one-use and belonged to stopped sidecars, so they cannot authenticate the
current build, but the design was rejected before handoff.

The corrected build keeps the pending token only in Rust memory, binds its
one-time Tauri command to the main window's exact ready origin, and navigates
with no query or fragment. On the physical Windows rerun:

- the native UI reached `Engine ready`, proving the IPC-to-cookie exchange;
- the number of legacy token-shaped History records did not increase;
- the new clean loopback origin was recorded instead;
- normal window close left zero desktop/sidecar processes; and
- the session cookie had no persisted row after shutdown.

The existing development WebView profile still contains expired pre-fix History
records. They are retained as audit evidence and are not copied through Git.
Deleting the whole profile would also reset WebView-local state, so cleanup
should be an explicit operator choice after the app is closed, not an automatic
build step.

The generated artifacts and staged resources are gitignored and remain on this
Windows machine only. Their presence does not imply install testing, signing,
redistribution approval, or macOS acceptance.

## Resume marker

Next work:

1. run a safe spaces/Unicode fixture through the native dialog and managed-copy
   path;
2. complete a full GUI render, playback/save, Library restart, interrupted-job
   reconciliation, and chunk-resume pass;
3. close during a live Edge/FFmpeg render and prove the process tree exits;
4. exercise MSI/NSIS install, launch, repair/upgrade where applicable, and
   uninstall on an appropriate test account or machine;
5. complete third-party notices and FFmpeg redistribution review before any
   public artifact; and
6. when the user switches to macOS, rebuild natively and run every Mac gate.

When moving to macOS, start from
[MACHINE_SWITCH.md](MACHINE_SWITCH.md) and retain the Windows evidence rather
than overwriting it.
