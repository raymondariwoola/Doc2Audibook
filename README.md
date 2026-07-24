# Doc2Audiobook — `narrate`

Turn a document (`.docx`, `.pdf`, `.txt`, or `.md`) into a natural-sounding,
audiobook-style **MP3** using Microsoft's free
[`edge-tts`](https://github.com/rany2/edge-tts) neural voices.

**No paid API key required.** The voices are genuinely natural (not the robotic
`say`/`espeak` kind), the pipeline is **resumable** if it's interrupted, and the
final audio is stitched together losslessly with `ffmpeg`.

Two ways to use it:

- **CLI** — `narrate mydoc.docx` (scriptable, resumable, batteries-included).
- **Browser UI** — `python app.py` opens a local app to attach a document,
  **preview a voice before committing**, tune pitch & pace, and export MP3/M4A/WAV.

See [`ROADMAP.md`](ROADMAP.md) for where this is headed (paid-voice providers,
a full local web app, and native macOS/Windows desktop builds).

---

## Quick start (and switching machines)

One command sets up **everything** on macOS, Windows, or Linux — and it's the
same command you run when you move to another machine or clone fresh:

```bash
python bootstrap.py
```

It creates a `.venv`, installs dependencies, checks for `ffmpeg` (with install
hints if missing), and downloads the offline voice models listed in
[`voices.manifest.json`](voices.manifest.json). It's **idempotent** — re-run it
after every `git pull` to stay in lockstep.

**What syncs across machines:** all code, the roadmap, and *which* voices you use
(the manifest) travel via git. The large bits — the `.venv`, the voice model
files, generated audio, and the resumable chunk cache — are gitignored and
rebuilt/re-fetched locally by `bootstrap.py`. So on a new machine: `git pull`
→ `python bootstrap.py` → you're back where you left off. To add a voice for all
machines, edit `voices.manifest.json`, commit, and run `python sync_voices.py`
on each. AI agents working in this repo should read
[`AGENTS.md`](AGENTS.md) for this workflow.

---

## Features

- **Real neural voices** via `edge-tts` — pick any with `--voice`, list them with `--list-voices`.
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
- **Chunked + resumable.** Text is split into `<3000`-char chunks on paragraph
  boundaries (never mid-sentence). Each chunk becomes its own MP3; **re-running
  the same command skips any chunk whose MP3 already exists**, so an interrupted
  run picks up where it left off.
- **Configurable rate** with `--rate` (default `-4%`, a touch slower than normal).
- **Lossless assembly.** Chunks are concatenated with `ffmpeg -c copy` (stream
  copy, no re-encoding), and the final duration + file size are printed.
- **Corporate-proxy SSL fallback** (see [below](#ssl--corporate-proxy-networks)).

---

## Installation

The easiest path is `python bootstrap.py` (see [Quick start](#quick-start-and-switching-machines)
above). To do it by hand instead:

### 1. Python dependencies

Requires **Python 3.9+**.

```bash
# from the project folder
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt                 # core CLI (edge-tts online)
pip install -r requirements-ui.txt              # optional: browser UI
pip install -r requirements-local.txt           # optional: offline Piper/Kokoro
```

Or install it as a command (`narrate`) on your PATH, with optional extras:

```bash
pip install -e .                    # core
pip install -e ".[ui,local]"        # + browser UI + offline providers
```

### 2. `ffmpeg` (external dependency — required)

`ffmpeg` (and `ffprobe`, which ships with it) must be on your PATH.

| OS | Install |
|----|---------|
| **macOS** | `brew install ffmpeg` |
| **Windows** | `winget install Gyan.FFmpeg`  (or `choco install ffmpeg`) |
| **Linux (Debian/Ubuntu)** | `sudo apt install ffmpeg` |
| **Linux (Fedora)** | `sudo dnf install ffmpeg` |

Verify with `ffmpeg -version`.

---

## Browser UI (Gradio prototype)

A local web app over the same engine — nothing is uploaded anywhere; it runs on
your machine.

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

Renders land in `outputs/`; a chunk cache in `.narrate_cache/` (keyed by the
document and voice settings) makes re-runs resume instantly. This is **Phase 1** of the
[roadmap](ROADMAP.md) — the long-term UI is a FastAPI + React app and native
desktop builds.

---

## Usage (CLI)

```
narrate <input_file> [--output out.mp3] [--voice VOICE] [--rate -4%]
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

Just run the **exact same command again**. Chunk MP3s already on disk are
skipped and only the missing ones are generated:

```
Synthesizing chunks:
  [  1/6]   24 chars  — skipped (already exists)
  [  2/6]  114 chars  — done
  [  3/6]  104 chars  — skipped (already exists)
  ...
```

Chunks live in `<output>_chunks/` (override with `--work-dir`). They are deleted
on success unless you pass `--keep-chunks`.

---

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `input` | — | Document to narrate (`.docx`, `.pdf`, `.txt`, `.md`). |
| `-o, --output` | `<input>.mp3` | Output MP3 path. |
| `--voice` | `en-US-AndrewNeural` | Any `edge-tts` voice (`--list-voices` to browse). |
| `--rate` | `-4%` | Speech rate, e.g. `-10%` slower, `+5%` faster. |
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

Each offline provider appears in the CLI (`--provider`) and the browser UI's
provider dropdown once its package + model are present.

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
extract (ordered blocks)  ->  clean for speech  ->  chunk (<3000 chars, on
paragraph boundaries)  ->  edge-tts per chunk (resumable)  ->  ffmpeg concat
(stream copy)  ->  final .mp3  (+ duration & size)
```

Everything lives in a single module, [`narrate.py`](narrate.py).

## Requirements

- Python 3.9+
- `edge-tts`, `python-docx`, `pypdf` (see [requirements.txt](requirements.txt))
- `ffmpeg` / `ffprobe` on PATH
- An internet connection (edge-tts calls Microsoft's free voice service)
