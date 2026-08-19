# Orpheus — the expressive, self-hosted provider

Orpheus is the only provider in this project that **performs** emotion tags such
as `<laugh>` and `<sigh>` rather than reading the words aloud. It is also the
only one that cannot be installed by `bootstrap.py`.

> **Status:** Built, verified end-to-end against a live container on The Beast,
> and confirmed by listening. Ready to use — see
> [Verification status](#verification-status).

## Why this provider is different

`piper` and `kokoro` are portable: their models are listed in
`voices.manifest.json` and `bootstrap.py` fetches them on any machine. Orpheus
cannot work that way. It needs:

- an NVIDIA GPU with roughly **4.5 GB** of VRAM free,
- **two** Docker containers — `orpheus-llm` (a `llama.cpp` server holding the
  model) and `orpheus-tts` (the SNAC decoder that presents the HTTP API),
- a **~2 GB** GGUF model that is in neither this repository nor the manifest.

So it is treated like the `apple` provider: present in the registry, and
`is_available()` returns `False` everywhere it is not explicitly configured. It
is deliberately **excluded from the Phase 4A Edge-only desktop build**.

## Setup

The containers live in the `Ãye` project under `deploy/orpheus`. Once
`docker compose up -d` reports both healthy, point this project at the decoder:

```bash
# macOS / Linux
export ORPHEUS_BASE_URL=http://localhost:8013

# Windows PowerShell
$env:ORPHEUS_BASE_URL = "http://localhost:8013"
```

Confirm it is now offered:

```bash
.venv/Scripts/python -c "import providers as P; print([p.id for p in P.available_providers()])"
```

`orpheus` appears in that list only when `ORPHEUS_BASE_URL` is set to an
`http://` or `https://` URL. **Unset it and the provider silently disappears
again**, which is exactly what makes the same checkout safe on a laptop with no
GPU.

### Environment variables

| Variable | Default | Notes |
| --- | --- | --- |
| `ORPHEUS_BASE_URL` | _(empty)_ | Decoder base URL. **Empty means the provider is not offered at all** |
| `ORPHEUS_TTS_MODEL` | `orpheus` | Model name forwarded to the decoder; part of the chunk cache identity |
| `ORPHEUS_TIMEOUT` | `300` | Seconds to wait for one chunk. An unparseable value falls back to 300 |

`ORPHEUS_TTS_MODEL` and `ORPHEUS_TIMEOUT` are read when the provider is first
constructed, and `get_provider()` memoizes instances — so change them before the
first call, not mid-session. `ORPHEUS_BASE_URL` is read live on every call and
can be set at any time.

## Usage

```bash
.venv/Scripts/python narrate.py book.docx --provider orpheus --voice tara
```

Voices: `tara`, `leah`, `jess`, `mia`, `zoe` (female); `leo`, `dan`, `zac`
(male). The catalogue is static — the decoder is not queried for it.

## Chunk size is negotiated, not configured

Orpheus declares `preferred_max_chars = 400` in its capabilities, and
`narrate.resolve_max_chars()` applies it automatically. You do **not** need to
pass `--max-chars` — omitting it now means "whatever this engine wants" rather
than the old fixed 3000.

The global default is sized for edge-tts, where chunking is network-bound and
large requests are cheap. Orpheus is a 3B LLM emitting audio codes token by
token; at 3000 characters it drifts or truncates well before the end of a chunk.
Roughly a short paragraph is the safe unit.

```bash
narrate.py book.docx --provider orpheus --voice tara         # 400, chosen for you
narrate.py book.docx --provider orpheus --voice tara --max-chars 250   # override
```

An explicit `--max-chars` still wins, so the flag remains available if a
particular document needs a smaller unit. Whatever value is resolved goes into
the chunk-cache fingerprint, so changing it correctly invalidates the cache
instead of mixing chunk sizes inside one book.

## Emotion tags

Orpheus performs these eight, and only in angle brackets:

`<laugh>` `<chuckle>` `<sigh>` `<gasp>` `<groan>` `<yawn>` `<cough>` `<sniffle>`

Square brackets, asterisks and parentheses are **not** understood by the model
and would be read aloud as words.

### Nothing inserts them for you

A `.docx` does not contain `<laugh>`. Choosing Orpheus buys you a markedly more
natural *baseline* voice on every sentence; the performed laughs appear only
where the source text has the tags. There are two ways to get them there.

**Write them into the document directly.** They survive text cleaning untouched.

**Map existing prose cues with replacement rules.** If your manuscript already
writes `(laughs)` or `*sighs*`, the shipped preset converts them:

```bash
narrate.py book.docx --provider orpheus --voice tara \
  --rules rules.orpheus.example.json
```

Rules run *before* tag handling, so a rule may introduce a tag. You can also do
it inline: `--replace "(laughs)=<laugh>"`.

### The tags are stripped for every other engine

This is the part that matters. `edge`, `piper`, `kokoro`, `apple` and `openai`
would all read `<laugh>` as the word "laugh". So `clean_for_speech()` removes
the tags unless the selected provider advertises `supports_emotion_tags`, and
`build_audiobook_async()` reads that capability from the provider rather than
taking it on trust from the caller.

The practical consequence: **a tagged document is safe to render with any
provider.** Orpheus performs the tags; everything else silently drops them,
including the whitespace and stray punctuation they would otherwise leave
behind. You do not have to maintain two copies of the manuscript.

Only the eight known tags are touched. A document containing `<placeholder>` or
`<html>` keeps today's behaviour of having it read aloud — the change is
deliberately narrow rather than "strip anything in angle brackets".

## What it does not support

`capabilities()` reports `supports_rate=False` and `supports_pitch=False`, so
both controls disable themselves in the UI. This is not an omission: Orpheus is
an autoregressive LLM writing audio tokens and has no speed or pitch parameter
to set. Changing tempo would mean an ffmpeg `atempo` pass after synthesis, which
would also have to enter the cache fingerprint — deliberately out of scope.

## The two decisions worth knowing

**It requests WAV, not MP3, even though the container can return either.**
`narrate.assemble()` concatenates chunks with an ffmpeg **stream copy** (`-c
copy`), which requires every chunk to share identical codec parameters. Taking
MP3 straight from the container would inherit whatever its encoder is configured
to emit; if that configuration ever changed, chunks already sitting in the
resumable cache would silently disagree with new ones and the concatenated book
would come out corrupt or mistimed. Requesting WAV and encoding locally through
`_transcode_wav_to_mp3` pins every chunk to the same libmp3lame 64k settings that
`piper` and `kokoro` already produce.

As a side benefit this is robust to the decoder ignoring `response_format`:
ffmpeg probes the container rather than trusting the file extension, so MP3 bytes
arriving in the temporary `.wav` still transcode correctly.

**`is_available()` never makes a network request.** `available_providers()`
calls it on every provider, so a reachability probe there would stall every UI
that lists providers — by the full connect timeout, on a machine where the
container is simply not running. It therefore only checks that the URL is
configured and well-formed; an unreachable decoder surfaces as a clear error at
synthesize time instead.

## Expect it to be slow

Measured real-time factor is **0.55–0.62 idle**, and about **0.9 when the GPU is
also serving a chat model**. That is generation time, not playback: a six-hour
audiobook is upwards of three and a half hours of GPU work, and
`narrate.generate_chunks()` is a strictly sequential loop.

Two things make this tolerable:

- **Renders are resumable.** Completed chunk MP3s are skipped on the next run, so
  an interrupted book picks up where it stopped.
- **Nothing else has to wait.** Start it and come back later.

If you are also running Ollama on the same GPU, expect contention — the failure
mode is audio stuttering inside a chunk rather than an error.

## Two behaviours to expect

**Chunks are synthesized three at a time.** Orpheus declares
`max_concurrent_chunks = 3`, measured rather than guessed — over six chunks on The
Beast: 1x took 29s, 2x took 18s, 3x took 15s, and 4x regressed to 16s as the GPU
began contending with itself. End to end through the CLI that is roughly a 1.5-1.9x
speedup. Override it with `--concurrency N` (`1` forces the old serial behaviour).

**The same text does not produce identical audio twice.** Orpheus samples like any
other LLM, so re-rendering a chunk gives a slightly different take — two identical
serial runs of the same document measured 33.7s and 36.2s of audio. Two consequences:

- Deleting one chunk from the resume cache and re-rendering it can leave a subtle
  seam, because its neighbours came from a different sampling run. If a render is
  worth keeping, keep the whole `_chunks` directory.
- Duration will not match exactly between runs. That is the model, not a fault, and
  not something concurrency changed — the variation is identical with
  `--concurrency 1`.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `orpheus` missing from `available_providers()` | `ORPHEUS_BASE_URL` unset, or set to something that is not an `http(s)://` URL |
| `Could not reach the Orpheus decoder at …` | Containers not running, or the port differs from 8013 |
| `Orpheus decoder timed out after 300s` | Chunk too large or GPU contended — lower `--max-chars`, or raise `ORPHEUS_TIMEOUT` |
| A voice says "laugh" out loud | Should no longer happen — tags are stripped for engines that cannot perform them. If it does, the text bypassed `clean_for_speech()` |
| `ffmpeg is required to encode local-provider audio` | `ffmpeg` not on PATH; run `python bootstrap.py` |

## Verification status

Honest accounting of what has actually been exercised:

| | Status |
| --- | --- |
| Provider surface, gating, request shape, error mapping | **Tested** — offline unit tests in `tests/test_orpheus_provider.py` |
| Chunk-size negotiation, emotion-tag stripping | **Tested** — including an end-to-end check that the same document yields tags for Orpheus and none for edge |
| Live synthesis against a real `orpheus-tts` container | **Verified** — 2026-08-18, `http://localhost:8013` |
| Output encoding | **Verified** — MP3 / 24 kHz / mono / 64 kbps, matching Piper and Kokoro |
| End-to-end audiobook render | **Verified** — 4 blocks → 2 chunks → ffmpeg stream-copy assembly |
| Resume from cached chunks | **Verified** — a second run reused existing chunk MP3s |
| Rule-driven tags reaching the engine | **Verified** — `(laughs)` → `<laugh>` for Orpheus, removed for edge |
| **How the audio sounds** | **Confirmed by ear** — 2026-08-19 |

Every HTTP call in the *test suite* is mocked, so the tests prove the provider
sends the right request and handles the documented failures. The live checks
above were run separately against the real container and confirm the decoder
accepts that request and returns usable audio.

Subjective quality was checked separately, by listening: the sample renders were
confirmed good on 2026-08-19. Note that no automated check covers this — the test
suite cannot tell a natural read from a mangled one, so **re-listen after changing
the decoder, the model, or the chunk size** rather than trusting a green suite.

Measured throughput was roughly 0.70 RTF with other containers sharing the GPU
(5.8 s of generation for 6.1 s of audio on a single utterance; 18.9 s for 27 s of
assembled audiobook).
