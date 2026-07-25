# Doc2Audiobook — `narrate`

Turn a document (`.docx`, `.pdf`, `.txt`, or `.md`) into a natural-sounding
**MP3, M4A, or WAV** audiobook. Microsoft
[`edge-tts`](https://github.com/rany2/edge-tts) is the free online default;
Piper and Kokoro provide fully offline alternatives.

**No paid API key required.** The voices are genuinely natural (not the robotic
`say`/`espeak` kind), the pipeline is **resumable** if it's interrupted, and the
final audio is stitched together losslessly with `ffmpeg`.

Four supported ways to use it:

- **CLI** — `narrate mydoc.docx` (scriptable, resumable, batteries-included).
- **Gradio prototype** — `python app.py` opens the original browser UI.
- **Phase 3 web app** — `python -m web_api` serves the React UI and FastAPI API
  together at `http://127.0.0.1:8000`.
- **Phase 4A desktop app** — a Tauri v2 shell packages that React UI with a
  frozen Python sidecar. Windows x64 internal artifacts now exist; macOS and
  public-release gates remain pending.

Both browser interfaces remain available and reuse the same engine/provider
layers. The Phase 3 app does not replace or redirect the prototype.

See [`ROADMAP.md`](ROADMAP.md) for progress on paid providers, the local web app,
and native macOS/Windows desktop builds.

---

## Quick start (and switching machines)

One command sets up **everything** on macOS, Windows, or Linux — and it's the
same command you run when you move to another machine or clone fresh:

```bash
python bootstrap.py
```

It creates or repairs `.venv`, installs the pinned Python dependencies, installs
and builds the React client when Node.js 22.13+ is available, checks for
`ffmpeg` (with install hints if missing), and downloads the offline voice models
listed in [`voices.manifest.json`](voices.manifest.json). Every model is verified
against its recorded SHA-256 digest. The command is **idempotent** — re-run it
after every `git pull` to stay in lockstep. Use `--no-web` to skip Phase 3, or
`--minimal` for the core CLI only. Desktop contributors should use
`python bootstrap.py --desktop`; it adds the pinned PyInstaller requirements and
checks Rust plus the platform-native WebView2/Xcode prerequisites without
silently installing or building native components.

**What syncs across machines:** all code, the roadmap, and *which* voices you use
(the manifest) travel via git. The large bits — `.venv`, `web/node_modules`,
the built web client, Rust `target/`, frozen sidecars/installers, media binaries,
voice model files, generated audio, and the resumable chunk cache — are
gitignored and rebuilt/re-fetched locally.
So on a new machine: `git pull`
→ `python bootstrap.py` → you're back where you left off. To add a voice for all
machines, edit `voices.manifest.json`, commit, and run `python sync_voices.py`
on each. AI agents working in this repo should read
[`AGENTS.md`](AGENTS.md) for this workflow.

---

## Features

- **Pluggable voices** — Edge TTS online by default, Piper/Kokoro offline,
  Apple `say` on macOS, and an experimental key-gated OpenAI provider.
- **Correct reading order.**
  - `.docx`: walks the document body in true document order, so **tables are read
    in place** relative to surrounding paragraphs. Each table is read row by row
    as `"term. definition."` pairs (header row skipped).
  - `.pdf`: extracts text preserving reading order.
  - `.md`: ATX headings + pipe tables understood.
- **Speech-friendly cleanup.**
  - A spoken **pause cue** (an ellipsis on its own line) is inserted before every
    major (Heading 1 / Title) section break.
  - Inline formulas/symbols are verbalized: `X = Y - Z` → *"X equals Y minus Z"*,
    `%` → *"percent"*, `→` → *"leads to"*, and many more.
  - Markdown/formatting artifacts (`**`, `_`, `` ` ``, table `|` pipes, list
    bullets, links) are stripped so they aren't read literally.
  - **Your own** find/replace rules via `--replace` and/or `--rules file.json`.
- **Chunked + safely resumable.** Text targets 3,000-character chunks on
  paragraph and sentence boundaries. A cache manifest fingerprints the cleaned
  text, provider/model, voice, pitch, rate, and chunk size. The exact same render
  skips existing chunks; changed inputs invalidate stale generated chunks.
- **Configurable rate** with `--rate` (default `-4%`, a touch slower than normal).
- **Lossless assembly.** Chunks are concatenated with `ffmpeg -c copy` (stream
  copy, no re-encoding), and the final duration + file size are printed.
- **Corporate-proxy SSL fallback** (see [below](#ssl--corporate-proxy-networks)).

---

## Installation

The easiest path is `python bootstrap.py` (see [Quick start](#quick-start-and-switching-machines)
above). To do it by hand instead:

### 1. Python dependencies

Requires **Python 3.10+** (the pinned Gradio UI requires 3.10; the core engine
itself remains compatible with Python 3.9).

```bash
# from the project folder
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt                 # core CLI (edge-tts online)
pip install -r requirements-ui.txt              # optional: Gradio prototype
pip install -r requirements-web.txt             # optional: Phase 3 API
pip install -r requirements-local.txt           # optional: offline Piper/Kokoro
cd web && npm ci && npm run build               # Phase 3 React client
```

Or install it as a command (`narrate`) on your PATH, with optional extras:

```bash
pip install -e .                    # core
pip install -e ".[ui,web,local]"    # + both UIs + offline providers
```

### 2. `ffmpeg` (external dependency — required)

For the source CLI, Gradio, and Phase 3 web interfaces, `ffmpeg` (and
`ffprobe`, which ships with it) must be on your `PATH`.

| OS | Install |
|----|---------|
| **macOS** | `brew install ffmpeg` |
| **Windows** | `winget install Gyan.FFmpeg`  (or `choco install ffmpeg`) |
| **Linux (Debian/Ubuntu)** | `sudo apt install ffmpeg` |
| **Linux (Fedora)** | `sudo dnf install ffmpeg` |

Verify with `ffmpeg -version`.

The Phase 4A packaged desktop does not use `PATH`: its build stages exact
absolute FFmpeg/ffprobe resources, records their hashes/configuration, and fails
closed if either is missing. The current Windows bundle uses an unreviewed GPL
build for internal testing only; see
[`docs/desktop/RELEASE_CHECKLIST.md`](docs/desktop/RELEASE_CHECKLIST.md).

> **Just installed ffmpeg?** Open a **new** terminal (and restart your editor so
> its integrated terminal reloads `PATH`) before launching the app — an existing
> shell keeps its old `PATH` and won't see ffmpeg. The web app detects this and
> shows a warning banner; without ffmpeg, audiobook assembly and the offline
> (Piper/Kokoro) previews fail while Edge previews still work.

---

## Phase 3 local web app

The React/Vite client and FastAPI backend are isolated in `web/` and `web_api/`.
The production-style local command serves both from one loopback-only origin:

```bash
python bootstrap.py                 # first run, or after dependency changes
.venv/bin/python -m web_api         # Windows: .venv\Scripts\python -m web_api
# open http://127.0.0.1:8000
```

Use `--port 8010` if 8000 is already occupied. The launcher fails clearly
rather than moving silently to another port.

For frontend development, run the API and Vite in separate terminals. Vite
proxies relative `/api` requests to the API, so API URLs stay environment-free:

```bash
.venv/bin/python -m web_api
cd web
npm run dev                         # http://127.0.0.1:5173
```

The web app keeps generated jobs and preview audio in dedicated machine-local
subdirectories; the prototype continues to use its existing paths. The web
server binds to `127.0.0.1` and rejects non-loopback hosts because it is not a
public or multi-user service.

“Local app” describes where the interface and files run. Edge and OpenAI still
send narration text to their respective online TTS services; choose Piper,
Kokoro, or Apple `say` when document content must stay offline.

Current restart-recovery and SSE limitations are tracked in
[`RISKS_AND_INCONSISTENCIES.md`](RISKS_AND_INCONSISTENCIES.md).

## Phase 4A native desktop

Phase 4A uses Tauri v2 to own an Edge-only frozen Python/FastAPI sidecar. The
desktop API binds an ephemeral loopback port, requires a one-use per-launch
session secret passed through origin-bound Tauri IPC without entering the URL,
uses native app-data paths, and takes `ffmpeg`/`ffprobe` only from explicitly
staged resources. The CLI, Gradio prototype, and Phase 3 web app keep their full
existing provider behavior.

On a desktop build machine:

```powershell
python bootstrap.py --desktop
.\.venv\Scripts\python.exe scripts\build_desktop.py --ffmpeg-dir "C:\path\to\ffmpeg\bin" --allow-unreviewed-ffmpeg --bundle
```

On macOS, use `.venv/bin/python` and a native macOS FFmpeg directory. Build each
artifact on its target operating system; do not reuse a Windows `.venv`,
sidecar, or installer on a Mac.

The current Windows x64 MSI/NSIS builds are **unsigned internal development
artifacts**. The explicit FFmpeg override records a GPL/unknown build for local
testing; it does not grant redistribution approval. Build instructions, exact
smoke commands, current evidence, pending macOS work, and release/licensing
gates live in [`docs/desktop/`](docs/desktop/). The owner-specific release
queue—including licenses, signing identities, secret-handling rules, and what
each item blocks—is in
[`OWNER_ACTIONS.md`](docs/desktop/OWNER_ACTIONS.md).

## Gradio prototype (retained)

The original prototype remains supported as a lightweight comparison and
recovery interface. Nothing is uploaded to Doc2Audiobook; it runs on your
machine and does not depend on the React client.

```bash
pip install -r requirements-ui.txt      # adds gradio (CLI doesn't need it)
python app.py                           # opens http://127.0.0.1:7860
```

In the app you can:

- Attach a `.docx/.pdf/.txt/.md` document.
- Filter voices by locale and **🔊 Preview** the selected voice (at your current
  pitch/pace) before committing to a full render.
- Adjust **pace** and **pitch** sliders, with a **Recommended defaults** reset.
- Choose **MP3 / M4A / WAV** output, add find/replace rules, and download the result.

Renders land in `outputs/` with a short render digest in the filename, preventing
same-stem uploads from overwriting different audiobooks. A chunk cache in
`.narrate_cache/` is keyed by document content, provider/model, voice settings,
chunk size, and replacement rules. This is **Phase 1** of the
[roadmap](ROADMAP.md). Phase 3 adds a richer FastAPI + React interface alongside
it, and Phase 4 targets native desktop builds.

### Running & verifying the prototype (dev ops)

> Style/theme changes are passed to Gradio's `launch()`, so a **running instance
> will not hot-reload them** — you must restart the process to see UI changes.
> The commands below assume Git Bash on Windows (the repo's default shell); on
> macOS/Linux swap `.venv/Scripts/python` → `.venv/bin/python` and use
> `lsof -i :7860` in place of `netstat`.

**When to use:** you changed `app.py` styling/layout and want to see it, or you
have stray/duplicate instances hogging ports and need exactly one clean server.

**1. See which instances are running (and on which port).** Gradio uses `7860` by
default and falls back to `7861`, `7862`, … when a port is taken — so duplicates
hide on higher ports:

```bash
echo "=== Gradio listeners ==="; netstat -ano | grep -E ":78[0-9][0-9] " | grep LISTEN
```

**2. Identify a process before killing it** (confirm it's our app, not something
else on that port). Replace the IDs with the PIDs from step 1:

```bash
powershell -Command "Get-CimInstance Win32_Process -Filter 'ProcessId=1234 or ProcessId=5678' | Select-Object ProcessId, @{n='Cmd';e={\$_.CommandLine}} | Format-List"
```

**3. Kill stray instances** (use the PIDs from step 1). Do this before a restart
so you end up with a single server:

```bash
powershell -Command "Stop-Process -Id 1234,5678 -Force -ErrorAction SilentlyContinue"
sleep 1
netstat -ano | grep -E ":78[0-9][0-9] " | grep LISTEN || echo "all clear"
```

**4. Launch one clean instance and wait for it to come up:**

```bash
.venv/Scripts/python app.py > /tmp/d2a.log 2>&1 &
for i in $(seq 1 12); do
  netstat -ano | grep -qE ":7860 .*LISTEN" && { echo "up on 7860"; break; }
  sleep 1
done
grep -iE "running on|url|error|traceback" /tmp/d2a.log | head    # startup errors, if any
```

**5. Health-check it:**

```bash
curl -s -o /dev/null -w "http://127.0.0.1:7860 -> HTTP %{http_code}\n" http://127.0.0.1:7860/
```

**Screenshot-verify the UI in both themes (headless).** Playwright + Chromium are
installed in the `.venv` as a **dev-only** aid (not a runtime dependency). Force
each theme with the `?__theme=` query param and always capture **both** — the UI
is theme-aware and a light-only change can silently break dark mode (that was the
original bug). Save the snippet as e.g. `shot.py` and run it while the app is up:

```python
from playwright.sync_api import sync_playwright  # .venv/Scripts/python shot.py
URL = "http://127.0.0.1:7860"
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    for theme in ("light", "dark"):
        pg.emulate_media(color_scheme=theme)
        pg.goto(f"{URL}/?__theme={theme}", wait_until="networkidle")
        pg.wait_for_timeout(2500)                       # let Gradio settle
        pg.screenshot(path=f"{theme}.png", full_page=True)
    b.close()
```

First-time setup of the screenshot tooling (already done on this machine):
`.venv/Scripts/python -m pip install playwright && .venv/Scripts/python -m playwright install chromium`.

---

## Usage (CLI)

```
narrate <input_file> [--output out.mp3] [--provider PROVIDER] [--voice VOICE]
                     [--rate -4%] [--pitch +0Hz] [--format mp3|m4a|wav]
                     [--replace FROM=TO] [--rules rules.json]
                     [--keep-chunks] [--work-dir DIR] [--max-chars N]
narrate --list-voices [FILTER]
```

If you installed with `pip install -e .` you can call `narrate ...` directly;
otherwise use `python narrate.py ...`.

### Examples

```bash
# Simplest — writes mydoc.mp3 next to the input
narrate mydoc.docx

# Choose a voice and a slightly slower rate, custom output name
narrate book.pdf --output book.mp3 --voice en-US-AriaNeural --rate -8%

# List all US English voices to choose from
narrate --list-voices en-US

# Reword document-specific labels for natural speech
narrate notes.md --replace "NOTE:=Here is an important note." \
                 --replace "TIP:=A quick tip." --keep-chunks

# Or keep the rules in a file (see rules.example.json)
narrate handbook.docx --rules rules.example.json
```

### Resuming an interrupted run

Run the **exact same command again**. Valid chunk MP3s already on disk are
skipped and only the missing ones are generated:

```
Synthesizing chunks:
  [  1/6]   24 chars  — skipped (already exists)
  [  2/6]  114 chars  — done
  [  3/6]  104 chars  — skipped (already exists)
  ...
```

Chunks live in `<output>_chunks/` (override with `--work-dir`). They are deleted
on success unless you pass `--keep-chunks`. If any audio-affecting input changes,
the engine clears its stale generated artifacts before synthesis.

---

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `input` | — | Document to narrate (`.docx`, `.pdf`, `.txt`, `.md`). |
| `-o, --output` | `<input>.<format>` | Output path; its extension selects the format. |
| `--provider` | `edge` | TTS provider ID. |
| `--voice` | `en-US-AndrewNeural` | Voice ID for the selected provider. |
| `--rate` | `-4%` | Speech rate, e.g. `-10%` slower, `+5%` faster. |
| `--pitch` | `+0Hz` | Pitch adjustment; ignored by providers that do not support it. |
| `--format` | `mp3` | Format used when `--output` is omitted: MP3, M4A, or WAV. |
| `--list-voices [FILTER]` | — | List voices (optional locale/name filter) and exit. |
| `--replace FROM=TO` | — | Find/replace rule for speech. Repeatable. |
| `--rules FILE` | — | JSON of rules — a `{"from":"to"}` object or `[{ "from":..,"to":.. }]`. |
| `--keep-chunks` | off | Keep the intermediate per-chunk MP3s. |
| `--work-dir DIR` | `<output>_chunks` | Where chunk MP3s are written (drives resume). |
| `--max-chars N` | `3000` | Max characters per chunk. |

---

## TTS providers

Speech engines sit behind a small `TTSProvider` interface in
[`providers.py`](providers.py), so the CLI and UI stay the same regardless of
engine. Pick one with `--provider`:

| Provider | Cost | Offline | Notes |
|---|---|---|---|
| `edge` *(default)* | Free | No | Microsoft edge-tts neural voices, no key. Needs internet. |
| `piper` | Free | Yes | **Fully offline** neural TTS. Needs `piper-tts` + a model in `piper_voices/`. Rate ✓, pitch ✗. |
| `kokoro` | Free | Yes | **Fully offline** Kokoro-82M neural TTS (higher quality). Needs `kokoro-onnx` + model in `kokoro_models/`. Rate ✓, pitch ✗. |
| `apple` | Free | Yes | macOS built-in `say`. Zero downloads. macOS only. Rate ✓, pitch ✗. |
| `openai` | Paid | No | OpenAI TTS. **Experimental** — set `OPENAI_API_KEY`; rate maps to `speed`, pitch unsupported. Not yet verified end-to-end. |

Offline models are managed by the manifest — run `python sync_voices.py` (or
`python bootstrap.py`) to download exactly what `voices.manifest.json` lists.

```bash
narrate --provider edge --list-voices en-US        # free online voices
narrate mydoc.docx --provider piper --voice en_US-lessac-medium   # offline
export OPENAI_API_KEY=sk-...                        # to try the paid provider
narrate mydoc.docx --provider openai --voice nova
```

### Offline providers (Piper & Kokoro)

The simplest way is `python bootstrap.py`, which installs the packages and
downloads the models from `voices.manifest.json`. Manually:

```bash
pip install -r requirements-local.txt        # piper-tts, kokoro-onnx, soundfile
python sync_voices.py                         # download models per the manifest
narrate mydoc.docx --provider piper  --voice en_US-lessac-medium
narrate mydoc.docx --provider kokoro --voice af_heart
```

- **Piper** models live in `piper_voices/` (override with `PIPER_VOICE_DIR`).
  Browse voices at <https://huggingface.co/rhasspy/piper-voices>; add names to the
  manifest's `piper.voices` list.
- **Kokoro** models (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) live in
  `kokoro_models/` (override with `KOKORO_MODEL_DIR`); voices like `af_heart`,
  `am_adam`. Models from <https://github.com/thewh1teagle/kokoro-onnx/releases>.
- **Apple `say`** (macOS) needs nothing — it appears automatically on a Mac.

Each provider always appears in the UI with its availability status. Offline
providers become usable once their package and checksum-verified models are
present.

Adding a provider (ElevenLabs/Azure/…) means subclassing `TTSProvider` and
registering it — see [`ROADMAP.md`](ROADMAP.md) Phase 2.

---

## SSL / corporate-proxy networks

Some corporate networks, proxies, and sandboxes intercept HTTPS with a
**self-signed certificate** that your operating system trusts but that isn't in
the `certifi` package's bundled CA store. Because `edge-tts` hardcodes
`ssl.create_default_context(cafile=certifi.where())` internally, synthesis can
fail there with a `self-signed certificate in certificate chain` error **even
though normal HTTPS works fine**.

`narrate` handles this automatically: it tries the default `edge-tts` SSL context
first, and on a certificate-verification error it **falls back to the system
trust store** by monkey-patching `edge_tts.communicate._SSL_CTX` and
`edge_tts.voices._SSL_CTX` to `ssl.create_default_context()`, then retries. When
this happens you'll see a clear note on stderr:

```
! SSL certificate verification failed against certifi's bundle.
! Falling back to the system trust store (edge_tts._SSL_CTX -> ssl.create_default_context()).
```

No configuration needed — it only engages if the default path fails.

---

## How it works (pipeline)

```
extract (ordered blocks)  ->  clean for speech  ->  chunk (~3000 chars, on
paragraph/sentence boundaries)  ->  selected TTS provider (resumable)
->  ffmpeg concat/transcode
->  final MP3/M4A/WAV  (+ duration & size)
```

The reusable engine lives in [`narrate.py`](narrate.py); provider integrations
live in [`providers.py`](providers.py). Synchronous callers use
`build_audiobook(...)`, while async servers can await
`build_audiobook_async(...)` without nesting an event loop.

## Testing

After bootstrapping:

```bash
python -m unittest discover -s tests -v
python -m compileall -q narrate.py providers.py media_tools.py app.py sync_voices.py bootstrap.py web_api scripts
npm --prefix web test
cargo fmt --manifest-path web/src-tauri/Cargo.toml -- --check
cargo clippy --manifest-path web/src-tauri/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path web/src-tauri/Cargo.toml
```

The suite covers DOCX/PDF/Markdown/text extraction, cleanup, chunking, rules,
resumable cache invalidation, UI cache/output identities, checksum enforcement,
provider conversions, and the async pipeline. Piper and Kokoro can also be
exercised locally with the sample fixture in `tests/fixtures/sample.txt`.

## Requirements

- Python 3.10+ for the full bootstrap/UI
- `edge-tts`, `python-docx`, `pypdf` (see [requirements.txt](requirements.txt))
- `ffmpeg` / `ffprobe` on `PATH` for source interfaces; explicitly staged
  resources for the packaged desktop
- An internet connection for Edge/OpenAI synthesis and initial model downloads;
  Piper, Kokoro, and Apple run offline afterward
