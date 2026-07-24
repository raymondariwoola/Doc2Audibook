# Doc2Audiobook — `narrate`

Turn a document (`.docx`, `.pdf`, `.txt`, or `.md`) into a natural-sounding,
audiobook-style **MP3** using Microsoft's free
[`edge-tts`](https://github.com/rany2/edge-tts) neural voices.

**No paid API key required.** The voices are genuinely natural (not the robotic
`say`/`espeak` kind), the pipeline is **resumable** if it's interrupted, and the
final audio is stitched together losslessly with `ffmpeg`.

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

### 1. Python dependencies

Requires **Python 3.9+**.

```bash
# from the project folder
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Or install it as a command (`narrate`) on your PATH:

```bash
pip install -e .
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

## Usage

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
