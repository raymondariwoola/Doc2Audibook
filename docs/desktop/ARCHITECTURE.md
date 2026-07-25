# Phase 4A desktop architecture

Status: architecture accepted and implemented. An **unsigned Windows x64
internal development build** has passed frozen-artifact and partial physical
acceptance; the remaining Windows gates are tracked in
[STATUS.md](STATUS.md). macOS uses the same contracts, but must be built and
verified natively when work moves to a Mac.

## Decision

Doc2Audiobook uses **Tauri v2** as its desktop shell.

Tauri reuses the Phase 3 React interface and manages a frozen Python sidecar.
The sidecar runs FastAPI, serves the built React application and `/api` from one
origin, and calls the existing engine and provider layers. The desktop work
therefore remains additive:

- `narrate.py` stays the UI-independent engine.
- `providers.py` stays the provider registry and abstraction.
- `app.py` remains the supported Gradio prototype.
- `web_api/` remains the FastAPI adapter.
- `web/` remains the shared React interface.
- `web/src-tauri/` owns native lifecycle and packaging.

The shell choice is complete. Packaging, licensing, signing, and platform
acceptance are separate gates; selecting Tauri does not imply that a
distributable release is ready.

## Runtime topology

```text
Tauri process (Rust)
  ├─ resolves native data, cache, log, and resource directories
  ├─ creates an unpredictable launch-scoped one-use bootstrap token
  ├─ starts the frozen Python sidecar
  ├─ reads one token-free readiness frame from sidecar stdout
  ├─ navigates the webview to the ephemeral loopback origin
  └─ owns graceful shutdown and forced-kill fallback
                         │
                         ▼
FastAPI sidecar on 127.0.0.1:<ephemeral>
  ├─ authenticates the desktop session
  ├─ serves the built React SPA and all /api routes
  ├─ writes jobs, outputs, previews, and logs only to native app directories
  └─ calls narrate.py and providers.py
                         │
                         ▼
TTS provider + ffmpeg/ffprobe
  └─ Phase 4A frozen scope: Edge
```

FastAPI serves both the UI and API deliberately. Existing relative fetch,
`EventSource`, preview-audio, playback, and download URLs continue to work
without a second desktop-specific API client.

## Startup protocol

The source implementation is [`web_api/desktop.py`](../../web_api/desktop.py).
The frozen executable must preserve the same protocol.

1. Tauri resolves every runtime path to an absolute path.
2. Tauri generates an unpredictable launch-scoped secret and supplies it
   through `DOC2AUDIOBOOK_DESKTOP_TOKEN`. The current Rust scaffold concatenates
   two independent UUID v4 simple encodings: 64 hexadecimal characters with
   about 244 random bits. It is not a command-line argument.
3. Tauri starts the sidecar with explicit paths and bundled-resource overrides.
4. Python validates the configuration before importing and constructing the
   application.
5. Python binds an owned socket to `127.0.0.1:0`. The operating system chooses
   the port, avoiding a check-then-bind race.
6. After Uvicorn reports that it started, Python emits one JSON line:

   ```json
   {"event":"ready","protocol":1,"host":"127.0.0.1","port":49152}
   ```

7. The readiness frame contains no secret. Tauri rejects malformed frames,
   unsupported protocol versions, non-loopback hosts, and startup timeout.
8. Rust stores the webview's pending copy in process memory, bound to the ready
   frame's exact origin, and navigates the webview to the clean URL
   `http://127.0.0.1:<port>/` with no query or fragment.
9. The main window invokes the narrowly permitted
   `take_desktop_bootstrap_token` Tauri command. Rust verifies the window label
   and current exact origin, returns the value once, and leaves no reusable
   copy in its pending state.
10. React exchanges the value immediately at `POST /api/desktop/session`, does
    not retain it at module scope, and then uses the resulting session cookie.
    A page reload after consumption reuses the existing HttpOnly cookie.

The rejected first implementation used a URL fragment and removed it with
`history.replaceState`. Physical Windows inspection showed that WebView2 had
already persisted the navigated fragment URL in its History database. Therefore
no bootstrap secret may enter any URL component, even one omitted from HTTP
requests or removed by JavaScript.

## Authentication and local threat model

Loopback binding alone is not authentication. Another process or browser page
on the same machine can send requests to a loopback port, so desktop API and
media routes require a session.

The current contract provides:

- a random one-use bootstrap token;
- an origin-bound Rust-memory handoff with a least-privilege Tauri ACL;
- constant-time secret comparison;
- an independent random session value;
- an `HttpOnly`, `SameSite=Strict`, path-wide session cookie;
- authentication on `/api` and compatibility API/media routes;
- same-origin validation for state-changing browser requests;
- loopback-only host validation;
- a content security policy with `frame-ancestors 'none'`;
- `Referrer-Policy: no-referrer`;
- `X-Content-Type-Options: nosniff`; and
- no secret in the readiness frame or ordinary access logs.

The cookie is not marked `Secure` because the private origin is plain HTTP.
That is acceptable only while the listener remains strictly loopback-only; the
desktop server must never bind a LAN/public interface.

The static SPA files may load before authentication because they contain no user
documents or generated audio. API responses, previews, job state, and output
downloads remain protected. Desktop mode also accepts a native absolute source
path only after authentication, copies the document into managed job storage,
and does not return the original directory in its API response.

This boundary protects against opportunistic loopback access and hostile web
origins. It is not intended to protect against malware already running as the
same operating-system user, a compromised Python/Rust process, debugger access,
or an administrator.

The React page needs only narrowly scoped native capabilities: consuming its
one-use bootstrap value and opening the document dialog. Tauri's capability URL
pattern admits an ephemeral `http://127.0.0.1:*` origin, while both the runtime
navigation callback and bootstrap command lock the actual window to the one
host and port reported by the sidecar. Sidecar process control stays in Rust;
frontend JavaScript must not receive a general shell-spawn capability.

## Runtime path contract

Installed application resources can be read-only. No desktop runtime path may
default to the repository, current working directory, installation directory,
or Tauri resource directory for writes.

| Tauri location | Child path | Purpose | Persistence |
|---|---|---|---|
| `appLocalDataDir` | `library/` | uploaded source copies, job metadata, resumable chunks, completed audio | durable |
| `appLocalDataDir` | `models/piper/` and `models/kokoro/` | reserved desktop-managed offline models | durable |
| `appCacheDir` | `previews/` | generated voice previews | disposable |
| `appLogDir` | root | Rust and Python diagnostic logs | diagnostic |
| `resourceDir` | `frontend/` | built React application served by FastAPI | read-only |
| `resourceDir` | `tools/` | policy-audited `ffmpeg` and `ffprobe` binaries | read-only |
| `resourceDir` | `licenses/` | third-party notices, licenses, and required source information | read-only |

The sidecar currently receives the core directories through:

| Environment variable | Meaning |
|---|---|
| `DOC2AUDIOBOOK_WEB_DATA_DIR` | durable library root |
| `DOC2AUDIOBOOK_WEB_PREVIEW_DIR` | preview-cache root |
| `DOC2AUDIOBOOK_FRONTEND_DIST` | built SPA root containing `index.html` |
| `DOC2AUDIOBOOK_LOG_DIR` | log root |
| `DOC2AUDIOBOOK_DESKTOP_TOKEN` | one-use bootstrap secret |
| `DOC2AUDIOBOOK_FFMPEG` | exact absolute `ffmpeg` executable |
| `DOC2AUDIOBOOK_FFPROBE` | exact absolute `ffprobe` executable |
| `PIPER_VOICE_DIR` | reserved writable Piper model root |
| `KOKORO_MODEL_DIR` | reserved writable Kokoro model root |

An invalid explicit media-tool path fails closed rather than falling back to a
different executable on `PATH`. Source CLI, Gradio, and Phase 3 web use retain
their historical `PATH` fallback when no desktop override is supplied.

## Shutdown and recovery

Tauri owns the sidecar for the lifetime of the desktop application.
The single-instance plugin prevents two ordinary app launches from sharing and
mutating the same library; a second launch focuses the existing main window.

Normal shutdown writes a single control frame to sidecar stdin:

```json
{"command":"shutdown"}
```

Closing stdin has the same meaning. Python asks Uvicorn to exit and returns
success. Tauri waits for a bounded grace period and then terminates the child as
a fallback. Frozen-artifact smoke and physical idle-close checks pass on
Windows. Closing during an active provider/`ffmpeg` render remains a physical
acceptance gate on both platforms.

Job state and resumable chunks live in the durable library. If the operating
system, app, or sidecar stops unexpectedly, the existing FastAPI startup
reconciliation marks interrupted jobs clearly; resubmitting the same
content-addressed render reuses valid chunks.

If startup fails or the sidecar exits unexpectedly, Tauri must retain an
actionable local error/splash view. A blank webview is not an acceptable error
state. The Windows fault-injection check removes the staged FFmpeg resource and
confirms that this local error view remains visible with an actionable resource
path.

## Build and resource graph

```text
web source
  └─ npm build ───────────────► web/dist
                                      │
Python modules + pinned desktop deps  │
  └─ PyInstaller ─► native sidecar    │
                         │            │
audited ffmpeg + build provenance     │
  └─ checksum-verified staging ───────┤
                                      ▼
                         web/src-tauri resources
                                      │
Rust shell + Tauri config ────────────┤
                                      ▼
                       native unsigned bundle/installer
```

[`scripts/build_desktop.py`](../../scripts/build_desktop.py) is the intended
single staging entry point. It must build the React client before freezing or
bundling it, stage target-triple-named sidecars, verify resource checksums,
reject any `--enable-nonfree` FFmpeg build, and require an explicit
internal-only override for GPL/unknown inputs. It records missing public-release
approval in provenance; third-party notices remain a separate hard gate before
redistribution.
The current Rust path contract points offline providers at writable app-local
model directories, but the Phase 4A frozen sidecar is deliberately
**Edge-only**. Local-provider dependencies and voice models are not bundled.
A later approved provider/model install workflow must populate those
directories and extend the frozen dependency set; merely placing a model under
`resourceDir` would not make the current providers find it.
Generated binaries, models, media tools, and installers remain gitignored.

Expected source layout:

```text
docs/desktop/
  ARCHITECTURE.md
  BUILDING.md
  MACHINE_SWITCH.md
  RELEASE_CHECKLIST.md
  STATUS.md
  TESTING.md
scripts/
  build_desktop.py
web/
  src-tauri/
    Cargo.toml
    Cargo.lock
    build.rs
    tauri.conf.json
    capabilities/
    src/
    binaries/       # generated, target-triple-named sidecar
    resources/      # generated/staged frontend, tools, models, notices
```

## Native target rule

The Python sidecar and Tauri application are built natively on each supported
operating system. PyInstaller is not a cross-compiler.

| Platform | Rust target | Validation owner |
|---|---|---|
| Windows x64 | `x86_64-pc-windows-msvc` | current Phase 4A machine |
| macOS Apple silicon | `aarch64-apple-darwin` | next Apple-silicon Mac session |
| macOS Intel | `x86_64-apple-darwin` | an Intel Mac or a separately approved strategy |

Artifacts are not copied between Windows and macOS as substitutes for a native
build. See [MACHINE_SWITCH.md](MACHINE_SWITCH.md).

## Phase 4A boundary

Phase 4A includes the architecture, authenticated sidecar contract, native file
selection, deterministic staging, automated tests, and an unsigned Windows x64
internal development build. Its frozen provider scope is Edge only. The source
CLI, Gradio, and Phase 3 interfaces retain their existing provider set.

It does not include:

- public distribution;
- Windows Authenticode signing;
- macOS Developer ID signing or notarization;
- auto-update;
- production credential/keychain work;
- a universal macOS binary;
- permission to redistribute the currently installed FFmpeg build; or
- permission to redistribute models before their provenance and notices are
  recorded.

Those items remain explicit release gates in
[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).
